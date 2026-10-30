"""Persistent flight and coherent club motion from this capture's Doppler peaks."""

from dataclasses import dataclass

import numpy as np
from scipy.signal import find_peaks

POLICY = {
    "version": 2,
    "window_samples": 256,
    "hop_samples": 32,
    "window_function": "hamming",
    "retained_peaks": 8,
    "max_gap_frames": 3,
    "hypotheses_per_peak": 2,
    "innovation_resolution_units": 3,
    "flight_span_windows": 3,
    "club_span_windows": 1,
    "club_unaccelerated_span_windows": 2,
    "club_acceleration_resolution_units": 2,
    "club_statistic_peaks": 5,
    "flight_band_min_resolution_units": 0.5,
    "flight_band_sigma": 3,
    "flight_model_interval": "full_qualified_span",
    "continuation_span_windows": 1,
    "continuation_change_resolution_units": 3,
    "approach_change_resolution_units": 2,
}


@dataclass(frozen=True)
class Peak:
    """Observed spectral peak timestamped at the window centre."""

    frame: int
    time_ms: float
    speed_mph: float
    snr: float


@dataclass(frozen=True)
class Path:
    """Bounded-gap association hypothesis with a local velocity prediction."""

    peaks: tuple
    score: float
    velocity: float


@dataclass(frozen=True)
class MotionTrack:
    """Radial-speed statistic and its exact supporting spectral path."""

    speed_mph: float
    peaks: tuple[Peak, ...]
    reference_index: int = 0

    @property
    def timestamp_ms(self):
        """Selected observation's centre, never physical contact time."""
        return self.peaks[self.reference_index].time_ms


@dataclass(frozen=True)
class FlightRejection:
    """A stable portion embedded in a measured changing-speed path."""

    track: MotionTrack
    approach: tuple[Peak, ...]
    continuation: tuple[Peak, ...]
    reason: str = "stable_portion_with_changing_speed_before_and_after"


@dataclass(frozen=True)
class DopplerMotion:
    """Club evidence survives without a qualified ball-flight track."""

    ball: MotionTrack | None
    club: MotionTrack | None
    alternatives: tuple[MotionTrack, ...]
    resolution_mph: float
    window_samples: int = 256
    flight_rejections: tuple[FlightRejection, ...] = ()


def extract(processor, capture):
    """Extract weak as well as strong measured peaks from qualified windows."""
    size = POLICY["window_samples"]
    hop = POLICY["hop_samples"]
    i_values = np.asarray(capture.i_samples, dtype=float)
    q_values = np.asarray(capture.q_samples, dtype=float)
    quality = processor.capture_quality(capture)
    starts = np.arange(0, len(i_values) - size + 1, hop)
    samples = starts[:, None] + np.arange(size)
    blocks = i_values[samples] + 1j * q_values[samples]
    blocks = (blocks - blocks.mean(axis=1, keepdims=True)) * np.hamming(size)
    spectra = np.abs(np.fft.fft(blocks, processor.FFT_SIZE, axis=1)[:, : processor.FFT_SIZE // 2])
    factor = (
        processor.SAMPLE_RATE
        / processor.FFT_SIZE
        * processor.WAVELENGTH_M
        / 2
        * processor.MPS_TO_MPH
    )
    resolution = processor.SAMPLE_RATE / size * processor.WAVELENGTH_M / 2 * processor.MPS_TO_MPH
    frames = []
    for frame, (start, magnitude) in enumerate(zip(starts, spectra)):
        if not quality.supports_window(int(start), int(start) + size):
            frames.append([])
            continue
        floor = np.median(magnitude[processor.DC_MASK_BINS :])
        if floor <= 0:
            frames.append([])
            continue
        bins, _ = find_peaks(
            magnitude,
            height=processor.BALL_LINE_ONSET_SNR * floor,
            distance=round(resolution * 2 / factor),
        )
        bins = [int(index) for index in bins if index >= processor.DC_MASK_BINS]
        bins = sorted(bins, key=magnitude.__getitem__, reverse=True)[: POLICY["retained_peaks"]]
        frames.append(
            [
                Peak(
                    frame,
                    (start + size / 2) / processor.SAMPLE_RATE * 1000,
                    index * factor,
                    float(magnitude[index] / floor),
                )
                for index in bins
            ]
        )
    return frames, resolution


def paths(frames, resolution):
    """Associate peaks with bounded prediction errors, retaining competing paths."""
    active = []
    history = []
    for index, peaks in enumerate(frames):
        candidates = []
        for peak in peaks:
            options = [Path((peak,), float(np.log1p(peak.snr)), 0)]
            for previous in active:
                last = previous.peaks[-1]
                gap = index - last.frame
                if not 1 <= gap <= POLICY["max_gap_frames"]:
                    continue
                dt = peak.time_ms - last.time_ms
                innovation = peak.speed_mph - (last.speed_mph + previous.velocity * dt)
                if abs(innovation) > resolution * POLICY["innovation_resolution_units"]:
                    continue
                velocity = (peak.speed_mph - last.speed_mph) / dt
                score = (
                    previous.score
                    + min(float(np.log1p(peak.snr)), 5)
                    - (innovation / resolution) ** 2
                    - 0.5 * (gap - 1)
                )
                options.append(
                    Path(previous.peaks + (peak,), score, 0.5 * previous.velocity + 0.5 * velocity)
                )
            candidates.extend(
                sorted(options, key=lambda path: path.score, reverse=True)[
                    : POLICY["hypotheses_per_peak"]
                ]
            )
        active = candidates + [
            path for path in active if index - path.peaks[-1].frame < POLICY["max_gap_frames"]
        ]
        history.extend(candidates)
    return history


def stable_flights(history, resolution, window_ms):
    """Find sustained slowly changing portions without assuming they are fastest."""
    candidates = []
    minimum_span = POLICY["flight_span_windows"] * window_ms
    for path in history:
        if len(path.peaks) < 12:
            continue
        peaks = path.peaks
        last = peaks[-1]
        for start in range(len(peaks) - 12):
            part = peaks[start:]
            if last.time_ms - part[0].time_ms < minimum_span:
                break
            speed = np.array([point.speed_mph for point in part])
            if np.ptp(speed) > 2 * resolution:
                continue
            times = np.array([point.time_ms for point in part])
            fit = np.polyfit(times - times[0], speed, 1)
            error = speed - np.polyval(fit, times - times[0])
            if abs(fit[0]) > resolution / window_ms or np.max(np.abs(error)) > resolution:
                continue
            candidates.append((float(np.median(speed)), part, float(np.std(error))))
            break
    return sorted(candidates, key=lambda candidate: (candidate[0], len(candidate[1])), reverse=True)


def qualify_continuations(flights, history, resolution, window_ms):
    """A flat portion of a longer accelerating/decelerating path is not flight."""
    changing = {}
    for path in history:
        last = path.peaks[-1]
        for index, peak in enumerate(path.peaks):
            if last.time_ms - peak.time_ms < POLICY["continuation_span_windows"] * window_ms:
                break
            if abs(last.speed_mph - peak.speed_mph) >= (
                POLICY["continuation_change_resolution_units"] * resolution
            ):
                changing.setdefault(peak, []).append((path.peaks, index))
    accepted, rejected = [], []
    for speed, peaks, residual in flights:
        for full_path, end in changing.get(peaks[-1], []):
            if peaks[0] not in full_path:
                continue
            start = full_path.index(peaks[0])
            if start < 3:
                continue
            approach_speed = float(np.mean([peak.speed_mph for peak in full_path[:3]]))
            if abs(approach_speed - speed) < (
                POLICY["approach_change_resolution_units"] * resolution
            ):
                continue
            rejected.append(
                FlightRejection(MotionTrack(speed, peaks), full_path[:start], full_path[end:])
            )
            break
        else:
            accepted.append((speed, peaks, residual))
    return accepted, tuple(rejected)


def analyze(processor, capture):
    """Recover a flight interval and a separate club peak, including club-only motion."""
    frames, resolution = extract(processor, capture)
    history = paths(frames, resolution)
    window_ms = POLICY["window_samples"] / processor.SAMPLE_RATE * 1000
    flights = stable_flights(history, resolution, window_ms)
    flights, rejected = qualify_continuations(flights, history, resolution, window_ms)
    flight = flights[0] if flights else None
    if flight:
        same = [candidate for candidate in flights if abs(candidate[0] - flight[0]) <= resolution]
        flight = min(same, key=lambda candidate: candidate[1][0].time_ms)
        model_peaks = flight[1]
        model_times = np.array([peak.time_ms for peak in model_peaks])
        model_fit = np.polyfit(
            model_times - model_times[0], [peak.speed_mph for peak in model_peaks], 1
        )
        model_error = np.array([peak.speed_mph for peak in model_peaks]) - np.polyval(
            model_fit, model_times - model_times[0]
        )
        band = max(
            POLICY["flight_band_sigma"] * float(np.std(model_error)),
            resolution * POLICY["flight_band_min_resolution_units"],
        )
        recovered = []
        missing = 0
        for frame in reversed(frames[: flight[1][-1].frame + 1]):
            matches = [
                peak
                for peak in frame
                if abs(peak.speed_mph - np.polyval(model_fit, peak.time_ms - model_times[0]))
                <= band
            ]
            if not matches:
                missing += 1
                if missing >= POLICY["max_gap_frames"]:
                    break
            else:
                recovered.append(
                    min(
                        matches,
                        key=lambda peak: abs(
                            peak.speed_mph - np.polyval(model_fit, peak.time_ms - model_times[0])
                        ),
                    )
                )
                missing = 0
        if recovered:
            recovered.reverse()
            flight = (
                float(np.median([peak.speed_mph for peak in recovered])),
                tuple(recovered),
                float(np.std(model_error)),
            )
    limit = flight[1][0].time_ms + 2 * window_ms if flight else float("inf")
    if flight:
        earliest_flight_ms = flight[1][0].time_ms
        flight_times = np.array([peak.time_ms for peak in flight[1]])
        flight_fit = np.polyfit(
            flight_times - flight_times[0], [peak.speed_mph for peak in flight[1]], 1
        )
        residual = np.array([peak.speed_mph for peak in flight[1]]) - np.polyval(
            flight_fit, flight_times - flight_times[0]
        )
        ball_band = max(
            POLICY["flight_band_sigma"] * float(np.std(residual)),
            resolution * POLICY["flight_band_min_resolution_units"],
        )
    eligible = []
    statistic_size = POLICY["club_statistic_peaks"]
    half_statistic = statistic_size // 2
    for path in history:
        part = tuple(peak for peak in path.peaks if peak.time_ms <= limit)
        if (
            len(part) < statistic_size + 1
            or part[-1].time_ms - part[0].time_ms < POLICY["club_span_windows"] * window_ms
        ):
            continue
        speeds = np.array([peak.speed_mph for peak in part])
        smoothed = np.convolve(speeds, np.ones(statistic_size) / statistic_size, mode="valid")
        if flight:
            is_ball = np.array(
                [
                    peak.time_ms >= earliest_flight_ms
                    and abs(peak.speed_mph - np.polyval(flight_fit, peak.time_ms - flight_times[0]))
                    <= ball_band
                    for peak in part
                ]
            )
            overlaps_ball = (
                np.convolve(is_ball.astype(int), np.ones(statistic_size), mode="valid") > 0
            )
            smoothed[overlaps_ball] = -np.inf
        if not np.isfinite(smoothed).any():
            continue
        position = int(np.argmax(smoothed)) + half_statistic
        approach = part[: position + half_statistic + 1]
        span = approach[-1].time_ms - approach[0].time_ms
        rise = float(smoothed.max()) - float(np.mean([peak.speed_mph for peak in approach[:3]]))
        if (
            span < POLICY["club_unaccelerated_span_windows"] * window_ms
            and rise < POLICY["club_acceleration_resolution_units"] * resolution
        ):
            continue
        if part[position].speed_mph > processor.CLUB_MAX_PLAUSIBLE_SPEED_MPH:
            continue
        eligible.append((float(smoothed.max()), part, position))
    club = max(eligible, key=lambda value: (value[0], len(value[1]))) if eligible else None
    ball_reference = 0
    if flight:
        ball_reference = next(
            (
                index
                for index, peak in enumerate(flight[1])
                if peak.time_ms >= flight[1][0].time_ms + window_ms
            ),
            len(flight[1]) - 1,
        )
    return DopplerMotion(
        MotionTrack(flight[0], flight[1], ball_reference) if flight else None,
        MotionTrack(club[0], club[1], club[2]) if club else None,
        tuple(MotionTrack(candidate[0], candidate[1]) for candidate in flights[:3]),
        resolution,
        flight_rejections=rejected,
    )

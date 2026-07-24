#!/usr/bin/env python3
"""Spin estimation from overlapped-STFT speed-track ripple (offline).

The golf ball seam modulates the radar return once per revolution: in
amplitude (AM — what production detect_spin sees via the bandpass envelope)
and in apparent Doppler frequency (FM — the "ripple in the speed reports"
described by OmniPreSense when the main speed FFT runs with overlapped
128-sample windows). This module extracts both tracks from an overlapped
STFT and recovers the seam tone from either, mirroring the production
detect_spin gates (seam band, SNR floor, split-half persistence, rails,
minimum cycles) so results are comparable.

Offline/experimental only — nothing in src/ imports this.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

# Radar/STFT constants — must match RollingBufferProcessor.
SAMPLE_RATE = 30000
WINDOW_SIZE = 128
FFT_SIZE = 4096
WAVELENGTH_M = 0.01243
MPS_TO_MPH = 2.23694

# Ball-tone tracking band around the detected ball speed.
TOLERANCE_MPH = 8.0

# Seam-tone gates — values mirror production detect_spin.
MIN_SEAM_HZ = 33.0            # ~2000 RPM
MAX_SEAM_HZ = 200.0           # 12000 RPM
TRACK_FFT_SIZE = 8192         # zero-padded ripple FFT
DETREND_POLY_ORDER = 3
SNR_MIN = 2.5
MIN_CYCLES = 2.0
MIN_TRACK_DURATION_MS = 20.0  # mirrors SPIN_MIN_SAMPLES (600 samples @ 30 ksps)
RAIL_GUARD_BINS = 2           # padded bins; production parity with
                              # SPIN_UPPER_RAIL_BINS — the peak must sit
                              # essentially at the band edge to be
                              # rail-flagged

# Ball-signal-loss trim — production constants, expressed in raw samples;
# converted to track windows by hop at use time.
SIGNAL_LOSS_SMOOTH_SAMPLES = 90
SIGNAL_LOSS_REF_SAMPLES = 450
SIGNAL_LOSS_THRESHOLD = 0.15
SIGNAL_LOSS_HOLD_SAMPLES = 150

# Expected-spin prior disambiguation — production thresholds.
PRIOR_MIN_RELATIVE_MAG = 0.40
PRIOR_MAX_RELATIVE_ERROR = 0.55
PRIOR_STRONGEST_FAR_ERROR = 0.45


def _mph_to_hz(mph: float) -> float:
    return 2 * (mph / MPS_TO_MPH) / WAVELENGTH_M


@dataclass
class RippleTrack:
    """Per-window ball-tone measurements from the overlapped STFT."""

    times_ms: np.ndarray   # window-center timestamps
    freq_hz: np.ndarray    # interpolated ball-peak frequency (FM ripple)
    magnitude: np.ndarray  # ball-peak magnitude (AM ripple)

    @property
    def n_windows(self) -> int:
        return len(self.times_ms)


def extract_ripple_track(
    i_samples,
    q_samples,
    ball_speed_mph: float,
    hop: int,
) -> RippleTrack:
    """Track the ball tone through every overlapped STFT window.

    Unlike process_overlapping, no CFAR/threshold gates are applied: the
    peak inside a ±TOLERANCE_MPH band around the expected ball frequency is
    taken in every window so the track is continuous. Peak frequency is
    refined by parabolic interpolation on the zero-padded spectrum.
    """
    i_data = np.asarray(i_samples, dtype=np.float64)
    q_data = np.asarray(q_samples, dtype=np.float64)
    hann = np.hanning(WINDOW_SIZE)

    # Guard against invalid or out-of-range ball speeds.
    if ball_speed_mph <= 0:
        return RippleTrack(
            times_ms=np.array([]),
            freq_hz=np.array([]),
            magnitude=np.array([]),
        )

    bin_hz = SAMPLE_RATE / FFT_SIZE
    center_hz = _mph_to_hz(ball_speed_mph)
    tol_hz = _mph_to_hz(TOLERANCE_MPH)
    lo_bin = max(1, int(np.floor((center_hz - tol_hz) / bin_hz)))
    hi_bin = min(FFT_SIZE // 2 - 2, int(np.ceil((center_hz + tol_hz) / bin_hz)))

    # Guard against out-of-range ball speeds that result in empty search band.
    if hi_bin < lo_bin:
        return RippleTrack(
            times_ms=np.array([]),
            freq_hz=np.array([]),
            magnitude=np.array([]),
        )

    times, freqs, mags = [], [], []
    for start in range(0, len(i_data) - WINDOW_SIZE + 1, hop):
        i_block = i_data[start : start + WINDOW_SIZE]
        q_block = q_data[start : start + WINDOW_SIZE]
        block = (i_block - i_block.mean()) + 1j * (q_block - q_block.mean())
        spectrum = np.abs(np.fft.fft(block * hann, FFT_SIZE))

        band = spectrum[lo_bin : hi_bin + 1]
        peak_bin = int(np.argmax(band)) + lo_bin

        # Parabolic interpolation on the peak and its neighbors.
        y0, y1, y2 = spectrum[peak_bin - 1], spectrum[peak_bin], spectrum[peak_bin + 1]
        denom = y0 - 2 * y1 + y2
        delta = 0.5 * (y0 - y2) / denom if denom != 0 else 0.0
        delta = float(np.clip(delta, -0.5, 0.5))

        times.append((start + WINDOW_SIZE / 2) / SAMPLE_RATE * 1000)
        freqs.append((peak_bin + delta) * bin_hz)
        mags.append(float(y1))

    return RippleTrack(
        times_ms=np.array(times),
        freq_hz=np.array(freqs),
        magnitude=np.array(mags),
    )


def trim_to_ball_window(
    track: RippleTrack, ball_timestamp_ms: float, hop: int
) -> RippleTrack:
    """Trim the track to [ball onset, ball-signal collapse).

    Mirrors production _ball_signal_end_sample: signal is lost when the
    smoothed magnitude stays below SIGNAL_LOSS_THRESHOLD x the early-window
    reference level for a sustained hold period. Returns the full post-onset
    track when no loss is found (outdoor shots).
    """
    mask = track.times_ms >= ball_timestamp_ms
    times = track.times_ms[mask]
    freqs = track.freq_hz[mask]
    mags = track.magnitude[mask]
    if len(mags) == 0:
        return RippleTrack(times, freqs, mags)

    smooth_n = max(1, SIGNAL_LOSS_SMOOTH_SAMPLES // hop)
    ref_n = max(3, SIGNAL_LOSS_REF_SAMPLES // hop)
    hold_n = max(1, SIGNAL_LOSS_HOLD_SAMPLES // hop)

    if len(mags) < ref_n:
        return RippleTrack(times, freqs, mags)
    kernel = np.ones(smooth_n) / smooth_n
    smoothed = np.convolve(mags, kernel, mode="same")
    reference = float(np.median(smoothed[:ref_n]))
    if reference <= 0:
        return RippleTrack(times, freqs, mags)

    below = smoothed < reference * SIGNAL_LOSS_THRESHOLD
    if len(below) < hold_n:
        return RippleTrack(times, freqs, mags)
    sustained = np.convolve(below.astype(float), np.ones(hold_n), mode="valid") >= hold_n
    if not sustained.any():
        return RippleTrack(times, freqs, mags)
    end = int(np.argmax(sustained))
    return RippleTrack(times[:end], freqs[:end], mags[:end])


@dataclass
class RippleSpinResult:
    """Seam-tone detection result from one ripple track."""

    spin_rpm: float
    snr: float
    peak_freq_hz: Optional[float] = None
    seam_cycles: Optional[float] = None
    n_windows: int = 0
    persistent: bool = False
    at_lower_rail: bool = False
    at_upper_rail: bool = False
    rejection_reason: Optional[str] = None

    @property
    def detected(self) -> bool:
        return self.rejection_reason is None


def _reject(reason: str, **kwargs) -> RippleSpinResult:
    return RippleSpinResult(spin_rpm=0.0, snr=0.0, rejection_reason=reason, **kwargs)


def _select_peak(
    valid_mag: np.ndarray, valid_freqs: np.ndarray, expected_spin_rpm: Optional[float]
) -> int:
    """Argmax, unless it is far from a supplied prior and a strong local
    maximum sits near the prior — production _select_spin_peak, simplified."""
    strongest = int(np.argmax(valid_mag))
    if expected_spin_rpm is None or expected_spin_rpm <= 0:
        return strongest
    strongest_error = abs(valid_freqs[strongest] * 60 - expected_spin_rpm) / expected_spin_rpm
    if strongest_error <= PRIOR_STRONGEST_FAR_ERROR:
        return strongest

    interior = (valid_mag[1:-1] > valid_mag[:-2]) & (valid_mag[1:-1] > valid_mag[2:])
    candidates = np.where(interior)[0] + 1
    peak_mag = valid_mag[strongest]
    best = None
    for idx in candidates:
        relative = valid_mag[idx] / peak_mag if peak_mag > 0 else 0.0
        error = abs(valid_freqs[idx] * 60 - expected_spin_rpm) / expected_spin_rpm
        if relative >= PRIOR_MIN_RELATIVE_MAG and error <= PRIOR_MAX_RELATIVE_ERROR:
            if best is None or valid_mag[idx] > valid_mag[best]:
                best = int(idx)
    return best if best is not None else strongest


def _band_spectrum(values: np.ndarray, track_rate_hz: float):
    """Hann-windowed zero-padded magnitude spectrum inside the seam band."""
    windowed = values * np.hanning(len(values))
    magnitude = np.abs(np.fft.fft(windowed, TRACK_FFT_SIZE))
    freqs = np.fft.fftfreq(TRACK_FFT_SIZE, d=1 / track_rate_hz)
    half = TRACK_FFT_SIZE // 2
    magnitude, freqs = magnitude[1:half], freqs[1:half]
    band = (freqs >= MIN_SEAM_HZ) & (freqs <= MAX_SEAM_HZ)
    return magnitude[band], freqs[band]


def _peak_is_persistent(
    values: np.ndarray, peak_freq_hz: float, track_rate_hz: float
) -> bool:
    """Production _spin_peak_is_persistent, ported to the track domain: the
    picked tone must be present and (near-)dominant in both track halves."""
    half = len(values) // 2
    if half < 8:
        return True
    for segment in (values[:half], values[half:]):
        seg = segment - np.mean(segment)
        valid_mag, valid_freqs = _band_spectrum(seg, track_rate_hz)
        if valid_mag.size == 0 or not np.any(valid_mag > 0):
            return False
        floor = float(np.median(valid_mag[valid_mag > 0]))
        tol_hz = 2.0 * track_rate_hz / len(seg)
        near = np.abs(valid_freqs - peak_freq_hz) <= tol_hz
        if not near.any() or floor <= 0:
            return False
        near_max = float(valid_mag[near].max())
        if near_max < 2.5 * floor or near_max < 0.7 * float(valid_mag.max()):
            return False
    return True


def detect_ripple_spin(
    values: np.ndarray,
    track_rate_hz: float,
    *,
    expected_spin_rpm: Optional[float] = None,
) -> RippleSpinResult:
    """Recover the seam tone from one ripple track (frequency or magnitude).

    Detrend (poly order 3) removes the deceleration chirp / range falloff;
    the zero-padded FFT of the residual is searched inside the seam band with
    production-mirrored gates: SNR floor, minimum seam cycles, rail guards,
    and split-half persistence.
    """
    values = np.asarray(values, dtype=np.float64)
    n = len(values)
    duration_ms = n / track_rate_hz * 1000
    if n < 8 or duration_ms < MIN_TRACK_DURATION_MS:
        return _reject(
            f"Track too short ({duration_ms:.1f} ms, need {MIN_TRACK_DURATION_MS:.0f})",
            n_windows=n,
        )

    centered = values - np.mean(values)
    if np.std(centered) < 1e-12:
        return _reject("No ripple variation in track", n_windows=n)
    x = np.arange(n, dtype=np.float64)
    trend = np.polyval(np.polyfit(x, centered, DETREND_POLY_ORDER), x)
    residual = centered - trend
    if np.std(residual) < 1e-12:
        return _reject("No ripple variation after detrend", n_windows=n)

    valid_mag, valid_freqs = _band_spectrum(residual, track_rate_hz)
    if valid_mag.size < 3:
        return _reject("No seam band in track spectrum", n_windows=n)

    peak_idx = _select_peak(valid_mag, valid_freqs, expected_spin_rpm)
    peak_freq = float(valid_freqs[peak_idx])
    peak_mag = float(valid_mag[peak_idx])

    positive = valid_mag[valid_mag > 0]
    noise_floor = float(np.median(positive)) if positive.size else 0.0
    snr = peak_mag / noise_floor if noise_floor > 0 else 0.0

    # Rail guards: fixed padded-bin margin, production parity with
    # SPIN_UPPER_RAIL_BINS. Short-track skepticism is carried by the SNR,
    # cycles, and persistence gates instead.
    rail_bins = RAIL_GUARD_BINS
    at_lower_rail = peak_idx < rail_bins
    at_upper_rail = peak_idx >= len(valid_mag) - rail_bins

    seam_cycles = peak_freq * (n / track_rate_hz)
    persistent = _peak_is_persistent(residual, peak_freq, track_rate_hz)
    diagnostics = dict(
        peak_freq_hz=peak_freq,
        seam_cycles=seam_cycles,
        n_windows=n,
        persistent=persistent,
        at_lower_rail=at_lower_rail,
        at_upper_rail=at_upper_rail,
    )

    if seam_cycles < MIN_CYCLES:
        result = _reject(
            f"Too few seam cycles ({seam_cycles:.1f}, need {MIN_CYCLES:.0f})",
            **diagnostics,
        )
        result.snr = round(snr, 2)
        return result
    if snr < SNR_MIN:
        result = _reject(f"SNR {snr:.2f} below {SNR_MIN}", **diagnostics)
        result.snr = round(snr, 2)
        return result
    if at_lower_rail or at_upper_rail:
        rail = "lower" if at_lower_rail else "upper"
        result = _reject(f"Peak at {rail} rail of seam band", **diagnostics)
        result.snr = round(snr, 2)
        return result
    if not persistent:
        result = _reject("Seam tone not persistent across track halves", **diagnostics)
        result.snr = round(snr, 2)
        return result

    return RippleSpinResult(
        spin_rpm=peak_freq * 60.0,
        snr=round(snr, 2),
        **diagnostics,
    )


VARIANT_NAMES = ("freq_hop32", "mag_hop32", "freq_hop16", "mag_hop16")


def run_ripple_variants(
    i_samples,
    q_samples,
    ball_speed_mph: float,
    ball_timestamp_ms: float,
    *,
    expected_spin_rpm: Optional[float] = None,
    hops: tuple[int, ...] = (32, 16),
) -> dict[str, RippleSpinResult]:
    """Run all hop x track ripple variants for one capture."""
    results: dict[str, RippleSpinResult] = {}
    for hop in hops:
        track = extract_ripple_track(i_samples, q_samples, ball_speed_mph, hop)
        trimmed = trim_to_ball_window(track, ball_timestamp_ms, hop)
        track_rate_hz = SAMPLE_RATE / hop
        results[f"freq_hop{hop}"] = detect_ripple_spin(
            trimmed.freq_hz, track_rate_hz, expected_spin_rpm=expected_spin_rpm
        )
        results[f"mag_hop{hop}"] = detect_ripple_spin(
            trimmed.magnitude, track_rate_hz, expected_spin_rpm=expected_spin_rpm
        )
    return results

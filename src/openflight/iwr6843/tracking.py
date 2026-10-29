"""Ball detection + range-walk tracking on raw L3 dumps.

The chain that found the ball on every swing this weekend:

1. TDM split + range FFT + MTI (subtract each bin's mean over the loops in a
   burst) — static clutter cancels; the static argmax NEVER sees the ball.
2. Per-loop peak detections inside meter-gates that exclude the golfer blob.
3. A line fit of range vs time = the unambiguous ball speed. Doppler is
   useless here: at ~90 us loop PRI a ~50 m/s ball aliases to walking pace.
   Every pair of detections is tried (no random sampling), the consensus lines
   are ranked by explicit rules, and a tie between two different walks is
   reported as ``ambiguous`` with both (``search_range_walks``).

Ring slots stream in memory order; the header's ``trigger_frame`` (fw v2+)
gives the true time order and is treated as authoritative.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

import numpy as np

LOOP_PRI_S = 90e-6  # TX1-to-next-TX1 loop period for the 2TX config
RANGE_SPAN_M = 6.0  # every cfg keeps a 6 m span: bin = 6.0/n_samples
BALL_GATES_M = ((2.25, 3.75), (3.75, 5.5))
SPEED_BOUNDS_MS = (20.0, 90.0)
# fastest-credible selection: slow objects (flying tee ~25 m/s, post-impact
# clubhead ~0.7x ball) outlast the ball in the gates and win a most-inliers
# search. 2026-07-14 TrackMan session: 5 driver + several SW live tracks
# stolen this way. A fast track wins if it has enough support of its own.
FAST_TRACK_MS = 26.5  # RADIAL m/s: slowest real SW ball reads ~27.5
#                               (66 mph x cos projection); flying tee ~25
FAST_SUPPORT_FRAC = 0.55  # of the most-inliers candidate
MAX_RADIAL_ACCEL = 200.0  # m/s^2 sanity for the quadratic refit
#                          and the most a walk's halves may differ by (drag + projection)
# The exhaustive search (search_range_walks). Pairs closer in time than this define
# no usable slope (one frame's loops span about 1 ms).
MIN_PAIR_DT_S = 3e-3
MIN_TRACK_INLIERS = 8
# Two consensus lines sharing this fraction of the smaller inlier set are one walk.
SAME_WALK_OVERLAP = 0.5
# The pick stands alone only when its inliers exceed the runner-up's by this many
# standard errors of a count difference, sqrt(n1 + n2): two-sided ~95 %. Loops within
# a frame repeat one echo, so the counts overstate independence: a flag is if anything
# raised too rarely, never too often.
SUPPORT_SIGNIFICANCE_Z = 2.0
AMBIGUOUS_REASON = "two IWR tracks compete"
# A walk's halves may differ in speed by MAX_RADIAL_ACCEL x their time apart plus this
# many standard errors of the difference before the line is two objects.
CONSISTENCY_SIGMAS = 2.0
# Fewest frames that split into two halves with a slope each.
MIN_CHECK_FRAMES = 4
# A frame's range point is never known better than the bin quantisation, 1/sqrt(12).
QUANTISATION_SD_BINS = 1.0 / math.sqrt(12.0)
CORRIDOR_SIGMAS = 3.0
REJECTED_RECORDED = 3
# Re-selection passes per seed line before its inlier set is taken as it stands.
MAX_REFINE_PASSES = 10
_PAIR_CHUNK = 4096
# The one m/s-to-mph factor for the IWR package.
MPH_PER_MS = 2.23694


def accepted_range_evidence(evidence):
    """Return fitted range evidence only when its radar estimator accepted the track.

    Rejected evidence never becomes camera depth; evidence without a status counts as accepted.
    """
    if evidence is None:
        return None
    status = getattr(evidence, "status", "accepted")
    return evidence if isinstance(status, str) and status.startswith("accepted") else None


@dataclass
class Geometry:
    """Per-dump capture geometry, derived from the dump header."""

    n_frames: int
    chirps_per_frame: int
    n_tx: int
    n_rx: int
    n_samples: int
    frame_period_s: float
    trigger_frame: int
    loop_period_s: float = LOOP_PRI_S
    range_bin_start: int = 0
    range_fft_size: int | None = None
    range_bin_starts: tuple[int, ...] | None = None
    range_bin_counts: tuple[int, ...] | None = None
    frame_time_offsets_s: tuple[float, ...] | None = None

    @property
    def n_loops(self) -> int:
        """TDM loops per frame."""
        return self.chirps_per_frame // self.n_tx

    @property
    def range_res_m(self) -> float:
        """Range bin size in meters."""
        return RANGE_SPAN_M / (self.range_fft_size or self.n_samples)

    def frame_bin_start(self, frame: int | None = None) -> int:
        """Absolute first bin for a frame, or the dump-wide start."""
        if self.range_bin_starts is None:
            return self.range_bin_start
        if frame is None:
            raise ValueError("frame is required for a windowed range dump")
        return self.range_bin_starts[frame]

    def local_bin(self, absolute_bin: int, frame: int | None = None) -> int:
        """Convert full-FFT bin coordinates into this dump's payload index."""
        return absolute_bin - self.frame_bin_start(frame)

    def frame_bin_count(self, frame: int | None = None) -> int:
        """Number of valid stored bins for one frame."""
        if self.range_bin_counts is None:
            return self.n_samples
        if frame is None:
            raise ValueError("frame is required for a variable-width range dump")
        return self.range_bin_counts[frame]

    def contains_bin(self, absolute_bin: int, margin: int = 0, frame: int | None = None) -> bool:
        """True when an absolute range bin is present in this dump payload."""
        local = self.local_bin(absolute_bin, frame)
        return margin <= local < self.frame_bin_count(frame) - margin

    def loop_time(self, frame: int, loop: int) -> float:
        """Seconds from window start for (ring-slot frame, loop)."""
        slot_order = (frame - self.trigger_frame) % self.n_frames
        frame_time = (
            self.frame_time_offsets_s[slot_order]
            if self.frame_time_offsets_s is not None
            else slot_order * self.frame_period_s
        )
        return frame_time + loop * self.loop_period_s

    @property
    def capture_duration_s(self) -> float:
        """Elapsed capture time including one base frame after the final sample."""
        if self.frame_time_offsets_s:
            return self.frame_time_offsets_s[-1] + self.frame_period_s
        return self.n_frames * self.frame_period_s


@dataclass
class BallTrack:
    """Fitted ball range-walk: range(t) = slope_bins*t + intercept_bins."""

    speed_ms: float
    slope_bins: float
    intercept_bins: float
    rms_bins: float
    n_inliers: int
    t_first: float
    t_last: float
    low_confidence: bool
    quad_bins: tuple[float, float, float] | None = None
    # set when another walk's support is not significantly below this one's
    ambiguity: TrackAmbiguity | None = field(default=None, repr=False, compare=False)

    @property
    def speed_mph(self) -> float:
        """Ball speed in mph."""
        return self.speed_ms * MPH_PER_MS

    def bin_at(self, t_s: float) -> float:
        """Predicted (fractional) range bin at time t."""
        return self.slope_bins * t_s + self.intercept_bins

    def range_at(self, t_s: float, range_res_m: float) -> float:
        """Predicted range in meters at time t."""
        return self.bin_at(t_s) * range_res_m

    def speed_ms_at(self, t_s: float, range_res_m: float) -> float:
        """LOCAL radial speed from the quadratic refit (line slope if none).

        The straight-line slope is the track-average; the ball's radial
        speed actually changes along the flight as the geometry projection
        changes. This local value is what the per-snapshot TDM Doppler
        correction needs (2026-07-15 truth-referenced finding).
        """
        if self.quad_bins is None:
            return self.speed_ms
        q2, q1, _q0 = self.quad_bins
        return (2.0 * q2 * t_s + q1) * range_res_m


def mti_filter(
    cube: np.ndarray,
    scope: str = "burst",
    *,
    range_domain: bool = False,
    geometry: Geometry | None = None,
) -> np.ndarray:
    """Raw cube [nf, cpf, nrx, ns] -> complex MTI [nf, 2(tx), loops, nrx, ns].

    Range FFT then static removal. ``scope="burst"`` subtracts each bin's
    mean over the loops of one burst — the standard filter, but it NOTCHES
    balls whose loop-to-loop phase is ~0 mod 2pi (radial speed near
    n x lambda / (2 x loop period): 26.9 m/s on 2-TX, 17.9 m/s on 3-TX; see
    ``shot.notch_spacing_ms``): the ball looks static within a burst and is removed,
    shattering the range walk (the 2026-07-14 5-iron failures).
    ``scope="window"`` subtracts the mean over ALL bursts: statics are
    constant across the full 72 ms window and still cancel, while a
    notch-speed ball moves range bins across the window and survives.
    """
    n_frames, cpf, n_rx, n_samples = cube.shape
    tdm = cube.reshape(n_frames, cpf // 2, 2, n_rx, n_samples)
    tdm = tdm.transpose(0, 2, 1, 3, 4)
    rfft = tdm if range_domain else np.fft.fft(tdm, axis=-1)
    if scope == "window" and geometry is not None and geometry.range_bin_starts is not None:
        fft_size = geometry.range_fft_size or max(
            start + geometry.frame_bin_count(frame)
            for frame, start in enumerate(geometry.range_bin_starts)
        )
        totals = np.zeros((tdm.shape[1], n_rx, fft_size), dtype=complex)
        counts = np.zeros(fft_size, dtype=float)
        for frame, start in enumerate(geometry.range_bin_starts):
            count = geometry.frame_bin_count(frame)
            stop = start + count
            totals[:, :, start:stop] += rfft[frame, ..., :count].sum(axis=1)
            counts[start:stop] += rfft.shape[2]
        counts[counts == 0] = 1.0
        means = totals / counts[None, None, :]
        out = np.zeros_like(rfft)
        for frame, start in enumerate(geometry.range_bin_starts):
            count = geometry.frame_bin_count(frame)
            stop = start + count
            out[frame, ..., :count] = rfft[frame, ..., :count] - means[:, None, :, start:stop]
        return out
    if scope == "window":
        return rfft - rfft.mean(axis=(0, 2), keepdims=True)
    return rfft - rfft.mean(axis=2, keepdims=True)


def loop_power(mti: np.ndarray) -> np.ndarray:
    """MTI residual power per loop: [nf*loops, n_samples]."""
    n_frames = mti.shape[0]
    n_loops = mti.shape[2]
    power = (np.abs(mti) ** 2).sum(axis=(1, 3))
    return power.reshape(n_frames * n_loops, mti.shape[-1])


def _gate_peak_bins(row, lo, hi, snr_min, max_peaks):
    gate = row[lo:hi]
    base = np.median(gate) + 1e-12
    if max_peaks == 1:
        candidates = [int(np.argmax(gate))]
    else:
        padded = np.pad(gate, 1, constant_values=-np.inf)
        candidates = np.flatnonzero((gate > padded[:-2]) & (gate >= padded[2:]))
        candidates = candidates[np.argsort(-gate[candidates], kind="stable")]
    peaks = []
    for idx in candidates:
        if gate[idx] / base <= snr_min:
            continue
        peak = float(lo + idx)
        j = int(peak)
        if lo < j < hi - 1:
            y_0, y_1, y_2 = row[j - 1], row[j], row[j + 1]
            den = y_0 - 2 * y_1 + y_2
            if den < 0 and abs((y_0 - y_2) / (2 * den)) < 1:
                peak += (y_0 - y_2) / (2 * den)
        # Separate refined peaks beyond two 1.2-bin inlier bands: one loop gets one vote.
        if any(abs(peak - previous) < 3.0 for previous in peaks):
            continue
        peaks.append(peak)
        if len(peaks) == max_peaks:
            break
    return peaks


def _detections(
    power: np.ndarray,
    geo: Geometry,
    snr_min: float = 4.0,
    max_range_m: float | None = None,
    gates_m: tuple[tuple[float, float], ...] = BALL_GATES_M,
    max_peaks_per_loop: int = 1,
) -> tuple[np.ndarray, np.ndarray]:
    """Per-loop sub-bin peaks inside ``gates_m``, SNR-gated.

    ``max_range_m`` clamps the gates — set it just short of the NET so
    ball-riding-up-the-net motion never enters the track or angle fits.
    Multiple peaks retain weaker movers; the default preserves single-peak searches.
    """
    if max_peaks_per_loop < 1:
        raise ValueError("max_peaks_per_loop must be positive")
    n_samples = power.shape[1]
    res = geo.range_res_m
    loops_idx: list[int] = []
    bins: list[float] = []
    if geo.range_bin_starts is not None:
        n_loops = geo.n_loops
        for row_index, row in enumerate(power):
            frame = row_index // n_loops
            frame_start = geo.frame_bin_start(frame)
            frame_count = geo.frame_bin_count(frame)
            for lo_m, hi_m in gates_m:
                if max_range_m is not None:
                    hi_m = min(hi_m, max_range_m)
                if hi_m <= lo_m:
                    continue
                abs_lo = int(lo_m / res)
                abs_hi = int(hi_m / res)
                g_lo = max(abs_lo - frame_start, 0)
                g_hi = min(abs_hi - frame_start, frame_count - 2)
                if g_hi - g_lo < 3:
                    continue
                for peak in _gate_peak_bins(row, g_lo, g_hi, snr_min, max_peaks_per_loop):
                    loops_idx.append(row_index)
                    bins.append(frame_start + peak)
        return np.asarray(loops_idx), np.asarray(bins)

    for lo_m, hi_m in gates_m:
        if max_range_m is not None:
            hi_m = min(hi_m, max_range_m)
        if hi_m <= lo_m:
            continue
        abs_lo = int(lo_m / res)
        abs_hi = int(hi_m / res)
        g_lo = max(abs_lo - geo.range_bin_start, 0)
        g_hi = min(abs_hi - geo.range_bin_start, n_samples - 2)
        if g_hi - g_lo < 3:
            continue
        if max_peaks_per_loop > 1:
            for row_index, row in enumerate(power):
                for peak in _gate_peak_bins(row, g_lo, g_hi, snr_min, max_peaks_per_loop):
                    loops_idx.append(row_index)
                    bins.append(geo.range_bin_start + peak)
            continue
        gate = power[:, g_lo:g_hi]
        base = np.median(gate, axis=1) + 1e-12
        idx = np.argmax(gate, axis=1)
        snr = gate[np.arange(len(power)), idx] / base
        for i in np.nonzero(snr > snr_min)[0]:
            peak_local = float(g_lo + idx[i])
            j = int(peak_local)
            if g_lo < j < g_hi - 1:
                y_0, y_1, y_2 = power[i, j - 1], power[i, j], power[i, j + 1]
                den = y_0 - 2 * y_1 + y_2
                if den < 0 and abs((y_0 - y_2) / (2 * den)) < 1:
                    peak_local += (y_0 - y_2) / (2 * den)
            loops_idx.append(i)
            bins.append(geo.range_bin_start + peak_local)
    return np.asarray(loops_idx), np.asarray(bins)


@dataclass(frozen=True)
class TrackAnchor:
    """Where and when the ball sat before it was struck, when another sensor says so.

    Evidence for the corridor gate, never a requirement: a range anchor (the camera's
    resting-ball range) refuses walks first seen short of the resting ball; an impact
    anchor (impact time on this capture's clock) refuses walks seen before impact and,
    with a range too, walks that are elsewhere at impact.
    """

    range_m: float | None = None
    range_sd_m: float | None = None
    impact_t_s: float | None = None
    impact_sd_s: float | None = None
    source: str = "unnamed"


@dataclass(frozen=True)
class TrackCandidate:  # pylint: disable=too-many-instance-attributes
    """One consensus line of the exhaustive search and the evidence it was ranked on."""

    track: BallTrack
    n_frames: int
    range_at_first_m: float
    check: str  # ok | untestable | stalled... | decelerates... | accelerates... | corridor...
    half_speeds_ms: tuple[float, float] | None = None
    ops_speed_ratio: float | None = None
    inliers: np.ndarray | None = field(default=None, repr=False, compare=False)

    @property
    def passes(self) -> bool:
        """Whether the walk survived the consistency and corridor gates."""
        return self.check in ("ok", "untestable")

    def to_dict(self) -> dict:
        """The candidate's evidence as plain JSON values."""
        track = self.track
        return {
            "speed_ms": float(track.speed_ms),
            "range_at_first_m": float(self.range_at_first_m),
            "t_first_s": float(track.t_first),
            "t_last_s": float(track.t_last),
            "n_inliers": int(track.n_inliers),
            "n_frames": int(self.n_frames),
            "rms_bins": float(track.rms_bins),
            "slope_bins": float(track.slope_bins),
            "intercept_bins": float(track.intercept_bins),
            "check": self.check,
            "half_speeds_ms": (
                [float(value) for value in self.half_speeds_ms] if self.half_speeds_ms else None
            ),
            "ops_speed_ratio": (
                float(self.ops_speed_ratio) if self.ops_speed_ratio is not None else None
            ),
        }


@dataclass(frozen=True)
class TrackAmbiguity:
    """Two distinct walks whose support the data cannot separate: both, with evidence."""

    reason: str
    support_z: float
    separation_bins: float
    selected: dict
    runner_up: dict
    runner_up_track: BallTrack = field(repr=False)

    def to_dict(self) -> dict:
        """Both walks' evidence and how far apart their support is, as plain values."""
        return {
            "reason": self.reason,
            "support_z": float(self.support_z),
            "separation_bins": float(self.separation_bins),
            "selected": dict(self.selected),
            "runner_up": dict(self.runner_up),
        }


@dataclass(frozen=True)
class TrackSearch:  # pylint: disable=too-many-instance-attributes
    """The exhaustive search's pick, its strongest distinct competitor, and the verdict."""

    selected: TrackCandidate | None
    runner_up: TrackCandidate | None = None
    ambiguous: bool = False
    support_z: float | None = None
    separation_bins: float | None = None
    # walks ranked above the pick that a gate refused (the strongest few)
    rejected: tuple[TrackCandidate, ...] = ()
    n_detections: int = 0
    n_pairs: int = 0
    n_consensus: int = 0

    @property
    def ambiguity(self) -> TrackAmbiguity | None:
        """The competing walks when the verdict is ambiguous, else None."""
        if not self.ambiguous or self.selected is None or self.runner_up is None:
            return None
        return TrackAmbiguity(
            reason=AMBIGUOUS_REASON,
            support_z=float(self.support_z),
            separation_bins=float(self.separation_bins),
            selected=self.selected.to_dict(),
            runner_up=self.runner_up.to_dict(),
            runner_up_track=self.runner_up.track,
        )

    @property
    def track(self) -> BallTrack | None:
        """The picked walk, carrying the ambiguity when there is one."""
        if self.selected is None:
            return None
        return replace(self.selected.track, ambiguity=self.ambiguity)

    def to_dict(self) -> dict:
        """The search's verdict and evidence as plain JSON values."""
        return {
            "selected": self.selected.to_dict() if self.selected else None,
            "runner_up": self.runner_up.to_dict() if self.runner_up else None,
            "ambiguous": bool(self.ambiguous),
            "reason": AMBIGUOUS_REASON if self.ambiguous else None,
            "support_z": self.support_z,
            "separation_bins": self.separation_bins,
            "rejected": [candidate.to_dict() for candidate in self.rejected],
            "n_detections": int(self.n_detections),
            "n_pairs": int(self.n_pairs),
            "n_consensus": int(self.n_consensus),
        }


def walk_check(  # pylint: disable=too-many-locals
    times: np.ndarray,
    bins: np.ndarray,
    frames: np.ndarray,
    *,
    range_res_m: float,
    min_speed_ms: float,
) -> tuple[str, tuple[float, float] | None]:
    """Whether a walk's inliers move as one ball: its older and newer halves agree.

    One point per frame (the median of that frame's inliers: a frame's loops repeat one
    echo's argmax). The halves' speeds may differ by what drag and the changing radial
    projection allow (MAX_RADIAL_ACCEL over the time between the halves) plus
    CONSISTENCY_SIGMAS standard errors. Beyond that the line joins two objects: a half
    slower than the search's slowest credible speed has ``stalled`` (a return that stays
    put); otherwise it ``decelerates`` or ``accelerates``. Under MIN_CHECK_FRAMES frames
    there is nothing to compare: ``untestable``.
    """
    labels = np.unique(frames)
    if labels.size < MIN_CHECK_FRAMES:
        return "untestable", None
    points = np.array(
        [
            (float(np.median(times[frames == label])), float(np.median(bins[frames == label])))
            for label in labels
        ]
    )
    points = points[np.argsort(points[:, 0], kind="stable")]
    half = len(points) // 2
    fits = []
    squared = 0.0
    for part in (points[:half], points[half:]):
        centred = part[:, 0] - part[:, 0].mean()
        sxx = float(np.dot(centred, centred))
        if sxx <= 0.0:
            return "untestable", None
        slope = float(np.dot(centred, part[:, 1] - part[:, 1].mean())) / sxx
        residual = part[:, 1] - part[:, 1].mean() - slope * centred
        squared += float(np.dot(residual, residual))
        fits.append((slope * range_res_m, sxx, float(part[:, 0].mean())))
    dof = len(points) - 4
    point_sd = max(math.sqrt(squared / dof) if dof > 0 else 0.0, QUANTISATION_SD_BINS)
    v_old, sxx_old, t_old = fits[0]
    v_new, sxx_new, t_new = fits[1]
    se_old = point_sd * range_res_m / math.sqrt(sxx_old)
    se_new = point_sd * range_res_m / math.sqrt(sxx_new)
    halves = (v_old, v_new)
    allowance = MAX_RADIAL_ACCEL * (t_new - t_old) + CONSISTENCY_SIGMAS * math.hypot(se_old, se_new)
    if abs(v_new - v_old) <= allowance:
        return "ok", halves
    slow_speed, slow_se = (v_old, se_old) if v_old < v_new else (v_new, se_new)
    if slow_speed < min_speed_ms - CONSISTENCY_SIGMAS * slow_se:
        return f"stalled: one half walks at {slow_speed:.1f} m/s", halves
    verb = "decelerates" if v_new < v_old else "accelerates"
    return f"{verb}: {v_old:.1f} -> {v_new:.1f} m/s between its halves", halves


def _corridor(  # pylint: disable=too-many-arguments
    anchor: TrackAnchor | None,
    *,
    slope_bins: float,
    intercept_bins: float,
    t_first: float,
    range_res_m: float,
    tol_bins: float,
) -> str | None:
    """Why a walk lies outside the anchor's corridor, or None when it fits (or no anchor)."""
    if anchor is None:
        return None
    band_m = tol_bins * range_res_m
    range_sd = float(anchor.range_sd_m or 0.0)
    if anchor.range_m is not None:
        start_m = (slope_bins * t_first + intercept_bins) * range_res_m
        if start_m < anchor.range_m - CORRIDOR_SIGMAS * range_sd - band_m:
            return (
                f"corridor: first seen at {start_m:.2f} m, short of the resting ball "
                f"({anchor.range_m:.2f} +/- {range_sd:.2f} m, {anchor.source})"
            )
    if anchor.impact_t_s is not None:
        impact_sd = float(anchor.impact_sd_s or 0.0)
        if t_first < anchor.impact_t_s - CORRIDOR_SIGMAS * impact_sd:
            return (
                f"corridor: seen at {1e3 * t_first:.1f} ms, before impact "
                f"({1e3 * anchor.impact_t_s:.1f} +/- {1e3 * impact_sd:.1f} ms, {anchor.source})"
            )
        if anchor.range_m is not None:
            at_impact_m = (slope_bins * anchor.impact_t_s + intercept_bins) * range_res_m
            allowed = (
                CORRIDOR_SIGMAS * math.hypot(range_sd, slope_bins * range_res_m * impact_sd)
                + band_m
            )
            if abs(at_impact_m - anchor.range_m) > allowed:
                return (
                    f"corridor: at impact it is at {at_impact_m:.2f} m, not at the resting "
                    f"ball ({anchor.range_m:.2f} m, {anchor.source})"
                )
    return None


def _fit_sets(
    sets: np.ndarray, times: np.ndarray, bins: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Least-squares lines of many inlier sets at once: (count, slope, intercept, usable).

    Sums are taken about the detections' centre so the normal equations keep precision.
    """
    weights = sets.astype(float)
    count = weights.sum(axis=1)
    t_centre, b_centre = float(times.mean()), float(bins.mean())
    tc, bc = times - t_centre, bins - b_centre
    s_t, s_b = weights @ tc, weights @ bc
    s_tt, s_tb = weights @ (tc * tc), weights @ (tc * bc)
    denominator = count * s_tt - s_t * s_t
    usable = denominator > 1e-12 * np.maximum(count * s_tt, 1e-300)
    with np.errstate(divide="ignore", invalid="ignore"):
        slopes = np.where(usable, (count * s_tb - s_t * s_b) / denominator, 0.0)
        centred_icpt = np.where(count > 0, (s_b - slopes * s_t) / count, 0.0)
    intercepts = b_centre + centred_icpt - slopes * t_centre
    return count, slopes, intercepts, usable


def _unique_rows(packed: np.ndarray) -> np.ndarray:
    """The distinct rows of a packed-bit matrix, in a fixed order (exact, hash-sorted)."""
    if len(packed) < 2:
        return packed
    width = -(-packed.shape[1] // 8) * 8
    padded = np.zeros((len(packed), width), dtype=np.uint8)
    padded[:, : packed.shape[1]] = packed
    words = padded.view(np.uint64)
    weights = np.arange(1, words.shape[1] + 1, dtype=np.uint64) * np.uint64(0x9E3779B97F4A7C15)
    with np.errstate(over="ignore"):
        hashed = (words * weights[None, :]).sum(axis=1, dtype=np.uint64)
    order = np.lexsort((np.arange(len(packed)), hashed))
    rows, keys = packed[order], hashed[order]
    distinct = np.ones(len(rows), dtype=bool)
    distinct[1:] = (keys[1:] != keys[:-1]) | np.any(rows[1:] != rows[:-1], axis=1)
    return rows[distinct]


def _inlier_masks(
    times: np.ndarray, bins: np.ndarray, slopes: np.ndarray, intercepts: np.ndarray, tol: float
) -> np.ndarray:
    """Each line's inliers (within ``tol``), packed: one row per line.

    Single precision: residuals are a few bins, kept to ~1e-5 bin, far inside ``tol``;
    the least-squares fits stay in double precision.
    """
    times32, bins32 = times.astype(np.float32), bins.astype(np.float32)
    slopes32, intercepts32 = slopes.astype(np.float32), intercepts.astype(np.float32)
    packed = np.zeros((slopes.size, (times.size + 7) // 8), dtype=np.uint8)
    for start in range(0, slopes.size, _PAIR_CHUNK):
        stop = start + _PAIR_CHUNK
        residual = slopes32[start:stop, None] * times32[None, :]
        residual -= bins32[None, :]
        residual += intercepts32[start:stop, None]
        np.abs(residual, out=residual)
        packed[start:stop] = np.packbits(residual < tol, axis=1)
    return packed


_POPCOUNT = np.array([bin(value).count("1") for value in range(256)], dtype=np.int32)


def _support(packed: np.ndarray) -> np.ndarray:
    """Inliers per packed row."""
    return _POPCOUNT[packed].sum(axis=1)


def _consensus_lines(
    times: np.ndarray,
    bins: np.ndarray,
    *,
    tol_bins: float,
    slope_bounds: tuple[float, float],
) -> tuple[np.ndarray, int]:
    """Every consensus line a pair of detections leads to: (inlier sets, pairs tried).

    Exhaustive: each pair at least MIN_PAIR_DT_S apart whose slope lies in the bounds
    seeds a line; its inliers (within ``tol_bins``) are refitted by least squares and
    re-selected until the set stops changing (RANSAC's final re-estimation, applied to
    every seed; at most MAX_REFINE_PASSES, then the set is taken as it stands). The
    fixed points are the consensus lines; equal sets from different seeds collapse into
    one, in a fixed order. A set under MIN_TRACK_INLIERS, or whose line leaves the
    slope bounds, is dropped.
    """
    n = times.size
    first, second = np.triu_indices(n, 1)
    gap = times[second] - times[first]
    usable = gap >= MIN_PAIR_DT_S
    first, second, gap = first[usable], second[usable], gap[usable]
    slopes = (bins[second] - bins[first]) / gap
    inside = (slopes >= slope_bounds[0]) & (slopes <= slope_bounds[1])
    first, slopes = first[inside], slopes[inside]
    n_pairs = int(slopes.size)
    seeds = _inlier_masks(times, bins, slopes, bins[first] - slopes * times[first], tol_bins)
    pending = _unique_rows(seeds[_support(seeds) >= MIN_TRACK_INLIERS])
    settled = []
    for _ in range(MAX_REFINE_PASSES):
        if pending.shape[0] == 0:
            break
        sets = np.unpackbits(pending, axis=1, count=n).astype(bool)
        _count, slopes, intercepts, fitted = _fit_sets(sets, times, bins)
        keep = fitted & (slopes >= slope_bounds[0]) & (slopes <= slope_bounds[1])
        refined = _inlier_masks(times, bins, slopes[keep], intercepts[keep], tol_bins)
        enough = _support(refined) >= MIN_TRACK_INLIERS
        unchanged = np.all(refined == pending[keep], axis=1)
        settled.append(refined[enough & unchanged])
        pending = _unique_rows(refined[enough & ~unchanged])
    settled.append(pending)
    lines = _unique_rows(np.vstack(settled))
    return np.unpackbits(lines, axis=1, count=n).astype(bool), n_pairs


def _separation_bins(track: BallTrack, other: BallTrack) -> float:
    """The largest gap between two lines, in bins, at either walk's first or last sample."""
    ends = (track.t_first, track.t_last, other.t_first, other.t_last)
    return float(max(abs(track.bin_at(t) - other.bin_at(t)) for t in ends))


def search_range_walks(  # pylint: disable=too-many-arguments,too-many-locals
    times: np.ndarray,
    bins: np.ndarray,
    frames: np.ndarray,
    *,
    range_res_m: float,
    tol_bins: float,
    speed_bounds_ms: tuple[float, float],
    min_ball_ms: float,
    anchor: TrackAnchor | None = None,
    ops_speed_ms: float | None = None,
) -> TrackSearch:
    """Rank every consensus line of (time, bin) detections and flag a tie between walks.

    The rules, in order:

    1. Candidates: every consensus line a pair of detections leads to
       (``_consensus_lines``), its speed inside ``speed_bounds_ms``.
    2. Gates: the walk's halves agree (``walk_check``: no stall, no two-object mix) and,
       with an ``anchor``, it lies in the anchor's corridor (``_corridor``).
    3. Rank: most inliers, then most frames, lowest residual, longest span, then the
       line itself (slope, intercept), so equal evidence still has one order.
    4. Pick, the fastest-credible contract: the best walk, unless it is slower than
       ``min_ball_ms`` and the best fast walk holds FAST_SUPPORT_FRAC of its inliers --
       then the fast one, and only fast walks compete with it.
    5. Verdict: the strongest passing competitor that is a different walk -- sharing under
       SAME_WALK_OVERLAP of the smaller inlier set and more than the inlier band's full
       width (2 x ``tol_bins``) from the pick somewhere over the two walks -- is the
       runner-up; the pick stands alone only when its inlier count exceeds the
       runner-up's by SUPPORT_SIGNIFICANCE_Z standard errors of the difference,
       ``(n1 - n2) / sqrt(n1 + n2)``. Otherwise the search is ``ambiguous``.

    The OPS speed is evidence only (each candidate's ``ops_speed_ratio``); callers that
    trust it narrow ``speed_bounds_ms`` around it.
    """
    times = np.asarray(times, dtype=float)
    bins = np.asarray(bins, dtype=float)
    frames = np.asarray(frames)
    order = np.lexsort((frames, bins, times))
    times, bins, frames = times[order], bins[order], frames[order]
    n_detections = int(times.size)
    if n_detections < MIN_TRACK_INLIERS:
        return TrackSearch(selected=None, n_detections=n_detections)
    slope_bounds = (speed_bounds_ms[0] / range_res_m, speed_bounds_ms[1] / range_res_m)
    sets, n_pairs = _consensus_lines(times, bins, tol_bins=tol_bins, slope_bounds=slope_bounds)
    if sets.shape[0] == 0:
        return TrackSearch(selected=None, n_detections=n_detections, n_pairs=n_pairs)
    count, slopes, intercepts, fitted = _fit_sets(sets, times, bins)
    weights = sets.astype(float)
    residual = bins[None, :] - (slopes[:, None] * times[None, :] + intercepts[:, None])
    rms = np.sqrt(np.where(sets, residual * residual, 0.0).sum(axis=1) / count)
    t_first = np.where(sets, times[None, :], np.inf).min(axis=1)
    t_last = np.where(sets, times[None, :], -np.inf).max(axis=1)
    labels, frame_index = np.unique(frames, return_inverse=True)
    one_hot = np.zeros((n_detections, labels.size))
    one_hot[np.arange(n_detections), frame_index] = 1.0
    n_frames = ((weights @ one_hot) > 0).sum(axis=1)
    speeds = slopes * range_res_m
    valid = fitted & (speeds >= speed_bounds_ms[0]) & (speeds <= speed_bounds_ms[1])
    ranked = [
        int(k)
        for k in np.lexsort((intercepts, slopes, -(t_last - t_first), rms, -n_frames, -count))
        if valid[k]
    ]
    if not ranked:
        return TrackSearch(
            selected=None, n_detections=n_detections, n_pairs=n_pairs, n_consensus=len(sets)
        )

    evaluated: dict[int, TrackCandidate] = {}

    def candidate(k: int) -> TrackCandidate:
        if k in evaluated:
            return evaluated[k]
        inl = sets[k]
        span = float(t_last[k] - t_first[k])
        track = BallTrack(
            speed_ms=float(speeds[k]),
            slope_bins=float(slopes[k]),
            intercept_bins=float(intercepts[k]),
            rms_bins=float(rms[k]),
            n_inliers=int(count[k]),
            t_first=float(t_first[k]),
            t_last=float(t_last[k]),
            low_confidence=bool(rms[k] >= 0.45 or span < 0.012),
        )
        check, halves = walk_check(
            times[inl],
            bins[inl],
            frames[inl],
            range_res_m=range_res_m,
            min_speed_ms=speed_bounds_ms[0],
        )
        outside = _corridor(
            anchor,
            slope_bins=track.slope_bins,
            intercept_bins=track.intercept_bins,
            t_first=track.t_first,
            range_res_m=range_res_m,
            tol_bins=tol_bins,
        )
        made = TrackCandidate(
            track=track,
            n_frames=int(n_frames[k]),
            range_at_first_m=float(track.range_at(track.t_first, range_res_m)),
            check=outside or check,
            half_speeds_ms=halves,
            ops_speed_ratio=(float(track.speed_ms / ops_speed_ms) if ops_speed_ms else None),
            inliers=inl,
        )
        evaluated[k] = made
        return made

    passing = (candidate(k) for k in ranked)
    best = next((c for c in passing if c.passes), None)
    if best is None:
        rejected = tuple(candidate(k) for k in ranked[:REJECTED_RECORDED])
        return TrackSearch(
            selected=None,
            rejected=rejected,
            n_detections=n_detections,
            n_pairs=n_pairs,
            n_consensus=len(sets),
        )
    pick, fast_only = best, False
    if best.track.speed_ms < min_ball_ms:
        best_fast = next(
            (candidate(k) for k in ranked if speeds[k] >= min_ball_ms and candidate(k).passes),
            None,
        )
        if best_fast is not None and best_fast.track.n_inliers >= (
            FAST_SUPPORT_FRAC * best.track.n_inliers
        ):
            pick, fast_only = best_fast, True
    runner_up, separation = None, None
    pick_size = int(pick.inliers.sum())
    for k in ranked:
        if fast_only and speeds[k] < min_ball_ms:
            continue
        shared = int(np.count_nonzero(pick.inliers & sets[k]))
        if shared >= SAME_WALK_OVERLAP * min(pick_size, int(count[k])):
            continue  # a variant of the picked walk (this includes the pick itself)
        apart = _separation_bins(
            pick.track,
            BallTrack(
                speed_ms=float(speeds[k]),
                slope_bins=float(slopes[k]),
                intercept_bins=float(intercepts[k]),
                rms_bins=float(rms[k]),
                n_inliers=int(count[k]),
                t_first=float(t_first[k]),
                t_last=float(t_last[k]),
                low_confidence=False,
            ),
        )
        if apart <= 2.0 * tol_bins:
            continue  # within the inlier band of the pick: the same answer
        other = candidate(k)
        if other.passes:
            runner_up, separation = other, apart
            break
    support_z = None
    ambiguous = False
    if runner_up is not None:
        n_pick, n_other = pick.track.n_inliers, runner_up.track.n_inliers
        support_z = float((n_pick - n_other) / math.sqrt(n_pick + n_other))
        ambiguous = support_z < SUPPORT_SIGNIFICANCE_Z
    above_pick = ranked[: next(i for i, k in enumerate(ranked) if evaluated.get(k) is pick)]
    stronger = [evaluated[k] for k in above_pick if k in evaluated and not evaluated[k].passes]
    return TrackSearch(
        selected=pick,
        runner_up=runner_up,
        ambiguous=ambiguous,
        support_z=support_z,
        separation_bins=separation,
        rejected=tuple(stronger[:REJECTED_RECORDED]),
        n_detections=n_detections,
        n_pairs=n_pairs,
        n_consensus=len(sets),
    )


def search_ball(  # pylint: disable=too-many-arguments,too-many-locals
    mti: np.ndarray,
    geo: Geometry,
    *,
    max_range_m: float | None = None,
    min_ball_ms: float = FAST_TRACK_MS,
    gates_m: tuple[tuple[float, float], ...] = BALL_GATES_M,
    speed_bounds_ms: tuple[float, float] = SPEED_BOUNDS_MS,
    time_window_s: tuple[float, float] | None = None,
    anchor: TrackAnchor | None = None,
    ops_speed_ms: float | None = None,
    max_peaks_per_loop: int = 1,
) -> TrackSearch:
    """The exhaustive range-walk search on one MTI cube (``search_range_walks``).

    Per-loop detections inside ``gates_m``, restricted to ``time_window_s``; the picked
    walk gets its quadratic refit (the local radial speed).
    """
    power = loop_power(mti)
    loops_idx, bins = _detections(
        power,
        geo,
        max_range_m=max_range_m,
        gates_m=gates_m,
        max_peaks_per_loop=max_peaks_per_loop,
    )
    if loops_idx.size < MIN_TRACK_INLIERS:
        return TrackSearch(selected=None, n_detections=int(loops_idx.size))
    res = geo.range_res_m
    n_loops = geo.n_loops
    times = np.array([geo.loop_time(i // n_loops, i % n_loops) for i in loops_idx])
    frames = loops_idx // n_loops
    if time_window_s is not None:
        keep = (times >= time_window_s[0]) & (times <= time_window_s[1])
        if keep.sum() < MIN_TRACK_INLIERS:
            return TrackSearch(selected=None, n_detections=int(keep.sum()))
        times, bins, frames = times[keep], bins[keep], frames[keep]
    tol = 1.2 if geo.n_samples >= 128 else 0.8
    search = search_range_walks(
        times,
        bins,
        frames,
        range_res_m=res,
        tol_bins=tol,
        speed_bounds_ms=speed_bounds_ms,
        min_ball_ms=min_ball_ms,
        anchor=anchor,
        ops_speed_ms=ops_speed_ms,
    )
    if search.selected is None:
        return search
    track = search.selected.track
    # quadratic refit of the picked walk's inliers: local radial speed
    inl = np.abs(bins - (track.slope_bins * times + track.intercept_bins)) < tol
    quad = None
    if inl.sum() >= 10:
        q2, q1, q0 = np.polyfit(times[inl], bins[inl], 2)
        if abs(2.0 * q2 * res) < MAX_RADIAL_ACCEL:
            quad = (float(q2), float(q1), float(q0))
    selected = replace(search.selected, track=replace(track, quad_bins=quad))
    return replace(search, selected=selected)


def find_ball(  # pylint: disable=too-many-arguments
    mti: np.ndarray,
    geo: Geometry,
    *,
    max_range_m: float | None = None,
    min_ball_ms: float = FAST_TRACK_MS,
    gates_m: tuple[tuple[float, float], ...] = BALL_GATES_M,
    speed_bounds_ms: tuple[float, float] = SPEED_BOUNDS_MS,
    time_window_s: tuple[float, float] | None = None,
    anchor: TrackAnchor | None = None,
    ops_speed_ms: float | None = None,
    max_peaks_per_loop: int = 1,
) -> BallTrack | None:
    """The picked range walk inside ``gates_m``/``speed_bounds_ms``; None when no
    plausible streak exists. Deterministic: every point pair is tried (``search_ball``).

    Selection is FASTEST-CREDIBLE, not most-inliers: the best fast
    (>= ``min_ball_ms``) candidate wins whenever it has at least
    FAST_SUPPORT_FRAC of the overall best candidate's inliers.
    ``min_ball_ms`` is the slowest RADIAL speed a real ball can read for
    the club in play (see shot.CLUB_POLICY) — it must sit above the
    class's post-impact club/tee speeds or they steal the track.

    When a different walk's support is not significantly below the pick's, the
    returned track carries ``ambiguity`` (both walks and their evidence); the pick is
    still returned, so callers that cannot use the flag keep their contract.

    Defaults reproduce the ball search. The club estimator passes its own
    (closer, slower) ``gates_m``/``speed_bounds_ms``; because the club's
    speed band overlaps the ball's, it must also pass ``time_window_s`` to
    restrict the search to pre-impact frames — otherwise this fitter will
    happily lock onto the ball instead of the club.
    """
    return search_ball(
        mti,
        geo,
        max_range_m=max_range_m,
        min_ball_ms=min_ball_ms,
        gates_m=gates_m,
        speed_bounds_ms=speed_bounds_ms,
        time_window_s=time_window_s,
        anchor=anchor,
        ops_speed_ms=ops_speed_ms,
        max_peaks_per_loop=max_peaks_per_loop,
    ).track

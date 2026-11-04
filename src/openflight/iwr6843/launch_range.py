"""Each swing's launch-point range from its own IWR capture.

The ball's post-impact range walk is searched at the OPS243 ball speed and its line is read
back at the contact time (``back_extrapolation``): a contact time on the IWR ring clock from
outside the IWR (measured), else the club walk reaching the tee bin, else the walk's
earliest consistent start (both estimated). The club and ball walks are never intersected:
they are nearly parallel (29-31 vs 36.5 m/s on Oct 27 shot 1, 1 bin = 6 ms). The camera
prior sizes the search and checks the answer; values are measured, estimated or not_measured."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from openflight.constants import BALL_DIAMETER_M
from openflight.iwr6843 import tracking
from openflight.iwr6843.club import CLUB_TRACK_SPEED_LIMIT_MS
from openflight.iwr6843.partial_dump import PARTIAL_LABEL
from openflight.iwr6843.shot import PreparedShotDump, near_mti_notch, track_broken

SCHEMA = "openflight.iwr_launch_range.v1"
SOURCE = "iwr_post_impact"
CAMERA_PRIOR_SOURCE = "camera_prior"
# The tee the camera located (camera.resting_ball_range.located_tee_range, C8a): the
# shot's own range; the IWR's launch point only confirms it.
LOCATED_TEE_SOURCE = "camera_resting_ball"

# Radial ball speed as a fraction of the OPS243's; the floor keeps the slower club out.
BALL_SPEED_FRACTION = (0.85, 1.10)
CLUB_SPEED_FRACTION = (0.70, 1.20)
# Club search window around when the ball walk reaches the tee (or starts).
CLUB_WINDOW_BEFORE_S = 0.015
CLUB_WINDOW_AFTER_S = 0.006
# Without a club walk the start may be up to ~0.2 m short; half is carried as uncertainty.
CLUB_CONTAMINATION_M = 0.11
# Where the ball walk's line is read back to (``_contact_time``): only a contact time from
# outside the IWR, on its ring clock, makes the launch point measured.
METHOD = "back_extrapolation"
CONTACT_SUPPLIED = "contact_time_supplied"
CONTACT_CLUB_AT_TEE = "club_walk_at_tee"
CONTACT_WALK_START = "walk_start"
NO_RING_CONTACT = "no camera contact time on the IWR ring clock"
# At contact the club's face touches the ball's back, one ball radius nearer the radar
# (behind the ball) than the resting ball's centre the prior locates: the club walk is
# read there.
CLUB_FACE_AT_CONTACT_M = BALL_DIAMETER_M / 2.0
MIN_SPEED_GAP_MS = 4.0
PRIOR_SEARCH_SIGMAS = 3.0
SEARCH_MARGIN_M = 0.10
MIN_SEARCH_FLOOR_M = 0.50
DEFAULT_SEARCH_FLOOR_M = 0.60
MAX_RANGE_M = 4.7
NET_MARGIN_M = 0.25
PRIOR_AGREEMENT_SIGMAS = 3.0
# Provisional, unmeasured floor: club reflecting centre vs ball near face at contact.
MODEL_FLOOR_M = 0.02
MIN_BALL_INLIERS = 12
MIN_BALL_SPAN_S = 0.006
# The walk starts at the first frame with this many samples whose next frame has them too.
WALK_START_MIN_SAMPLES = 2
# ... counted on each loop's strongest peak, the detections the rule was set on: with the
# ball's four peaks per loop any line through the pre-contact clutter collects two lobes a
# frame, and the start lands on the club's approach (Oct 24 shot 13: 1.2 ms and 1.49 m at
# four peaks, 9.8 ms and 1.91 m at one, the owner's ~1.91 m).
WALK_START_MAX_PEAKS_PER_LOOP = 1
# the range walk's inlier tolerance, in bins, as tracking.find_ball uses
_TRACK_TOLERANCE_BINS = 1.2
# The club's approach walk runs into the same golfer band as the ball's walk leaves it,
# so it is searched with the ball's slots per loop. Oct 27 shot 1: in the window MTI one
# peak per loop finds no club walk at the club's speed (23.6 m/s, three frames) where four
# find the 31.3 m/s approach; in the burst MTI both find it.
CLUB_WALK_MAX_PEAKS_PER_LOOP = tracking.BALL_MAX_PEAKS_PER_LOOP
# Two competing walks whose launch points lie within this many standard errors of their
# combined uncertainty are one answer (two-sided ~95 %); further apart, the range is
# labelled AMBIGUOUS_LABEL.
COMPETING_SIGMAS = 2.0
AMBIGUOUS_LABEL = "estimated"
# A shot's own prior (its resting-ball look) is used only when it lies within this many of
# the setup prior's sigma of it (``choose_range_prior``); further off, the camera has
# latched onto something else (27 Oct shots 2-4: 13-20 px objects for a ~25 px ball).
PER_SHOT_PRIOR_AGREEMENT_SIGMAS = 2.0
PER_SHOT_PRIOR_DISAGREES = "per_shot_prior_disagrees_with_setup_prior"
# A ball walk leaves the resting ball: back-extrapolated to the anchor range (the camera
# prior, else its own launch point) it crosses it within this many IWR frames of the
# club's walk crossing the same range. Contact falls somewhere inside one frame (the
# club's last samples before it and the ball's first after it bracket it), so one frame;
# the anchor range's own sigma adds ANCHOR_SIGMAS x sigma x |1/v_ball - 1/v_club|, at the
# confidence the prior is checked with everywhere else (PRIOR_AGREEMENT_SIGMAS).
CONTACT_AGREEMENT_FRAMES = 1
CLUB_ONLY_REASON = "ball walk not found (hidden by clutter); club walk only"
# An agreement the camera prior's own sigma carries is no measurement: when the prior's
# share of the tolerance is more than the frame's, the range is estimated and its sigma
# covers the prior's.
LOOSE_PRIOR_REASON = "anchor agreement rests on a loose prior (sigma {sigma:.2f} m)"
# The same for a walk anchored at its own launch point (no camera prior): that point's
# sigma is the anchor's, and the range's uncertainty already carries it.
LOOSE_ANCHOR_REASON = (
    "anchor agreement rests on a loose anchor (the launch point's own sigma {sigma:.2f} m)"
)
LOOSE_ANCHOR_STATUSES = ("agrees_loose_prior", "agrees_loose_anchor")
UNANCHORED_NOTE = (
    "no club walk anchors the ball walk's contact time (and no sound contact time in IWR "
    "time): estimated"
)


@dataclass(frozen=True)
class ContactTime:
    """When the club met the ball, on this capture's IWR ring clock (the dump's loop-time
    origin), from a sensor outside the IWR: the camera's contact through a qualified
    camera-to-IWR clock mapping. No producer maps a camera time onto the ring today (v7
    dumps carry no request tick); the input is here so that one makes the launch point
    measured without another path."""

    t_s: float
    sigma_s: float
    source: str

    def __post_init__(self) -> None:
        for name in ("t_s", "sigma_s"):
            value = float(getattr(self, name))
            if not math.isfinite(value):
                raise ValueError(f"the contact time's {name} must be finite")
            object.__setattr__(self, name, value)
        if self.sigma_s < 0.0:
            raise ValueError("the contact time's sigma must be non-negative")
        if not self.source:
            raise ValueError("the contact time must name its source")


@dataclass(frozen=True)
class LaunchRangePrior:
    """The camera's coarse estimate of the resting ball's range: a prior, never a value."""

    range_m: float
    uncertainty_m: float
    source: str

    def __post_init__(self) -> None:
        for name in ("range_m", "uncertainty_m"):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(f"the prior's {name} must be finite and positive")
            object.__setattr__(self, name, value)
        if not self.source:
            raise ValueError("the prior must name its source")

    def to_dict(self) -> dict:
        return {"range_m": self.range_m, "uncertainty_m": self.uncertainty_m, "source": self.source}

    @classmethod
    def from_dict(cls, payload: Any) -> "LaunchRangePrior | None":
        if not isinstance(payload, dict) or payload.get("range_m") is None:
            return None
        try:
            return cls(
                range_m=float(payload["range_m"]),
                uncertainty_m=float(payload["uncertainty_m"]),
                source=str(payload.get("source") or CAMERA_PRIOR_SOURCE),
            )
        except (KeyError, TypeError, ValueError):
            return None


@dataclass(frozen=True)
class RangePriorChoice:
    """The prior a shot uses: its own look's, else the setup's, and why the look was refused."""

    prior: LaunchRangePrior | None
    chosen: str | None  # per_shot | setup | None
    per_shot: LaunchRangePrior | None = None
    setup: LaunchRangePrior | None = None
    difference_m: float | None = None
    allowed_m: float | None = None
    rejection: str | None = None  # PER_SHOT_PRIOR_DISAGREES, or None
    reason: str | None = None

    def to_dict(self) -> dict:
        return {
            "chosen": self.chosen,
            "prior": self.prior.to_dict() if self.prior is not None else None,
            "per_shot": self.per_shot.to_dict() if self.per_shot is not None else None,
            "setup": self.setup.to_dict() if self.setup is not None else None,
            "difference_m": self.difference_m,
            "allowed_m": self.allowed_m,
            "rejection": self.rejection,
            "reason": self.reason,
        }


def choose_range_prior(
    per_shot: LaunchRangePrior | None,
    setup: LaunchRangePrior | None,
    *,
    per_shot_own_sigma_m: float | None = None,
) -> RangePriorChoice:
    """The prior a shot uses: its own look's when it agrees with the setup's, else the setup's.

    The look's prior is used only within ``PER_SHOT_PRIOR_AGREEMENT_SIGMAS`` setup sigmas
    of the setup prior, combined in quadrature with ``per_shot_own_sigma_m`` when the look
    has a sigma of its own. The resting-ball look's ``uncertainty_m`` is not its own: it
    is the setup prior's sigma scaled by the size ratio (``camera.ball_at_address``), so
    adding it would count the setup's error twice. Without a setup prior the look stands
    unchecked; without a look, the setup prior. Every caller (the IWR runtime, the
    kiosk's ball-range chain, the replays) chooses here.
    """
    if per_shot is not None and per_shot.source == LOCATED_TEE_SOURCE:
        # the camera located this shot's tee with its own sigma: no setup look outranks it
        return RangePriorChoice(prior=per_shot, chosen="per_shot", per_shot=per_shot, setup=setup)
    if per_shot is None or setup is None:
        prior = per_shot if per_shot is not None else setup
        chosen = None if prior is None else "per_shot" if per_shot is not None else "setup"
        return RangePriorChoice(prior=prior, chosen=chosen, per_shot=per_shot, setup=setup)
    sigma = setup.uncertainty_m
    if per_shot_own_sigma_m is not None and math.isfinite(per_shot_own_sigma_m):
        sigma = math.hypot(sigma, max(0.0, float(per_shot_own_sigma_m)))
    allowed = PER_SHOT_PRIOR_AGREEMENT_SIGMAS * sigma
    difference = abs(per_shot.range_m - setup.range_m)
    common = {
        "per_shot": per_shot,
        "setup": setup,
        "difference_m": difference,
        "allowed_m": allowed,
    }
    if difference <= allowed:
        return RangePriorChoice(prior=per_shot, chosen="per_shot", **common)
    return RangePriorChoice(
        prior=setup,
        chosen="setup",
        rejection=PER_SHOT_PRIOR_DISAGREES,
        reason=(
            f"the shot's own camera prior {per_shot.range_m:.2f} +/- "
            f"{per_shot.uncertainty_m:.2f} m ({per_shot.source}) disagrees with the setup "
            f"prior {setup.range_m:.2f} +/- {setup.uncertainty_m:.2f} m by "
            f"{difference:.2f} m, more than {allowed:.2f} m "
            f"({PER_SHOT_PRIOR_AGREEMENT_SIGMAS:g} sigma): the setup prior stands in"
        ),
        **common,
    )


@dataclass(frozen=True)
class LaunchRangeEstimate:  # pylint: disable=too-many-instance-attributes
    """One shot's launch-point range from its own IWR walk, or why there is none."""

    status: str  # measured | not_measured
    reason: str | None
    method: str | None = None
    range_m: float | None = None
    apparent_range_m: float | None = None
    uncertainty_m: float | None = None
    impact_t_s: float | None = None
    candidate_range_m: float | None = None
    search_gate_m: tuple[float, float] | None = None
    range_bias_m: float = 0.0
    prior: LaunchRangePrior | None = None
    ball_track: dict | None = None
    club_track: dict | None = None
    scope: str | None = None
    track: tracking.BallTrack | None = field(default=None, repr=False, compare=False)
    # the partial-dump record when the capture's transfer lost bytes (``partial_dump``)
    partial: dict | None = None
    # the tracker's competing walk and both launch points (``_settle_competition``)
    competing: dict | None = None
    # whether the ball walk leaves the resting ball with the club (``_anchor``)
    anchor: dict | None = None
    # when the walk was read back to, and how that time was obtained (``_contact_time``)
    contact: dict | None = None

    @property
    def contact_estimated(self) -> bool:
        """Whether the contact time the walk was read back to came from the IWR itself."""
        return self.contact is not None and self.contact.get("label") != "measured"

    @property
    def unanchored(self) -> bool:
        """Whether nothing firmly anchors the walk's contact time: no club walk, or an
        agreement a loose camera prior carries."""
        return self.anchor is not None and self.anchor.get("status") in (
            "unanchored",
            *LOOSE_ANCHOR_STATUSES,
        )

    @property
    def ambiguous(self) -> bool:
        """Whether two IWR walks with different launch points could not be told apart."""
        return self.competing is not None and self.competing.get("verdict") == "ambiguous"

    @property
    def measured(self) -> bool:
        return self.status == "measured" and self.range_m is not None

    @property
    def source(self) -> str | None:
        return SOURCE if self.measured else None

    @property
    def label(self) -> str:
        if not self.measured:
            return "not_measured"
        if self.ambiguous or self.unanchored or self.contact_estimated:
            return AMBIGUOUS_LABEL
        return PARTIAL_LABEL if self.partial is not None else "measured"

    def to_dict(self) -> dict:
        return {
            "schema": SCHEMA,
            "status": self.status,
            "reason": self.reason,
            "label": self.label,
            "source": self.source,
            "method": self.method,
            "range_m": self.range_m,
            "apparent_range_m": self.apparent_range_m,
            "uncertainty_m": self.uncertainty_m,
            "impact_t_s": self.impact_t_s,
            "candidate_range_m": self.candidate_range_m,
            "search_gate_m": list(self.search_gate_m) if self.search_gate_m else None,
            "range_bias_m": self.range_bias_m,
            "prior": self.prior.to_dict() if self.prior is not None else None,
            "ball_track": self.ball_track,
            "club_track": self.club_track,
            "scope": self.scope,
            "validated_against_tape": False,
            "partial": self.partial,
            "competing": self.competing,
            "anchor": self.anchor,
            "contact": self.contact,
        }


@dataclass(frozen=True)
class ShotBallRange:
    """The range a shot's range-dependent metrics use, with its source and label."""

    range_m: float | None
    uncertainty_m: float | None
    source: str | None  # iwr_post_impact | camera_prior | None
    label: str  # measured | iwr_partial | estimated | not_measured
    reason: str | None = None

    def to_dict(self) -> dict:
        return {
            "ball_range_m": self.range_m,
            "ball_range_uncertainty_m": self.uncertainty_m,
            "ball_range_source": self.source,
            "ball_range_label": self.label,
            "ball_range_reason": self.reason,
        }


def shot_ball_range(
    estimate: LaunchRangeEstimate | None, prior: LaunchRangePrior | None
) -> ShotBallRange:
    """The camera's located tee (its prior, C8a); else the shot's own IWR launch point;
    else the camera prior, labelled; else not measured."""
    if prior is not None and prior.source == LOCATED_TEE_SOURCE:
        launch = (
            f"IWR launch point {estimate.range_m:.2f} m ({estimate.label})"
            if estimate is not None and estimate.measured
            else f"no IWR launch point ({estimate.reason if estimate is not None else 'none'})"
        )
        return ShotBallRange(
            range_m=prior.range_m,
            uncertainty_m=prior.uncertainty_m,
            source=LOCATED_TEE_SOURCE,
            label="estimated",
            reason=f"the camera located the tee; {launch}",
        )
    if estimate is not None and estimate.measured:
        notes = []
        if estimate.ambiguous:
            notes.append(competition_note(estimate.competing))
        if estimate.reason:
            notes.append(estimate.reason)
        if estimate.unanchored and estimate.anchor.get("status") not in LOOSE_ANCHOR_STATUSES:
            notes.append(UNANCHORED_NOTE)
        if estimate.partial is not None:
            notes.append(f"from a partial IWR dump: {estimate.partial.get('missing')}")
        return ShotBallRange(
            range_m=estimate.range_m,
            uncertainty_m=estimate.uncertainty_m,
            source=SOURCE,
            label=estimate.label,
            reason="; ".join(notes) or None,
        )
    why = estimate.reason if estimate is not None and estimate.reason else "no IWR launch point"
    if prior is not None:
        return ShotBallRange(
            range_m=prior.range_m,
            uncertainty_m=prior.uncertainty_m,
            source=CAMERA_PRIOR_SOURCE,
            label="estimated",
            reason=f"the IWR did not measure this shot's launch point ({why}); the camera "
            "prior stands in",
        )
    return ShotBallRange(
        range_m=None,
        uncertainty_m=None,
        source=None,
        label="not_measured",
        reason=f"the IWR did not measure this shot's launch point ({why}) and there is no "
        "camera prior",
    )


def competition_note(competing: dict) -> str:
    """One line on two competing IWR walks: both launch points and the camera's check."""
    selected = competing["selected"]["launch_range_m"]
    other = competing["runner_up"]["launch_range_m"]
    note = (
        f"{competing['reason']}: launch points {selected:.2f} m and {other:.2f} m; "
        f"{selected:.2f} m is reported"
    )
    prior = competing.get("camera_prior")
    if prior is None:
        return f"{note}; no camera prior to check them against"
    agrees = prior["agrees_with"]
    if competing.get("resolved_by") == "camera_prior":
        return f"{note}, the one the camera prior ({prior['range_m']:.2f} m) agrees with"
    which = "both" if len(agrees) == 2 else "neither"
    return f"{note}; the camera prior ({prior['range_m']:.2f} m) agrees with {which}"


def search_gate_m(prior: LaunchRangePrior | None, net_range_m: float | None) -> tuple[float, float]:
    """Where the ball's walk is searched: just short of the prior, out to the net."""
    if prior is None:
        low = DEFAULT_SEARCH_FLOOR_M
    else:
        low = max(
            MIN_SEARCH_FLOOR_M,
            prior.range_m
            - max(PRIOR_SEARCH_SIGMAS * prior.uncertainty_m, SEARCH_MARGIN_M)
            - SEARCH_MARGIN_M,
        )
    high = MAX_RANGE_M if not net_range_m else min(MAX_RANGE_M, net_range_m - NET_MARGIN_M)
    return float(low), float(high)


def _line_sigma_m(track: tracking.BallTrack, t_s: float, res_m: float) -> float:
    """The fitted walk's position error at ``t_s``, from its residual and support."""
    n = max(int(track.n_inliers), 2)
    span = max(track.t_last - track.t_first, 1e-6)
    middle = 0.5 * (track.t_first + track.t_last)
    # the inliers spread over the span: sum((t - mean)^2) ~ n span^2 / 12
    leverage = 1.0 / n + 12.0 * (t_s - middle) ** 2 / (n * span * span)
    return float(res_m * max(track.rms_bins, 0.1) * math.sqrt(leverage))


def _track_facts(track: tracking.BallTrack | None, res_m: float) -> dict | None:
    if track is None:
        return None
    return {
        "speed_ms": float(track.speed_ms),
        "intercept_bins": float(track.intercept_bins),
        "rms_bins": float(track.rms_bins),
        "n_inliers": int(track.n_inliers),
        "t_first_s": float(track.t_first),
        "t_last_s": float(track.t_last),
        "range_at_first_m": float(track.range_at(track.t_first, res_m)),
        "bin_first": float(track.bin_at(track.t_first)),
        "bin_last": float(track.bin_at(track.t_last)),
    }


def _walk_record(
    prepared: PreparedShotDump, scope: str, gate: tuple[float, float], track: tracking.BallTrack
) -> dict:
    """What the shot record names a walk by, beyond its line: the frames its samples fill and
    their SNR (each sample's loop power over its gate's median, in dB; median and range)."""
    geometry = prepared.geometry
    loops, times, bins = _gate_detections(prepared, scope, gate)
    tolerance = _TRACK_TOLERANCE_BINS if geometry.n_samples >= 128 else 0.8
    inliers = np.abs(bins - track.bin_at(times)) < tolerance
    frames = sorted({int(loop) // geometry.n_loops for loop in loops[inliers]})
    power = tracking.loop_power(prepared.mti(scope))
    res = geometry.range_res_m
    snr_db = []
    for loop, absolute_bin in zip(loops[inliers], bins[inliers], strict=True):
        frame = int(loop) // geometry.n_loops
        start = geometry.frame_bin_start(frame)
        low = max(int(gate[0] / res) - start, 0)
        high = min(int(gate[1] / res) - start, geometry.frame_bin_count(frame) - 2)
        row = power[int(loop)]
        local = int(round(absolute_bin)) - start
        if high - low < 3 or not 0 <= local < row.size:
            continue
        snr_db.append(10.0 * math.log10(row[local] / (float(np.median(row[low:high])) + 1e-12)))
    return {
        "n_frames": len(frames),
        "frames": [frames[0], frames[-1]] if frames else None,
        "snr_db_median": float(np.median(snr_db)) if snr_db else None,
        "snr_db_range": [float(min(snr_db)), float(max(snr_db))] if snr_db else None,
        "scope": scope,
    }


def _gate_detections(
    prepared: PreparedShotDump,
    scope: str,
    gate: tuple[float, float],
    *,
    max_peaks_per_loop: int = tracking.BALL_MAX_PEAKS_PER_LOOP,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per-loop peaks in the gate: (loop index, time s, fractional bin), as many per loop
    as the walk they are read against was searched with."""
    geometry = prepared.geometry
    power = tracking.loop_power(prepared.mti(scope))
    # pylint: disable-next=protected-access
    loops, bins = tracking._detections(
        power, geometry, gates_m=(gate,), max_peaks_per_loop=max_peaks_per_loop
    )
    n_loops = geometry.n_loops
    times = np.array([geometry.loop_time(i // n_loops, i % n_loops) for i in loops], dtype=float)
    return np.asarray(loops), times, np.asarray(bins, dtype=float)


def walk_start_s(
    prepared: PreparedShotDump,
    scope: str,
    track: tracking.BallTrack,
    gate: tuple[float, float],
) -> float:
    """When the walk's samples start holding frame after frame (``t_first`` can be a noise peak)."""
    geometry = prepared.geometry
    loops, times, bins = _gate_detections(
        prepared, scope, gate, max_peaks_per_loop=WALK_START_MAX_PEAKS_PER_LOOP
    )
    if loops.size == 0:
        return float(track.t_first)
    n_loops = geometry.n_loops
    tolerance = _TRACK_TOLERANCE_BINS if geometry.n_samples >= 128 else 0.8
    inliers = np.abs(bins - track.bin_at(times)) < tolerance
    frames: dict[int, list[float]] = {}
    for loop_index, time_s, inlier in zip(loops, times, inliers, strict=True):
        if inlier:
            frames.setdefault(int(loop_index) // n_loops, []).append(float(time_s))
    ordered = sorted(frames.items(), key=lambda item: min(item[1]))
    for (_frame, samples), (_next, following) in zip(ordered, ordered[1:]):
        if len(samples) >= WALK_START_MIN_SAMPLES and len(following) >= WALK_START_MIN_SAMPLES:
            return float(min(samples))
    return float(track.t_first)


def speeds_separate(ball_ms: float, club_ms: float | None) -> bool:
    """Whether the OPS ball and club speeds are far enough apart (MIN_SPEED_GAP_MS, the gap
    the club and ball walks are told apart by) for a walk's speed to say which it is."""
    return club_ms is not None and club_ms > 0.0 and ball_ms - club_ms >= MIN_SPEED_GAP_MS


def is_club_walk_speed(speed_ms: float, *, ball_ms: float, club_ms: float | None) -> bool:
    """Whether a walk this fast is the club's: nearer the OPS club speed than the ball's,
    or inside the club window and slower than the ball by more than MIN_SPEED_GAP_MS.
    Never when the two OPS speeds are too close to tell apart (``speeds_separate``)."""
    if not speeds_separate(ball_ms, club_ms):
        return False
    speed = float(speed_ms)
    nearer_club = abs(speed - club_ms) < abs(speed - ball_ms)
    low, high = CLUB_SPEED_FRACTION
    in_club_window = low * club_ms <= speed <= high * club_ms
    return nearer_club or (in_club_window and speed < ball_ms - MIN_SPEED_GAP_MS)


def ball_walk_speed_floor_ms(ball_ms: float, club_ms: float | None) -> float:
    """The slowest walk the ball's search accepts: the ball window's floor, raised above
    every speed ``is_club_walk_speed`` gives to the club when the club speed is known."""
    floor = BALL_SPEED_FRACTION[0] * float(ball_ms)
    if not speeds_separate(ball_ms, club_ms):
        return floor
    midpoint = 0.5 * (float(ball_ms) + float(club_ms))
    club_window = min(float(ball_ms) - MIN_SPEED_GAP_MS, CLUB_SPEED_FRACTION[1] * float(club_ms))
    if club_window < CLUB_SPEED_FRACTION[0] * float(club_ms):
        club_window = floor
    return max(floor, midpoint, club_window)


def _ball_walk(
    prepared: PreparedShotDump,
    gate: tuple[float, float],
    speed_ms: float,
    *,
    prior: LaunchRangePrior | None = None,
    range_bias_m: float = 0.0,
    club_ms: float | None = None,
) -> tuple[tracking.BallTrack | None, str]:
    """The ball's walk in the gate at the OPS speed; the window MTI past a notch.

    The camera prior, when there is one, is the tracker's range anchor (in the radar's
    apparent range): a walk first seen short of the resting ball is refused. With the
    OPS club speed, no walk the club could make is searched (``ball_walk_speed_floor_ms``)."""
    geometry = prepared.geometry
    bounds = (ball_walk_speed_floor_ms(speed_ms, club_ms), BALL_SPEED_FRACTION[1] * speed_ms)
    if bounds[0] >= bounds[1]:
        return None, "burst"
    anchor = (
        tracking.TrackAnchor(
            range_m=prior.range_m + range_bias_m,
            range_sd_m=prior.uncertainty_m,
            source=prior.source,
        )
        if prior is not None
        else None
    )

    def find(scope: str) -> tracking.BallTrack | None:
        return tracking.find_ball(
            prepared.mti(scope),
            geometry,
            gates_m=(gate,),
            speed_bounds_ms=bounds,
            min_ball_ms=bounds[0],
            anchor=anchor,
            ops_speed_ms=speed_ms,
            max_peaks_per_loop=tracking.BALL_MAX_PEAKS_PER_LOOP,
        )

    track, scope = find("burst"), "burst"
    if track_broken(track) or (
        track is not None and near_mti_notch(track.speed_ms, loop_period_s=geometry.loop_period_s)
    ):
        window = find("window")
        if window is not None and (
            track is None or window.n_inliers > track.n_inliers or window.rms_bins < track.rms_bins
        ):
            track, scope = window, "window"
    return track, scope


def _club_walk(  # pylint: disable=too-many-arguments
    prepared: PreparedShotDump,
    scope: str,
    ball: tracking.BallTrack,
    start_s: float,
    gate_low_m: float,
    club_speed_ms: float,
) -> tracking.BallTrack | None:
    geometry = prepared.geometry
    res = geometry.range_res_m
    reach_m = ball.range_at(start_s + CLUB_WINDOW_AFTER_S, res)
    low = max(CLUB_TRACK_SPEED_LIMIT_MS[0], CLUB_SPEED_FRACTION[0] * club_speed_ms)
    # never as fast as the ball's own walk, or the ball's start is taken for the club
    high = min(
        CLUB_TRACK_SPEED_LIMIT_MS[1],
        CLUB_SPEED_FRACTION[1] * club_speed_ms,
        ball.speed_ms - MIN_SPEED_GAP_MS,
    )
    if high <= low:
        return None
    return tracking.find_ball(
        prepared.mti(scope),
        geometry,
        gates_m=((max(0.35, gate_low_m - 0.6), reach_m + 0.10),),
        speed_bounds_ms=(low, high),
        min_ball_ms=low,
        time_window_s=(
            max(0.0, start_s - CLUB_WINDOW_BEFORE_S),
            start_s + CLUB_WINDOW_AFTER_S,
        ),
        max_peaks_per_loop=CLUB_WALK_MAX_PEAKS_PER_LOOP,
    )


def _crossing_s(walk: tracking.BallTrack, range_m: float, res_m: float) -> float:
    """When a walk's line is at this (apparent) range."""
    return (float(range_m) / res_m - walk.intercept_bins) / walk.slope_bins


def _walk_start_sigma_m(
    geometry: tracking.Geometry, ball_speed_ms: float, club_speed_ms: float | None
) -> float:
    """A walk start's range error: half a frame either side, and the club's contamination.

    The club's approach runs nearly collinear into contact: its samples stay on the ball's
    line (within the inlier tolerance) for tolerance / (v_ball - v_club) before it, and the
    start can land that early. With a club speed that span, at the ball's speed, is the
    contamination when it exceeds CLUB_CONTAMINATION_M (Oct 27 shot 1: 6.4 ms, 0.23 m)."""
    contamination = CLUB_CONTAMINATION_M
    if club_speed_ms is not None and ball_speed_ms - club_speed_ms > 0.0:
        tolerance = _TRACK_TOLERANCE_BINS if geometry.n_samples >= 128 else 0.8
        merge_s = tolerance * geometry.range_res_m / (ball_speed_ms - club_speed_ms)
        contamination = max(contamination, ball_speed_ms * merge_s)
    return math.hypot(ball_speed_ms * geometry.frame_period_s / 2.0, contamination)


def _contact_time(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    prepared: PreparedShotDump,
    ball: tracking.BallTrack,
    start_s: float,
    club: tracking.BallTrack | None,
    prior: LaunchRangePrior | None,
    range_bias_m: float,
    contact: ContactTime | None,
) -> dict:
    """When the ball walk is read back to: (t, sigma, source, label, reason).

    A supplied contact time on the IWR clock is measured. Without one there are two IWR
    estimates, both labelled estimated with the reason: the club walk's line reaching the
    tee bin (the prior's apparent range less a ball radius, where the face meets the ball;
    inside the club walk's own samples), its sigma the club line's error there and the
    prior's sigma over the club speed; and the walk's earliest consistent start, half a
    frame either side plus the club's contamination. The one whose launch-point sigma is
    smaller is used (a loose prior's sigma, over the club speed, outweighs the start's)."""
    geometry = prepared.geometry
    res = geometry.range_res_m
    if contact is not None:
        return {
            "t_s": contact.t_s,
            "sigma_s": contact.sigma_s,
            "source": CONTACT_SUPPLIED,
            "supplied_by": contact.source,
            "label": "measured",
            "reason": None,
        }
    start = {
        "t_s": float(start_s),
        "sigma_s": float(geometry.frame_period_s / 2.0),
        "source": CONTACT_WALK_START,
        "label": "estimated",
    }

    def from_start(why: str) -> dict:
        return {
            **start,
            "reason": (
                f"contact time from the walk's earliest consistent start at "
                f"{1000.0 * start_s:.1f} ms ({why}); {NO_RING_CONTACT}"
            ),
        }

    if club is None or club.slope_bins <= 0.0:
        return from_start("no club walk")
    if prior is None:
        return from_start("no tee bin to read the club walk at")
    tee_apparent_m = prior.range_m + float(range_bias_m)
    t_club = _crossing_s(club, tee_apparent_m - CLUB_FACE_AT_CONTACT_M, res)
    frame = geometry.frame_period_s
    if not club.t_first - frame <= t_club <= club.t_last + frame:
        return from_start(
            f"the club walk ({1000.0 * club.t_first:.0f}-{1000.0 * club.t_last:.0f} ms) does not "
            f"reach the tee bin inside its samples ({1000.0 * t_club:.0f} ms)"
        )
    sigma_s = math.hypot(
        _line_sigma_m(club, t_club, res) / club.speed_ms, prior.uncertainty_m / club.speed_ms
    )
    club_sigma_m = ball.speed_ms * sigma_s
    start_sigma_m = _walk_start_sigma_m(geometry, ball.speed_ms, club.speed_ms)
    if club_sigma_m > start_sigma_m:
        return from_start(
            f"the club walk's arrival at the tee bin ({1000.0 * t_club:.1f} ms) rests on a "
            f"prior of sigma {prior.uncertainty_m:.2f} m: {club_sigma_m:.2f} m against the "
            f"start's {start_sigma_m:.2f} m"
        )
    return {
        "t_s": float(t_club),
        "sigma_s": float(sigma_s),
        "source": CONTACT_CLUB_AT_TEE,
        "label": "estimated",
        "club_speed_ms": float(club.speed_ms),
        "tee_apparent_m": float(tee_apparent_m),
        "reason": (
            f"contact time from the {club.speed_ms:.1f} m/s club walk reaching the tee bin at "
            f"{1000.0 * t_club:.1f} ms; {NO_RING_CONTACT}"
        ),
    }


def _launch_point(  # pylint: disable=too-many-arguments,too-many-locals
    prepared: PreparedShotDump,
    scope: str,
    gate: tuple[float, float],
    ball: tracking.BallTrack,
    club_speed_mph: float | None,
    *,
    prior: LaunchRangePrior | None = None,
    range_bias_m: float = 0.0,
    contact: ContactTime | None = None,
    club_candidate: tracking.BallTrack | None = None,
) -> dict | str:
    """One walk's launch point: its line read back at the contact time (apparent range,
    uncertainty, how the time was obtained), or why not.

    The uncertainty is the walk's range rate times the contact time's sigma, combined with
    the line's own error there (and, read back to its start, the club's contamination)."""
    geometry = prepared.geometry
    res = geometry.range_res_m
    if (
        ball.slope_bins <= 0.0
        or ball.n_inliers < MIN_BALL_INLIERS
        or (ball.t_last - ball.t_first) < MIN_BALL_SPAN_S
    ):
        return (
            f"the ball track is too thin to extrapolate ({ball.n_inliers} samples over "
            f"{1000.0 * (ball.t_last - ball.t_first):.1f} ms)"
        )
    start_s = walk_start_s(prepared, scope, ball, gate)
    tee_m = prior.range_m + float(range_bias_m) if prior is not None else None
    club, club_source = club_candidate, "club_by_speed"
    if club is None and club_speed_mph and club_speed_mph > 0.0:
        around_s = _crossing_s(ball, tee_m, res) if tee_m is not None else start_s
        club = _club_walk(
            prepared, scope, ball, around_s, gate[0], float(club_speed_mph) / tracking.MPH_PER_MS
        )
        club_source = "approach_walk"
    timing = _contact_time(prepared, ball, start_s, club, prior, range_bias_m, contact)
    impact_t_s = timing["t_s"]
    apparent = float(ball.range_at(impact_t_s, res))
    if timing["source"] == CONTACT_WALK_START:
        club_ms = club.speed_ms if club is not None else None
        terms = [_walk_start_sigma_m(geometry, ball.speed_ms, club_ms)]
    else:
        terms = [ball.speed_ms * timing["sigma_s"]]
    terms += [_line_sigma_m(ball, impact_t_s, res), MODEL_FLOOR_M]
    club_facts = _track_facts(club, res)
    if club_facts is not None:
        club_facts = {
            **club_facts,
            "used": timing["source"] == CONTACT_CLUB_AT_TEE,
            "source": club_source,
        }
    return {
        "method": METHOD,
        "impact_t_s": impact_t_s,
        "apparent_m": apparent,
        "uncertainty_m": float(math.hypot(*terms)),
        "contact": timing,
        "ball_track": {
            **_track_facts(ball, res),
            **_walk_record(prepared, scope, gate, ball),
            "walk_start_s": start_s,
        },
        "club_track": club_facts,
        "ball_walk": ball,
        "club_walk": club,
        "club_source": club_source if club is not None else None,
    }


def _point_facts(point: dict | str, range_bias_m: float) -> dict:
    """A launch point's range, uncertainty and method for the record, or why there is none."""
    if isinstance(point, str):
        return {"launch_range_m": None, "why_not": point}
    return {
        "launch_range_m": point["apparent_m"] - float(range_bias_m),
        "launch_uncertainty_m": point["uncertainty_m"],
        "method": point["method"],
    }


def _settle_competition(  # pylint: disable=too-many-arguments,too-many-locals
    prepared: PreparedShotDump,
    scope: str,
    gate: tuple[float, float],
    point: dict,
    ambiguity: tracking.TrackAmbiguity,
    *,
    club_speed_mph: float | None,
    prior: LaunchRangePrior | None,
    range_bias_m: float,
    contact: ContactTime | None = None,
    club_candidate: tracking.BallTrack | None = None,
) -> tuple[dict, dict]:
    """(the launch point to report, the competition's record) for an ambiguous walk.

    The runner-up walk's own launch point is worked out the same way. One that cannot be
    made, or lies outside the searched range, is no answer (``runner_up_unusable``). Two
    within COMPETING_SIGMAS of their combined uncertainty are one answer (``agree``).
    Otherwise the range is ``ambiguous``: the camera prior, when it agrees with exactly
    one of the two (PRIOR_AGREEMENT_SIGMAS), picks that one (``resolved_by``); the label
    stays AMBIGUOUS_LABEL either way, because the IWR alone could not decide.
    """
    other = _launch_point(
        prepared,
        scope,
        gate,
        ambiguity.runner_up_track,
        club_speed_mph,
        prior=prior,
        range_bias_m=range_bias_m,
        contact=contact,
        club_candidate=club_candidate,
    )
    if not isinstance(other, str) and not gate[0] <= other["apparent_m"] <= gate[1]:
        other = f"its launch point {other['apparent_m']:.2f} m lies outside the searched range"
    record = {
        "reason": ambiguity.reason,
        "support_z": ambiguity.support_z,
        "separation_bins": ambiguity.separation_bins,
        "selected": {**ambiguity.selected, **_point_facts(point, range_bias_m)},
        "runner_up": {**ambiguity.runner_up, **_point_facts(other, range_bias_m)},
        "camera_prior": None,
        "resolved_by": None,
    }
    if isinstance(other, str):
        return point, {**record, "verdict": "runner_up_unusable"}
    gap = abs(point["apparent_m"] - other["apparent_m"])
    if gap <= COMPETING_SIGMAS * math.hypot(point["uncertainty_m"], other["uncertainty_m"]):
        return point, {**record, "verdict": "agree"}
    record["verdict"] = "ambiguous"
    if prior is None:
        return point, record

    def agrees(candidate: dict) -> bool:
        true_range = candidate["apparent_m"] - float(range_bias_m)
        allowed = PRIOR_AGREEMENT_SIGMAS * math.hypot(
            prior.uncertainty_m, candidate["uncertainty_m"]
        )
        return abs(true_range - prior.range_m) <= allowed

    agree_selected, agree_other = agrees(point), agrees(other)
    swap = agree_other and not agree_selected
    if swap:
        record["selected"], record["runner_up"] = record["runner_up"], record["selected"]
        point, other = other, point
        agree_selected, agree_other = agree_other, agree_selected
    record["camera_prior"] = {
        **prior.to_dict(),
        "agrees_with": [
            name for name, ok in (("selected", agree_selected), ("runner_up", agree_other)) if ok
        ],
    }
    record["resolved_by"] = "camera_prior" if agree_selected != agree_other else None
    record["swapped"] = swap
    return point, record


def _honest_ambiguous_sigma(point: dict, competing: dict) -> dict:
    """An ambiguous launch point's sigma covers the other walk's: half their separation
    added in quadrature to the selected walk's own (the IWR could not tell them apart)."""
    if competing.get("verdict") != "ambiguous":
        return point
    selected = competing["selected"].get("launch_range_m")
    other = competing["runner_up"].get("launch_range_m")
    if selected is None or other is None:
        return point
    half = abs(float(selected) - float(other)) / 2.0
    competing["uncertainty_basis"] = "own_and_half_the_separation"
    competing["half_separation_m"] = half
    return {**point, "uncertainty_m": float(math.hypot(point["uncertainty_m"], half))}


def _club_by_speed(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    prepared: PreparedShotDump,
    gate: tuple[float, float],
    ball_ms: float,
    club_ms: float | None,
    prior: LaunchRangePrior | None,
    range_bias_m: float,
) -> tracking.BallTrack | None:
    """The walk the ball's old window (0.85-1.10 of the OPS ball) finds, when its speed is
    the club's (``is_club_walk_speed``); None otherwise."""
    if club_ms is None:
        return None
    walk, _scope = _ball_walk(prepared, gate, ball_ms, prior=prior, range_bias_m=range_bias_m)
    if walk is None or not is_club_walk_speed(walk.speed_ms, ball_ms=ball_ms, club_ms=club_ms):
        return None
    return walk


def _anchor(
    prepared: PreparedShotDump,
    point: dict,
    prior: LaunchRangePrior | None,
    range_bias_m: float,
) -> dict:
    """Whether the ball walk leaves the resting ball when the club gets there.

    Both walks are read at the anchor range (the camera prior, else the launch point
    itself): the ball's crossing is its implied contact, the club's is where it meets the
    ball. The club is the club-speed walk the ball's old window found, else the approach
    walk found beside it; a supplied contact time stands in for the club's crossing."""
    geometry = prepared.geometry
    res = geometry.range_res_m
    ball = point["ball_walk"]
    if prior is not None:
        anchor_m = prior.range_m + float(range_bias_m)
        sigma_m, source = float(prior.uncertainty_m), "camera_prior"
    else:
        anchor_m = float(point["apparent_m"])
        sigma_m, source = float(point["uncertainty_m"]), "launch_point"
    club, club_source = point.get("club_walk"), point.get("club_source")
    record = {
        "anchor_range_m": anchor_m - float(range_bias_m),
        "anchor_sigma_m": sigma_m,
        "anchor_source": source,
        "t_ball_s": float(_crossing_s(ball, anchor_m, res)),
        "ball_speed_ms": float(ball.speed_ms),
    }
    timing = point.get("contact") or {}
    if timing.get("source") == CONTACT_SUPPLIED:
        # the supplied contact time is the club's arrival: the ball's crossing is held to it
        club_speed, club_source = None, CONTACT_SUPPLIED
        t_club = float(timing["t_s"])
        prior_term = PRIOR_AGREEMENT_SIGMAS * math.hypot(
            sigma_m / ball.speed_ms, float(timing["sigma_s"])
        )
    elif club is None or club.slope_bins <= 0.0:
        return {**record, "status": "unanchored", "club_source": None}
    else:
        club_speed = float(club.speed_ms)
        t_club = float(_crossing_s(club, anchor_m, res))
        prior_term = (
            abs(1.0 / ball.speed_ms - 1.0 / club.speed_ms) * PRIOR_AGREEMENT_SIGMAS * sigma_m
        )
    frame_term = CONTACT_AGREEMENT_FRAMES * geometry.frame_period_s
    tolerance = frame_term + prior_term
    if abs(record["t_ball_s"] - t_club) > tolerance:
        status = "disagrees"
    elif prior_term > frame_term and club_source != CONTACT_SUPPLIED:
        # the honesty rule is about the tolerance, whatever anchors it (C7a); a supplied
        # contact time does not rest on the anchor, so a loose one only widens the check
        status = "agrees_loose_prior" if source == "camera_prior" else "agrees_loose_anchor"
    else:
        status = "agrees"
    return {
        **record,
        "status": status,
        "club_source": club_source,
        "club_speed_ms": club_speed,
        "t_club_s": t_club,
        "frame_term_s": float(frame_term),
        "prior_term_s": float(prior_term),
        "tolerance_s": float(tolerance),
    }


def estimate_launch_range(  # pylint: disable=too-many-arguments,too-many-locals,too-many-return-statements
    prepared: PreparedShotDump,
    *,
    ball_speed_mph: float | None,
    club_speed_mph: float | None,
    prior: LaunchRangePrior | None,
    net_range_m: float | None,
    range_bias_m: float = 0.0,
    contact_time: ContactTime | None = None,
) -> LaunchRangeEstimate:
    """This shot's launch-point range; ``prepared`` is the capture's vertical TX pair.

    The ball walk's line is read back at ``contact_time`` (on this capture's IWR clock)
    when there is one, measured; else at the club walk's arrival at the tee bin or the
    walk's start, estimated (``_contact_time``).

    When the tracker could not separate the ball's walk from a different one, both
    launch points are worked out (``_settle_competition``) and, if they differ, the
    estimate carries ``competing`` and the label AMBIGUOUS_LABEL."""
    gate = search_gate_m(prior, net_range_m)
    common: dict[str, Any] = {"search_gate_m": gate, "range_bias_m": range_bias_m, "prior": prior}
    if not ball_speed_mph or ball_speed_mph <= 0.0:
        return LaunchRangeEstimate(
            status="not_measured", reason="no OPS243 ball speed for this shot", **common
        )
    geometry = prepared.geometry
    res = geometry.range_res_m
    ball_ms = float(ball_speed_mph) / tracking.MPH_PER_MS
    club_ms = (
        float(club_speed_mph) / tracking.MPH_PER_MS
        if club_speed_mph and club_speed_mph > 0.0
        else None
    )
    club_candidate = _club_by_speed(prepared, gate, ball_ms, club_ms, prior, range_bias_m)
    club_facts = (
        {
            **_track_facts(club_candidate, res),
            "used": False,
            "why": "club_speed",
        }
        if club_candidate is not None
        else None
    )
    ball, scope = _ball_walk(
        prepared, gate, ball_ms, prior=prior, range_bias_m=range_bias_m, club_ms=club_ms
    )
    if ball is None:
        if club_candidate is not None:
            return LaunchRangeEstimate(
                status="not_measured",
                reason=(
                    f"{CLUB_ONLY_REASON} ({club_candidate.speed_ms:.1f} m/s; OPS club "
                    f"{club_ms:.1f}, ball {ball_ms:.1f} m/s)"
                ),
                club_track=club_facts,
                **common,
            )
        return LaunchRangeEstimate(
            status="not_measured", reason="no ball track after impact", **common
        )
    common.update(scope=scope, ball_track=_track_facts(ball, res), track=ball)
    point = _launch_point(
        prepared,
        scope,
        gate,
        ball,
        club_speed_mph,
        prior=prior,
        range_bias_m=range_bias_m,
        contact=contact_time,
        club_candidate=club_candidate,
    )
    if isinstance(point, str):
        return LaunchRangeEstimate(status="not_measured", reason=point, **common)
    if ball.ambiguity is not None:
        point, competing = _settle_competition(
            prepared,
            scope,
            gate,
            point,
            ball.ambiguity,
            club_speed_mph=club_speed_mph,
            prior=prior,
            range_bias_m=range_bias_m,
            contact=contact_time,
            club_candidate=club_candidate,
        )
        common["competing"] = competing
        if competing.get("swapped"):
            common["track"] = ball.ambiguity.runner_up_track
        point = _honest_ambiguous_sigma(point, competing)
    apparent, uncertainty = point["apparent_m"], point["uncertainty_m"]
    true_range = apparent - float(range_bias_m)
    anchor = _anchor(prepared, point, prior, range_bias_m)
    common.update(
        ball_track=point["ball_track"],
        club_track=point["club_track"] or club_facts,
        method=point["method"],
        impact_t_s=point["impact_t_s"],
        anchor=anchor,
        contact=point["contact"],
    )
    if not gate[0] <= apparent <= gate[1]:
        return LaunchRangeEstimate(
            status="not_measured",
            reason=f"the launch point {apparent:.2f} m lies outside the searched range",
            candidate_range_m=true_range,
            **common,
        )
    if prior is not None:
        combined = math.hypot(prior.uncertainty_m, uncertainty)
        if abs(true_range - prior.range_m) > PRIOR_AGREEMENT_SIGMAS * combined:
            return LaunchRangeEstimate(
                status="not_measured",
                reason=(
                    f"the launch point {true_range:.2f} m disagrees with the camera prior "
                    f"{prior.range_m:.2f} +/- {prior.uncertainty_m:.2f} m"
                ),
                candidate_range_m=true_range,
                **common,
            )
    if anchor["status"] == "disagrees":
        only = f"; {CLUB_ONLY_REASON}" if anchor["club_source"] == "club_by_speed" else ""
        return LaunchRangeEstimate(
            status="not_measured",
            reason=(
                f"the {anchor['ball_speed_ms']:.1f} m/s walk does not leave the resting ball: "
                f"it crosses {anchor['anchor_range_m']:.2f} m at "
                f"{1000.0 * anchor['t_ball_s']:.1f} ms, the club at "
                f"{1000.0 * anchor['t_club_s']:.1f} ms (within "
                f"{1000.0 * anchor['tolerance_s']:.1f} ms is the ball){only}"
            ),
            candidate_range_m=true_range,
            **common,
        )
    reasons = []
    if anchor["status"] == "agrees_loose_prior":
        reasons.append(LOOSE_PRIOR_REASON.format(sigma=anchor["anchor_sigma_m"]))
        uncertainty = math.hypot(uncertainty, anchor["anchor_sigma_m"])
    elif anchor["status"] == "agrees_loose_anchor":
        reasons.append(LOOSE_ANCHOR_REASON.format(sigma=anchor["anchor_sigma_m"]))
    if point["contact"]["label"] != "measured":
        reasons.append(point["contact"]["reason"])
    return LaunchRangeEstimate(
        status="measured",
        reason="; ".join(reasons) or None,
        range_m=true_range,
        apparent_range_m=apparent,
        uncertainty_m=float(uncertainty),
        **common,
    )


__all__ = [
    "AMBIGUOUS_LABEL",
    "CAMERA_PRIOR_SOURCE",
    "ContactTime",
    "LOCATED_TEE_SOURCE",
    "LaunchRangeEstimate",
    "LaunchRangePrior",
    "SCHEMA",
    "SOURCE",
    "ShotBallRange",
    "ball_walk_speed_floor_ms",
    "competition_note",
    "estimate_launch_range",
    "is_club_walk_speed",
    "search_gate_m",
    "shot_ball_range",
]

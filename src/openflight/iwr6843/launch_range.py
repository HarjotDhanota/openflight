"""Each swing's launch-point range from its own IWR capture (13 Oct decision "a").

The resting ball's distance is no longer measured by static empty/ball captures at
setup: indoors the scene changes between the two captures by 63-68 dB (sum) against
a ball echo of about 66 dB at 2.1 m, so the method fails beyond about 1.5 m
(iwr-range-research.md). TrackMan's radar needs motion too. Each swing's capture
does have motion: the ball's range walk after impact, and the club's approach.

**Method.** The ball's walk is searched from just short of the camera prior to the
net, at the OPS243's ball speed (radial speed 0.75-1.05 of it). The launch point is
where it began:

* ``club_ball_intersection``: the club's approach walk (searched in the frames before
  the ball's first sample, at the OPS243 club speed) meets the ball's walk at impact,
  because the club reaches the ball there. That meeting fixes both the impact instant
  and the range. With the ball at ``v_b`` and the club at ``v_c``, a line error of
  ``s_b`` and ``s_c`` moves the meeting range by
  ``sqrt((v_b s_c)^2 + (v_c s_b)^2) / (v_b - v_c)``: about 3x the line errors.
* ``ball_track_start``: without a club walk, the ball's walk at its first sample,
  which comes up to a frame after impact; half a frame of ball travel is in the
  uncertainty.

The radar's range bias is removed. The camera prior only sizes the search and checks
the answer: a launch point more than ``PRIOR_AGREEMENT_SIGMAS`` combined sigmas from
it is not used (kept as ``candidate_range_m`` for review).

**Not validated against a tape.** The first gate is the next session's per-swing
range against a taped ball (``scripts/analysis/grade_launch_range.py``).

Every value carries its label: ``measured`` (this shot's IWR walk), ``estimated``
(the camera prior standing in), or ``not_measured`` with the reason.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np

from openflight.iwr6843 import tracking
from openflight.iwr6843.shot import PreparedShotDump, near_mti_notch, track_broken

SCHEMA = "openflight.iwr_launch_range.v1"
SOURCE = "iwr_post_impact"
CAMERA_PRIOR_SOURCE = "camera_prior"

# Radial ball speed against the OPS243's (both sit behind the ball, so the radial
# speed is the ball speed times a projection near 1; the walk's own slope reads up to
# a few per cent off it). The floor keeps the slower post-impact club out.
BALL_SPEED_FRACTION = (0.85, 1.10)
# The club's radial speed against the OPS club speed (iwr6843.club's window).
CLUB_SPEED_FRACTION = (0.70, 1.20)
CLUB_TRACK_SPEED_LIMIT_MS = (12.0, 60.0)
# Around the ball walk's start, where the club's approach is searched. The start can
# come early: the club's last samples lie within the line's tolerance of the ball's
# walk extended back (they close at only v_b - v_c), so the window reaches past it.
CLUB_WINDOW_BEFORE_S = 0.015
CLUB_WINDOW_AFTER_S = 0.006
# Without a club walk, the walk's start may be the club's last samples: within the
# line tolerance (1.2 bins) of a ball line it closes on at ~0.25 v_b, i.e. up to
# ~0.2 m short. Half of that is carried as an uncertainty, not corrected.
CLUB_CONTAMINATION_M = 0.11
# The ball must outrun the club by this much for their meeting to be well posed.
MIN_SPEED_GAP_MS = 4.0
# The search starts this many prior sigmas (and a margin) short of the prior.
PRIOR_SEARCH_SIGMAS = 3.0
SEARCH_MARGIN_M = 0.10
MIN_SEARCH_FLOOR_M = 0.50
DEFAULT_SEARCH_FLOOR_M = 0.60
MAX_RANGE_M = 4.7
NET_MARGIN_M = 0.25
# A launch point this many combined sigmas from the camera prior is not used.
PRIOR_AGREEMENT_SIGMAS = 3.0
# Provisional model floor: the club's reflecting centre against the ball's near face
# at contact, and the ball's first instants in the club's range bin. Not measured.
MODEL_FLOOR_M = 0.02
# The ball's walk needs this support before its start or slope is trusted.
MIN_BALL_INLIERS = 12
MIN_BALL_SPAN_S = 0.006
# Where the walk starts: the first frame holding at least this many of its samples
# whose next frame does too. A stray noise peak on the line before impact, or the
# club's last samples within the line's tolerance, must not start it early.
WALK_START_MIN_SAMPLES = 2
# the RANSAC line's inlier tolerance, in bins, as tracking.find_ball uses
_TRACK_TOLERANCE_BINS = 1.2


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

    @property
    def measured(self) -> bool:
        return self.status == "measured" and self.range_m is not None

    @property
    def source(self) -> str | None:
        return SOURCE if self.measured else None

    @property
    def label(self) -> str:
        return "measured" if self.measured else "not_measured"

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
        }


@dataclass(frozen=True)
class ShotBallRange:
    """The range a shot's range-dependent metrics use, with its source and label."""

    range_m: float | None
    uncertainty_m: float | None
    source: str | None  # iwr_post_impact | camera_prior | None
    label: str  # measured | estimated | not_measured
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
    """The shot's own IWR launch point; else the camera prior, labelled; else none.

    Never a default number: without either, every range-dependent metric of the shot
    is not measured, and says why.
    """
    if estimate is not None and estimate.measured:
        return ShotBallRange(
            range_m=estimate.range_m,
            uncertainty_m=estimate.uncertainty_m,
            source=SOURCE,
            label="measured",
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
    }


def _gate_detections(
    prepared: PreparedShotDump, scope: str, gate: tuple[float, float]
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per-loop peaks in the gate: (loop index, time s, fractional bin)."""
    geometry = prepared.geometry
    power = tracking.loop_power(prepared.mti(scope))
    # pylint: disable-next=protected-access
    loops, bins = tracking._detections(power, geometry, gates_m=(gate,))
    n_loops = geometry.n_loops
    times = np.array([geometry.loop_time(i // n_loops, i % n_loops) for i in loops], dtype=float)
    return np.asarray(loops), times, np.asarray(bins, dtype=float)


def refit_segment(
    track: tracking.BallTrack,
    detections: tuple[np.ndarray, np.ndarray, np.ndarray],
    *,
    after_s: float | None = None,
    before_s: float | None = None,
    tolerance_bins: float = _TRACK_TOLERANCE_BINS,
) -> tracking.BallTrack:
    """The walk refitted on its own samples on one side of impact only.

    The ball's walk extended back before impact runs within the line tolerance of the
    club's last samples, and the club's past impact runs on slower: each line is
    refitted on its own side so neither drags the other's meeting point.
    """
    _loops, times, bins = detections
    keep = np.abs(bins - track.bin_at(times)) < tolerance_bins
    if after_s is not None:
        keep &= times > after_s
    if before_s is not None:
        keep &= times < before_s
    if int(keep.sum()) < 4 or float(np.ptp(times[keep])) <= 0.0:
        return track
    slope, intercept = np.polyfit(times[keep], bins[keep], 1)
    residual = bins[keep] - (slope * times[keep] + intercept)
    return replace(
        track,
        speed_ms=float(slope) * (track.speed_ms / track.slope_bins),
        slope_bins=float(slope),
        intercept_bins=float(intercept),
        rms_bins=float(np.sqrt(np.mean(residual**2))),
        n_inliers=int(keep.sum()),
        t_first=float(times[keep].min()),
        t_last=float(times[keep].max()),
        quad_bins=None,
    )


def walk_start_s(
    prepared: PreparedShotDump,
    scope: str,
    track: tracking.BallTrack,
    gate: tuple[float, float],
) -> float:
    """When the walk's samples start holding frame after frame (see WALK_START_MIN_SAMPLES).

    ``BallTrack.t_first`` is its earliest inlier, which a single noise peak on the
    extended line can pull before impact.
    """
    geometry = prepared.geometry
    loops, times, bins = _gate_detections(prepared, scope, gate)
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


def _ball_walk(
    prepared: PreparedShotDump, gate: tuple[float, float], speed_ms: float
) -> tuple[tracking.BallTrack | None, str]:
    """The ball's walk in the gate at the OPS speed; the window MTI past a notch."""
    geometry = prepared.geometry
    low, high = BALL_SPEED_FRACTION
    bounds = (low * speed_ms, high * speed_ms)

    def find(scope: str) -> tracking.BallTrack | None:
        return tracking.find_ball(
            prepared.mti(scope),
            geometry,
            gates_m=(gate,),
            speed_bounds_ms=bounds,
            min_ball_ms=bounds[0],
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
    )


def _meeting(  # pylint: disable=too-many-arguments
    prepared: PreparedShotDump,
    scope: str,
    gate: tuple[float, float],
    ball: tracking.BallTrack,
    club: tracking.BallTrack,
    start_s: float,
) -> tuple[float, tracking.BallTrack, tracking.BallTrack] | str:
    """Where the club's approach meets the ball's walk, each refitted on its own side.

    Returns (impact time, ball walk, club walk), or why they do not meet.
    """
    geometry = prepared.geometry
    ball_detections = _gate_detections(prepared, scope, gate)
    club_gate = (
        max(0.35, gate[0] - 0.6),
        ball.range_at(start_s + CLUB_WINDOW_AFTER_S, geometry.range_res_m) + 0.10,
    )
    club_detections = _gate_detections(prepared, scope, club_gate)
    earliest = start_s - CLUB_WINDOW_BEFORE_S
    latest = start_s + CLUB_WINDOW_AFTER_S + geometry.frame_period_s
    meet_s = None
    for _ in range(4):
        if ball.slope_bins - club.slope_bins <= 0.0 or (
            ball.speed_ms - club.speed_ms < MIN_SPEED_GAP_MS
        ):
            return "the club walk is about as fast as the ball's"
        meet_s = (club.intercept_bins - ball.intercept_bins) / (ball.slope_bins - club.slope_bins)
        if not earliest <= meet_s <= latest:
            return "the club and ball walks meet outside the impact window"
        ball = refit_segment(ball, ball_detections, after_s=meet_s)
        club = refit_segment(club, club_detections, before_s=meet_s, after_s=earliest)
    final = (club.intercept_bins - ball.intercept_bins) / (ball.slope_bins - club.slope_bins)
    if not earliest <= final <= latest:
        return "the club and ball walks meet outside the impact window"
    return float(final), ball, club


def estimate_launch_range(  # pylint: disable=too-many-arguments,too-many-locals,too-many-return-statements
    prepared: PreparedShotDump,
    *,
    ball_speed_mph: float | None,
    club_speed_mph: float | None,
    prior: LaunchRangePrior | None,
    net_range_m: float | None,
    range_bias_m: float = 0.0,
) -> LaunchRangeEstimate:
    """This shot's launch-point range from its own capture (see the module docstring).

    ``prepared`` is the capture's vertical TX pair (``prepare_lcmf_capture(raw).vertical``).
    """
    gate = search_gate_m(prior, net_range_m)
    common: dict[str, Any] = {"search_gate_m": gate, "range_bias_m": range_bias_m, "prior": prior}
    if not ball_speed_mph or ball_speed_mph <= 0.0:
        return LaunchRangeEstimate(
            status="not_measured", reason="no OPS243 ball speed for this shot", **common
        )
    geometry = prepared.geometry
    res = geometry.range_res_m
    ball_ms = float(ball_speed_mph) / tracking.MPH_PER_MS
    ball, scope = _ball_walk(prepared, gate, ball_ms)
    if ball is None:
        return LaunchRangeEstimate(
            status="not_measured", reason="no ball track after impact", **common
        )
    common.update(scope=scope, ball_track=_track_facts(ball, res), track=ball)
    if (
        ball.slope_bins <= 0.0
        or ball.n_inliers < MIN_BALL_INLIERS
        or (ball.t_last - ball.t_first) < MIN_BALL_SPAN_S
    ):
        return LaunchRangeEstimate(
            status="not_measured",
            reason=(
                f"the ball track is too thin to extrapolate ({ball.n_inliers} samples over "
                f"{1000.0 * (ball.t_last - ball.t_first):.1f} ms)"
            ),
            **common,
        )

    start_s = walk_start_s(prepared, scope, ball, gate)
    common["ball_track"] = {**common["ball_track"], "walk_start_s": start_s}
    method, impact_t_s, club, club_facts = "ball_track_start", start_s, None, None
    if club_speed_mph and club_speed_mph > 0.0:
        club = _club_walk(
            prepared, scope, ball, start_s, gate[0], float(club_speed_mph) / tracking.MPH_PER_MS
        )
        club_facts = _track_facts(club, res)
    if club is not None:
        meeting = _meeting(prepared, scope, gate, ball, club, start_s)
        if isinstance(meeting, str):
            club_facts = {**club_facts, "used": False, "why": meeting}
        else:
            impact_t_s, ball, club = meeting
            method = "club_ball_intersection"
            common["ball_track"] = {
                **_track_facts(ball, res),
                "walk_start_s": start_s,
                "refit": "post_impact_samples_only",
            }
            club_facts = {**_track_facts(club, res), "used": True, "refit": "pre_impact_only"}

    apparent = float(ball.range_at(impact_t_s, res))
    if method == "club_ball_intersection":
        sigma_b = _line_sigma_m(ball, impact_t_s, res)
        sigma_c = _line_sigma_m(club, impact_t_s, res)
        walks = math.hypot(ball.speed_ms * sigma_c, club.speed_ms * sigma_b) / (
            ball.speed_ms - club.speed_ms
        )
        uncertainty = math.hypot(walks, MODEL_FLOOR_M)
    else:
        start_gap = ball.speed_ms * geometry.frame_period_s / 2.0
        uncertainty = math.hypot(
            _line_sigma_m(ball, impact_t_s, res), start_gap, CLUB_CONTAMINATION_M, MODEL_FLOOR_M
        )
    true_range = apparent - float(range_bias_m)
    common.update(club_track=club_facts, method=method, impact_t_s=impact_t_s)
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
    return LaunchRangeEstimate(
        status="measured",
        reason=None,
        range_m=true_range,
        apparent_range_m=apparent,
        uncertainty_m=float(uncertainty),
        **common,
    )


__all__ = [
    "CAMERA_PRIOR_SOURCE",
    "LaunchRangeEstimate",
    "LaunchRangePrior",
    "SCHEMA",
    "SOURCE",
    "ShotBallRange",
    "estimate_launch_range",
    "search_gate_m",
    "shot_ball_range",
]

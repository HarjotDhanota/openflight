"""Historical ball-speed correction: the peak-radial model.

The OPS243 measures RADIAL speed — the component of the ball's velocity
along the radar's line of sight. The ball departs upward at the launch
angle while the radar sits low behind the tee, so the radial reading is
compressed by cos(angle between velocity and LOS). This is the dominant
cause of the long-observed ~2-2.5 mph OPS-below-TrackMan ball speed gap.

Model (zero free parameters): the OPS mode-based extraction reads near
the MAXIMUM of the radial-speed profile — radial speed rises early in
flight as the LOS aligns with the velocity vector, then falls with drag,
so the profile has a peak. The correction divides the measured speed by
that predicted peak fraction.

Historical pre-v3 evidence reported against TrackMan ball speeds (2026-06):
- 2026-06-08 bay, 128 shots: bias -2.15 -> +0.33 mph, |err| 2.83 -> 1.48
- 2026-05-30 holdout, 26 shots: bias -2.26 -> +0.30, |err| 2.35 -> 1.31
- Coleman cross-rig outdoor, 62 shots: bias -2.09 -> +0.65, median 0.58
- Production configuration (OUR launch angles — 100 two-ray + 28
  club-fallback — instead of TrackMan's): bias +0.32, |err| 1.52,
  median 0.67. The LA dependence couples speed accuracy to launch-angle
  accuracy, but LA errors are zero-mean post-calibration, so the
  coupling adds ~0.1 mph of scatter and no bias.

Known caveat: high-launch wedges on one outdoor rig overcorrected (~+3);
under observation. Club speed does NOT need this correction — the club
head's delivery is nearly parallel to the LOS (error ~0.1-0.2 mph).
"""

from __future__ import annotations

import math
from typing import Any

from .numeric import finite_float as finite_number

MPH_TO_FTS = 1.4666667
DRAG_MPH_PER_MS = 0.027  # iron-speed drag deceleration of the ball
EXPERIMENTAL_MODEL = "legacy_peak_radial_v1"
MEASURED_DIRECTION_SOURCES = frozenset({"radar", "camera"})
# Measured OPS-relative ball geometry: by tape, or the rig's OPS offsets on the IWR tee range.
MEASURED_OPS_GEOMETRY_SOURCES = frozenset({"ops_measured", "ops_rig_offsets"})
MAX_WINDOW_MS = 250.0


def evaluate_experimental_total_speed(
    ops_radial_speed_mph: Any,
    launch_angle_vertical_deg: Any,
    launch_angle_vertical_source: str | None,
    ops_ball_distance_ft: Any,
    ball_above_ops_ft: Any,
    *,
    window_ms: float = 70.0,
    geometry_source: str | None = None,
    ops_ball_lateral_ft: Any = 0.0,
) -> dict[str, Any]:
    """Evaluate the historical peak-radial model without changing canonical speed."""

    inputs = {
        "ops_radial_speed_mph": finite_number(ops_radial_speed_mph),
        "launch_angle_vertical_deg": finite_number(launch_angle_vertical_deg),
        "launch_angle_vertical_source": (
            launch_angle_vertical_source if isinstance(launch_angle_vertical_source, str) else None
        ),
        "ops_ball_distance_ft": finite_number(ops_ball_distance_ft),
        "ball_above_ops_ft": finite_number(ball_above_ops_ft),
        "ops_ball_lateral_ft": finite_number(ops_ball_lateral_ft),
        "geometry_source": geometry_source if isinstance(geometry_source, str) else None,
        "window_ms": finite_number(window_ms),
        "drag_mph_per_ms": DRAG_MPH_PER_MS,
    }
    result = {
        "status": "withheld",
        "value_mph": None,
        "source": "ops_radial_cosine_candidate",
        "validation": "unvalidated",
        "model": EXPERIMENTAL_MODEL,
        "reason": None,
        "inputs": inputs,
        "assumptions": [
            "OPS mode extraction represents the peak radial speed within the stated window.",
            "Vertical launch and OPS line-of-sight geometry describe the same shot.",
            "The fixed drag rate is historical and is not validated for the measured v3 rig.",
            "This scalar model does not prove co-temporal 3D velocity/LOS alignment.",
        ],
    }
    numeric = tuple(
        inputs[key]
        for key in (
            "ops_radial_speed_mph",
            "launch_angle_vertical_deg",
            "ops_ball_distance_ft",
            "ball_above_ops_ft",
            "ops_ball_lateral_ft",
            "window_ms",
        )
    )
    if any(value is None for value in numeric):
        result["reason"] = "candidate inputs must be finite numbers"
        return result
    if (
        ops_radial_speed_mph <= 0
        or ops_ball_distance_ft <= 0
        or window_ms <= 0
        or window_ms > MAX_WINDOW_MS
    ):
        result["reason"] = (
            f"radial speed and OPS distance must be positive and window must be in "
            f"(0, {MAX_WINDOW_MS:g}] ms"
        )
        return result
    if launch_angle_vertical_source not in MEASURED_DIRECTION_SOURCES:
        result["reason"] = "a measured radar or camera vertical launch direction is required"
        return result
    if geometry_source not in MEASURED_OPS_GEOMETRY_SOURCES:
        result["reason"] = (
            "measured OPS-relative ball distance and height geometry is unavailable; "
            "IWR or K-LD7 geometry proxies are not promoted"
        )
        return result
    factor = radial_speed_factor(
        float(launch_angle_vertical_deg),
        float(ops_radial_speed_mph),
        float(ops_ball_distance_ft),
        float(ball_above_ops_ft),
        float(window_ms),
        ball_lateral_ft=float(ops_ball_lateral_ft),
    )
    value_mph = float(ops_radial_speed_mph) / factor
    if not math.isfinite(value_mph):
        result["reason"] = "candidate output is nonfinite"
        return result
    result.update(
        status="available",
        value_mph=value_mph,
        reason=None,
        radial_factor=factor,
    )
    return result


def radial_speed_factor(
    launch_angle_deg: float,
    ball_speed_mph: float,
    ball_distance_ft: float,
    ball_above_radar_ft: float,
    window_ms: float = 70.0,
    *,
    ball_lateral_ft: float = 0.0,
) -> float:
    """Predicted (OPS radial reading) / (true ball speed), in (0, 1].

    Maximum of the radial-speed profile over the capture window:
    radial(t) = v(t) * cos(launch - elevation_of_ball_from_radar(t)), scaled
    by the share of the line of sight a sideways OPS offset takes: the ball
    flies downrange while the OPS sits ``ball_lateral_ft`` to one side.
    """
    if ball_speed_mph <= 0:
        return 1.0
    la = math.radians(launch_angle_deg)
    v_fts = ball_speed_mph * MPH_TO_FTS
    best = 0.0
    t_ms = 0.0
    while t_ms <= window_ms:
        v_frac = max(1.0 - DRAG_MPH_PER_MS * t_ms / ball_speed_mph, 0.0)
        t = t_ms / 1000.0
        x = ball_distance_ft + v_fts * math.cos(la) * t
        y = ball_above_radar_ft + v_fts * math.sin(la) * t
        sideways = math.hypot(x, y) / math.hypot(ball_lateral_ft, x, y)
        best = max(best, v_frac * math.cos(la - math.atan2(y, x)) * sideways)
        t_ms += 2.0
    return min(max(best, 0.5), 1.0)


def correct_ball_speed(
    measured_mph: float,
    launch_angle_deg: float,
    ball_distance_ft: float,
    ball_above_radar_ft: float,
) -> float:
    """True ball speed from the OPS radial measurement."""
    factor = radial_speed_factor(
        launch_angle_deg, measured_mph, ball_distance_ft, ball_above_radar_ft
    )
    return measured_mph / factor

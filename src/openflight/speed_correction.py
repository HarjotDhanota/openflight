"""Historical ball-speed correction and an unvalidated measured projection.

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

import numpy as np

MPH_TO_FTS = 1.4666667
DRAG_MPH_PER_MS = 0.027  # iron-speed drag deceleration of the ball
EXPERIMENTAL_MODEL = "legacy_peak_radial_v1"
MEASURED_DIRECTION_SOURCES = frozenset({"radar", "camera"})
MAX_WINDOW_MS = 250.0
MEASURED_PROJECTION_MODEL = "measured_los_projection_v1"


def evaluate_measured_projection_total_speed(
    *,
    ops_radial_speed_mph: Any,
    ops_origin_m: Any,
    ball_position_m: Any,
    trajectory_direction: Any,
    shot_id: Any,
    direction_shot_id: Any,
    radial_observed_at_ns: Any,
    radial_window_start_ns: Any,
    radial_window_end_ns: Any,
    radial_quantity: Any,
    radial_clock_domain_id: Any,
    direction_clock_domain_id: Any,
    radial_target_frame_id: Any,
    direction_target_frame_id: Any,
    direction_dependencies: Any,
    minimum_projection: Any,
    direction_observed_at_ns: Any,
    association_tolerance_ns: Any,
    ops_geometry_source: Any,
    direction_source: Any,
) -> dict[str, Any]:
    """Project a co-temporal radial observation onto an independent 3D direction."""

    result = {
        "status": "withheld",
        "value_mph": None,
        "source": "ops_measured_los_projection_candidate",
        "validation": "unvalidated",
        "model": MEASURED_PROJECTION_MODEL,
        "reason": None,
        "canonical_speed_contract": "ops_radial_unchanged",
        "inputs": {
            "ops_radial_speed_mph": ops_radial_speed_mph,
            "ops_origin_m": ops_origin_m,
            "ball_position_m": ball_position_m,
            "trajectory_direction": trajectory_direction,
            "shot_id": shot_id,
            "direction_shot_id": direction_shot_id,
            "radial_clock_domain_id": radial_clock_domain_id,
            "direction_clock_domain_id": direction_clock_domain_id,
            "radial_target_frame_id": radial_target_frame_id,
            "direction_target_frame_id": direction_target_frame_id,
            "direction_dependencies": direction_dependencies,
            "minimum_projection": minimum_projection,
        },
    }
    if isinstance(ops_radial_speed_mph, bool) or any(
        isinstance(item, bool)
        for vector in (ops_origin_m, ball_position_m, trajectory_direction)
        if isinstance(vector, (list, tuple))
        for item in vector
    ):
        result["reason"] = "speed and vectors must not contain booleans"
        return result
    try:
        radial = float(ops_radial_speed_mph)
        origin = np.asarray(ops_origin_m, dtype=float)
        position = np.asarray(ball_position_m, dtype=float)
        direction = np.asarray(trajectory_direction, dtype=float)
    except (TypeError, ValueError, OverflowError):
        result["reason"] = "speed and vectors must be finite numeric values"
        return result
    if (
        not math.isfinite(radial)
        or radial <= 0
        or origin.shape != (3,)
        or position.shape != (3,)
        or direction.shape != (3,)
        or not np.all(np.isfinite(origin))
        or not np.all(np.isfinite(position))
        or not np.all(np.isfinite(direction))
    ):
        result["reason"] = "speed and vectors must be finite with three components"
        return result
    if not isinstance(shot_id, str) or not shot_id or direction_shot_id != shot_id:
        result["reason"] = "radial and direction evidence must identify the same shot"
        return result
    if (
        any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in (
                radial_observed_at_ns,
                radial_window_start_ns,
                radial_window_end_ns,
                direction_observed_at_ns,
                association_tolerance_ns,
            )
        )
        or association_tolerance_ns < 0
    ):
        result["reason"] = "association timestamps and tolerance must be integer nanoseconds"
        return result
    if radial_quantity != "fft_window_center_radial_speed":
        result["reason"] = "OPS radial quantity must be one FFT-window center reading"
        return result
    if radial_window_start_ns >= radial_window_end_ns or not (
        radial_window_start_ns <= radial_observed_at_ns <= radial_window_end_ns
    ):
        result["reason"] = "radial observation timestamp must lie inside its FFT window"
        return result
    if any(
        value < 0 or value > 2**63 - 1
        for value in (
            radial_observed_at_ns,
            radial_window_start_ns,
            radial_window_end_ns,
            direction_observed_at_ns,
            association_tolerance_ns,
        )
    ):
        result["reason"] = "association timestamps must be non-negative signed 64-bit values"
        return result
    if (
        not isinstance(radial_clock_domain_id, str)
        or radial_clock_domain_id != direction_clock_domain_id
        or not isinstance(radial_target_frame_id, str)
        or radial_target_frame_id != direction_target_frame_id
    ):
        result["reason"] = "evidence must share explicit clock-domain and target-frame identities"
        return result
    delta_ns = abs(radial_observed_at_ns - direction_observed_at_ns)
    if delta_ns > association_tolerance_ns:
        result["reason"] = "radial and direction evidence are outside the association tolerance"
        return result
    if not isinstance(ops_geometry_source, str) or not ops_geometry_source:
        result["reason"] = "an operator-attested measured OPS origin is required"
        return result
    if not isinstance(direction_source, str) or not direction_source:
        result["reason"] = "an operator-attested trajectory direction source is required"
        return result
    if (
        not isinstance(direction_dependencies, list)
        or any(not isinstance(item, str) for item in direction_dependencies)
        or any("ops_radial" in item for item in direction_dependencies)
    ):
        result["reason"] = "direction dependencies must declare independence from OPS radial speed"
        return result
    if isinstance(minimum_projection, bool) or not isinstance(minimum_projection, (int, float)):
        result["reason"] = "minimum_projection must be explicitly declared"
        return result
    minimum_projection = float(minimum_projection)
    if not math.isfinite(minimum_projection) or not 0 < minimum_projection <= 1:
        result["reason"] = "minimum_projection must be finite in (0, 1]"
        return result
    los = position - origin
    los_norm = float(np.linalg.norm(los))
    direction_norm = float(np.linalg.norm(direction))
    if los_norm <= 0 or direction_norm <= 0:
        result["reason"] = "LOS and trajectory direction vectors must be nonzero"
        return result
    los_unit = los / los_norm
    direction_unit = direction / direction_norm
    projection = float(np.dot(los_unit, direction_unit))
    if math.isfinite(projection) and 1.0 < projection <= 1.0 + 1e-12:
        projection = 1.0
    if not math.isfinite(projection) or projection < minimum_projection or projection > 1.0:
        result["reason"] = "trajectory direction is not a valid outbound OPS projection"
        return result
    value = radial / projection
    result.update(
        status="available",
        value_mph=value,
        reason=None,
        projection_factor=projection,
        association={
            "shot_id": shot_id,
            "radial_observed_at_ns": radial_observed_at_ns,
            "radial_window_start_ns": radial_window_start_ns,
            "radial_window_end_ns": radial_window_end_ns,
            "direction_observed_at_ns": direction_observed_at_ns,
            "delta_ns": delta_ns,
            "tolerance_ns": association_tolerance_ns,
            "clock_domain_id": radial_clock_domain_id,
            "target_frame_id": radial_target_frame_id,
            "quantity": "window-centered radial projection approximation",
            "assumption": "direction and LOS are treated as constant across the FFT window",
        },
        resolved_inputs={
            "ops_radial_speed_mph": radial,
            "ops_origin_m": origin.tolist(),
            "ball_position_m": position.tolist(),
            "trajectory_direction_unit": direction_unit.tolist(),
            "ops_geometry_source": ops_geometry_source,
            "direction_source": direction_source,
        },
    )
    return result


def evaluate_experimental_total_speed(
    ops_radial_speed_mph: Any,
    launch_angle_vertical_deg: Any,
    launch_angle_vertical_source: str | None,
    ops_ball_distance_ft: Any,
    ball_above_ops_ft: Any,
    *,
    window_ms: float = 70.0,
    geometry_source: str | None = None,
) -> dict[str, Any]:
    """Evaluate the historical peak-radial model without changing canonical speed."""

    def finite_number(value: Any) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        number = float(value)
        return number if math.isfinite(number) else None

    inputs = {
        "ops_radial_speed_mph": finite_number(ops_radial_speed_mph),
        "launch_angle_vertical_deg": finite_number(launch_angle_vertical_deg),
        "launch_angle_vertical_source": (
            launch_angle_vertical_source if isinstance(launch_angle_vertical_source, str) else None
        ),
        "ops_ball_distance_ft": finite_number(ops_ball_distance_ft),
        "ball_above_ops_ft": finite_number(ball_above_ops_ft),
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
    if geometry_source != "ops_measured":
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
) -> float:
    """Predicted (OPS radial reading) / (true ball speed), in (0, 1].

    Maximum of the radial-speed profile over the capture window:
    radial(t) = v(t) * cos(launch - elevation_of_ball_from_radar(t)).
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
        best = max(best, v_frac * math.cos(la - math.atan2(y, x)))
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

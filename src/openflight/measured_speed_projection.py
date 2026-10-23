"""Unvalidated research candidate: total ball speed from a measured line-of-sight projection.

The OPS radial speed is divided by the cosine between the OPS line of sight and a
measured trajectory direction, only when both come from the same shot, clock and frame.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

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

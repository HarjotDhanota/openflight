"""Shared fixtures for the every-metric coverage tests (V-wiring).

``EXPECTED_METRIC_IDS`` is the owner's metric list (TrackMan's metrics plus
ours), written out by hand so a metric dropped from the catalog fails every
coverage test that is parametrized over it. ``fusion_shot`` builds one shot the
way the kiosk does, with every Part E producer attached (E1 arc, E2 collision,
E3 full speeds, E4 flight), so each metric has a value or a stated reason.
"""

from __future__ import annotations

import math
from datetime import datetime

import numpy as np

from openflight.clubs import ClubType
from openflight.launch_monitor import (
    CARRY_CALCULATED,
    CARRY_NOT_MEASURED,
    SMASH_RADIAL_RATIO,
    Shot,
)

BALL = (
    "ball_speed",
    "ball_speed_full",
    "launch_vertical",
    "launch_horizontal",
    "spin_rate",
    "spin_axis",
    "gyro",
    "smash_factor",
    "smash_factor_full",
)
FLIGHT = (
    "carry",
    "carry_side",
    "total",
    "total_side",
    "apex",
    "landing_angle",
    "hang_time",
    "curve",
)
CLUB = (
    "club_speed",
    "club_speed_full",
    "club_path",
    "attack_angle",
    "face_angle",
    "face_to_path",
    "dynamic_loft",
    "spin_loft",
    "d_plane_tilt",
    "swing_plane",
    "swing_direction",
    "low_point_distance",
    "low_point_height",
    "low_point_side",
    "impact_offset",
    "impact_height",
    "dynamic_lie",
)
EXPECTED_METRIC_IDS = BALL + FLIGHT + CLUB
# Not built yet: shown as not measured, with the item that builds them (E5, E6).
PENDING_METRIC_IDS = ("impact_offset", "impact_height", "dynamic_lie")
LABELS = ("measured", "calculated", "model_geometry", "estimated", "not_measured")


def arc_positions():
    """Five pre-impact head positions on a 57 deg, 1.0 m right-handed arc (2 ms frames)."""
    theta, radius, omega, t_low = math.radians(57.0), 1.0, 38.0, 0.002
    e1 = np.array([1.0, 0.0, 0.0])
    e2 = math.cos(theta) * np.array([0.0, -1.0, 0.0]) + math.sin(theta) * np.array([0, 0, 1.0])
    impact = np.array([1.26, 0.0, 0.021])

    def at(time_s):
        phi = omega * (time_s - t_low)
        return radius * math.sin(phi) * e1 + radius * (1.0 - math.cos(phi)) * e2

    positions = []
    for t_s in (-0.0085, -0.0065, -0.0045, -0.0025, -0.0005):
        point = impact + at(t_s) - at(0.0)
        positions.append(
            {"t_s": t_s, "lateral_m": point[1], "up_m": point[2], "forward_m": point[0]}
        )
    return positions


def full_ball_speed(value_mph: float = 124.0) -> dict:
    """An E3 full ball speed, in the shape openflight.full_speed returns."""
    return {
        "status": "calculated",
        "value_mph": value_mph,
        "range_mph": [value_mph - 0.6, value_mph + 0.6],
        "source": "measured projection: OPS radial + measured launch + rig geometry",
        "method": "measured_projection_v1",
        "reason": None,
    }


def fusion_shot(**overrides) -> Shot:
    """A 7-iron fusion shot with every Part E producer attached, as the kiosk does."""
    from openflight import server as server_module  # noqa: PLC0415
    from openflight.flight import compute_flight  # noqa: PLC0415
    from openflight.full_speed import evaluate_full_club_speed, evaluate_full_smash  # noqa: PLC0415

    values = {
        "ball_speed_mph": 120.0,
        "club_speed_mph": 85.0,
        "timestamp": datetime(2026, 10, 12, 9, 0, 0),
        "shot_number": 3,
        "club": ClubType.IRON_7,
        "ball_speed_contract": "radial",
        "launch_angle_horizontal": 1.0,
        "launch_angle_horizontal_source": "camera",
        "launch_angle_horizontal_confidence": 0.8,
        "launch_angle_horizontal_frame": "target_line",
        "launch_angle_vertical": 18.0,
        "launch_angle_vertical_source": "radar",
        "launch_angle_vertical_confidence": 0.9,
        "spin_rpm": 6800.0,
        "spin_confidence": 0.9,
        "spin_source": "measured",
        "spin_method": "envelope",
        "smash_factor_status": SMASH_RADIAL_RATIO,
    }
    values.update(overrides)
    shot = Shot(**values)
    server_module._attach_club_arc(  # pylint: disable=protected-access
        shot,
        {
            "status": "chained_high",
            "club_path_deg": -2.0,
            "attack_angle_deg": -4.0,
            "path_confidence_tier": "medium",
            "attack_confidence_tier": "medium",
            "club_path_frame": "target_line",
            "head_positions": arc_positions(),
            "contact_t_s": 0.0,
            "horizontal_offset_deg": 0.0,
        },
    )
    shot.experimental_fused_club_path_deg = -2.0
    shot.experimental_fused_club_path_frame = "target_line"
    shot.experimental_fused_attack_angle_deg = -4.0
    shot.experimental_fused_status = "chained_high"
    shot.experimental_fused_attack_angle_confidence = "medium"
    shot.experimental_fused_club_path_confidence = "medium"
    server_module._attach_collision_model(shot)  # pylint: disable=protected-access
    shot.ball_speed_full = full_ball_speed()
    shot.ball_speed_full_mph = shot.ball_speed_full["value_mph"]
    shot.club_speed_full = evaluate_full_club_speed(
        radial_mph=None,
        head_position_m=None,
        direction=None,
        ops_position_m=None,
        direction_source=None,
        reason=server_module.CLUB_SPEED_NOT_FULL_REASON,
    )
    shot.smash_factor_full = evaluate_full_smash(shot.ball_speed_full, shot.club_speed_full)
    flight = compute_flight(shot, substitutes=False)
    shot.flight = flight.to_dict()
    if flight.as_measured is not None:
        shot.carry_spin_adjusted = flight.as_measured.carry_yards
        shot.carry_status = CARRY_CALCULATED
    else:
        shot.carry_status = CARRY_NOT_MEASURED
    return shot

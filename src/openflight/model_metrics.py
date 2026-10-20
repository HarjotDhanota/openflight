"""The shot models' metrics in the shot-metric catalogue (E1-E6).

``openflight.shot_metrics`` holds the catalogue and the metrics every unit
measures. This module adds, in their places in the catalogue, the metrics the
shot models produce: the full speeds (E3), the collision model's face, loft,
spin loft, face to path, D-plane tilt and gyro (E2), the arc's swing plane,
direction and low point (E1), every flight output (E4), and the metrics not
built yet (impact location, E5; dynamic lie, E6). It also gives carry its
flight-model builder. It is loaded through the ``openflight.shot_metrics``
entry point the first time the catalogue is read, and reads only the records
the models left on the shot.
"""

from __future__ import annotations

from typing import Any, Mapping

from openflight import shot_metrics as sm
from openflight.collision_model import GYRO_REASON

NO_COLLISION_MODEL = "collision model did not run on this shot"
NO_FULL_SPEED = "full speed not computed on this shot (not finalized)"

IMPACT_LOCATION_PENDING = (
    "pending E5: impact location is not built yet (needs the 6 mm / 8 mm lens and crop mode, "
    "Tier 0 impact timing and the W9 head model)"
)
DYNAMIC_LIE_PENDING = (
    "pending E6: dynamic lie is not built yet (the lower shaft line from the camera at impact, "
    "after the lens and crop mode are chosen)"
)

# Collision-model metric names (collision_model.evaluate) per metric id.
_COLLISION = {
    "face_angle": "face_angle",
    "face_to_path": "face_to_path",
    "dynamic_loft": "dynamic_loft",
    "spin_loft": "spin_loft",
    "d_plane_tilt": "d_plane_tilt",
    "gyro": "gyro",
}
# Club-delivery metric names (club_arc.club_delivery_payload) per metric id.
_ARC_DELIVERY = {
    "swing_plane": "swing_plane_deg",
    "swing_direction": "swing_direction_deg",
    "low_point_distance": "low_point_distance_mm",
    "low_point_height": "low_point_height_mm",
    "low_point_side": "low_point_side_mm",
}
_FLIGHT_HORIZONTAL = ("carry_side", "total_side", "curve")


def _full_speed(metric_id: str, entry: Any, value_key: str):
    entry = sm.as_mapping(entry)
    if not entry:
        return sm.build_record(metric_id, None, None, reason=NO_FULL_SPEED)
    return sm.producer_record(metric_id, entry, value_key=value_key)


def _collision(metric_id: str):
    def build(shot):
        entry = sm.collision_metric(shot, _COLLISION[metric_id])
        if not entry:
            reason = GYRO_REASON if metric_id == "gyro" else NO_COLLISION_MODEL
            return sm.build_record(metric_id, None, None, reason=reason)
        return sm.producer_record(metric_id, entry)

    return build


def _flight_entry(shot, block: str, name: str) -> Mapping[str, Any]:
    flight = sm.as_mapping(shot.get("flight"))
    return sm.as_mapping(sm.as_mapping(sm.as_mapping(flight.get(block)).get("metrics")).get(name))


def _no_flight_reason(shot) -> str:
    if sm.is_mock(shot):
        return "flight model does not run on mock shots"
    return "flight not computed on this shot (not finalized)"


def _flight(metric_id: str):
    def build(shot):
        entry = _flight_entry(shot, "as_measured", metric_id)
        normalized_entry = _flight_entry(shot, "normalized", metric_id)
        normalized = None
        if sm.finite_or_none(normalized_entry.get("value")) is not None:
            normalized = {
                "value": float(normalized_entry["value"]),
                "range": sm.value_range(normalized_entry.get("range")),
                "status": normalized_entry.get("status"),
            }
        frame = (
            sm.as_mapping(shot.get("flight")).get("frame")
            if metric_id in _FLIGHT_HORIZONTAL
            else None
        )
        if metric_id == "carry" and sm.finite_or_none(entry.get("value")) is None:
            value, status, source = sm.reported_carry(shot)
            if value is not None:
                return sm.build_record("carry", value, status, source=source)
        if not entry:
            return sm.build_record(metric_id, None, None, reason=_no_flight_reason(shot))
        return sm.build_record(
            metric_id,
            entry.get("value"),
            entry.get("status"),
            source=entry.get("source"),
            range_=entry.get("range"),
            reason=entry.get("reason"),
            frame=frame,
            normalized=normalized,
        )

    return build


def _arc_delivery(metric_id: str):
    def build(shot):
        metrics = sm.as_mapping(sm.as_mapping(shot.get("club_delivery")).get("metrics"))
        entry = sm.as_mapping(metrics.get(_ARC_DELIVERY[metric_id]))
        if not entry:
            return sm.build_record(metric_id, None, None, reason=sm.NO_CLUB_DELIVERY)
        return sm.producer_record(metric_id, entry)

    return build


def _pending(metric_id: str):
    def build(_shot):
        return sm.build_record(
            metric_id, None, None, reason=sm.metric_spec(metric_id).pending_reason
        )

    return build


def _full(metric_id: str, field: str, value_key: str):
    return lambda shot: _full_speed(metric_id, shot.get(field), value_key)


Spec = sm.MetricSpec
BALL, CLUB, FLIGHT = sm.BALL, sm.CLUB, sm.FLIGHT

sm.register_metrics(
    [
        (
            Spec("ball_speed_full", "Ball speed (full)", BALL, "mph"),
            _full("ball_speed_full", "ball_speed_full", "value_mph"),
        )
    ],
    after="ball_speed",
)
sm.register_metrics([(Spec("gyro", "Gyro", BALL, "deg"), _collision("gyro"))], after="spin_axis")
sm.register_metrics(
    [
        (
            Spec("smash_factor_full", "Smash (full)", BALL, ""),
            _full("smash_factor_full", "smash_factor_full", "value"),
        )
    ],
    after="smash_factor",
)
sm.register_metrics(
    [
        (Spec("carry_side", "Carry side", FLIGHT, "yd"), _flight("carry_side")),
        (Spec("total", "Total", FLIGHT, "yd"), _flight("total")),
        (Spec("total_side", "Total side", FLIGHT, "yd"), _flight("total_side")),
        (Spec("apex", "Apex", FLIGHT, "yd"), _flight("apex")),
        (Spec("landing_angle", "Landing angle", FLIGHT, "deg"), _flight("landing_angle")),
        (Spec("hang_time", "Hang time", FLIGHT, "s"), _flight("hang_time")),
        (Spec("curve", "Curve", FLIGHT, "yd"), _flight("curve")),
    ],
    after="carry",
)
sm.register_metrics(
    [
        (
            Spec("club_speed_full", "Club speed (full)", CLUB, "mph"),
            _full("club_speed_full", "club_speed_full", "value_mph"),
        )
    ],
    after="club_speed",
)
sm.register_metrics(
    [
        (Spec("face_angle", "Face angle", CLUB, "deg"), _collision("face_angle")),
        (Spec("face_to_path", "Face to path", CLUB, "deg"), _collision("face_to_path")),
        (Spec("dynamic_loft", "Dynamic loft", CLUB, "deg"), _collision("dynamic_loft")),
        (Spec("spin_loft", "Spin loft", CLUB, "deg"), _collision("spin_loft")),
        (Spec("d_plane_tilt", "D-plane tilt", CLUB, "deg"), _collision("d_plane_tilt")),
        (Spec("swing_plane", "Swing plane", CLUB, "deg"), _arc_delivery("swing_plane")),
        (Spec("swing_direction", "Swing direction", CLUB, "deg"), _arc_delivery("swing_direction")),
        (
            Spec("low_point_distance", "Low point distance", CLUB, "mm"),
            _arc_delivery("low_point_distance"),
        ),
        (
            Spec("low_point_height", "Low point height", CLUB, "mm"),
            _arc_delivery("low_point_height"),
        ),
        (Spec("low_point_side", "Low point side", CLUB, "mm"), _arc_delivery("low_point_side")),
        (
            Spec("impact_offset", "Impact offset", CLUB, "mm", "E5", IMPACT_LOCATION_PENDING),
            _pending("impact_offset"),
        ),
        (
            Spec("impact_height", "Impact height", CLUB, "mm", "E5", IMPACT_LOCATION_PENDING),
            _pending("impact_height"),
        ),
        (
            Spec("dynamic_lie", "Dynamic lie", CLUB, "deg", "E6", DYNAMIC_LIE_PENDING),
            _pending("dynamic_lie"),
        ),
    ],
    after="attack_angle",
)
# carry reads the flight model's as-measured carry first, then the committed one
sm.replace_builder("carry", _flight("carry"))

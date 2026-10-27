"""The shot models' metrics in the shot-metric catalogue.

Loaded through the ``openflight.shot_metrics`` entry point the first time the catalogue is
read; it reads only the records the models left on the shot.
"""

from __future__ import annotations

from typing import Any, Mapping

from openflight import shot_metrics as sm
from openflight.collision_model import GYRO_REASON

NO_COLLISION_MODEL = "collision model did not run on this shot"
NO_FULL_SPEED = "full speed not computed on this shot (not finalized)"

IMPACT_SOURCE = "camera head outline and resting ball, read after the shot (experimental)"
IMPACT_NOTE = "not validated yet: compare it with the face spray marks"
NO_IMPACT_READING = "needs this shot's camera clip; impact location is read after the shot"
NO_DYNAMIC_LIE = "needs this shot's camera clip with a visible lower shaft near impact"

# Collision-model metric names (collision_model.evaluate) per metric id.
_COLLISION = {
    "face_angle": "face_angle",
    "face_to_path": "face_to_path",
    "dynamic_loft": "dynamic_loft",
    "spin_loft": "spin_loft",
    "spin_index": "spin_index",
    "smash_index": "smash_index",
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
        record = sm.producer_record(metric_id, entry)
        if record["value"] is not None:
            record["note"] = sm.centre_strike_note(shot, record.get("note"), entry.get("reason"))
        return record

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


def _impact(metric_id: str):
    """Heel-toe (+ toe) or height (+ up) from the shot's live impact reading, experimental."""
    prefix = "" if metric_id == "impact_offset" else "height_"

    def build(shot):
        entry = sm.as_mapping(shot.get("impact_location"))
        if not entry:
            return sm.build_record(metric_id, None, None, reason=NO_IMPACT_READING)
        if entry.get(f"{prefix}status") != "ok":
            reason = entry.get(f"{prefix}reason") or entry.get("reason")
            return sm.build_record(metric_id, None, None, reason=reason or NO_IMPACT_READING)
        value = sm.finite_or_none(entry.get(f"{metric_id}_mm"))
        sigma = sm.finite_or_none(entry.get(f"{metric_id}_sigma_mm"))
        return sm.build_record(
            metric_id,
            entry.get(f"{metric_id}_mm"),
            sm.EXPERIMENTAL,
            source=IMPACT_SOURCE,
            range_=[value - sigma, value + sigma] if value is not None and sigma else None,
            note=IMPACT_NOTE,
        )

    return build


def _dynamic_lie(shot):
    reading = sm.as_mapping(shot.get("impact_location"))
    entry = sm.as_mapping(reading.get("dynamic_lie"))
    value = sm.finite_or_none(entry.get("value"))
    if entry.get("status") != sm.MODEL_GEOMETRY or value is None or not 0 < value <= 90:
        return sm.build_record(
            "dynamic_lie",
            None,
            None,
            reason=entry.get("reason") or reading.get("reason") or NO_DYNAMIC_LIE,
        )
    return sm.producer_record("dynamic_lie", entry)


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
        (Spec("spin_index", "Spin index", CLUB, "%"), _collision("spin_index")),
        (Spec("smash_index", "Smash index", CLUB, "%"), _collision("smash_index")),
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
        (Spec("impact_offset", "Impact offset", CLUB, "mm"), _impact("impact_offset")),
        (Spec("impact_height", "Impact height", CLUB, "mm"), _impact("impact_height")),
        (
            Spec("dynamic_lie", "Dynamic lie", CLUB, "deg"),
            _dynamic_lie,
        ),
    ],
    after="attack_angle",
)
# carry reads the flight model's as-measured carry first, then the committed one
sm.replace_builder("carry", _flight("carry"))

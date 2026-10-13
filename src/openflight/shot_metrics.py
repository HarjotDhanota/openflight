"""Every golf metric of one shot, each with its label, source, range, reason and frame.

TrackMan's metrics plus ours, in one fixed list (``METRICS``). ``from_record``
turns one shot record (``Shot.to_dict()`` or a logged ``shot_detected`` entry)
into one record per metric, so the kiosk, the tester page, the session log, the
session review and the pipeline check all read the same values under the same
labels. It reads the producers' own outputs (E1 ``club_delivery``, E2
``collision_model``, E3 full speeds, E4 ``flight``) and the shot's measured
fields; it never computes a metric itself.

Every record has the same keys:

- ``value``: a finite number, or None;
- ``unit``;
- ``status``: one of ``LABELS``. ``measured`` (a sensor saw it), ``calculated``
  (a named model on measured or calculated inputs, D38), ``model_geometry``
  (rests on assumed swing geometry, D39), ``estimated`` (an OPS-only install's
  labelled stand-in), ``not_measured`` (no value; ``reason`` says why, D17);
- ``source``: the sensor or model that produced the value;
- ``range``: ``[low, high]`` when the producer states one, else None;
- ``reason``: why a value is not measured (always set then), else None;
- ``frame``: the zero of a horizontal metric (``horizontal_frame``), else None;
- ``note``: a label the value carries (an uncalibrated azimuth, mixed frames);
- ``normalized``: a flight metric's normalized-air value and range, else None.

A metric that is not built yet (impact location, E5; dynamic lie, E6) is not
measured, with the item that builds it as its reason.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional

from .collision_model import GYRO_REASON, is_calculated_source, is_model_geometry_source

SCHEMA = "openflight.shot_metrics"
VERSION = 1

MEASURED = "measured"
CALCULATED = "calculated"
MODEL_GEOMETRY = "model_geometry"
ESTIMATED = "estimated"
NOT_MEASURED = "not_measured"
LABELS = (MEASURED, CALCULATED, MODEL_GEOMETRY, ESTIMATED, NOT_MEASURED)

BALL, CLUB, FLIGHT = "ball", "club", "flight"

OPS_RADIAL_SOURCE = "OPS243 radial (line of sight)"
MOCK_SOURCE = "mock shot (simulated)"
SMASH_RADIAL_SOURCE = "radial ratio: OPS radial ball speed / OPS radial club speed (D21)"
SMASH_SOURCE = "ball speed / club speed (D37)"
TABLE_CARRY_SOURCE = "ball-speed carry table (OPS-only estimate)"
COMMITTED_CARRY_SOURCE = "the kiosk's committed carry (no flight record on this shot)"
NO_COLLISION_MODEL = "collision model did not run on this shot"
NO_CLUB_DELIVERY = "no camera club delivery on this shot"
NO_FULL_SPEED = "full speed not computed on this shot (not finalized)"
# Where the club path / attack angle the collision model used came from
# (server.displayed_club_path, displayed_attack_angle).
_PATH_SOURCES = {
    "canonical": "radar club track",
    "camera_fused_chained": "camera + IWR chained delivery",
    "camera_fused_ops": "camera + OPS chained delivery",
    "iwr": "IWR club track",
}


@dataclass(frozen=True)
class MetricSpec:
    """One metric: its id, display name, group, unit and, if unbuilt, the item that builds it."""

    id: str
    label: str
    group: str
    unit: str
    pending: Optional[str] = None
    pending_reason: Optional[str] = None


IMPACT_LOCATION_PENDING = (
    "pending E5: impact location is not built yet (needs the 6 mm / 8 mm lens and crop mode, "
    "Tier 0 impact timing and the W9 head model)"
)
DYNAMIC_LIE_PENDING = (
    "pending E6: dynamic lie is not built yet (the lower shaft line from the camera at impact, "
    "after the lens and crop mode are chosen)"
)

METRICS: tuple[MetricSpec, ...] = (
    MetricSpec("ball_speed", "Ball speed (radial)", BALL, "mph"),
    MetricSpec("ball_speed_full", "Ball speed (full)", BALL, "mph"),
    MetricSpec("launch_vertical", "Vertical launch", BALL, "deg"),
    MetricSpec("launch_horizontal", "Horizontal launch", BALL, "deg"),
    MetricSpec("spin_rate", "Spin rate", BALL, "rpm"),
    MetricSpec("spin_axis", "Spin axis", BALL, "deg"),
    MetricSpec("gyro", "Gyro", BALL, "deg"),
    MetricSpec("smash_factor", "Smash (radial)", BALL, ""),
    MetricSpec("smash_factor_full", "Smash (full)", BALL, ""),
    MetricSpec("ball_range", "Ball distance", BALL, "m"),
    MetricSpec("carry", "Carry", FLIGHT, "yd"),
    MetricSpec("carry_side", "Carry side", FLIGHT, "yd"),
    MetricSpec("total", "Total", FLIGHT, "yd"),
    MetricSpec("total_side", "Total side", FLIGHT, "yd"),
    MetricSpec("apex", "Apex", FLIGHT, "yd"),
    MetricSpec("landing_angle", "Landing angle", FLIGHT, "deg"),
    MetricSpec("hang_time", "Hang time", FLIGHT, "s"),
    MetricSpec("curve", "Curve", FLIGHT, "yd"),
    MetricSpec("club_speed", "Club speed (radial)", CLUB, "mph"),
    MetricSpec("club_speed_full", "Club speed (full)", CLUB, "mph"),
    MetricSpec("club_path", "Club path", CLUB, "deg"),
    MetricSpec("attack_angle", "Attack angle", CLUB, "deg"),
    MetricSpec("face_angle", "Face angle", CLUB, "deg"),
    MetricSpec("face_to_path", "Face to path", CLUB, "deg"),
    MetricSpec("dynamic_loft", "Dynamic loft", CLUB, "deg"),
    MetricSpec("spin_loft", "Spin loft", CLUB, "deg"),
    MetricSpec("d_plane_tilt", "D-plane tilt", CLUB, "deg"),
    MetricSpec("swing_plane", "Swing plane", CLUB, "deg"),
    MetricSpec("swing_direction", "Swing direction", CLUB, "deg"),
    MetricSpec("low_point_distance", "Low point distance", CLUB, "mm"),
    MetricSpec("low_point_height", "Low point height", CLUB, "mm"),
    MetricSpec("low_point_side", "Low point side", CLUB, "mm"),
    MetricSpec("impact_offset", "Impact offset", CLUB, "mm", "E5", IMPACT_LOCATION_PENDING),
    MetricSpec("impact_height", "Impact height", CLUB, "mm", "E5", IMPACT_LOCATION_PENDING),
    MetricSpec("dynamic_lie", "Dynamic lie", CLUB, "deg", "E6", DYNAMIC_LIE_PENDING),
)
METRIC_IDS = tuple(spec.id for spec in METRICS)
SPECS = {spec.id: spec for spec in METRICS}

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
_DELIVERY = {
    "club_path": "club_path_deg",
    "attack_angle": "attack_angle_deg",
    "swing_plane": "swing_plane_deg",
    "swing_direction": "swing_direction_deg",
    "low_point_distance": "low_point_distance_mm",
    "low_point_height": "low_point_height_mm",
    "low_point_side": "low_point_side_mm",
}
_FLIGHT_HORIZONTAL = ("carry_side", "total_side", "curve")


def catalog() -> dict[str, Any]:
    """The metric list and label vocabulary, for pages that render records by id."""
    return {
        "schema": f"{SCHEMA}.catalog",
        "version": VERSION,
        "labels": list(LABELS),
        "metrics": [
            {
                "id": spec.id,
                "label": spec.label,
                "group": spec.group,
                "unit": spec.unit,
                "pending": spec.pending,
            }
            for spec in METRICS
        ],
    }


# ---------------------------------------------------------------- helpers


def _finite(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _range(value: Any) -> Optional[list[float]]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    low, high = _finite(value[0]), _finite(value[1])
    return [low, high] if low is not None and high is not None else None


def _record(  # pylint: disable=too-many-arguments
    metric_id: str,
    value: Any,
    status: Optional[str],
    *,
    source: Optional[str] = None,
    range_: Any = None,
    reason: Optional[str] = None,
    frame: Optional[str] = None,
    note: Optional[str] = None,
    normalized: Optional[dict] = None,
) -> dict[str, Any]:
    """One record; a missing or non-finite value is not measured, with a reason (D17)."""
    spec = SPECS[metric_id]
    number = _finite(value)
    if number is None:
        if value is not None:
            reason = f"{spec.label.lower()} not finite"
        return {
            "value": None,
            "unit": spec.unit,
            "status": NOT_MEASURED,
            "source": source,
            "range": None,
            "reason": reason or f"{spec.label.lower()} not measured",
            "frame": None,
            "note": note,
            "normalized": None,
        }
    return {
        "value": number,
        "unit": spec.unit,
        "status": status or MEASURED,
        "source": source,
        "range": _range(range_),
        "reason": None,
        "frame": frame,
        "note": note,
        "normalized": normalized,
    }


def _from_producer(metric_id: str, entry: Mapping[str, Any], *, value_key: str = "value"):
    """A producer's own record (value, status, source, range, reason, frame, note)."""
    range_ = entry.get("range") if "range" in entry else entry.get("range_mph")
    return _record(
        metric_id,
        entry.get(value_key),
        entry.get("status"),
        source=entry.get("source"),
        range_=range_,
        reason=entry.get("reason"),
        frame=entry.get("frame"),
        note=entry.get("note"),
    )


def _mock(shot: Mapping[str, Any]) -> bool:
    return shot.get("mode") == "mock"


def _sensor_source(shot: Mapping[str, Any], source: Optional[str]) -> Optional[str]:
    return MOCK_SOURCE if _mock(shot) else source


# ---------------------------------------------------------------- ball


def _ball_speed(shot):
    return _record(
        "ball_speed",
        shot.get("ball_speed_mph"),
        MEASURED,
        source=_sensor_source(shot, OPS_RADIAL_SOURCE),
        reason="no OPS243 ball speed",
    )


def _full_speed(metric_id: str, entry: Any, value_key: str):
    entry = _mapping(entry)
    if not entry:
        return _record(metric_id, None, None, reason=NO_FULL_SPEED)
    return _from_producer(metric_id, entry, value_key=value_key)


def _launch_status(source: Optional[str]) -> str:
    return ESTIMATED if source == "estimated" else MEASURED


def _launch_vertical(shot):
    source = shot.get("launch_angle_vertical_source") or shot.get("angle_source")
    return _record(
        "launch_vertical",
        shot.get("launch_angle_vertical"),
        _launch_status(source),
        source=source,
        reason="vertical launch not measured on this shot",
    )


def _launch_horizontal(shot):
    source = shot.get("launch_angle_horizontal_source") or shot.get("angle_source")
    uncalibrated = shot.get("launch_angle_horizontal_status") == "azimuth_uncalibrated"
    return _record(
        "launch_horizontal",
        shot.get("launch_angle_horizontal"),
        _launch_status(source),
        source=source,
        reason="horizontal launch not measured on this shot",
        frame=shot.get("launch_angle_horizontal_frame"),
        note="azimuth uncalibrated: the IWR start direction has its own zero"
        if uncalibrated
        else None,
    )


def _collision_metric(shot, name: str) -> Mapping[str, Any]:
    return _mapping(_mapping(_mapping(shot.get("collision_model")).get("metrics")).get(name))


def _spin_rate(shot):
    source = shot.get("spin_source")
    # any value label the producer gave (estimated too); an unlabelled spin is measured
    status = source if source in LABELS and source != NOT_MEASURED else MEASURED
    collision = _collision_metric(shot, "spin_rate")
    reason = (
        shot.get("spin_rejection_reason")
        or (f"collision model: {collision['reason']}" if collision.get("reason") else None)
        or "spin not measured on this shot"
    )
    quality = shot.get("spin_quality")
    return _record(
        "spin_rate",
        shot.get("spin_rpm"),
        status,
        source=_sensor_source(shot, shot.get("spin_method") or source),
        reason=reason,
        note=f"confidence {quality}" if status == MEASURED and quality else None,
    )


def _spin_axis(shot):
    source = shot.get("spin_axis_source")
    collision_status = _collision_metric(shot, "spin_axis").get("status")
    if is_model_geometry_source(source):
        status = MODEL_GEOMETRY
    elif source and source.startswith(ESTIMATED):
        # a weak horizontal launch under the axis (agent/v-formulas): labelled, not withheld
        status = ESTIMATED
    elif is_calculated_source(source):
        # the collision model's own label when it weakened it (estimated, model geometry)
        status = (
            collision_status
            if collision_status in (CALCULATED, MODEL_GEOMETRY, ESTIMATED)
            else CALCULATED
        )
    elif _mock(shot):
        status, source = MEASURED, MOCK_SOURCE
    else:
        # an unlabelled axis predates E2: the "launch minus path" proxy
        status, source = ESTIMATED, source or "unlabelled spin axis (before E2)"
    collision = _collision_metric(shot, "spin_axis")
    return _record(
        "spin_axis",
        shot.get("spin_axis_deg"),
        status,
        source=source,
        reason=collision.get("reason") or "spin axis not measured on this shot",
        frame=shot.get("spin_axis_frame"),
    )


def _collision(metric_id: str):
    def build(shot):
        entry = _collision_metric(shot, _COLLISION[metric_id])
        if not entry:
            reason = GYRO_REASON if metric_id == "gyro" else NO_COLLISION_MODEL
            return _record(metric_id, None, None, reason=reason)
        return _from_producer(metric_id, entry)

    return build


def _smash(shot):
    radial = shot.get("smash_factor_status") == "radial_ratio"
    club = _finite(shot.get("club_speed_mph"))
    return _record(
        "smash_factor",
        shot.get("smash_factor"),
        CALCULATED,
        source=SMASH_RADIAL_SOURCE if radial else SMASH_SOURCE,
        reason="no club speed: no ratio" if club is None or club <= 0 else "smash undefined",
    )


# ---------------------------------------------------------------- flight


def _flight_entry(shot, block: str, name: str) -> Mapping[str, Any]:
    flight = _mapping(shot.get("flight"))
    return _mapping(_mapping(_mapping(flight.get(block)).get("metrics")).get(name))


def _no_flight_reason(shot) -> str:
    if _mock(shot):
        return "flight model does not run on mock shots"
    return "flight not computed on this shot (not finalized)"


def _reported_carry(shot) -> tuple[Optional[float], Optional[str], Optional[str]]:
    """The carry the kiosk reports (launch_monitor.Shot.reported_carry_yards), labelled."""
    status = shot.get("carry_status")
    if status == NOT_MEASURED:
        return None, None, None
    committed = _finite(shot.get("carry_spin_adjusted"))
    if committed is not None:
        # a simulated carry is calculated, never measured (D38): "measured" is an older
        # log's name for it; an unlabelled one (a replay, an older log) is an estimate
        label = {None: ESTIMATED, MEASURED: CALCULATED}.get(status, status)
        return committed, label, COMMITTED_CARRY_SOURCE
    if status in (None, ESTIMATED):
        table = _finite(shot.get("estimated_carry_yards"))
        if table is not None:
            return table, ESTIMATED, TABLE_CARRY_SOURCE
    return None, None, None


def _flight(metric_id: str):
    def build(shot):
        entry = _flight_entry(shot, "as_measured", metric_id)
        normalized_entry = _flight_entry(shot, "normalized", metric_id)
        normalized = None
        if _finite(normalized_entry.get("value")) is not None:
            normalized = {
                "value": float(normalized_entry["value"]),
                "range": _range(normalized_entry.get("range")),
                "status": normalized_entry.get("status"),
            }
        frame = (
            _mapping(shot.get("flight")).get("frame") if metric_id in _FLIGHT_HORIZONTAL else None
        )
        if metric_id == "carry" and _finite(entry.get("value")) is None:
            value, status, source = _reported_carry(shot)
            if value is not None:
                return _record("carry", value, status, source=source)
        if not entry:
            return _record(metric_id, None, None, reason=_no_flight_reason(shot))
        return _record(
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


# ---------------------------------------------------------------- club


def _club_speed(shot):
    return _record(
        "club_speed",
        shot.get("club_speed_mph"),
        MEASURED,
        source=_sensor_source(shot, OPS_RADIAL_SOURCE),
        reason="no OPS243 club speed reading",
    )


def _delivery_entry(shot, metric_id: str) -> Mapping[str, Any]:
    metrics = _mapping(_mapping(shot.get("club_delivery")).get("metrics"))
    return _mapping(metrics.get(_DELIVERY[metric_id]))


_LEGACY_DELIVERY = {"club_path": "club_path_deg", "attack_angle": "club_angle_deg"}
_COLLISION_INPUT = {"club_path": "club_path", "attack_angle": "attack_angle"}


def _path_or_attack(metric_id: str):
    """The path / attack the shot uses: the collision model's input, else the arc's."""

    def build(shot):
        delivery = _delivery_entry(shot, metric_id)
        inputs = _mapping(_mapping(shot.get("collision_model")).get("inputs"))
        used = _mapping(inputs.get(_COLLISION_INPUT[metric_id]))
        if _finite(used.get("value")) is not None:
            source = used.get("source")
            same_as_arc = delivery and delivery.get("source") == source
            return _record(
                metric_id,
                used["value"],
                used.get("label"),
                source=_PATH_SOURCES.get(source, source),
                range_=delivery.get("range") if same_as_arc else None,
                frame=used.get("frame") if metric_id == "club_path" else None,
            )
        if _finite(delivery.get("value")) is not None:
            return _from_producer(metric_id, delivery)
        legacy = shot.get(_LEGACY_DELIVERY[metric_id])
        if _finite(legacy) is not None:
            return _record(
                metric_id,
                legacy,
                MEASURED,
                source=_sensor_source(shot, "radar club track"),
                frame=shot.get("club_path_frame") if metric_id == "club_path" else None,
            )
        reasons = [text for text in (used.get("reason"), delivery.get("reason")) if text]
        if not shot.get("club_delivery"):
            reasons.append(NO_CLUB_DELIVERY)
        return _record(metric_id, None, None, reason="; ".join(dict.fromkeys(reasons)) or None)

    return build


def _delivery(metric_id: str):
    def build(shot):
        entry = _delivery_entry(shot, metric_id)
        if not entry:
            return _record(metric_id, None, None, reason=NO_CLUB_DELIVERY)
        return _from_producer(metric_id, entry)

    return build


def _pending(metric_id: str):
    def build(_shot):
        return _record(metric_id, None, None, reason=SPECS[metric_id].pending_reason)

    return build


# ---------------------------------------------------------------- ball range

BALL_RANGE_SOURCES = {
    "iwr_post_impact": "IWR post-impact launch point (this shot's own capture)",
    "camera_prior": "camera prior: the resting ball's size, coarse",
    "configured_tee": "range configured for the run (--iwr6843-tee-m)",
}
BALL_RANGE_UNVALIDATED = "per-swing IWR range: not validated against a tape yet"
CAMERA_PRIOR_NOTE = "rests on the camera prior's ball distance (coarse, estimated)"
# What does not use the ball's range: the OPS243's own radial speeds and spin.
RANGE_INDEPENDENT = frozenset({"ball_speed", "club_speed", "smash_factor", "spin_rate"})


def _ball_range(shot):
    source = shot.get("ball_range_source")
    value = shot.get("ball_range_m")
    uncertainty = _finite(shot.get("ball_range_uncertainty_m"))
    number = _finite(value)
    span = (
        [number - uncertainty, number + uncertainty]
        if number is not None and uncertainty is not None
        else None
    )
    status = {
        "iwr_post_impact": MEASURED,
        "camera_prior": ESTIMATED,
        "configured_tee": MEASURED,
    }.get(source, ESTIMATED)
    return _record(
        "ball_range",
        value,
        status,
        source=BALL_RANGE_SOURCES.get(source, source),
        range_=span,
        reason=shot.get("ball_range_reason") or "the ball's distance was not measured",
        note=BALL_RANGE_UNVALIDATED
        if source == "iwr_post_impact"
        else CAMERA_PRIOR_NOTE
        if source == "camera_prior"
        else None,
    )


def _with_prior_label(metrics: dict[str, dict[str, Any]], shot) -> dict[str, dict[str, Any]]:
    """Every range-dependent metric of a shot that rested on the camera prior is estimated.

    The prior is a coarse size estimate; a metric built on it carries that weaker label
    and says why (13 Oct, B3).
    """
    if shot.get("ball_range_source") != "camera_prior":
        return metrics
    for metric_id, record in metrics.items():
        if metric_id in RANGE_INDEPENDENT or metric_id == "ball_range":
            continue
        if record.get("status") in (MEASURED, CALCULATED, MODEL_GEOMETRY):
            note = record.get("note")
            record["status"] = ESTIMATED
            record["note"] = f"{note}; {CAMERA_PRIOR_NOTE}" if note else CAMERA_PRIOR_NOTE
    return metrics


_BUILDERS: dict[str, Callable[[Mapping[str, Any]], dict]] = {
    "ball_speed": _ball_speed,
    "ball_range": _ball_range,
    "ball_speed_full": lambda shot: _full_speed(
        "ball_speed_full", shot.get("ball_speed_full"), "value_mph"
    ),
    "launch_vertical": _launch_vertical,
    "launch_horizontal": _launch_horizontal,
    "spin_rate": _spin_rate,
    "spin_axis": _spin_axis,
    "gyro": _collision("gyro"),
    "smash_factor": _smash,
    "smash_factor_full": lambda shot: _full_speed(
        "smash_factor_full", shot.get("smash_factor_full"), "value"
    ),
    **{name: _flight(name) for name in ("carry", "carry_side", "total", "total_side")},
    **{name: _flight(name) for name in ("apex", "landing_angle", "hang_time", "curve")},
    "club_speed": _club_speed,
    "club_speed_full": lambda shot: _full_speed(
        "club_speed_full", shot.get("club_speed_full"), "value_mph"
    ),
    "club_path": _path_or_attack("club_path"),
    "attack_angle": _path_or_attack("attack_angle"),
    **{name: _collision(name) for name in ("face_angle", "face_to_path", "dynamic_loft")},
    **{name: _collision(name) for name in ("spin_loft", "d_plane_tilt")},
    **{name: _delivery(name) for name in ("swing_plane", "swing_direction")},
    **{name: _delivery(name) for name in ("low_point_distance", "low_point_height")},
    "low_point_side": _delivery("low_point_side"),
    **{name: _pending(name) for name in ("impact_offset", "impact_height", "dynamic_lie")},
}
assert tuple(sorted(_BUILDERS)) == tuple(sorted(METRIC_IDS)), "every metric needs a builder"


def from_record(shot: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Every metric of one shot record, in catalog order, built from its fields."""
    shot = _mapping(shot)
    return _with_prior_label({spec.id: _BUILDERS[spec.id](shot) for spec in METRICS}, shot)


def records(shot: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """The shot's logged metric records when it carries them, else rebuilt (older logs).

    A logged record missing a metric (a log from before that metric existed) gets
    the rebuilt record for it.
    """
    shot = _mapping(shot)
    logged = _mapping(shot.get("metrics"))
    rebuilt = None
    result = {}
    for metric_id in METRIC_IDS:
        entry = logged.get(metric_id)
        if isinstance(entry, Mapping):
            result[metric_id] = dict(entry)
            continue
        rebuilt = rebuilt if rebuilt is not None else from_record(shot)
        result[metric_id] = rebuilt[metric_id]
    return result


if __name__ == "__main__":  # pragma: no cover - regenerates ui/src/data/metricCatalog.json
    import json
    import sys

    sys.stdout.write(json.dumps(catalog(), indent=2) + "\n")

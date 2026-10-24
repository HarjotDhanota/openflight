"""Every golf metric of one shot as a labelled record, in one ordered catalogue.

``from_record`` turns a shot record into one record per metric (value, unit, status in
``LABELS``, source, range, reason, frame, note, normalized); it never computes a metric.
Model modules under the ``openflight.shot_metrics`` entry point add their own metrics.
"""

from __future__ import annotations

import importlib.metadata
import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Sequence

from .numeric import finite_float as _finite
from .source_labels import is_calculated_source, is_measured_source, is_model_geometry_source

logger = logging.getLogger(__name__)

SCHEMA = "openflight.shot_metrics"
VERSION = 1

MEASURED = "measured"
CALCULATED = "calculated"
MODEL_GEOMETRY = "model_geometry"
ESTIMATED = "estimated"
EXPERIMENTAL = "experimental"
NOT_MEASURED = "not_measured"
LABELS = (MEASURED, CALCULATED, MODEL_GEOMETRY, ESTIMATED, EXPERIMENTAL, NOT_MEASURED)

BALL, CLUB, FLIGHT = "ball", "club", "flight"

OPS_RADIAL_SOURCE = "OPS243 radial (line of sight)"
MOCK_SOURCE = "mock shot (simulated)"
SMASH_RADIAL_SOURCE = "radial ratio: OPS radial ball speed / OPS radial club speed (D21)"
SMASH_SOURCE = "ball speed / club speed (D37)"
TABLE_CARRY_SOURCE = "ball-speed carry table (OPS-only estimate)"
COMMITTED_CARRY_SOURCE = "the kiosk's committed carry (no flight record on this shot)"
NO_CLUB_DELIVERY = "no camera club delivery on this shot"
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


_CORE_METRICS: tuple[MetricSpec, ...] = (
    MetricSpec("ball_speed", "Ball speed (radial)", BALL, "mph"),
    MetricSpec("launch_vertical", "Vertical launch", BALL, "deg"),
    MetricSpec("launch_horizontal", "Horizontal launch", BALL, "deg"),
    MetricSpec("spin_rate", "Spin rate", BALL, "rpm"),
    MetricSpec("spin_axis", "Spin axis", BALL, "deg"),
    MetricSpec("smash_factor", "Smash (radial)", BALL, ""),
    MetricSpec("ball_range", "Ball distance", BALL, "m"),
    MetricSpec("carry", "Carry", FLIGHT, "yd"),
    MetricSpec("club_speed", "Club speed (radial)", CLUB, "mph"),
    MetricSpec("club_path", "Club path", CLUB, "deg"),
    MetricSpec("attack_angle", "Attack angle", CLUB, "deg"),
)
ENTRY_POINT_GROUP = "openflight.shot_metrics"

_DELIVERY = {
    "club_path": "club_path_deg",
    "attack_angle": "attack_angle_deg",
}


def catalog() -> dict[str, Any]:
    """The metric list and label vocabulary, for pages that render records by id."""
    _load_metric_modules()
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
            for spec in _metrics
        ],
    }


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
    """One record; a missing or non-finite value is not measured, with a reason."""
    spec = _specs[metric_id]
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


def _ball_speed(shot):
    return _record(
        "ball_speed",
        shot.get("ball_speed_mph"),
        MEASURED,
        source=_sensor_source(shot, OPS_RADIAL_SOURCE),
        reason="no OPS243 ball speed",
    )


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
    status = source if source in LABELS and source != NOT_MEASURED else MEASURED
    collision = _collision_metric(shot, "spin_rate")
    reason = (
        shot.get("spin_rejection_reason")
        or (f"collision model: {collision['reason']}" if collision.get("reason") else None)
        or "spin not measured on this shot"
    )
    quality = shot.get("spin_quality")
    note = f"confidence {quality}" if status == MEASURED and quality else None
    model_spin = _finite(shot.get("spin_rpm_collision"))
    if (
        status != MEASURED
        and model_spin is not None
        and model_spin == _finite(shot.get("spin_rpm"))
    ):
        note = collision.get("note")
    return _record(
        "spin_rate",
        shot.get("spin_rpm"),
        status,
        source=_sensor_source(shot, shot.get("spin_method") or source),
        reason=reason,
        note=note,
    )


def _spin_axis(shot):
    source = shot.get("spin_axis_source")
    collision_status = _collision_metric(shot, "spin_axis").get("status")
    if is_model_geometry_source(source):
        status = MODEL_GEOMETRY
    elif is_measured_source(source):
        status = MEASURED
    elif source and source.startswith(ESTIMATED):
        status = ESTIMATED
    elif is_calculated_source(source):
        status = (
            collision_status
            if collision_status in (CALCULATED, MODEL_GEOMETRY, ESTIMATED)
            else CALCULATED
        )
    elif _mock(shot):
        status, source = MEASURED, MOCK_SOURCE
    else:
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


def _reported_carry(shot) -> tuple[Optional[float], Optional[str], Optional[str]]:
    """The carry the kiosk reports (launch_monitor.Shot.reported_carry_yards), labelled."""
    status = shot.get("carry_status")
    if status == NOT_MEASURED:
        return None, None, None
    committed = _finite(shot.get("carry_spin_adjusted"))
    if committed is not None:
        # a simulated carry is calculated, never measured; unlabelled ones are estimates
        label = {None: ESTIMATED, MEASURED: CALCULATED}.get(status, status)
        return committed, label, COMMITTED_CARRY_SOURCE
    if status in (None, ESTIMATED):
        table = _finite(shot.get("estimated_carry_yards"))
        if table is not None:
            return table, ESTIMATED, TABLE_CARRY_SOURCE
    return None, None, None


def _carry(shot):
    """The carry the kiosk reports (a flight model's builder replaces this one)."""
    value, status, source = _reported_carry(shot)
    return _record("carry", value, status, source=source)


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


BALL_RANGE_SOURCES = {
    "iwr_post_impact": "IWR post-impact launch point (this shot's own capture)",
    "camera_prior": "camera prior: the resting ball's size, coarse",
    "configured_tee": "a fixed range set in code (not used by the kiosk)",
}
BALL_RANGE_UNVALIDATED = "per-swing IWR range: not validated against a tape yet"
CAMERA_PRIOR_NOTE = "rests on the camera prior's ball distance (coarse, estimated)"
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
    """Every range-dependent metric of a shot that rested on the camera prior is estimated."""
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


Builder = Callable[[Mapping[str, Any]], dict]

_lock = threading.RLock()
_metrics: list[MetricSpec] = list(_CORE_METRICS)
_specs: dict[str, MetricSpec] = {spec.id: spec for spec in _CORE_METRICS}
_builders: dict[str, Builder] = {
    "ball_speed": _ball_speed,
    "launch_vertical": _launch_vertical,
    "launch_horizontal": _launch_horizontal,
    "spin_rate": _spin_rate,
    "spin_axis": _spin_axis,
    "smash_factor": _smash,
    "ball_range": _ball_range,
    "carry": _carry,
    "club_speed": _club_speed,
    "club_path": _path_or_attack("club_path"),
    "attack_angle": _path_or_attack("attack_angle"),
}
_modules_loaded = False


def register_metrics(metrics: Sequence[tuple[MetricSpec, Builder]], *, after: str) -> None:
    """Add a model's metrics to the catalogue, in order, after ``after``; existing ids are kept."""
    with _lock:
        position = [spec.id for spec in _metrics].index(after) + 1
        for spec, builder in metrics:
            if spec.id in _specs:
                continue
            _metrics.insert(position, spec)
            _specs[spec.id] = spec
            _builders[spec.id] = builder
            position += 1


def replace_builder(metric_id: str, builder: Builder) -> None:
    """Give a catalogue metric the builder a model module brings (carry, say)."""
    with _lock:
        if metric_id not in _specs:
            raise KeyError(metric_id)
        _builders[metric_id] = builder


def _load_metric_modules() -> None:
    """Import every model module declared under ``ENTRY_POINT_GROUP`` once, logging failures."""
    global _modules_loaded  # pylint: disable=global-statement
    if _modules_loaded:
        return
    with _lock:
        if _modules_loaded:
            return
        _modules_loaded = True
        for entry_point in importlib.metadata.entry_points(group=ENTRY_POINT_GROUP):
            try:
                entry_point.load()
            except Exception:  # pylint: disable=broad-exception-caught
                logger.error("[METRICS] %s failed to load", entry_point.value, exc_info=True)


def __getattr__(name: str):
    """METRICS, METRIC_IDS and SPECS: the catalogue as it stands, model metrics included."""
    if name in ("METRICS", "METRIC_IDS", "SPECS"):
        _load_metric_modules()
        with _lock:
            if name == "METRICS":
                return tuple(_metrics)
            if name == "METRIC_IDS":
                return tuple(spec.id for spec in _metrics)
            return dict(_specs)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def metric_spec(metric_id: str) -> MetricSpec:
    """One catalogue metric's spec."""
    _load_metric_modules()
    return _specs[metric_id]


# The kit a model module builds its records with.
build_record = _record
producer_record = _from_producer
as_mapping = _mapping
finite_or_none = _finite
value_range = _range
is_mock = _mock
reported_carry = _reported_carry
collision_metric = _collision_metric


def from_record(shot: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Every metric of one shot record, in catalog order, built from its fields."""
    _load_metric_modules()
    shot = _mapping(shot)
    with _lock:
        built = [(spec.id, _builders[spec.id]) for spec in _metrics]
    return _with_prior_label({metric_id: build(shot) for metric_id, build in built}, shot)


def records(shot: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """The shot's logged metric records, each missing one rebuilt (older logs)."""
    _load_metric_modules()
    shot = _mapping(shot)
    logged = _mapping(shot.get("metrics"))
    rebuilt = None
    result = {}
    for metric_id in [spec.id for spec in _metrics]:
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

    from openflight.shot_metrics import catalog as installed_catalog

    # Entry points register into the canonical module, not this __main__ registry.
    sys.stdout.write(json.dumps(installed_catalog(), indent=2) + "\n")

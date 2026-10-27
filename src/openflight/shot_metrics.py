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

from .horizontal_frame import implausible_launch_reason
from .numeric import finite_float as _finite
from .source_chain import weakest_label
from .source_labels import (
    CAMERA_HEAD_TRACK_SOURCE,
    CENTRE_STRIKE_NOTE,
    EXPERIMENTAL_INPUT_SOURCES,
    EXPERIMENTAL_LAUNCH_NOTE,
    EXPERIMENTAL_LAUNCH_SOURCES,
    IWR_COMPETING_NOTE,
    IWR_PARTIAL_NOTE,
    LABEL_CALCULATED as CALCULATED,
    LABEL_ESTIMATED as ESTIMATED,
    LABEL_EXPERIMENTAL as EXPERIMENTAL,
    LABEL_IWR_PARTIAL,
    LABEL_MEASURED as MEASURED,
    LABEL_MODEL_GEOMETRY as MODEL_GEOMETRY,
    MODEL_LABELS,
    SPIN_AXIS_UNRECORDED_NOTE,
    is_calculated_source,
    is_measured_source,
    is_model_geometry_source,
    record_label,
    shot_launch_label,
)

logger = logging.getLogger(__name__)

SCHEMA = "openflight.shot_metrics"
VERSION = 1

NOT_MEASURED = "not_measured"
LABELS = (MEASURED, CALCULATED, MODEL_GEOMETRY, ESTIMATED, EXPERIMENTAL, NOT_MEASURED)

BALL, CLUB, FLIGHT = "ball", "club", "flight"

OPS_RADIAL_SOURCE = "OPS243 radial (line of sight)"
MOCK_SOURCE = "mock shot (simulated)"
SMASH_RADIAL_SOURCE = "radial ratio: OPS radial ball speed / OPS radial club speed (D21)"
SMASH_SOURCE = "ball speed / club speed (D37)"
COMMITTED_CARRY_SOURCE = "the kiosk's committed carry (no flight record on this shot)"
NO_CLUB_DELIVERY = "no camera club delivery on this shot"
HEAD_TRACK_SOURCE_LABEL = "camera head track: hosel + OPS speed (experimental)"
_PATH_SOURCES = {
    "canonical": "radar club track",
    "camera_fused_chained": "camera + IWR chained delivery",
    "camera_fused_ops": "camera + OPS chained delivery",
    "iwr": "IWR club track",
    # the IWR attack that passed its fit gates but failed its acceptance gate (Q4)
    "iwr_candidate": "IWR attack-angle candidate (experimental)",
    CAMERA_HEAD_TRACK_SOURCE: HEAD_TRACK_SOURCE_LABEL,
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


def _experimental_launch_note(source: Optional[str]) -> Optional[str]:
    if source not in EXPERIMENTAL_LAUNCH_SOURCES:
        return None
    return f"{EXPERIMENTAL_LAUNCH_SOURCES[source]}: {EXPERIMENTAL_LAUNCH_NOTE}"


def _launch_vertical(shot):
    """The vertical launch under the policy's label: estimated on the IWR's configured
    tilt (no LIS3DH reading applied), the camera fallback's by the lens."""
    source = shot.get("launch_angle_vertical_source") or shot.get("angle_source")
    label, why = shot_launch_label(shot, "vertical")
    notes = [why, _experimental_launch_note(source) if label == EXPERIMENTAL else None]
    return _record(
        "launch_vertical",
        shot.get("launch_angle_vertical"),
        label,
        source=source,
        reason=shot.get("launch_angle_vertical_reason")
        or "vertical launch not measured on this shot",
        note="; ".join(dict.fromkeys(note for note in notes if note)) or None,
    )


def _launch_horizontal(shot):
    """The horizontal launch: estimated on a weak source or an uncalibrated azimuth, withheld
    beyond a shank's reach."""
    source = shot.get("launch_angle_horizontal_source") or shot.get("angle_source")
    value = shot.get("launch_angle_horizontal")
    reason = shot.get("launch_angle_horizontal_reason") or (
        "horizontal launch not measured on this shot"
    )
    implausible = implausible_launch_reason(value)
    if implausible is not None:
        value, reason = None, f"{source or 'horizontal launch'} {implausible}"
    label, why = shot_launch_label(shot, "horizontal")
    notes = [why, _experimental_launch_note(source) if label == EXPERIMENTAL else None]
    notes.append(_mapping(shot.get("iwr_azimuth_check")).get("note"))
    return _record(
        "launch_horizontal",
        value,
        label,
        source=source,
        reason=reason,
        frame=shot.get("launch_angle_horizontal_frame"),
        note="; ".join(dict.fromkeys(note for note in notes if note)) or None,
    )


def _collision_metric(shot, name: str) -> Mapping[str, Any]:
    return _mapping(_mapping(_mapping(shot.get("collision_model")).get("metrics")).get(name))


OPS_SPIN_UNVALIDATED = "OPS spin not validated against a reference"


def _experimental_spin_note(shot) -> str:
    """Why an OPS spin of experimental quality is not a measurement, rail included."""
    confidence = _finite(shot.get("spin_confidence"))
    parts = [f"confidence {confidence:.2f}" if confidence is not None else None]
    parts.append(OPS_SPIN_UNVALIDATED)
    if shot.get("spin_at_upper_rail"):
        parts.append("at the search band's upper rail")
    elif shot.get("spin_at_lower_rail"):
        parts.append("at the search band's lower rail")
    return "; ".join(part for part in parts if part)


def _spin_rate(shot):
    source = shot.get("spin_source")
    status = source if source in LABELS and source != NOT_MEASURED else MEASURED
    collision = _collision_metric(shot, "spin_rate")
    # the spin chain's own record names every route (server._resolve_spin)
    chain = _mapping(_mapping(shot.get("input_sources")).get("spin"))
    reason = (
        chain.get("reason")
        or shot.get("spin_rejection_reason")
        or (f"collision model: {collision['reason']}" if collision.get("reason") else None)
        or "spin not measured on this shot"
    )
    quality = shot.get("spin_quality")
    note = f"confidence {quality}" if status == MEASURED and quality else None
    if status == MEASURED and (quality == EXPERIMENTAL or chain.get("route") == "ops_below_floor"):
        # an OPS reading below the confidence floor is not a measurement: it feeds nothing
        status, note = EXPERIMENTAL, _experimental_spin_note(shot)
    model_spin = _finite(shot.get("spin_rpm_collision"))
    if (
        status != MEASURED
        and model_spin is not None
        and model_spin == _finite(shot.get("spin_rpm"))
    ):
        note = _centre_strike_note(shot, collision.get("note"), collision.get("reason"))
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
    elif _finite(shot.get("spin_axis_deg")) is not None:
        # an older log's axis whose source names no label: weaker than measured
        status, source = ESTIMATED, source or SPIN_AXIS_UNRECORDED_NOTE
    else:
        status = None
    collision = _collision_metric(shot, "spin_axis")
    computed = status in (CALCULATED, MODEL_GEOMETRY, ESTIMATED) and collision
    return _record(
        "spin_axis",
        shot.get("spin_axis_deg"),
        status,
        source=source,
        reason=collision.get("reason") or "spin axis not measured on this shot",
        frame=shot.get("spin_axis_frame"),
        note=_centre_strike_note(shot, collision.get("note"), collision.get("reason"))
        if computed
        else None,
    )


def _centre_strike_note(shot, note: Optional[str], reason: Optional[str] = None) -> Optional[str]:
    """A collision output's note with the model's own assumption (Q2): a centre-face
    strike, while no impact location reached the model."""
    note = "; ".join(dict.fromkeys(text for text in (note, reason) if text)) or None
    inputs = _mapping(_mapping(shot.get("collision_model")).get("inputs"))
    if "impact_offset_mm" in inputs or "impact_height_mm" in inputs:
        return note
    if note and "centre-face strike" in note:
        return note
    return f"{CENTRE_STRIKE_NOTE}; {note}" if note else CENTRE_STRIKE_NOTE


centre_strike_note = _centre_strike_note


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
    # an older log's ball-speed table carry is not a carry: nothing is filled in
    # (owner decision 1, 25 Oct 2026)
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
CAMERA_CLUB_NOTE = (
    "camera club delivery on an inferred camera geometry: experimental, "
    "not validated against TrackMan"
)


HEAD_TRACK_CLUB_NOTE = (
    "camera head track (experimental): hosel + OPS speed, not validated against TrackMan; "
    "it feeds the collision model, so every output built on it is experimental"
)


def _camera_club_label(record: dict, source: Optional[str]) -> dict:
    """An older log's (or the arc's) camera club value labelled measured is experimental:
    its record carries no tier and lens label (source_labels.camera_club_label).

    The head track's value enters the collision model "estimated" (it has no
    experimental label); its own record is experimental, and with_input_labels lifts
    every output built on it to experimental (owner decision 5, 25 Oct 2026).
    """
    status = record.get("status")
    head_track = source == CAMERA_HEAD_TRACK_SOURCE and status in (
        MEASURED,
        ESTIMATED,
        EXPERIMENTAL,
    )
    if head_track or (status == MEASURED and str(source or "").startswith("camera")):
        why = HEAD_TRACK_CLUB_NOTE if head_track else CAMERA_CLUB_NOTE
        note = record.get("note")
        record["status"] = EXPERIMENTAL
        record["note"] = f"{note}; {why}" if note else why
    return record


def _path_or_attack(metric_id: str):
    """The path / attack the shot uses: the collision model's input, else the arc's."""

    def build(shot):
        delivery = _delivery_entry(shot, metric_id)
        inputs = _mapping(_mapping(shot.get("collision_model")).get("inputs"))
        used = _mapping(inputs.get(_COLLISION_INPUT[metric_id]))
        if _finite(used.get("value")) is not None:
            source = used.get("source")
            same_as_arc = delivery and delivery.get("source") == source
            # the input's own policy label: its flag says experimental (an older log
            # without the flag is read by its source, _camera_club_label)
            experimental = (
                bool(used["experimental"])
                if "experimental" in used
                else source in EXPERIMENTAL_INPUT_SOURCES
            )
            status = EXPERIMENTAL if experimental else used.get("label")
            record = _record(
                metric_id,
                used["value"],
                status,
                source=_PATH_SOURCES.get(source, source),
                range_=delivery.get("range") if same_as_arc else None,
                frame=used.get("frame") if metric_id == "club_path" else None,
                # a weaker input says why (the IWR candidate's reason, a nominal lens)
                note=used.get("reason")
                if status in (ESTIMATED, EXPERIMENTAL) and source != CAMERA_HEAD_TRACK_SOURCE
                else None,
            )
            if "experimental" in used and source != CAMERA_HEAD_TRACK_SOURCE:
                return record
            return _camera_club_label(record, source)
        if _finite(delivery.get("value")) is not None:
            source = delivery.get("source")
            return _camera_club_label(
                _from_producer(
                    metric_id, {**delivery, "source": _PATH_SOURCES.get(source, source)}
                ),
                source,
            )
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
    "camera_ball_size": "this shot's resting ball: its outline size at the lens's focal length",
    "configured_tee": "a fixed range set in code (not used by the kiosk)",
}
BALL_RANGE_UNVALIDATED = "per-swing IWR range: not validated against a tape yet"
CAMERA_PRIOR_NOTE = "rests on the camera prior's ball distance (coarse, estimated)"
BALL_SIZE_RANGE_NOTE = (
    "rests on the resting ball's own size for its distance (experimental, not validated "
    "against a tape)"
)
BALL_SIZE_RANGE_CALIBRATED_NOTE = (
    "rests on the resting ball's own size through a calibrated lens (estimated, not "
    "validated against a tape)"
)
# The label every range-dependent value takes from the range's source, and why.
RANGE_SOURCE_LABELS = {
    "camera_prior": (ESTIMATED, CAMERA_PRIOR_NOTE),
    "camera_ball_size": (EXPERIMENTAL, BALL_SIZE_RANGE_NOTE),
}


def _range_source_label(shot) -> tuple[Optional[str], Optional[str]]:
    """(label, why) every range-dependent value takes from the shot's range, or (None,
    None) for a measured one. The chain's own label decides where it is weaker than the
    source alone says: a partial IWR dump (estimated), the resting ball's size through a
    calibrated lens (estimated, Q6; experimental through a nominal one)."""
    source, label = shot.get("ball_range_source"), shot.get("ball_range_label")
    if source == "iwr_post_impact" and label == LABEL_IWR_PARTIAL:
        return record_label(LABEL_IWR_PARTIAL), IWR_PARTIAL_NOTE
    if source == "iwr_post_impact" and label == ESTIMATED:
        # two IWR tracks compete for the launch point (iwr6843.launch_range): its reason
        return ESTIMATED, shot.get("ball_range_reason") or IWR_COMPETING_NOTE
    if source == "camera_ball_size" and label == ESTIMATED:
        return ESTIMATED, BALL_SIZE_RANGE_CALIBRATED_NOTE
    return RANGE_SOURCE_LABELS.get(source, (None, None))


# Sensor readings that do not rest on the ball's range. The spin is one only when the
# OPS measured it: a model's spin is built on the launch and the club (audit 25 Oct).
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
        "camera_ball_size": EXPERIMENTAL,
        # a fixed range set in code is assumed, not measured (not used by the kiosk)
        "configured_tee": ESTIMATED,
    }.get(source, ESTIMATED)
    weaker, why = _range_source_label(shot)
    if weaker is not None:
        status = weaker
    note = BALL_RANGE_UNVALIDATED if source == "iwr_post_impact" else why
    if source == "iwr_post_impact" and shot.get("ball_range_label") in (
        LABEL_IWR_PARTIAL,
        ESTIMATED,
    ):
        # the IWR range's own reason (which frames it lost, which tracks compete)
        note = "; ".join(
            dict.fromkeys(text for text in (shot.get("ball_range_reason"), why, note) if text)
        )
    return _record(
        "ball_range",
        value,
        status,
        source=BALL_RANGE_SOURCES.get(source, source),
        range_=span,
        reason=shot.get("ball_range_reason") or "the ball's distance was not measured",
        note=note,
    )


# What each model output is built on (the inputs' metric ids). A record a model built
# takes the weakest label among these, so a value never reads stronger than its
# weakest input (D17, 25 Oct): measured > calculated > model geometry > estimated >
# experimental. Readings a sensor took (ball and club speed, launch, club path) are
# not in here: they carry their own label.
_D_PLANE = ("launch_vertical", "launch_horizontal", "club_path", "attack_angle")
_FLIGHT = ("ball_speed_full", "launch_vertical", "launch_horizontal", "spin_rate", "spin_axis")
UPSTREAM = {
    "ball_speed_full": ("launch_vertical", "launch_horizontal"),
    "smash_factor_full": ("ball_speed_full", "club_speed_full"),
    "spin_rate": (*_D_PLANE, "ball_speed_full"),
    "spin_axis": _D_PLANE,
    "face_angle": (*_D_PLANE, "ball_speed_full"),
    "face_to_path": ("face_angle", "club_path"),
    "dynamic_loft": (*_D_PLANE, "ball_speed_full"),
    "spin_loft": ("dynamic_loft", "attack_angle"),
    "d_plane_tilt": ("face_angle", "club_path", "spin_loft"),
    "spin_index": ("spin_rate", "spin_loft"),
    "smash_index": ("ball_speed_full", "spin_loft"),
    **{
        name: _FLIGHT
        for name in (
            "carry",
            "carry_side",
            "total",
            "total_side",
            "apex",
            "landing_angle",
            "hang_time",
            "curve",
        )
    },
}
ROUTE_UPSTREAM = {("ball_speed_full", "ops_total_projection"): ("launch_vertical",)}
_ROUTE_FIELDS = {"ball_speed_full": "ball_speed_full"}
_LOWERABLE = (CALCULATED, MODEL_GEOMETRY, ESTIMATED)


def _upstream_of(metric_id: str, inputs: tuple, shot) -> tuple:
    """The inputs this shot's value of ``metric_id`` was built on (its route's, if named)."""
    field_name = _ROUTE_FIELDS.get(metric_id)
    route = _mapping(shot.get(field_name)).get("route") if field_name else None
    return ROUTE_UPSTREAM.get((metric_id, route), inputs)


def _lower(record: dict[str, Any], label: str, why: str) -> bool:
    """Take ``label`` when it is weaker than the record's own; True when it changed."""
    status = record.get("status")
    if status not in (*_LOWERABLE, MEASURED) or record.get("value") is None:
        return False
    weaker = weakest_label(status, label)
    if weaker == status:
        return False
    note = record.get("note")
    record["status"] = weaker
    record["note"] = f"{note}; {why}" if note else why
    return True


def _add_note(record: dict[str, Any], why: Optional[str]) -> None:
    """Append ``why`` to a record's note once (a value only)."""
    note = record.get("note") or ""
    if why and record.get("value") is not None and why not in note:
        record["note"] = f"{note}; {why}" if note else why


def _model_spin(shot) -> bool:
    """Whether the shot's spin is a model's (built on its launch and club), not the OPS's."""
    return shot.get("spin_source") in MODEL_LABELS


def _range_label(metrics: dict[str, dict[str, Any]], shot) -> None:
    """Every range-dependent value takes the label of the range it rested on."""
    label, why = _range_source_label(shot)
    if label is None:
        return
    for metric_id, entry in metrics.items():
        if metric_id == "ball_range":
            continue
        if metric_id in RANGE_INDEPENDENT and not (metric_id == "spin_rate" and _model_spin(shot)):
            continue
        if not _lower(entry, label, why) and entry.get("status") == label:
            # already that weak for another reason: the range still says it rests on it
            _add_note(entry, why)


def with_input_labels(metrics: dict[str, dict[str, Any]], shot) -> dict[str, dict[str, Any]]:
    """Every value takes its weakest input's label: the range's, then each model input's."""
    _range_label(metrics, shot)
    changed = True
    while changed:
        changed = False
        for metric_id, inputs in UPSTREAM.items():
            entry = metrics.get(metric_id)
            if entry is None or entry.get("status") not in _LOWERABLE:
                continue
            if metric_id == "spin_rate" and not _model_spin(shot):
                continue
            for name in _upstream_of(metric_id, inputs, shot):
                upstream = metrics.get(name) or {}
                status = upstream.get("status")
                if status not in (EXPERIMENTAL, *_LOWERABLE) or upstream.get("value") is None:
                    continue
                if name == "spin_rate" and not _model_spin(shot):
                    # an OPS spin below the confidence floor feeds no model
                    continue
                spec = _specs.get(name)
                title = spec.label.lower() if spec else name
                changed |= _lower(entry, status, f"rests on the {status} {title}")
    _name_experimental_club_inputs(metrics)
    return metrics


_CLUB_INPUTS = ("club_path", "attack_angle")


def _name_experimental_club_inputs(metrics: dict[str, dict[str, Any]]) -> None:
    """An output already experimental for another reason (a camera launch, the ball's own
    size) still names the experimental club path or attack it is built on: the camera
    head track feeds the collision model (owner decision 5, 25 Oct 2026)."""
    for metric_id, inputs in UPSTREAM.items():
        entry = metrics.get(metric_id)
        if not entry or entry.get("status") != EXPERIMENTAL or entry.get("value") is None:
            continue
        for name in inputs:
            upstream = metrics.get(name) or {}
            if (
                name not in _CLUB_INPUTS
                or upstream.get("status") != EXPERIMENTAL
                or upstream.get("value") is None
            ):
                continue
            why = f"rests on the experimental {_specs[name].label.lower()}"
            note = entry.get("note") or ""
            if why not in note:
                entry["note"] = f"{note}; {why}" if note else why


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
    return with_input_labels({metric_id: build(shot) for metric_id, build in built}, shot)


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

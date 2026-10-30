"""The shot models' kiosk extension: club arc, collision model, full speeds and flight.

``openflight.server`` measures and publishes; it builds no model of its own. This
module registers ``extension`` on import (an ``openflight.kiosk_extensions`` entry
point loads it at kiosk start-up) and, through the ``KioskExtension`` hooks:

- attaches the arc's club delivery to each camera club delivery (E1);
- runs the collision model on the measured inputs once enrichment is done: face
  angle, dynamic loft, spin loft, face to path, D-plane tilt, spin and spin axis
  (E2); its spin becomes the shot's when no OPS spin is accepted;
- adds the full ball speed, full club speed and full smash beside the radial
  speeds (E3);
- simulates the flight and commits the carry (E4).

A kiosk without this module still measures and publishes every radar and camera
value; it reports none of these models' outputs, and its carry is the plain
simulator's on measured inputs only, else not measured (D17; owner decision 1).
"""

from __future__ import annotations

import logging
from dataclasses import asdict, replace

# model_metrics adds these models' metrics to the shot-metric catalogue on import
from openflight import (  # noqa: F401  pylint: disable=unused-import
    collision_model,
    kiosk_extensions,
    model_metrics,
    server,
)
from openflight.camera.head_track import SOURCE as HEAD_TRACK_SOURCE
from openflight.flight import STATUS_MODEL_GEOMETRY as FLIGHT_STATUS_MODEL_GEOMETRY, compute_flight
from openflight.horizontal_frame import MIXED, TARGET_LINE, UNIT_BORESIGHT, to_reported_deg
from openflight.launch_monitor import (
    CARRY_CALCULATED,
    CARRY_ESTIMATED,
    CARRY_MODEL_GEOMETRY,
    CARRY_NOT_MEASURED,
    SPIN_CONFIDENCE_HIGH,
    Shot,
)
from openflight.numeric import finite as _finite
from openflight.source_chain import Candidate, Resolution, Route, resolve
from openflight.source_labels import (
    AZIMUTH_UNCALIBRATED,
    AZIMUTH_UNCALIBRATED_NOTE,
    EXPERIMENTAL_LAUNCH_NOTE,
    EXPERIMENTAL_LAUNCH_SOURCES,
    LABEL_ESTIMATED,
    LABEL_EXPERIMENTAL,
    LABEL_MEASURED,
    MODEL_LABELS,
    TILT_CONFIGURED_NOTE,
    WEAK_HORIZONTAL_SOURCES,
    camera_club_label,
    camera_club_note,
    is_measured_source,
    shot_launch_label,
)

logger = logging.getLogger("openflight.server")


def _attach_club_arc(shot: Shot, delivery) -> None:
    """E1: the shot's club delivery from the arc, beside the chained cross-check.

    ``delivery`` is the chained club delivery (dataclass or its persisted dict),
    or None. Right-handed unless the golfer is set left-handed (owner rule), in the
    hand the shot was detected with.
    """
    from openflight.club_arc import club_delivery_payload  # noqa: PLC0415

    shot.club_delivery = club_delivery_payload(
        delivery, club=shot.club, handedness=server.shot_handedness(shot)
    )


def _fused_rejected(shot: Shot) -> bool:
    status = shot.experimental_fused_status or ""
    return status.startswith("rejected") or status == "error"


def _fused_source(shot: Shot) -> str:
    status = shot.experimental_fused_status
    if status == HEAD_TRACK_SOURCE:
        return HEAD_TRACK_SOURCE
    return "camera_fused_ops" if status == "camera_ops_fallback" else "camera_fused_chained"


def displayed_club_path(shot: Shot) -> tuple[float | None, str | None]:
    """Return the club path the kiosk shows, when it is accepted, and its source.

    The precedence mirrors the kiosk's club-path tile (ui liveMetrics.ts): the
    canonical field, the camera-fused path, an accepted IWR path, then the camera
    head track (experimental). A rejected camera fusion hides nothing. Candidate,
    out-of-bounds, noisy, rejected and withheld paths return (None, None), so
    face angle is never built on a path the kiosk does not show as a
    measurement (audit F1). An IWR path without a horizontal phase reference
    carries status "azimuth_uncalibrated", not "accepted", so it is left out
    too (audit F8).
    """
    if shot.club_path_deg is not None:
        return shot.club_path_deg, "canonical"
    fused = shot.experimental_fused_club_path_deg
    withheld = shot.experimental_fused_club_path_confidence == "withheld"
    head_track = shot.experimental_fused_status == HEAD_TRACK_SOURCE
    if fused is not None and not head_track and not _fused_rejected(shot):
        return (None, None) if withheld else (fused, _fused_source(shot))
    iwr_status = shot.experimental_club_path_status or ""
    if shot.experimental_club_path_deg is not None and iwr_status.startswith("accepted"):
        return shot.experimental_club_path_deg, "iwr"
    if fused is not None and head_track and not withheld:
        return fused, HEAD_TRACK_SOURCE
    return None, None


_FUSED_SOURCES = ("camera_fused_chained", "camera_fused_ops")


def camera_fused_label(shot: Shot, metric: str, source: str | None) -> tuple[str, str | None]:
    """(label, note) of the fused camera path or attack (``metric``: "club_path" or
    "attack_angle"): its own tier through the shot's lens (source_labels.camera_club_label),
    the same for the collision input and the record (fix 1, 25 Oct 2026)."""
    tier = (
        shot.experimental_fused_club_path_confidence
        if metric == "club_path"
        else shot.experimental_fused_attack_angle_confidence
    )
    intrinsics = shot.camera_lens_intrinsics
    return camera_club_label(tier, intrinsics, source), camera_club_note(tier, intrinsics, source)


def _club_path_frame_of(shot: Shot, path_source: str | None) -> str | None:
    if path_source == "canonical":
        return shot.club_path_frame
    if path_source in ("camera_fused_chained", "camera_fused_ops", HEAD_TRACK_SOURCE):
        return shot.experimental_fused_club_path_frame
    if path_source == "iwr":
        return shot.experimental_club_path_frame
    return None


# The camera head track's path and attack feed the collision model (owner decision 5,
# 25 Oct 2026). The model has no experimental label (it would read one as missing), so
# they enter "estimated" with this reason; shot_metrics labels the head track's own
# records experimental and lifts every output built on them to experimental too.
HEAD_TRACK_REASON = "camera head track (experimental)"
# The IWR attack angle that passed its own fit gates ("candidate_available") but not its
# acceptance gate: a route of its own (owner decision 3, 25 Oct 2026). Every other
# candidate status (out of bounds, noisy fit, a club-speed or contact mismatch) is a
# refusal.
IWR_CANDIDATE_SOURCE = "iwr_candidate"
IWR_CANDIDATE_STATUS = "candidate_available"
# Q4 (owner, 25 Oct 2026): the candidate failed its own acceptance gate, the same rule as
# Q3, so it is experimental. It still feeds the collision model as the last route before
# the arc, entering "estimated" with its reason and flagged experimental (the model has no
# experimental label); every output built on it is experimental and no simulator gets it.
IWR_CANDIDATE_LABEL = LABEL_EXPERIMENTAL
IWR_CANDIDATE_REASON = (
    "IWR attack-angle candidate: passed its fit gates but failed its acceptance gate "
    f"({IWR_CANDIDATE_LABEL})"
)
# The routes' collision-input reasons: a route weaker than measured enters "estimated"
# with its reason (the head track, the IWR candidate, a camera fused value on a nominal
# lens or a weak tier).
_ATTACK_INPUT_REASONS = {
    HEAD_TRACK_SOURCE: HEAD_TRACK_REASON,
    IWR_CANDIDATE_SOURCE: IWR_CANDIDATE_REASON,
}


def attack_angle_resolution(shot: Shot) -> Resolution:
    """The shot's attack angle: the first route, best first, that gives a value.

    1. the canonical radar club track (measured);
    2. the camera + IWR (or OPS) fused delivery, unless fusion rejected it or withheld
       its confidence (its tier through the lens: ``camera_fused_label``);
    3. an accepted IWR attack (measured);
    4. the camera head track (experimental);
    5. the IWR candidate (``IWR_CANDIDATE_LABEL``, experimental: Q4);
    6. E1's arc, under its own label (calculated or model geometry, D39).

    The head track goes before the candidate though its label is weaker: it carries a
    per-shot sigma from its own error budget (contact time, range, focal length, pitch,
    the hosel standing in for the face centre; 2.8-10.1 deg, median 3.8, on the 15
    head-track attacks of Harjot-outdoor-test-1),
    while the candidate has no per-shot uncertainty and the last radar AoA candidate
    checked against TrackMan (2026-08-07) ran 9-22 deg off by club. The ui's attack tile
    (liveMetrics.ts) mirrors this order.
    """
    fused = shot.experimental_fused_attack_angle_deg
    head_track = shot.experimental_fused_status == HEAD_TRACK_SOURCE
    withheld = shot.experimental_fused_attack_angle_confidence == "withheld"
    iwr_value = shot.experimental_attack_angle_deg
    iwr_status = shot.experimental_attack_angle_status or ""

    def canonical():
        if _finite(shot.club_angle_deg):
            return Candidate(
                float(shot.club_angle_deg), collision_model.LABEL_MEASURED, "canonical"
            )
        return "no radar club track attack angle"

    def camera_fused():
        if head_track or not _finite(fused):
            return f"no camera fused attack ({shot.experimental_fused_status or 'not run'})"
        if _fused_rejected(shot):
            return f"camera fusion {shot.experimental_fused_status}"
        if withheld:
            return "camera fused attack withheld"
        source = _fused_source(shot)
        label, note = camera_fused_label(shot, "attack_angle", source)
        return Candidate(float(fused), label, source, note=note)

    def iwr_accepted():
        if _finite(iwr_value) and iwr_status.startswith("accepted"):
            return Candidate(float(iwr_value), collision_model.LABEL_MEASURED, "iwr")
        return f"no accepted IWR attack ({iwr_status or 'no IWR club track'})"

    def camera_head_track():
        if not head_track:
            status = shot.experimental_fused_status
            return "the camera head track did not run" + (f" ({status})" if status else "")
        if not _finite(fused) or withheld:
            return "the camera head track flagged its attack angle"
        return Candidate(
            float(fused),
            LABEL_EXPERIMENTAL,
            HEAD_TRACK_SOURCE,
            note=HEAD_TRACK_REASON,
        )

    def iwr_candidate():
        if not _finite(iwr_value):
            return f"no IWR attack candidate ({iwr_status or 'no IWR club track'})"
        if iwr_status != IWR_CANDIDATE_STATUS:
            return f"IWR attack candidate {iwr_status or 'unlabelled'}"
        return Candidate(
            float(iwr_value), IWR_CANDIDATE_LABEL, IWR_CANDIDATE_SOURCE, note=IWR_CANDIDATE_REASON
        )

    def club_arc():
        arc = _club_arc_input(shot, "attack_angle_deg", "no club arc attack angle")
        if not arc.present:
            return arc.reason
        return Candidate(arc.value, arc.label, arc.source)

    return resolve(
        "attack_angle",
        [
            Route("canonical", canonical),
            Route("camera_fused", camera_fused),
            Route("iwr_accepted", iwr_accepted),
            Route("camera_head_track", camera_head_track),
            Route("iwr_candidate", iwr_candidate),
            Route("club_arc", club_arc),
        ],
    )


def displayed_attack_angle(shot: Shot) -> tuple[float | None, str | None]:
    """The attack angle the kiosk's attack tile shows, and its source.

    The chain (``attack_angle_resolution``) before the arc: the arc's value reaches the
    tile through the server's record instead. (None, None) when no such route gave one.
    """
    resolution = attack_angle_resolution(shot)
    if not resolution.present or resolution.route == "club_arc":
        return None, None
    return resolution.value, resolution.source


def _attack_input(resolution: Resolution) -> collision_model.Input:
    """The chained attack as a collision input: measured, the arc's label, or estimated
    with the route's reason, flagged when the route is experimental (the head track, the
    IWR candidate, a weak camera tier)."""
    if not resolution.present:
        return collision_model.Input(None, reason=resolution.reason)
    if resolution.label in (LABEL_ESTIMATED, LABEL_EXPERIMENTAL):
        reason = _ATTACK_INPUT_REASONS.get(resolution.source) or resolution.note
        return collision_model.Input(
            resolution.value,
            collision_model.LABEL_ESTIMATED,
            resolution.source,
            reason=reason,
            experimental=resolution.label == LABEL_EXPERIMENTAL,
        )
    return collision_model.Input(resolution.value, resolution.label, resolution.source)


def _horizontal_launch_input(shot: Shot, source) -> collision_model.Input:
    """The horizontal launch as a collision input under the policy's label (C2).

    Its label is the record's (``source_labels.shot_launch_label``): measured, estimated
    (an uncalibrated azimuth, a camera value on a nominal lens) or experimental (a failed
    gate: a low-coherence IWR or low-consensus camera horizontal, a weak camera tier).
    A measured one whose confidence is below the spin axis's floor
    (``SPIN_AXIS_MIN_CONFIDENCE``) is too weak to stand as one. Weaker than measured, it
    enters "estimated" with the reason, flagged when experimental.
    """
    launch = _launch_input(shot, "horizontal", source, frame=shot.launch_angle_horizontal_frame)
    if not launch.present or launch.label != collision_model.LABEL_MEASURED:
        return launch
    confidence = shot.launch_angle_horizontal_confidence
    if not _finite(confidence):
        reason = "horizontal launch confidence not given"
    elif confidence < server.SPIN_AXIS_MIN_CONFIDENCE:
        reason = (
            f"horizontal launch confidence {confidence:.2f} below {server.SPIN_AXIS_MIN_CONFIDENCE}"
        )
    else:
        return launch
    return collision_model.Input(
        launch.value, collision_model.LABEL_ESTIMATED, launch.source, launch.frame, reason=reason
    )


def _launch_reason(shot: Shot, axis: str, name: str, note: str | None) -> str:
    """Why a launch angle enters weaker than measured, in the words the records use."""
    raw = (
        shot.launch_angle_vertical_source
        if axis == "vertical"
        else shot.launch_angle_horizontal_source
    )
    if raw in WEAK_HORIZONTAL_SOURCES:
        return f"{name} is {WEAK_HORIZONTAL_SOURCES[raw]} ({raw})"
    if raw in EXPERIMENTAL_LAUNCH_SOURCES:
        why = note or EXPERIMENTAL_LAUNCH_NOTE
        return f"{name} from the {EXPERIMENTAL_LAUNCH_SOURCES[raw]} ({why})"
    if axis == "horizontal" and shot.launch_angle_horizontal_status == AZIMUTH_UNCALIBRATED:
        return f"{name} {AZIMUTH_UNCALIBRATED_NOTE} ({raw})"
    if axis == "vertical" and note == TILT_CONFIGURED_NOTE:
        return f"{name}: {TILT_CONFIGURED_NOTE}"
    return f"{name}: {note or 'estimated'}"


def _launch_input(shot: Shot, axis: str, source, *, frame=None) -> collision_model.Input:
    """A launch angle as a collision input under the policy's label: measured as is,
    estimated with its reason, experimental entered estimated with its reason and flagged;
    an estimated (table) angle is not an input."""
    name = f"{axis} launch" if axis == "vertical" else "horizontal launch"
    value = shot.launch_angle_vertical if axis == "vertical" else shot.launch_angle_horizontal
    if value is None or source is None:
        return collision_model.Input(None, reason=f"{name} not measured")
    if source == "estimated":
        return collision_model.Input(None, reason=f"{name} is a table estimate, not measured")
    label, note = shot_launch_label(shot, axis)
    if label == LABEL_MEASURED:
        return collision_model.Input(value, collision_model.LABEL_MEASURED, source, frame)
    return collision_model.Input(
        value,
        collision_model.LABEL_ESTIMATED,
        source,
        frame,
        reason=_launch_reason(shot, axis, name, note),
        experimental=label == LABEL_EXPERIMENTAL,
    )


# Arc labels the collision model takes as inputs: a "measured" arc entry is the chained
# value displayed_club_path / displayed_attack_angle already read.
_ARC_INPUT_LABELS = (collision_model.LABEL_CALCULATED, collision_model.LABEL_MODEL_GEOMETRY)


def _club_arc_input(shot: Shot, name: str, missing: str) -> collision_model.Input:
    """The arc's selected value for ``name`` under its own label, or a missing input.

    Used only without an accepted measured value; outputs built on it inherit its label.
    """
    from openflight.club_arc import selected_path_and_attack  # noqa: PLC0415

    entry = selected_path_and_attack(shot.club_delivery).get(name)
    entry = entry if isinstance(entry, dict) else {}
    value, label = entry.get("value"), entry.get("status")
    if label in _ARC_INPUT_LABELS and _finite(value):
        frame = entry.get("frame") if name == "club_path_deg" else None
        return collision_model.Input(float(value), label, entry.get("source"), frame)
    reason = entry.get("reason")
    return collision_model.Input(
        None, reason=f"{missing}; club arc: {reason}" if reason else missing
    )


def _displayed_input(value, source, frame=None, shot: Shot | None = None) -> collision_model.Input:
    """A displayed path as a collision input: measured; the head track's, estimated with
    its reason and flagged experimental; a fused camera path under its tier and the lens
    (``camera_fused_label``), entered estimated with the reason when weaker than measured."""
    if source == HEAD_TRACK_SOURCE:
        return collision_model.Input(
            value,
            collision_model.LABEL_ESTIMATED,
            source,
            frame,
            reason=HEAD_TRACK_REASON,
            experimental=True,
        )
    if source in _FUSED_SOURCES and shot is not None:
        label, note = camera_fused_label(shot, "club_path", source)
        if label != LABEL_MEASURED:
            return collision_model.Input(
                value,
                collision_model.LABEL_ESTIMATED,
                source,
                frame,
                reason=note,
                experimental=label == LABEL_EXPERIMENTAL,
            )
    return collision_model.Input(value, collision_model.LABEL_MEASURED, source, frame)


def _collision_inputs(shot: Shot) -> dict:
    """The shot's own collision inputs, each labelled (E2).

    The kiosk's displayed path and attack come first: measured, or the camera head
    track's, estimated (decision 5). Without one, E1's arc value enters under its own
    label ("calculated", or "model_geometry" for the arc prior, D39), which every
    output built on it then inherits.
    """
    path, path_source = displayed_club_path(shot)
    launch_source = shot.launch_angle_horizontal_source
    if launch_source and shot.launch_angle_horizontal_status == AZIMUTH_UNCALIBRATED:
        launch_source = f"{launch_source} (azimuth uncalibrated)"
    return {
        "club_path": (
            _displayed_input(path, path_source, _club_path_frame_of(shot, path_source), shot)
            if path is not None
            else _club_arc_input(shot, "club_path_deg", "no accepted club path")
        ),
        "attack_angle": _attack_input(attack_angle_resolution(shot)),
        "launch_horizontal": _horizontal_launch_input(
            shot,
            launch_source if shot.launch_angle_horizontal_source != "estimated" else "estimated",
        ),
        "launch_vertical": _launch_input(shot, "vertical", shot.launch_angle_vertical_source),
        "club_speed_mph": _full_impact_club_speed_input(shot),
        "ball_speed_mph": _full_ball_speed_input(shot),
        "spin_rpm": _spin_input(shot),
    }


def _full_impact_club_speed_input(shot: Shot) -> collision_model.Input:
    """Only a full clubhead speed referred to impact belongs in the collision."""
    full = shot.club_speed_full if isinstance(shot.club_speed_full, dict) else {}
    value, label = full.get("value_mph"), full.get("status")
    if (
        not _finite(value)
        or value <= 0.0
        or full.get("referred_to") != "impact"
        or full.get("reference_point") != "clubhead"
        or label not in (*MODEL_LABELS, collision_model.LABEL_MEASURED, LABEL_EXPERIMENTAL)
    ):
        return collision_model.Input(
            None,
            reason="full club speed at impact not measured for the clubhead: the OPS reading is radial; "
            "a precontact full-speed reading is not an impact referral",
        )
    return collision_model.Input(
        float(value),
        collision_model.LABEL_ESTIMATED if label == LABEL_EXPERIMENTAL else label,
        full.get("source") or "full clubhead speed at impact",
        reason=full.get("note")
        or ("full clubhead speed experimental" if label == LABEL_EXPERIMENTAL else None),
        experimental=label == LABEL_EXPERIMENTAL,
    )


def _normalize_collision_horizontal_inputs(inputs: dict, target_line) -> tuple[dict, dict | None]:
    """Use the shot's saved aim to put compatible horizontal inputs in one frame."""
    names = ("club_path", "launch_horizontal")
    given = [inputs[name] for name in names]
    snapshot = target_line if isinstance(target_line, dict) else {}
    offset = snapshot.get("horizontal_offset_deg")
    if (
        not all(value.present for value in given)
        or {value.frame for value in given} != {UNIT_BORESIGHT, TARGET_LINE}
        or snapshot.get("frame") != TARGET_LINE
        or not _finite(offset)
    ):
        return inputs, None
    normalized = dict(inputs)
    for name, value in zip(names, given, strict=True):
        if value.frame == UNIT_BORESIGHT:
            normalized[name] = replace(
                value, value=float(to_reported_deg(value.value, offset)), frame=TARGET_LINE
            )
    return normalized, {
        "output_frame": TARGET_LINE,
        "offset_deg": float(offset),
        "target_line_revision": snapshot.get("revision"),
        "original_inputs": {name: asdict(value) for name, value in zip(names, given, strict=True)},
    }


def _low_point_input(shot: Shot) -> collision_model.Input | None:
    """The arc's low point (contact note only); None without camera club data."""
    from openflight.club_arc import NO_CAMERA_DELIVERY  # noqa: PLC0415

    delivery = shot.club_delivery if isinstance(shot.club_delivery, dict) else {}
    metrics = delivery.get("metrics") if isinstance(delivery.get("metrics"), dict) else {}
    entry = metrics.get("low_point_distance_mm")
    if not isinstance(entry, dict) or entry.get("reason") == NO_CAMERA_DELIVERY:
        return None
    value, label = entry.get("value"), entry.get("status")
    if _finite(value) and label == LABEL_EXPERIMENTAL:
        # the head track's positions (club_arc): the model has no experimental label
        return collision_model.Input(
            float(value),
            LABEL_ESTIMATED,
            entry.get("source"),
            reason=HEAD_TRACK_REASON,
            experimental=True,
        )
    if _finite(value):
        return collision_model.Input(float(value), label, entry.get("source"))
    return collision_model.Input(None, reason=entry.get("reason") or "no low point")


def _full_ball_speed_input(shot: Shot) -> collision_model.Input:
    """The full ball speed, calculated; the radial speed never stands in for it."""
    speed = shot.ball_speed_full_mph
    full = shot.ball_speed_full if isinstance(shot.ball_speed_full, dict) else {}
    status = full.get("status")
    if full and status not in (*MODEL_LABELS, collision_model.LABEL_MEASURED, LABEL_EXPERIMENTAL):
        why = full.get("reason") or f"unsupported full ball speed status {status!r}"
        return collision_model.Input(None, reason=f"full ball speed not measured ({why})")
    if full.get("referred_to") not in (None, "separation", "launch"):
        return collision_model.Input(None, reason="full ball speed is not referred to separation")
    if _finite(speed) and speed > 0:
        if status in (collision_model.STATUS_ESTIMATED, LABEL_EXPERIMENTAL):
            # an experimental full speed feeds on, entered estimated and flagged
            return collision_model.Input(
                float(speed),
                collision_model.LABEL_ESTIMATED,
                "full ball speed",
                reason=f"full ball speed {status} ({full.get('note') or 'a weak input'})",
                experimental=status == LABEL_EXPERIMENTAL,
            )
        return collision_model.Input(
            float(speed),
            collision_model.LABEL_MODEL_GEOMETRY
            if status == collision_model.LABEL_MODEL_GEOMETRY
            else collision_model.LABEL_CALCULATED,
            "full ball speed",
        )
    why = full.get("reason") or "not computed on this shot yet"
    return collision_model.Input(None, reason=f"full ball speed not measured ({why})")


def _spin_input(shot: Shot) -> collision_model.Input:
    """The shot's spin under its own label: an accepted measured spin, or a model's spin.

    A model's spin enters labelled, so the collision model can refuse it for face and loft.
    """
    if _accepted_measured_spin(shot):
        return collision_model.Input(
            float(shot.spin_rpm), collision_model.LABEL_MEASURED, shot.spin_method or "measured"
        )
    if shot.spin_source in MODEL_LABELS and _finite(shot.spin_rpm):
        return collision_model.Input(float(shot.spin_rpm), shot.spin_source, shot.spin_method)
    if _finite(shot.spin_rpm):
        return collision_model.Input(
            None, reason=f"measured spin confidence below {SPIN_CONFIDENCE_HIGH}"
        )
    return collision_model.Input(None, reason="spin not measured")


def _round_or_none(value, digits: int):
    return round(value, digits) if value is not None else None


def _accepted_measured_spin(shot: Shot) -> bool:
    """A measured spin carry and simulators accept (ballistics.resolve_launch, sim resolver)."""
    return (
        shot.spin_source not in MODEL_LABELS
        and _finite(shot.spin_rpm)
        and shot.spin_rpm > 0
        and _finite(shot.spin_confidence)
        and shot.spin_confidence >= SPIN_CONFIDENCE_HIGH
    )


def _collision_spin_status(shot: Shot) -> str | None:
    """The label of this shot's collision spin (calculated, model_geometry, estimated), or None.

    A model-geometry spin feeds the flight model, labelled, but simulators do not get it.
    """
    metrics = (shot.collision_model or {}).get("metrics", {})
    status = metrics.get("spin_rate", {}).get("status")
    if shot.spin_rpm_collision is None or status not in MODEL_LABELS:
        return None
    return status


_SPIN_INPUTS = (
    "club_path",
    "attack_angle",
    "launch_horizontal",
    "launch_vertical",
    "club_speed_mph",
)


def _collision_spin_method(shot: Shot) -> str:
    """Shot.spin_method for the collision spin: the fitted model and its inputs' label."""
    inputs = (shot.collision_model or {}).get("inputs", {})
    labels = [
        record.get("label")
        for name, record in inputs.items()
        if name in _SPIN_INPUTS and record.get("value") is not None
    ]
    return collision_model.shot_spin_method(_collision_spin_status(shot) or "", labels)


def _attach_spin_axis(shot: Shot, result) -> None:
    """The model's axis on the shot under its label; a measured axis is never replaced."""
    if _finite(shot.spin_axis_deg) and is_measured_source(shot.spin_axis_source):
        return
    axis = result.metrics["spin_axis"]
    if axis.present and axis.status in (
        collision_model.STATUS_CALCULATED,
        collision_model.STATUS_MODEL_GEOMETRY,
        collision_model.STATUS_ESTIMATED,
    ):
        shot.spin_axis_deg = round(axis.value, 1)
        shot.spin_axis_source = collision_model.shot_spin_axis_source(
            axis, result.inputs, result.face_method
        )
        shot.spin_axis_frame = axis.frame
    elif (
        collision_model.is_calculated_source(shot.spin_axis_source)
        or collision_model.is_model_geometry_source(shot.spin_axis_source)
        or collision_model.is_estimated_source(shot.spin_axis_source)
    ):
        shot.spin_axis_deg = None
        shot.spin_axis_source = None
        shot.spin_axis_frame = None


def _attach_collision_model(shot: Shot) -> None:
    """Face, loft, spin loft, face to path, D-plane tilt, spin and spin axis.

    Each run starts from scratch, so a late re-run never keeps an earlier run's values.
    """
    inputs, conversion = _normalize_collision_horizontal_inputs(
        _collision_inputs(shot), shot.target_line
    )
    result = collision_model.evaluate(
        club=shot.club,
        handedness=server.shot_handedness(shot),
        low_point_distance_mm=_low_point_input(shot),
        **inputs,
    )
    metrics = result.metrics
    shot.collision_model = result.to_dict()
    if conversion is not None:
        shot.collision_model["horizontal_frame_conversion"] = conversion
    # how the attack angle was chosen, every route's refusal kept (decision 3)
    sources = dict(shot.input_sources or {})
    sources["attack_angle"] = attack_angle_resolution(shot).to_dict()
    shot.input_sources = sources
    shot.face_angle_deg = _round_or_none(metrics["face_angle"].value, 1)
    shot.dynamic_loft_deg = _round_or_none(metrics["dynamic_loft"].value, 1)
    shot.spin_loft_deg = _round_or_none(metrics["spin_loft"].value, 1)
    shot.face_to_path_deg = _round_or_none(metrics["face_to_path"].value, 1)
    shot.d_plane_tilt_deg = _round_or_none(metrics["d_plane_tilt"].value, 1)
    shot.spin_rpm_collision = _round_or_none(metrics["spin_rate"].value, 0)

    # The kiosk's face tile reads the experimental fields; they mirror the new value.
    face = metrics["face_angle"]
    shot.experimental_face_angle_deg = shot.face_angle_deg
    shot.experimental_face_angle_frame = face.frame if face.present else None
    if face.present:
        status = "d_plane_estimate"
        if shot.launch_angle_horizontal_status == AZIMUTH_UNCALIBRATED:
            status += "_azimuth_uncalibrated"
        if face.status == collision_model.STATUS_ESTIMATED:
            status += "_estimated"
        if face.frame == MIXED:
            status += "_mixed_frames"
        if face.status == collision_model.STATUS_MODEL_GEOMETRY:
            status += "_model_geometry"
        shot.experimental_face_angle_status = status
        shot.experimental_face_angle_path_source = result.inputs["club_path"].source
        shot.experimental_face_angle_launch_source = shot.launch_angle_horizontal_source
    else:
        shot.experimental_face_angle_status = (
            "mixed_horizontal_frames"
            if face.reason == collision_model.MIXED_FRAME_REASON
            else "missing_measured_start_direction"
            if not result.inputs["launch_horizontal"].present
            else "missing_accepted_club_path"
            if not result.inputs["club_path"].present
            else "missing_club_category"
        )
        shot.experimental_face_angle_path_source = None
        shot.experimental_face_angle_launch_source = None

    _attach_spin_axis(shot, result)

    spin_status = _collision_spin_status(shot)
    if spin_status is not None and not _accepted_measured_spin(shot):
        if shot.spin_source not in MODEL_LABELS:
            shot.spin_rpm_measured = shot.spin_rpm
            shot.spin_method_measured = shot.spin_method
        shot.spin_rpm = shot.spin_rpm_collision
        shot.spin_source = spin_status
        shot.spin_method = _collision_spin_method(shot)
        shot.spin_confidence = None
    elif shot.spin_source in MODEL_LABELS and spin_status is None:
        # an earlier run's model spin; give back the measured one it displaced
        shot.spin_rpm = shot.spin_rpm_measured
        shot.spin_method = shot.spin_method_measured
        shot.spin_source = "measured" if shot.spin_rpm_measured is not None else None
    if shot.face_angle_deg is not None or shot.spin_rpm_collision is not None:
        logger.info(
            "[SERVER] Collision model: face=%s loft=%s spin loft=%s spin=%s rpm axis=%s",
            shot.face_angle_deg,
            shot.dynamic_loft_deg,
            shot.spin_loft_deg,
            shot.spin_rpm_collision,
            shot.spin_axis_deg,
        )


BALL_SPEED_FULL_ROUTES = ("measured_projection", "ops_total_projection")


def _tee_side_offset(shot: Shot) -> tuple:
    """The resting ball's side offset: the setup's measured one, else this shot's resting
    ball on its camera bearing (experimental), else none (no ball is assumed dead ahead)."""
    from openflight.full_speed import CAMERA_SIDE_OFFSET_SOURCE  # noqa: PLC0415

    scene = server.setup_handoff_config.get("scene") or {}
    if _finite(scene.get("tee_side_offset_m")):
        return float(scene["tee_side_offset_m"]), "setup"
    details = ((shot.input_sources or {}).get("ball_range") or {}).get("details") or {}
    if _finite(details.get("side_offset_m")):
        return float(details["side_offset_m"]), CAMERA_SIDE_OFFSET_SOURCE
    fallback = shot.camera_fallback_launch if isinstance(shot.camera_fallback_launch, dict) else {}
    ball = (fallback.get("camera") or {}).get("ball_lfu_m")
    if isinstance(ball, list) and len(ball) == 3 and _finite(ball[0]):
        return float(ball[0]), CAMERA_SIDE_OFFSET_SOURCE
    return None, None


def _full_ball_speed_chain(shot: Shot, measured_projection, total_projection) -> dict:
    """The full ball speed's routes in order; the winner's record with every refusal kept.

    ``total_projection`` runs the OPS total-speed projection only when the launch-based
    projection (``measured_projection``) refused. The resolution is recorded under
    ``input_sources["ball_speed_full"]``; a shot neither route resolves keeps the first
    route's record with both reasons.
    """
    records: dict[str, dict] = {}

    def route(name: str, produce):
        def run():
            record = produce()
            records[name] = record
            value, status = record.get("value_mph"), record.get("status")
            if status in (None, "not_measured") or not _finite(value):
                return record.get("reason") or "not measured"
            return Candidate(
                float(value), status, record.get("source") or name, note=record.get("note")
            )

        return Route(name, run)

    resolution = resolve(
        "ball_speed_full",
        [
            route(BALL_SPEED_FULL_ROUTES[0], measured_projection),
            route(BALL_SPEED_FULL_ROUTES[1], total_projection),
        ],
    )
    sources = dict(shot.input_sources or {})
    sources["ball_speed_full"] = resolution.to_dict()
    shot.input_sources = sources
    if resolution.present:
        return {**records[resolution.route], "route": resolution.route}
    first = records.get(BALL_SPEED_FULL_ROUTES[0]) or {
        "status": "not_measured",
        "value_mph": None,
        "range_mph": None,
    }
    return {**first, "route": None, "reason": resolution.reason}


def _attach_full_speeds(shot: Shot) -> None:
    """E3: full ball speed by measured projection, full club speed and full smash.

    The canonical radial ball speed is unchanged; each full speed is its own field
    with its status, range and reason (openflight.full_speed). The full ball speed is
    the launch-based projection, else the OPS total-speed projection; the full club
    speed is the OPS club reading on the camera head track's direction (experimental).
    """
    from openflight.full_speed import (  # noqa: PLC0415
        evaluate_full_ball_speed,
        evaluate_full_club_speed_head_track,
        evaluate_full_smash,
        evaluate_total_speed_projection,
    )

    calibration = server.shot_iwr_calibration(shot)

    def calibrated(name: str):
        try:
            return getattr(calibration, name, None)
        except ValueError:  # the radar height is unset until the rig sets it
            return None

    side_offset, side_source = _tee_side_offset(shot)
    sources = dict(shot.input_sources or {})
    sources["tee_side_offset"] = {
        "value": side_offset,
        "source": side_source,
        "label": None
        if side_source is None
        else "measured"
        if side_source == "setup"
        else "experimental",
        "reason": None
        if side_source
        else "the setup measured no side offset and this shot's resting ball gave none",
    }
    shot.input_sources = sources
    vertical_label, vertical_note = shot_launch_label(shot, "vertical")
    horizontal_label, horizontal_note = shot_launch_label(shot, "horizontal")
    shot.ball_speed_full = _full_ball_speed_chain(
        shot,
        lambda: evaluate_full_ball_speed(
            vertical_label=vertical_label,
            vertical_note=vertical_note,
            horizontal_label=horizontal_label,
            horizontal_note=horizontal_note,
            tee_range_label=getattr(shot, "ball_range_label", None),
            ops_track=shot.ops_speed_track,
            vertical_deg=shot.launch_angle_vertical,
            vertical_source=shot.launch_angle_vertical_source,
            horizontal_deg=shot.launch_angle_horizontal,
            horizontal_source=shot.launch_angle_horizontal_source,
            horizontal_status=shot.launch_angle_horizontal_status,
            horizontal_frame=shot.launch_angle_horizontal_frame,
            horizontal_offset_deg=server.horizontal_offset_deg,
            rig=server.rig_geometry,
            tee_range_m=calibrated("tee_range_m"),
            tee_range_uncertainty_m=getattr(shot, "ball_range_uncertainty_m", None),
            tee_range_source=getattr(shot, "ball_range_source", None),
            radar_height_m=calibrated("radar_height_m"),
            ball_height_m=calibrated("tee_ball_height_m"),
            tee_side_offset_m=side_offset,
            tee_side_offset_source=side_source,
        ),
        lambda: evaluate_total_speed_projection(
            vertical_label=vertical_label,
            vertical_note=vertical_note,
            tee_range_label=getattr(shot, "ball_range_label", None),
            ball_radial_mph=shot.ball_speed_mph,
            vertical_deg=shot.launch_angle_vertical,
            vertical_source=shot.launch_angle_vertical_source,
            rig=server.rig_geometry,
            tee_range_m=calibrated("tee_range_m"),
            tee_range_uncertainty_m=getattr(shot, "ball_range_uncertainty_m", None),
            tee_range_source=getattr(shot, "ball_range_source", None),
            radar_height_m=calibrated("radar_height_m"),
            ball_height_m=calibrated("tee_ball_height_m"),
            tee_side_offset_m=side_offset,
            tee_side_offset_source=side_source,
        ),
    )
    # the flight model (E4) reads the value alone: calculated, estimated or experimental
    # (an experimental value feeds on; every output built on it is labelled so), or None
    shot.ball_speed_full_mph = (
        shot.ball_speed_full["value_mph"]
        if shot.ball_speed_full.get("status") in ("calculated", "estimated", LABEL_EXPERIMENTAL)
        else None
    )
    delivery = shot.club_delivery if isinstance(shot.club_delivery, dict) else {}
    shot.club_speed_full = evaluate_full_club_speed_head_track(
        ops_track=shot.ops_speed_track,
        head_track=delivery.get("head_track"),
        rig=server.rig_geometry,
    )
    shot.smash_factor_full = evaluate_full_smash(shot.ball_speed_full, shot.club_speed_full)


def _simulate_carry(shot: Shot, *, enabled: bool) -> kiosk_extensions.CarryOutcome:
    """E4: every flight output, as measured and normalized, and the as-measured carry.

    The as-measured flight uses the shot's supplied air density, or ISA; every
    output is labelled. Carry is built only from the shot's own inputs, on every
    install; without them it is not measured, with the reason (D17; owner decision 1,
    25 Oct 2026).
    """
    flight = compute_flight(shot, enabled=enabled)
    shot.flight = flight.to_dict()
    conditions, trajectory = flight.conditions, flight.as_measured
    if trajectory is None:
        # nothing already on the shot stands in for the simulator
        shot.carry_spin_adjusted = None
        shot.carry_status = CARRY_NOT_MEASURED
        logger.info(
            "[SERVER] Carry not measured: %s",
            shot.flight["as_measured"]["metrics"]["carry"]["reason"],
        )
        return kiosk_extensions.CarryOutcome(source=None)
    shot.carry_spin_adjusted = trajectory.carry_yards
    if flight.status == FLIGHT_STATUS_MODEL_GEOMETRY:
        shot.carry_status = CARRY_MODEL_GEOMETRY
    elif flight.status == CARRY_ESTIMATED:
        # an estimated input (the OPS line-of-sight ball speed): the flight's label
        shot.carry_status = CARRY_ESTIMATED
    else:
        # the flight's own label: a simulated carry is calculated (D38)
        shot.carry_status = CARRY_CALCULATED
    logger.info(
        "[SERVER] Ballistic carry: %.0f yds (spin: %.0f rpm, source: %s)",
        shot.carry_spin_adjusted,
        conditions.spin_rpm,
        conditions.spin_source,
    )
    return kiosk_extensions.CarryOutcome(source="ballistic")


class ShotModelsExtension(kiosk_extensions.KioskExtension):
    name = "shot models"

    def on_club_delivery(self, shot, delivery) -> None:
        _attach_club_arc(shot, delivery)

    def after_enrichment(self, shot) -> None:
        _attach_collision_model(shot)

    def after_speed_contract(self, shot) -> None:
        _attach_full_speeds(shot)
        # the rigid-body face and the smash index need the full ball speed, known only now
        if shot.collision_model is not None and _finite(shot.ball_speed_full_mph):
            _attach_collision_model(shot)

    def simulate_carry(self, shot, *, enabled: bool):
        return _simulate_carry(shot, enabled=enabled)


extension = kiosk_extensions.register(ShotModelsExtension())

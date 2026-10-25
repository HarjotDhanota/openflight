"""The shot models' hooks into the kiosk: club arc (E1), collision model (E2),
full speeds (E3) and the flight model (E4).

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

# model_metrics adds these models' metrics to the shot-metric catalogue on import
from openflight import (  # noqa: F401  pylint: disable=unused-import
    collision_model,
    kiosk_extensions,
    model_metrics,
    server,
)
from openflight.camera.head_track import SOURCE as HEAD_TRACK_SOURCE
from openflight.flight import STATUS_MODEL_GEOMETRY as FLIGHT_STATUS_MODEL_GEOMETRY, compute_flight
from openflight.horizontal_frame import MIXED
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
    MODEL_LABELS,
    WEAK_HORIZONTAL_SOURCES,
    is_measured_source,
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


def _club_path_frame_of(shot: Shot, path_source: str | None) -> str | None:
    """The frame of the club path ``displayed_club_path`` chose (D36 item 2)."""
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
# The IWR attack angle that passed its own fit gates ("candidate_available") but no
# acceptance gate: a route of its own, estimated (owner decision 3, 25 Oct 2026). Every
# other candidate status (out of bounds, noisy fit, a club-speed or contact mismatch) is
# a refusal.
IWR_CANDIDATE_SOURCE = "iwr_candidate"
IWR_CANDIDATE_STATUS = "candidate_available"
IWR_CANDIDATE_REASON = (
    "IWR attack-angle candidate: passed its fit gates but no acceptance gate, and not "
    "validated against TrackMan (estimated)"
)
# The routes' collision-input labels: the head track and the IWR candidate enter
# "estimated" with their reason (the model has no experimental label).
_ATTACK_INPUT_REASONS = {
    HEAD_TRACK_SOURCE: HEAD_TRACK_REASON,
    IWR_CANDIDATE_SOURCE: IWR_CANDIDATE_REASON,
}


def attack_angle_resolution(shot: Shot) -> Resolution:
    """The shot's attack angle: the first route, best first, that gives a value.

    1. the canonical radar club track (measured);
    2. the camera + IWR (or OPS) fused delivery, unless fusion rejected it or withheld
       its confidence (measured; shot_metrics labels a camera value experimental);
    3. an accepted IWR attack (measured);
    4. the camera head track (experimental);
    5. the IWR candidate (estimated);
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
        return Candidate(float(fused), collision_model.LABEL_MEASURED, _fused_source(shot))

    def iwr_accepted():
        if _finite(iwr_value) and iwr_status.startswith("accepted"):
            return Candidate(float(iwr_value), collision_model.LABEL_MEASURED, "iwr")
        return f"no accepted IWR attack ({iwr_status or 'no IWR club track'})"

    def camera_head_track():
        if not head_track:
            return "the camera head track did not run"
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
            float(iwr_value), LABEL_ESTIMATED, IWR_CANDIDATE_SOURCE, note=IWR_CANDIDATE_REASON
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
    with the route's reason (the head track, the IWR candidate)."""
    if not resolution.present:
        return collision_model.Input(None, reason=resolution.reason)
    reason = _ATTACK_INPUT_REASONS.get(resolution.source)
    if reason is not None:
        return collision_model.Input(
            resolution.value, collision_model.LABEL_ESTIMATED, resolution.source, reason=reason
        )
    return collision_model.Input(resolution.value, resolution.label, resolution.source)


def _horizontal_launch_input(shot: Shot, source) -> collision_model.Input:
    """The horizontal launch as a collision input, "estimated" when it is weak (C2).

    A weak source (a low-coherence IWR or low-consensus camera horizontal), an IWR
    horizontal whose azimuth is uncalibrated (the same ones the kiosk labels
    "estimated", ``source_labels.horizontal_launch_label``), or one whose confidence is
    below the spin axis's floor (``SPIN_AXIS_MIN_CONFIDENCE``), is a measured angle too
    weak to stand as one: every output built on it is labelled "estimated", with this
    reason.
    """
    measured = _measured_angle_input(
        shot.launch_angle_horizontal,
        source,
        frame=shot.launch_angle_horizontal_frame,
        name="launch direction",
    )
    if not measured.present or measured.label != collision_model.LABEL_MEASURED:
        return measured
    confidence = shot.launch_angle_horizontal_confidence
    weak = shot.launch_angle_horizontal_source
    if weak in WEAK_HORIZONTAL_SOURCES:
        reason = f"horizontal launch is {WEAK_HORIZONTAL_SOURCES[weak]} ({weak})"
    elif shot.launch_angle_horizontal_status == AZIMUTH_UNCALIBRATED:
        reason = f"horizontal launch {AZIMUTH_UNCALIBRATED_NOTE} ({weak})"
    elif not _finite(confidence):
        reason = "horizontal launch confidence not given"
    elif confidence < server.SPIN_AXIS_MIN_CONFIDENCE:
        reason = (
            f"horizontal launch confidence {confidence:.2f} below {server.SPIN_AXIS_MIN_CONFIDENCE}"
        )
    else:
        return measured
    return collision_model.Input(
        measured.value,
        collision_model.LABEL_ESTIMATED,
        measured.source,
        measured.frame,
        reason=reason,
    )


def _measured_angle_input(value, source, *, frame=None, name: str) -> collision_model.Input:
    """A launch angle as a collision input: an estimated (table) angle is not an input."""
    if value is None or source is None:
        return collision_model.Input(None, reason=f"{name} not measured")
    if source == "estimated":
        return collision_model.Input(None, reason=f"{name} is a table estimate, not measured")
    if source in EXPERIMENTAL_LAUNCH_SOURCES:
        # a camera launch with no validated geometry: used, and every output built on it
        # is at best "estimated" here (shot_metrics labels it experimental), with the reason
        return collision_model.Input(
            value,
            collision_model.LABEL_ESTIMATED,
            source,
            frame,
            reason=f"{name} from the {EXPERIMENTAL_LAUNCH_SOURCES[source]} "
            f"({EXPERIMENTAL_LAUNCH_NOTE})",
        )
    return collision_model.Input(value, collision_model.LABEL_MEASURED, source, frame)


# E1 arc labels the collision model takes as inputs: a "measured" arc entry is the
# chained value displayed_club_path / displayed_attack_angle already read.
_ARC_INPUT_LABELS = (collision_model.LABEL_CALCULATED, collision_model.LABEL_MODEL_GEOMETRY)


def _club_arc_input(shot: Shot, name: str, missing: str) -> collision_model.Input:
    """E1's selected arc value for ``name`` under its own label, or a missing input.

    Used only when the kiosk has no accepted measured value. The arc's value enters
    as "calculated" (fitted from the shot alone) or "model_geometry" (leaning on the
    published prior, D39), and every collision output built on it inherits that label.
    A not-measured or non-finite arc value is a missing input (nothing is filled in).
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


def _displayed_input(value, source, frame=None) -> collision_model.Input:
    """A displayed path or attack as a collision input: measured, or the head track's,
    estimated with its reason."""
    if source == HEAD_TRACK_SOURCE:
        return collision_model.Input(
            value, collision_model.LABEL_ESTIMATED, source, frame, reason=HEAD_TRACK_REASON
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
    speed = shot.club_speed_mph
    return {
        "club_path": (
            _displayed_input(path, path_source, _club_path_frame_of(shot, path_source))
            if path is not None
            else _club_arc_input(shot, "club_path_deg", "no accepted club path")
        ),
        "attack_angle": _attack_input(attack_angle_resolution(shot)),
        "launch_horizontal": _horizontal_launch_input(
            shot,
            launch_source if shot.launch_angle_horizontal_source != "estimated" else "estimated",
        ),
        "launch_vertical": _measured_angle_input(
            shot.launch_angle_vertical, shot.launch_angle_vertical_source, name="vertical launch"
        ),
        "club_speed_mph": (
            collision_model.Input(speed, collision_model.LABEL_MEASURED, "ops243_radial")
            if _finite(speed) and speed > 0
            else collision_model.Input(None, reason="club speed not measured")
        ),
        "ball_speed_mph": _full_ball_speed_input(shot),
        "spin_rpm": _spin_input(shot),
    }


def _low_point_input(shot: Shot) -> collision_model.Input | None:
    """E1's low point under its own label (the contact note only); a missing input when
    the camera's club delivery gave none; None without camera club data."""
    from openflight.club_arc import NO_CAMERA_DELIVERY  # noqa: PLC0415

    delivery = shot.club_delivery if isinstance(shot.club_delivery, dict) else {}
    metrics = delivery.get("metrics") if isinstance(delivery.get("metrics"), dict) else {}
    entry = metrics.get("low_point_distance_mm")
    if not isinstance(entry, dict) or entry.get("reason") == NO_CAMERA_DELIVERY:
        return None
    value, label = entry.get("value"), entry.get("status")
    if _finite(value):
        return collision_model.Input(float(value), label, entry.get("source"))
    return collision_model.Input(None, reason=entry.get("reason") or "no low point")


def _full_ball_speed_input(shot: Shot) -> collision_model.Input:
    """The full ball speed, calculated; the radial speed never stands in for it."""
    speed = shot.ball_speed_full_mph
    full = shot.ball_speed_full if isinstance(shot.ball_speed_full, dict) else {}
    if _finite(speed) and speed > 0:
        if full.get("status") == collision_model.STATUS_ESTIMATED:
            return collision_model.Input(
                float(speed),
                collision_model.LABEL_ESTIMATED,
                "full ball speed",
                reason=f"full ball speed estimated ({full.get('note') or 'a weak input'})",
            )
        return collision_model.Input(
            float(speed), collision_model.LABEL_CALCULATED, "full ball speed"
        )
    why = full.get("reason") or "not computed on this shot yet"
    return collision_model.Input(None, reason=f"full ball speed not measured ({why})")


def _spin_input(shot: Shot) -> collision_model.Input:
    """The shot's spin under its own label: an accepted measured spin, or a model's spin.

    A model's spin enters labelled, so the collision model can refuse it for face and
    loft; a measured spin below the confidence floor is not an input.
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

    Either becomes the shot's spin, under its own label, when there is no accepted
    measured spin. A spin built on model geometry (D39) feeds the flight model (D38)
    labelled so, but is not "measured or calculated" (D31): simulators do not get it.
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
    """The model's axis on the shot under its label; a measured axis is never replaced.

    A weak horizontal launch does not withhold the axis: that input is "estimated"
    (``_horizontal_launch_input``) and so is the axis, with the reason.
    """
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
    """Face, loft, spin loft, face to path, D-plane tilt, spin and spin axis (E2).

    Replaces the fixed 0.8-weight face estimate and the "launch minus path" spin-axis
    proxy with ``collision_model`` (published sources, labelled calculated). Each run
    starts from scratch, so a late re-run never keeps an earlier run's values.
    """
    result = collision_model.evaluate(
        club=shot.club,
        handedness=server.shot_handedness(shot),
        low_point_distance_mm=_low_point_input(shot),
        **_collision_inputs(shot),
    )
    metrics = result.metrics
    shot.collision_model = result.to_dict()
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
            "missing_measured_start_direction"
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


# E3: why the OPS club speed cannot be projected yet (W6, W9 and E1 fix it).
CLUB_SPEED_NOT_FULL_REASON = (
    "the OPS club speed is a percentile over 4-18 ms before impact, not one FFT-window "
    "reading, and the head's direction at that time needs the swing arc (E1) and a fixed "
    "head point (W6, W9)"
)


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


def _attach_full_speeds(shot: Shot) -> None:
    """E3: full ball speed by measured projection, full club speed and full smash.

    The canonical radial ball speed is unchanged; each full speed is its own field
    with its status, range and reason (openflight.full_speed).
    """
    from openflight.full_speed import (  # noqa: PLC0415
        evaluate_full_ball_speed,
        evaluate_full_club_speed,
        evaluate_full_smash,
    )

    # this shot's own range and how sure it is (13 Oct)
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
    shot.ball_speed_full = evaluate_full_ball_speed(
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
    )
    # the flight model (E4) reads the value alone: calculated or estimated (on a camera
    # launch or a stand-in range, labelled so where it is used), or None
    shot.ball_speed_full_mph = (
        shot.ball_speed_full["value_mph"]
        if shot.ball_speed_full.get("status") in ("calculated", "estimated")
        else None
    )
    shot.club_speed_full = evaluate_full_club_speed(
        radial_mph=None,
        head_position_m=None,
        direction=None,
        ops_position_m=None,
        direction_source=None,
        reason=CLUB_SPEED_NOT_FULL_REASON,
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
    """The club arc, collision model, full speeds and flight model (E1-E4)."""

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

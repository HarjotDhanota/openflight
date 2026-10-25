"""The shot models' kiosk extension: club arc, collision model, full speeds and flight.

Registers ``extension`` on import (an ``openflight.kiosk_extensions`` entry point). Without
it the kiosk still measures and publishes every radar and camera value, an OPS-only unit
takes the table carry, and a fusion unit has no carry.
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
from openflight.source_labels import MODEL_LABELS, is_measured_source

logger = logging.getLogger("openflight.server")


def _attach_club_arc(shot: Shot, delivery) -> None:
    """The shot's club delivery from the arc, beside the chained cross-check (RH unless LH)."""
    from openflight.club_arc import club_delivery_payload  # noqa: PLC0415

    shot.club_delivery = club_delivery_payload(
        delivery, club=shot.club, handedness=server.golfer_handedness()["handed"]
    )


def displayed_club_path(shot: Shot) -> tuple[float | None, str | None]:
    """Return the club path the kiosk shows, when it is accepted, and its source.

    Mirrors the kiosk's club-path tile (ui liveMetrics.ts): the canonical field, the
    camera-fused path, then the IWR path only when camera fusion did not run. Any path not
    accepted (including "azimuth_uncalibrated") returns (None, None), so face angle is
    never built on a path the kiosk does not show as a measurement.
    """
    if shot.club_path_deg is not None:
        return shot.club_path_deg, "canonical"
    fused_status = shot.experimental_fused_status or ""
    if shot.experimental_fused_club_path_deg is not None:
        if (
            fused_status.startswith("rejected")
            or fused_status == "error"
            or shot.experimental_fused_club_path_confidence == "withheld"
        ):
            return None, None
        source = (
            "camera_fused_ops" if fused_status == "camera_ops_fallback" else "camera_fused_chained"
        )
        return shot.experimental_fused_club_path_deg, source
    if shot.experimental_fused_status is not None:
        return None, None
    iwr_status = shot.experimental_club_path_status or ""
    if shot.experimental_club_path_deg is not None and iwr_status.startswith("accepted"):
        return shot.experimental_club_path_deg, "iwr"
    return None, None


def _club_path_frame_of(shot: Shot, path_source: str | None) -> str | None:
    """The frame of the club path ``displayed_club_path`` chose."""
    if path_source == "canonical":
        return shot.club_path_frame
    if path_source in ("camera_fused_chained", "camera_fused_ops"):
        return shot.experimental_fused_club_path_frame
    if path_source == "iwr":
        return shot.experimental_club_path_frame
    return None


def displayed_attack_angle(shot: Shot) -> tuple[float | None, str | None]:
    """The attack angle the collision model may use, and its source.

    Mirrors the kiosk's attack tile (ui liveMetrics.ts): the canonical field, then the
    camera-fused attack unless rejected or withheld. The IWR attack angle is only ever a
    candidate, so it is never a collision input.
    """
    if _finite(shot.club_angle_deg):
        return shot.club_angle_deg, "canonical"
    fused_status = shot.experimental_fused_status or ""
    if _finite(shot.experimental_fused_attack_angle_deg):
        if (
            fused_status.startswith("rejected")
            or fused_status == "error"
            or shot.experimental_fused_attack_angle_confidence == "withheld"
        ):
            return None, None
        source = (
            "camera_fused_ops" if fused_status == "camera_ops_fallback" else "camera_fused_chained"
        )
        return shot.experimental_fused_attack_angle_deg, source
    return None, None


def _horizontal_launch_input(shot: Shot, source) -> collision_model.Input:
    """The horizontal launch as a collision input, "estimated" when it is weak.

    Weak: a low-coherence IWR horizontal, or confidence below ``SPIN_AXIS_MIN_CONFIDENCE``.
    """
    measured = _measured_angle_input(
        shot.launch_angle_horizontal,
        source,
        frame=shot.launch_angle_horizontal_frame,
        name="launch direction",
    )
    if not measured.present:
        return measured
    confidence = shot.launch_angle_horizontal_confidence
    if shot.launch_angle_horizontal_source == "radar_low_coherence":
        reason = "horizontal launch is low-coherence (radar_low_coherence)"
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
    return collision_model.Input(value, collision_model.LABEL_MEASURED, source, frame)


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


def _collision_inputs(shot: Shot) -> dict:
    """The shot's own collision inputs, each labelled; measured path and attack come first."""
    path, path_source = displayed_club_path(shot)
    attack, attack_source = displayed_attack_angle(shot)
    launch_source = shot.launch_angle_horizontal_source
    if launch_source and shot.launch_angle_horizontal_status == "azimuth_uncalibrated":
        launch_source = f"{launch_source} (azimuth uncalibrated)"
    speed = shot.club_speed_mph
    return {
        "club_path": (
            collision_model.Input(
                path,
                collision_model.LABEL_MEASURED,
                path_source,
                _club_path_frame_of(shot, path_source),
            )
            if path is not None
            else _club_arc_input(shot, "club_path_deg", "no accepted club path")
        ),
        "attack_angle": (
            collision_model.Input(attack, collision_model.LABEL_MEASURED, attack_source)
            if attack is not None
            else _club_arc_input(shot, "attack_angle_deg", "no accepted attack angle")
        ),
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
    """The arc's low point (contact note only); None without camera club data."""
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
    if _finite(speed) and speed > 0:
        return collision_model.Input(
            float(speed), collision_model.LABEL_CALCULATED, "full ball speed"
        )
    full = shot.ball_speed_full if isinstance(shot.ball_speed_full, dict) else {}
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
    """The label of this shot's collision spin, "calculated" or "model_geometry", or None.

    A model-geometry spin feeds the flight model, labelled, but simulators do not get it.
    """
    metrics = (shot.collision_model or {}).get("metrics", {})
    status = metrics.get("spin_rate", {}).get("status")
    if shot.spin_rpm_collision is None or status not in MODEL_LABELS:
        return None
    return status


def _collision_spin_feeds(shot: Shot) -> bool:
    """Whether the collision spin is calculated (then --calculated-spin yields to it)."""
    return _collision_spin_status(shot) == "calculated"


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
    result = collision_model.evaluate(
        club=shot.club,
        handedness=server.golfer_handedness()["handed"],
        low_point_distance_mm=_low_point_input(shot),
        **_collision_inputs(shot),
    )
    metrics = result.metrics
    shot.collision_model = result.to_dict()
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
        if shot.launch_angle_horizontal_status == "azimuth_uncalibrated":
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


# Why the OPS club speed cannot be projected yet.
CLUB_SPEED_NOT_FULL_REASON = (
    "the OPS club speed is a percentile over 4-18 ms before impact, not one FFT-window "
    "reading, and the head's direction at that time needs the swing arc (E1) and a fixed "
    "head point (W6, W9)"
)


def _attach_full_speeds(shot: Shot) -> None:
    """Full ball speed by measured projection, full club speed and full smash.

    The canonical radial ball speed is unchanged; each full speed is its own field.
    """
    from openflight.full_speed import (  # noqa: PLC0415
        evaluate_full_ball_speed,
        evaluate_full_club_speed,
        evaluate_full_smash,
    )

    # this shot's own range and how sure it is
    calibration = server.shot_iwr_calibration(shot)

    def calibrated(name: str):
        try:
            return getattr(calibration, name, None)
        except ValueError:  # the radar height is unset until the rig sets it
            return None

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
        tee_side_offset_m=(server.setup_handoff_config.get("scene") or {}).get("tee_side_offset_m"),
    )
    # the flight model reads the value alone: calculated, or None
    shot.ball_speed_full_mph = (
        shot.ball_speed_full["value_mph"]
        if shot.ball_speed_full.get("status") == "calculated"
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


def _simulate_carry(shot: Shot, *, fusion: bool, enabled: bool) -> kiosk_extensions.CarryOutcome:
    """Every flight output, as measured and normalized, and the as-measured carry.

    As-measured flight uses the shot's air density, or ISA. A fusion unit builds carry only
    from measured inputs; an OPS-only unit that cannot simulate takes the table estimate.
    """
    flight = compute_flight(shot, substitutes=not fusion, enabled=enabled)
    shot.flight = flight.to_dict()
    conditions, trajectory = flight.conditions, flight.as_measured
    if trajectory is None and fusion:
        # nothing already on the shot stands in for the simulator
        shot.carry_spin_adjusted = None
        shot.carry_status = CARRY_NOT_MEASURED
        logger.info(
            "[SERVER] Carry not measured: %s",
            shot.flight["as_measured"]["metrics"]["carry"]["reason"],
        )
        return kiosk_extensions.CarryOutcome(source=None, table_reason=None)
    if trajectory is not None:
        shot.carry_spin_adjusted = trajectory.carry_yards
        if not fusion:
            shot.carry_status = CARRY_ESTIMATED
        elif flight.status == FLIGHT_STATUS_MODEL_GEOMETRY:
            shot.carry_status = CARRY_MODEL_GEOMETRY
        elif flight.status == CARRY_ESTIMATED:
            # an estimated input (the OPS line-of-sight ball speed): the flight's label
            shot.carry_status = CARRY_ESTIMATED
        else:
            # the flight's own label: a simulated carry is calculated
            shot.carry_status = CARRY_CALCULATED
        logger.info(
            "[SERVER] Ballistic carry: %.0f yds (spin: %.0f rpm, source: %s)",
            shot.carry_spin_adjusted,
            conditions.spin_rpm,
            conditions.spin_source,
        )
        return kiosk_extensions.CarryOutcome(source="ballistic", table_reason=None)
    reason = (
        "ballistics disabled"
        if not enabled
        else ("no launch angle" if conditions is None else "supplied air density invalid")
    )
    return kiosk_extensions.CarryOutcome(source=None, table_reason=reason)


class ShotModelsExtension(kiosk_extensions.KioskExtension):
    """The club arc, collision model, full speeds and flight model."""

    name = "shot models"

    def on_club_delivery(self, shot, delivery) -> None:
        _attach_club_arc(shot, delivery)

    def after_enrichment(self, shot) -> None:
        _attach_collision_model(shot)

    def model_spin(self, shot):
        if not _collision_spin_feeds(shot):
            return None
        return shot.spin_rpm_collision, _collision_spin_method(shot)

    def after_speed_contract(self, shot) -> None:
        _attach_full_speeds(shot)
        # the rigid-body face and the smash index need the full ball speed, known only now
        if shot.collision_model is not None and _finite(shot.ball_speed_full_mph):
            _attach_collision_model(shot)

    def simulate_carry(self, shot, *, fusion: bool, enabled: bool):
        return _simulate_carry(shot, fusion=fusion, enabled=enabled)


extension = kiosk_extensions.register(ShotModelsExtension())

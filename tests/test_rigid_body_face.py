"""Face angle and dynamic loft from the ball and the club's direction (rigid-body relation).

The face normal n lies in the plane of the ball's launch direction b and the club's
direction v, on the far side of b from v, at an angle theta from b with

    ball speed x sin(theta) = c x spin,   c = I / (m r)

the tangential-impulse identity m v_t r = I omega for a rigid ball (Cross & Nathan
2007, Am. J. Phys. 75, 658; Penner 2001, Am. J. Phys. 69, 563). The known answers
below are built forward from a chosen face, so the inverse is checked, not restated.
"""

from __future__ import annotations

import math

import pytest

from openflight import collision_model as cm, metric_definitions as md
from openflight.clubs import ClubType
from tests.tm_owner_export import number, owner_shots, rms

M, C = cm.LABEL_MEASURED, cm.LABEL_CALCULATED
MPH = 0.44704


def _inp(value, label=M, source="test", frame="target_line", reason=None):
    return cm.Input(value=value, label=label, source=source, frame=frame, reason=reason)


def _forward(face, loft, path, attack, theta_deg, ball_speed_mph):
    """(launch direction, vertical launch, spin rpm) a delivery gives under the relation."""
    n = md.face_normal(loft, face)
    v = md.club_direction(attack, path)
    along = sum(p * q for p, q in zip(v, n))
    w = [p - along * q for p, q in zip(v, n)]
    length = math.sqrt(sum(p * p for p in w))
    w = [p / length for p in w]
    theta = math.radians(theta_deg)
    b = [math.cos(theta) * p + math.sin(theta) * q for p, q in zip(n, w)]
    launch_v = math.degrees(math.asin(b[2]))
    launch_h = math.degrees(math.atan2(b[1], b[0]))
    spin_rad_s = ball_speed_mph * MPH * math.sin(theta) / cm.RIGID_BODY_C_M
    return launch_h, launch_v, spin_rad_s * 60.0 / (2.0 * math.pi)


def _face(
    *,
    ball_speed=111.84681460272012,
    spin=5548.1370350518255,
    launch_h=0.0,
    launch_v=15.0,
    path=0.0,
    attack=0.0,
    labels=None,
    spin_source="camera spin",
    spin_reason=None,
):
    labels = labels or {}
    return cm.rigid_body_face(
        ball_speed_mph=_inp(ball_speed, labels.get("ball_speed", C), "full ball speed", None),
        spin_rpm=_inp(spin, labels.get("spin", M), spin_source, None, spin_reason),
        launch_horizontal=_inp(launch_h, labels.get("launch_h", M)),
        launch_vertical=_inp(launch_v, labels.get("launch_v", M), frame=None),
        club_path=_inp(path, labels.get("path", M)),
        attack_angle=_inp(attack, labels.get("attack", M), frame=None),
    )


def test_the_constant_is_i_over_m_r_for_the_stated_ball():
    assert cm.RIGID_BODY_C_M == pytest.approx(7.5005e-3)
    assert cm.RIGID_BODY_C_M / cm.RIGID_BODY_BALL_RADIUS_M == pytest.approx(0.352, abs=5e-4)


def test_hand_computed_straight_strike():
    """Square face, level path: 50 m/s, theta 5 deg needs 5548.14 rpm; loft = 15 + 5."""
    face, loft = _face()

    assert face.value == pytest.approx(0.0, abs=1e-9)
    assert loft.value == pytest.approx(20.0, abs=1e-9)
    assert face.source == loft.source == cm.SOURCE_RIGID_BODY


@pytest.mark.parametrize(
    ("face", "loft", "path", "attack", "theta", "speed"),
    [
        (2.0, 20.0, -3.0, -4.0, 8.0, 120.0),
        (-3.0, 12.0, 4.0, 3.0, 4.0, 160.0),
        (0.5, 30.0, -1.0, -5.0, 12.0, 100.0),
        (-6.0, 45.0, 2.0, -6.0, 18.0, 80.0),
    ],
)
def test_a_forward_built_delivery_is_recovered(face, loft, path, attack, theta, speed):
    launch_h, launch_v, spin = _forward(face, loft, path, attack, theta, speed)

    got_face, got_loft = _face(
        ball_speed=speed, spin=spin, launch_h=launch_h, launch_v=launch_v, path=path, attack=attack
    )

    assert got_face.value == pytest.approx(face, abs=1e-9)
    assert got_loft.value == pytest.approx(loft, abs=1e-9)


def test_zero_spin_puts_the_face_normal_on_the_ball():
    face, loft = _face(spin=0.0, launch_h=1.5, launch_v=12.0, path=-2.0, attack=-3.0)

    assert face.value == pytest.approx(1.5)
    assert loft.value == pytest.approx(12.0)


def test_a_spin_too_high_for_the_ball_speed_is_not_measured():
    face, loft = _face(ball_speed=10.0, spin=20000.0)

    assert face.status == loft.status == cm.STATUS_NOT_MEASURED
    assert "spin" in face.reason


@pytest.mark.parametrize(
    ("kwargs", "word"),
    [
        ({"spin": None}, "spin rate"),
        ({"ball_speed": None}, "ball speed"),
        ({"path": None}, "club path"),
        ({"attack": None}, "attack angle"),
        ({"launch_h": None}, "launch direction"),
        ({"launch_v": None}, "vertical launch"),
        ({"ball_speed": 0.0}, "ball speed"),
        ({"spin": -100.0}, "spin rate"),
    ],
)
def test_a_missing_input_leaves_face_and_loft_not_measured_with_the_reason(kwargs, word):
    face, loft = _face(**kwargs)

    for metric in (face, loft):
        assert metric.value is None
        assert metric.status == cm.STATUS_NOT_MEASURED
        assert word in metric.reason


def test_measured_inputs_and_a_calculated_ball_speed_give_calculated():
    face, loft = _face()

    assert face.status == loft.status == cm.STATUS_CALCULATED
    assert face.frame == "target_line"


@pytest.mark.parametrize(
    ("labels", "status"),
    [
        ({"path": cm.LABEL_MODEL_GEOMETRY}, cm.STATUS_MODEL_GEOMETRY),
        ({"attack": cm.LABEL_MODEL_GEOMETRY}, cm.STATUS_MODEL_GEOMETRY),
        ({"spin": C}, cm.STATUS_CALCULATED),
        ({"spin": cm.LABEL_ESTIMATED}, cm.STATUS_ESTIMATED),
        ({"launch_h": cm.LABEL_ESTIMATED, "path": cm.LABEL_MODEL_GEOMETRY}, cm.STATUS_ESTIMATED),
    ],
)
def test_face_and_loft_carry_the_weakest_input_label(labels, status):
    face, loft = _face(labels=labels, spin_reason="weak spin")

    assert face.status == loft.status == status


def test_an_estimated_spin_names_its_reason():
    face, _loft = _face(labels={"spin": cm.LABEL_ESTIMATED}, spin_reason="low-confidence spin")

    assert face.reason == "low-confidence spin"


@pytest.mark.parametrize(
    "source", [cm.SOURCE_PENNER, "Penner 2001; club inputs measured", cm.SPIN_METHOD_MODEL_GEOMETRY]
)
def test_a_spin_built_from_face_and_loft_is_refused_as_circular(source):
    face, loft = _face(labels={"spin": C}, spin_source=source)

    for metric in (face, loft):
        assert metric.status == cm.STATUS_NOT_MEASURED
        assert "circular" in metric.reason


# ---- inside the collision model ------------------------------------------------------


def _evaluate(spin=None, ball_speed=None, *, club=ClubType.DRIVER, **delivery):
    values = {"path": 2.0, "attack": 3.0, "launch_h": 1.0, "launch_v": 13.0}
    values.update(delivery)
    return cm.evaluate(
        club=club,
        club_path=_inp(values["path"]),
        attack_angle=_inp(values["attack"], frame=None),
        launch_horizontal=_inp(values["launch_h"]),
        launch_vertical=_inp(values["launch_v"], frame=None),
        club_speed_mph=_inp(105.0, frame=None),
        ball_speed_mph=ball_speed,
        spin_rpm=spin,
    )


def test_a_measured_spin_builds_face_and_loft_by_the_rigid_body_relation():
    launch_h, launch_v, spin = _forward(-1.0, 14.0, 2.0, 3.0, 5.0, 155.0)

    result = _evaluate(
        _inp(spin, M, "camera spin", None),
        _inp(155.0, C, "full ball speed", None),
        launch_h=launch_h,
        launch_v=launch_v,
    )

    metrics = result.metrics
    assert result.face_method == cm.FACE_METHOD_RIGID_BODY
    assert metrics["face_angle"].value == pytest.approx(-1.0, abs=1e-9)
    assert metrics["dynamic_loft"].value == pytest.approx(14.0, abs=1e-9)
    assert metrics["face_angle"].source == cm.SOURCE_RIGID_BODY
    assert result.to_dict()["face_method"] == cm.FACE_METHOD_RIGID_BODY
    assert result.to_dict()["inputs"]["spin_rpm"]["value"] == pytest.approx(spin)


def test_spin_loft_face_to_path_and_tilt_consume_the_rigid_body_face():
    launch_h, launch_v, spin = _forward(-1.0, 14.0, 2.0, 3.0, 5.0, 155.0)
    metrics = _evaluate(
        _inp(spin, M, "camera spin", None),
        _inp(155.0, C, "full ball speed", None),
        launch_h=launch_h,
        launch_v=launch_v,
    ).metrics

    face, loft = metrics["face_angle"].value, metrics["dynamic_loft"].value
    assert metrics["spin_loft"].value == pytest.approx(md.spin_loft_deg(loft, face, 3.0, 2.0))
    assert metrics["face_to_path"].value == pytest.approx(md.face_to_path_deg(face, 2.0))
    assert metrics["d_plane_tilt"].value == pytest.approx(md.d_plane_tilt_deg(loft, face, 3.0, 2.0))


def test_without_a_spin_the_published_inversion_is_kept_and_says_why():
    result = _evaluate()

    face = result.metrics["face_angle"]
    assert result.face_method == cm.FACE_METHOD_INVERSION
    assert face.source == cm.SOURCE_INVERSION
    assert "spin_rpm" not in result.inputs and "ball_speed_mph" not in result.inputs
    assert "rigid-body relation not used" in face.note
    assert "spin" in face.note
    assert "rigid-body relation not used" in result.metrics["dynamic_loft"].note


def test_a_calculated_spin_keeps_the_published_inversion():
    result = _evaluate(
        _inp(3000.0, C, "empirical formula", None), _inp(150.0, C, "full ball speed", None)
    )

    assert result.face_method == cm.FACE_METHOD_INVERSION
    assert "not measured" in result.metrics["face_angle"].note
    assert "spin_rpm" not in result.inputs


def test_the_collision_models_own_spin_never_feeds_face_and_loft():
    """The Penner spin is built from face and loft; the guard keeps the inversion."""
    first = _evaluate()
    penner = first.metrics["spin_rate"]

    second = _evaluate(
        _inp(penner.value, C, cm.shot_spin_method(penner.status, [M]), None),
        _inp(150.0, C, "full ball speed", None),
    )

    assert second.face_method == cm.FACE_METHOD_INVERSION
    assert second.metrics["face_angle"].value == first.metrics["face_angle"].value
    assert "circular" in second.metrics["face_angle"].note


def test_a_measured_spin_without_a_full_ball_speed_keeps_the_inversion():
    result = _evaluate(
        _inp(2600.0, M, "camera spin", None), cm.Input(None, reason="full ball speed refused")
    )

    assert result.face_method == cm.FACE_METHOD_INVERSION
    assert "full ball speed refused" in result.metrics["face_angle"].note


def test_an_unknown_club_still_gets_a_rigid_body_face():
    launch_h, launch_v, spin = _forward(0.0, 25.0, 0.0, -4.0, 9.0, 110.0)

    result = _evaluate(
        _inp(spin, M, "camera spin", None),
        _inp(110.0, C, "full ball speed", None),
        club=ClubType.UNKNOWN,
        attack=-4.0,
        path=0.0,
        launch_h=launch_h,
        launch_v=launch_v,
    )

    assert result.metrics["dynamic_loft"].value == pytest.approx(25.0, abs=1e-9)


# ---- the owner's TM4 export ------------------------------------------------------------


def _owner_measured_spin_shots():
    shots = []
    for shot in owner_shots():
        if shot["Spin Rate Type"] != "Measured":
            continue
        columns = (
            "Ball Speed",
            "Spin Rate",
            "Launch Angle",
            "Launch Direction",
            "Club Path",
            "Attack Angle",
            "Face Angle",
            "Dyn. Loft",
        )
        if all(number(shot, column) is not None for column in columns):
            shots.append(shot)
    return shots


def test_the_owner_export_face_and_loft_are_reproduced():
    """The clean-room study reproduced TM4's face to 0.024 deg RMS (max 0.062) and loft
    to 0.004 deg RMS (max 0.016) on the measured-spin shots."""
    face_errors, loft_errors = [], []
    shots = _owner_measured_spin_shots()
    for shot in shots:
        face, loft = cm.rigid_body_face(
            ball_speed_mph=_inp(number(shot, "Ball Speed"), M, "TM4", None),
            spin_rpm=_inp(number(shot, "Spin Rate"), M, "TM4", None),
            launch_horizontal=_inp(number(shot, "Launch Direction")),
            launch_vertical=_inp(number(shot, "Launch Angle"), frame=None),
            club_path=_inp(number(shot, "Club Path")),
            attack_angle=_inp(number(shot, "Attack Angle"), frame=None),
        )
        face_errors.append(face.value - number(shot, "Face Angle"))
        loft_errors.append(loft.value - number(shot, "Dyn. Loft"))

    assert len(shots) == 21
    assert rms(face_errors) < 0.025
    assert max(map(abs, face_errors)) < 0.065
    assert rms(loft_errors) < 0.005
    assert max(map(abs, loft_errors)) < 0.02


def test_the_owner_export_spin_loft_face_to_path_and_tilt_follow():
    """The exact 3-D definitions on the rebuilt face and loft land where they land on TM4's
    stored face and loft; face to path lands on TM4's own column."""
    errors = {"spin_loft": [], "face_to_path": [], "d_plane_tilt": []}
    for shot in _owner_measured_spin_shots():
        path, attack = number(shot, "Club Path"), number(shot, "Attack Angle")
        metrics = cm.evaluate(
            club=ClubType.IRON_7,
            club_path=_inp(path),
            attack_angle=_inp(attack, frame=None),
            launch_horizontal=_inp(number(shot, "Launch Direction")),
            launch_vertical=_inp(number(shot, "Launch Angle"), frame=None),
            club_speed_mph=_inp(number(shot, "Club Speed"), frame=None),
            ball_speed_mph=_inp(number(shot, "Ball Speed"), M, "TM4", None),
            spin_rpm=_inp(number(shot, "Spin Rate"), M, "TM4", None),
        ).metrics
        face, loft = number(shot, "Face Angle"), number(shot, "Dyn. Loft")
        expected = {
            "spin_loft": md.spin_loft_deg(loft, face, attack, path),
            "face_to_path": number(shot, "Face To Path"),
            "d_plane_tilt": md.d_plane_tilt_deg(loft, face, attack, path),
        }
        for name, value in expected.items():
            errors[name].append(metrics[name].value - value)

    assert rms(errors["spin_loft"]) < 0.01
    assert rms(errors["face_to_path"]) < 0.025
    # the tilt magnifies the face residual by about 1 / sin(spin loft): 0.069 deg measured
    assert rms(errors["d_plane_tilt"]) < 0.08

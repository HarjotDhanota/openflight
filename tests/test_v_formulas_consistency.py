"""Cross-module consistency of the Part E formulas."""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import datetime

import pytest

from openflight import (
    collision_forward,
    collision_model as cm,
    flight_sim as ballistics,
    full_speed,
    metric_definitions as md,
)
from openflight.clubs import ClubType
from openflight.flight import AIR_DENSITY_NORMALIZED, compute_flight
from openflight.flight_sim import LaunchConditions, simulate
from openflight.launch_monitor import Shot

CLUBS = [club for club in ClubType if club is not ClubType.UNKNOWN]


def test_full_speed_referral_uses_the_flight_model_s_drag_constants():
    assert full_speed.CD_POLY is ballistics.CD_POLY
    assert full_speed.BALL_AREA_M2 == ballistics.BALL_AREA_M2
    assert full_speed.BALL_MASS_KG == ballistics.BALL_MASS_KG
    assert full_speed.AIR_DENSITY_STD == ballistics.AIR_DENSITY_STD
    for sp in (*full_speed.DRAG_SPIN_PARAMETER_BAND, 0.2, 0.35):
        assert full_speed._cd(sp) == pytest.approx(ballistics._cd(sp), abs=1e-12)
    # the referral's deceleration is the flight model's drag at the band's centre, plus
    # gravity's share along the path
    v, launch = 60.0, 14.0
    q = 0.5 * ballistics.AIR_DENSITY_STD * v * v * ballistics.BALL_AREA_M2 / ballistics.BALL_MASS_KG
    assert full_speed.drag_deceleration_mps2(v, launch) == pytest.approx(
        full_speed.CD_CENTRAL * q + ballistics.GRAVITY * math.sin(math.radians(launch))
    )


@pytest.mark.parametrize("club", CLUBS, ids=lambda club: club.name)
@pytest.mark.parametrize(
    "path, attack, face, loft",
    [
        (3.0, -4.5, 1.0, 21.0),
        (-2.0, 3.0, 1.0, 13.0),
        (0.0, 0.0, 0.0, 30.0),
        (-6.0, -8.0, 4.0, 50.0),
    ],
)
def test_the_collision_model_inverts_its_own_forward_launch(club, path, attack, face, loft):
    weights = cm.weights_for_club(club)
    launch_h, launch_v = cm.forward_launch(
        face_angle_deg=face,
        dynamic_loft_deg=loft,
        club_path_deg=path,
        attack_angle_deg=attack,
        weights=weights,
    )
    back = cm.invert_d_plane(
        launch_horizontal_deg=launch_h,
        launch_vertical_deg=launch_v,
        club_path_deg=path,
        attack_angle_deg=attack,
        weights=weights,
    )
    assert back == pytest.approx((face, loft), abs=1e-9)
    impact = collision_forward.forward(
        club=club,
        club_speed_mph=90.0,
        attack_angle_deg=attack,
        club_path_deg=path,
        face_angle_deg=face,
        dynamic_loft_deg=loft,
        assume_centred=True,
    )
    result = cm.evaluate(
        club=club,
        club_path=cm.Input(path, frame="target_line"),
        attack_angle=cm.Input(attack),
        launch_horizontal=cm.Input(impact.launch_horizontal_deg, frame="target_line"),
        launch_vertical=cm.Input(impact.launch_vertical_deg),
        club_speed_mph=cm.Input(90.0),
    ).metrics
    assert result["face_angle"].value == pytest.approx(face, abs=1e-6)
    assert result["dynamic_loft"].value == pytest.approx(loft, abs=1e-6)
    # the metric definitions' spin loft and tilt are the ones the collision model reports
    assert result["spin_loft"].value == pytest.approx(md.spin_loft_deg(loft, face, attack, path))
    tilt = md.d_plane_tilt_deg(loft, face, attack, path)
    if tilt is None:
        assert not result["d_plane_tilt"].present
    else:
        assert result["d_plane_tilt"].value == pytest.approx(tilt)
    # face to path is face minus path, in the frame both angles share
    assert result["face_to_path"].value == pytest.approx(face - path, abs=1e-9)
    assert result["face_to_path"].frame == "target_line"


def test_spin_loft_matches_its_closed_form():
    loft, face, attack, path = 21.0, 1.0, -4.5, 3.0
    r = math.radians
    expected = math.degrees(
        math.acos(
            math.cos(r(attack)) * math.cos(r(loft)) * math.cos(r(face - path))
            + math.sin(r(attack)) * math.sin(r(loft))
        )
    )
    assert md.spin_loft_deg(loft, face, attack, path) == pytest.approx(expected, abs=1e-9)


@pytest.mark.parametrize("axis", [-10.0, -3.0, 3.0, 10.0])
@pytest.mark.parametrize("launch_h", [-4.0, 0.0, 4.0])
def test_curve_and_carry_side_follow_the_spin_axis(axis, launch_h):
    traj = simulate(LaunchConditions(150.0, 12.0, launch_h, 2800.0, axis, "measured"))
    curve = md.curve(traj.carry_yards, traj.carry_side_yards, launch_h)
    straight = simulate(LaunchConditions(150.0, 12.0, launch_h, 2800.0, 0.0, "measured"))
    assert math.copysign(1, curve) == math.copysign(1, axis)
    # the curve is the side gained over the straight shot launched the same way
    assert md.curve(straight.carry_yards, straight.carry_side_yards, launch_h) == pytest.approx(
        0.0, abs=1e-6
    )
    if launch_h == 0.0:
        assert curve == pytest.approx(traj.carry_side_yards, abs=1e-9)


def _fusion_shot(**overrides):
    values = dict(
        ball_speed_mph=150.0,
        timestamp=datetime(2026, 10, 13),
        club=ClubType.DRIVER,
        launch_angle_vertical=12.0,
        launch_angle_vertical_source="radar",
        launch_angle_horizontal=1.0,
        launch_angle_horizontal_source="radar",
        spin_rpm=2700.0,
        spin_confidence=0.9,
        spin_source="measured",
        spin_axis_deg=2.0,
        spin_axis_source=cm.SPIN_AXIS_SOURCE_MODEL,
        ball_speed_contract="full",
    )
    values.update(overrides)
    return Shot(**values)


def test_raw_and_normalized_differ_only_in_the_air():
    payload = compute_flight(_fusion_shot(air_density_kg_m3=AIR_DENSITY_NORMALIZED)).to_dict()
    raw, norm = payload["as_measured"], payload["normalized"]
    assert raw["launch"] == norm["launch"]
    for name, metric in raw["metrics"].items():
        assert metric["value"] == pytest.approx(norm["metrics"][name]["value"]), name
        assert metric["status"] == norm["metrics"][name]["status"]
    thin = compute_flight(_fusion_shot(air_density_kg_m3=1.0)).to_dict()
    assert thin["as_measured"]["launch"] == thin["normalized"]["launch"]
    assert (
        thin["as_measured"]["metrics"]["carry"]["value"]
        > thin["normalized"]["metrics"]["carry"]["value"]
    )


def test_penner_spin_matches_the_paper_s_equations():
    v, theta = 45.0, 12.0
    m, r, big_m = 0.0459, 0.0427 / 2, 0.200
    t = math.radians(theta)
    vp = v * math.sin(t) / (1 + m / big_m + 2.5)
    omega = m * vp * r / (0.4 * m * r * r)
    impact = cm.penner_impact(v, theta)
    assert impact.spin_rad_s == pytest.approx(omega)
    assert impact.spin_rpm == pytest.approx(omega * 60 / (2 * math.pi))
    e = 0.86 - 0.0029 * v * math.cos(t)
    assert impact.normal_speed_m_s == pytest.approx((1 + e) * v * math.cos(t) / (1 + m / big_m))
    spin = cm.calculated_spin(
        club_speed=cm.Input(v / 0.44704), spin_loft=cm.Metric(theta, "calculated", None, "deg")
    )
    assert spin.value == pytest.approx(impact.spin_rpm)


_RANK = {"model_geometry": 0, "calculated": 1, "measured": 2}
# each output's own inputs (launch angles and club speed are measured here); the
# D-plane inversion takes face and loft together from all four angles
_DEPENDS = {
    "face_angle": ("path", "attack"),
    "face_to_path": ("path", "attack"),
    "dynamic_loft": ("path", "attack"),
    "spin_loft": ("path", "attack"),
    "d_plane_tilt": ("path", "attack"),
    "spin_rate": ("path", "attack"),
    "spin_axis": ("path", "attack"),
}


@pytest.mark.parametrize("path_label", ["measured", "calculated", "model_geometry"])
@pytest.mark.parametrize("attack_label", ["measured", "calculated", "model_geometry"])
def test_collision_outputs_take_their_own_weakest_input_label(path_label, attack_label):
    labels = {"path": path_label, "attack": attack_label}
    metrics = cm.evaluate(
        club=ClubType.IRON_7,
        club_path=cm.Input(2.0, path_label, frame="target_line"),
        attack_angle=cm.Input(-4.0, attack_label),
        launch_horizontal=cm.Input(1.0, frame="target_line"),
        launch_vertical=cm.Input(17.0),
        club_speed_mph=cm.Input(87.0),
    ).metrics
    for name, inputs in _DEPENDS.items():
        weakest = min((labels[key] for key in inputs), key=_RANK.get)
        # a model's output is at best calculated
        expected = "model_geometry" if weakest == "model_geometry" else "calculated"
        assert metrics[name].status == expected, (name, labels)


def test_a_flight_on_model_geometry_spin_is_never_calculated():
    shot = _fusion_shot(
        spin_confidence=None,
        spin_source="model_geometry",
        spin_method=cm.SPIN_METHOD_MODEL_GEOMETRY,
    )
    result = compute_flight(shot)
    assert result.status == "model_geometry"
    shot = _fusion_shot(spin_axis_source="model geometry (D-plane tilt, ...)")
    assert compute_flight(shot).status == "model_geometry"
    shot = replace(_fusion_shot())
    assert compute_flight(shot).status == "calculated"

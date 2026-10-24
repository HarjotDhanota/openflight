"""The V-formulas audit's fixes (C1-C5), each test first."""

from __future__ import annotations

from datetime import datetime

import pytest

from openflight import (
    collision_model as cm,
    flight_sim as ballistics,
    shot_models,
)
from openflight.clubs import ClubType
from openflight.flight import compute_flight
from openflight.launch_monitor import Shot


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
        ball_speed_full_mph=155.0,
    )
    values.update(overrides)
    return Shot(**values)


# ---- C1: a flight on the OPS line-of-sight speed is estimated -------------------------

RADIAL_REASON = "ball speed is the OPS line-of-sight speed (full speed refused: {}), about 3-5% low"


def _radial_shot(**overrides):
    shot = _fusion_shot(ball_speed_full_mph=None, **overrides)
    shot.ball_speed_full = {
        "status": "not_measured",
        "value_mph": None,
        "reason": "the tee side offset (D36) is not measured by the setup pair",
    }
    return shot


def test_a_flight_on_the_radial_speed_is_estimated_with_the_reason():
    result = compute_flight(_radial_shot(), substitutes=False)
    payload = result.to_dict()

    assert result.status == "estimated"
    reason = RADIAL_REASON.format("the tee side offset (D36) is not measured by the setup pair")
    for block in ("as_measured", "normalized"):
        for name, metric in payload[block]["metrics"].items():
            assert metric["value"] is not None, (block, name)
            assert metric["status"] == "estimated", (block, name)
            assert metric["reason"] == reason, (block, name)
            assert metric["source"].startswith("estimated"), (block, name)


def test_a_radial_flight_with_no_full_speed_record_names_that():
    shot = _fusion_shot(ball_speed_full_mph=None)
    payload = compute_flight(shot, substitutes=False).to_dict()
    carry = payload["normalized"]["metrics"]["carry"]
    assert carry["status"] == "estimated"
    assert carry["reason"] == RADIAL_REASON.format("no full ball speed on the shot")


def test_a_flight_on_the_full_speed_stays_calculated():
    result = compute_flight(_fusion_shot(), substitutes=False)
    carry = result.to_dict()["as_measured"]["metrics"]["carry"]
    assert result.status == "calculated"
    assert carry["status"] == "calculated" and carry["reason"] is None


def test_a_full_speed_contract_is_not_a_radial_flight():
    shot = _fusion_shot(ball_speed_full_mph=None, ball_speed_contract="full")
    assert compute_flight(shot, substitutes=False).status == "calculated"


def test_estimated_is_weaker_than_model_geometry_in_the_flight():
    shot = _radial_shot(
        spin_confidence=None,
        spin_source="model_geometry",
        spin_method=cm.SPIN_METHOD_MODEL_GEOMETRY,
    )
    assert compute_flight(shot, substitutes=False).status == "estimated"


@pytest.fixture
def fusion_finalize(monkeypatch):
    from types import SimpleNamespace

    import openflight.server as server_module

    monkeypatch.setattr(server_module, "iwr6843_runtime", SimpleNamespace())
    monkeypatch.setattr(server_module, "ballistics_enabled", True)
    monkeypatch.setattr(server_module, "calculated_spin_enabled", False)
    monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
    monkeypatch.setattr(server_module.socketio, "emit", lambda *args, **kwargs: None)
    return server_module


def test_a_fusion_carry_on_the_radial_speed_is_estimated(fusion_finalize):
    shot = _fusion_shot(ball_speed_full_mph=None, mode="rolling-buffer")

    fusion_finalize._finalize_shot_detected(shot, emit_event="shot")

    assert shot.ball_speed_full["status"] == "not_measured"
    carry = shot.flight["as_measured"]["metrics"]["carry"]
    assert carry["status"] == "estimated"
    assert carry["reason"].startswith(
        "ball speed is the OPS line-of-sight speed (full speed refused: "
    )
    assert shot.carry_status == "estimated"
    assert shot.carry_spin_adjusted == pytest.approx(carry["value"])


# ---- C2: a low-coherence or low-confidence horizontal launch makes an estimate ----------

LOW_COHERENCE = "horizontal launch is low-coherence (radar_low_coherence)"
LOW_CONFIDENCE = "horizontal launch confidence 0.50 below 0.6"
_USES_LAUNCH_H = (
    "face_angle",
    "face_to_path",
    "spin_loft",
    "d_plane_tilt",
    "spin_rate",
    "spin_axis",
)


def _collision_shot(**overrides):
    """A 7-iron with a measured path, attack, vertical launch and club speed."""
    values = dict(
        ball_speed_mph=120.0,
        club_speed_mph=85.0,
        timestamp=datetime(2026, 10, 13),
        club=ClubType.IRON_7,
        launch_angle_horizontal=1.0,
        launch_angle_horizontal_source="radar",
        launch_angle_horizontal_confidence=0.9,
        launch_angle_horizontal_frame="target_line",
        launch_angle_vertical=18.0,
        launch_angle_vertical_source="radar",
        experimental_fused_status="chained_high",
        experimental_fused_club_path_deg=2.0,
        experimental_fused_club_path_frame="target_line",
        experimental_fused_attack_angle_deg=-4.0,
        experimental_fused_club_path_confidence="high",
        experimental_fused_attack_angle_confidence="high",
        ball_speed_contract="full",
        mode="rolling-buffer",
    )
    values.update(overrides)
    return Shot(**values)


def _attach_collision(shot):
    shot_models._attach_collision_model(shot)  # pylint: disable=protected-access
    return shot.collision_model["metrics"]


@pytest.mark.parametrize(
    "overrides, reason",
    [
        ({"launch_angle_horizontal_source": "radar_low_coherence"}, LOW_COHERENCE),
        ({"launch_angle_horizontal_confidence": 0.5}, LOW_CONFIDENCE),
    ],
)
def test_outputs_built_on_a_weak_horizontal_launch_are_estimated(overrides, reason):
    shot = _collision_shot(**overrides)
    metrics = _attach_collision(shot)

    for name in _USES_LAUNCH_H:
        assert metrics[name]["value"] is not None, name
        assert metrics[name]["status"] == "estimated", name
        assert reason in metrics[name]["reason"], name
    # the D-plane inversion takes the loft from the horizontal launch too
    assert metrics["dynamic_loft"]["status"] == "estimated"
    assert shot.collision_model["inputs"]["launch_horizontal"]["label"] == "estimated"
    # the spin axis is no longer withheld: estimated, with the reason
    assert shot.spin_axis_deg == pytest.approx(metrics["spin_axis"]["value"], abs=0.05)
    assert shot.spin_axis_source.startswith("estimated")
    assert reason in shot.spin_axis_source
    assert shot.spin_source == "estimated"
    assert shot.spin_rpm == pytest.approx(metrics["spin_rate"]["value"], abs=0.5)
    assert "_estimated" in shot.experimental_face_angle_status


def test_a_good_horizontal_launch_stays_calculated():
    metrics = _attach_collision(_collision_shot())
    for name in _USES_LAUNCH_H:
        assert metrics[name]["status"] == "calculated", name
        assert metrics[name]["reason"] is None, name


def test_the_estimated_values_match_the_calculated_ones():
    good = _attach_collision(_collision_shot())
    weak = _attach_collision(_collision_shot(launch_angle_horizontal_source="radar_low_coherence"))
    for name in _USES_LAUNCH_H:
        assert weak[name]["value"] == pytest.approx(good[name]["value"]), name


def test_a_flight_on_an_estimated_spin_and_axis_is_estimated():
    shot = _collision_shot(launch_angle_horizontal_confidence=0.5)
    _attach_collision(shot)

    result = compute_flight(shot, substitutes=False)
    carry = result.to_dict()["normalized"]["metrics"]["carry"]

    assert result.status == "estimated"
    assert carry["status"] == "estimated"
    assert LOW_CONFIDENCE in carry["reason"]


def test_simulators_do_not_get_an_estimated_spin_axis():
    from openflight.sim.resolver import _spin_axis_provenance  # noqa: PLC2701

    shot = _collision_shot(launch_angle_horizontal_source="radar_low_coherence")
    _attach_collision(shot)
    assert _spin_axis_provenance(shot) is None


def test_estimated_is_the_weakest_collision_input_label():
    metrics = cm.evaluate(
        club=ClubType.IRON_7,
        club_path=cm.Input(2.0, cm.LABEL_MODEL_GEOMETRY, frame="target_line"),
        attack_angle=cm.Input(-4.0),
        launch_horizontal=cm.Input(
            1.0, cm.LABEL_ESTIMATED, frame="target_line", reason=LOW_COHERENCE
        ),
        launch_vertical=cm.Input(17.0),
        club_speed_mph=cm.Input(87.0),
    ).metrics
    assert metrics["face_angle"].status == "estimated"
    assert metrics["spin_axis"].status == "estimated"
    assert metrics["dynamic_loft"].status == "estimated"


# ---- C4: spin decays by Ferguson 2022's own moment model ------------------------------


def test_the_spin_moment_coefficient_is_ferguson_s():
    # Ferguson, McNally & McPhee 2022, Eq. (4) C_M = g S, with g = 0.01 (the last of
    # "{0.1304, 0.9287, -0.8259, 0.0504, 1.2031, -1.1490, 0.01}"). E8a's refit
    # (flight-fit-v1) kept g inside their 0.010-0.015 bounds and landed on 0.010.
    assert ballistics.CM_PER_SP == 0.01
    assert not hasattr(ballistics, "SPIN_DECAY_RATE")


def test_the_spin_deceleration_is_the_moment_over_the_ball_s_inertia():
    import math

    r, m = ballistics.BALL_RADIUS_M, ballistics.BALL_MASS_KG
    omega, v, rho = 280.0, 70.0, 1.2
    torque = 0.01 * (r * omega / v) * 0.5 * rho * v * v * math.pi * r * r * 2 * r
    expected = -torque / (0.4 * m * r * r)
    assert ballistics.spin_acceleration(omega, v, rho) == pytest.approx(expected)
    # the same law as Penner 2001 Eq. (16), alpha = -(0.00002) omega v / r (from Smits
    # & Smith 1994), to 0.5 % at Penner's 1.204 kg/m3
    penner = -0.00002 * omega * v / r
    assert ballistics.spin_acceleration(omega, v, 1.204) == pytest.approx(penner, rel=0.005)


def test_the_flight_s_spin_follows_the_moment_model():
    import math

    traj = ballistics.simulate(
        ballistics.LaunchConditions(160.0, 11.0, 0.0, 3000.0, 0.0, "measured"),
        air_density=1.2,
    )
    # d(omega)/dt = -k omega v  =>  ln(omega0 / omega) = k x path length
    k = 0.01 * 1.2 * math.pi * ballistics.BALL_RADIUS_M**2 / (0.4 * ballistics.BALL_MASS_KG)
    points = traj.points
    path = (
        sum(math.dist((a.x, a.y, a.z), (b.x, b.y, b.z)) for a, b in zip(points, points[1:]))
        / ballistics.M_TO_YD
    )
    assert math.log(3000.0 / points[-1].spin_rpm) == pytest.approx(k * path, rel=0.02)


# ---- C5: an explicit zero yaw is the target line; only an unset one is the boresight ----


@pytest.mark.parametrize("offset", [0.0, -0.0, 3.0, -3.0])
def test_an_explicit_offset_even_zero_is_the_target_line_frame(offset):
    from openflight.horizontal_frame import TARGET_LINE, frame_for

    assert frame_for(offset) == TARGET_LINE


def test_an_unset_offset_is_the_boresight_frame():
    from openflight.horizontal_frame import UNIT_BORESIGHT, frame_for

    assert frame_for(None) == UNIT_BORESIGHT


def test_the_kiosk_s_frame_for_a_zero_yaw_is_the_target_line(monkeypatch):
    import openflight.server as server_module

    monkeypatch.setattr(server_module, "horizontal_offset_deg", 0.0)
    assert server_module._horizontal_frame() == "target_line"
    monkeypatch.setattr(server_module, "horizontal_offset_deg", None)
    assert server_module._horizontal_frame() == "unit_boresight"


@pytest.mark.parametrize("offset, frame", [(0.0, "target_line"), (None, "unit_boresight")])
def test_the_club_arc_keeps_a_zero_offset_apart_from_none(offset, frame):
    from openflight.club_arc import club_delivery_payload

    delivery = {"status": "rejected_low_path_consensus", "head_positions": []}
    if offset is not None:
        delivery["horizontal_offset_deg"] = offset
    payload = club_delivery_payload(delivery, club=ClubType.IRON_7, handedness="RH")
    assert payload["arc"]["frame"] == frame


@pytest.mark.parametrize("offset, frame", [(0.0, "target_line"), (None, "unit_boresight")])
def test_the_camera_club_path_keeps_a_zero_offset_apart_from_none(offset, frame):
    from openflight.camera.club_delivery import ChainedDelivery, _in_output_frame

    delivery = _in_output_frame(ChainedDelivery(status="chained_high", club_path_deg=1.5), offset)
    assert delivery.club_path_frame == frame
    assert delivery.club_path_deg == 1.5


def test_the_camera_geometry_contract_carries_an_unset_offset_as_none():
    import dataclasses

    from openflight.camera.geometry_contract import EffectiveCameraGeometryInputs

    fields = {field.name for field in dataclasses.fields(EffectiveCameraGeometryInputs)}
    assert "ball_horizontal_output_offset_deg" in fields
    base = dict(
        camera_height_m=0.095,
        radar_height_m=0.044,
        tee_slant_range_m=2.1,
        ball_height_m=0.021,
        camera_lateral_offset_m=0.0,
        camera_forward_offset_m=0.0,
        image_width_px=320,
        image_height_px=200,
        horizontal_pixel_sign=1.0,
        roll_correction_deg=0.0,
        ball_diameter_m=0.0427,
        ball_lateral_offset_m=0.0,
    )
    base = {key: value for key, value in base.items() if key in fields}
    unset = EffectiveCameraGeometryInputs(ball_horizontal_output_offset_deg=None, **base)
    zero = EffectiveCameraGeometryInputs(ball_horizontal_output_offset_deg=0.0, **base)
    assert unset.ball_geometry().horizontal_offset_deg is None
    assert zero.ball_geometry().horizontal_offset_deg == 0.0
    assert unset.delivery_geometry().horizontal_offset_deg is None


# ---- C6: simulators on a fusion unit never get an estimated value (D31) -----------------


def _sim_shot(**overrides):
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
        carry_spin_adjusted=250.0,
        carry_status="calculated",
    )
    values.update(overrides)
    return Shot(**values)


def test_a_fusion_unit_does_not_send_an_estimated_carry():
    from openflight.sim.resolver import resolve_shot
    from openflight.sim.types import PlayerState

    resolved = resolve_shot(_sim_shot(carry_status="estimated"), PlayerState(), substitutes=False)
    assert resolved.carry_yards is None
    assert "carry" not in resolved.provenance
    # a calculated carry still goes
    sent = resolve_shot(_sim_shot(), PlayerState(), substitutes=False)
    assert sent.carry_yards == pytest.approx(250.0)


def test_the_estimated_switch_sends_it_labelled(monkeypatch):
    from openflight.sim import resolver
    from openflight.sim.types import PlayerState

    monkeypatch.setattr(resolver, "SIMULATORS_ACCEPT_ESTIMATED", True)
    resolved = resolver.resolve_shot(
        _sim_shot(carry_status="estimated"), PlayerState(), substitutes=False
    )
    assert resolved.carry_yards == pytest.approx(250.0)
    assert resolved.provenance["carry"] == "estimated"


def test_an_ops_only_estimated_carry_is_still_sent_labelled():
    from openflight.sim.resolver import resolve_shot
    from openflight.sim.types import PlayerState

    resolved = resolve_shot(_sim_shot(carry_status="estimated"), PlayerState(), substitutes=True)
    assert resolved.carry_yards == pytest.approx(250.0)
    assert resolved.provenance["carry"] == "estimated"


def test_a_fusion_shot_with_only_an_estimated_spin_axis_is_refused_naming_it():
    from openflight.sim.resolver import resolve_shot
    from openflight.sim.types import PlayerState, UnmeasuredShotError

    shot = _collision_shot(
        launch_angle_horizontal_confidence=0.5, spin_rpm=7000.0, spin_confidence=0.9
    )
    _attach_collision(shot)
    assert shot.spin_axis_source.startswith("estimated")

    with pytest.raises(UnmeasuredShotError) as refused:
        resolve_shot(shot, PlayerState(), substitutes=False)
    assert refused.value.missing == ["spin axis"]
    assert "estimated (horizontal launch confidence 0.50 below 0.6)" in str(refused.value)


def test_a_fusion_shot_with_only_an_estimated_spin_is_refused_naming_it():
    from openflight.sim.resolver import resolve_shot
    from openflight.sim.types import PlayerState, UnmeasuredShotError

    shot = _collision_shot(launch_angle_horizontal_source="radar_low_coherence")
    _attach_collision(shot)
    assert shot.spin_source == "estimated"

    with pytest.raises(UnmeasuredShotError) as refused:
        resolve_shot(shot, PlayerState(), substitutes=False)
    message = str(refused.value)
    assert "spin" in refused.value.missing
    assert "estimated (horizontal launch is low-coherence (radar_low_coherence))" in message


# ---- C7: the normalized reference is TrackMan's 77 F, sea level, with a named humidity ----


def test_buck_1981_saturation_vapour_pressure():
    import math

    from openflight.flight import saturation_vapour_pressure_pa

    # Buck 1981: e_s = 6.1121 exp(17.502 T / (240.97 + T)) hPa, T in deg C
    assert saturation_vapour_pressure_pa(25.0) == pytest.approx(
        611.21 * math.exp(17.502 * 25.0 / (240.97 + 25.0))
    )
    assert saturation_vapour_pressure_pa(25.0) == pytest.approx(3167.0, abs=5.0)


def test_humid_air_density_from_temperature_pressure_and_humidity():
    from openflight.flight import humid_air_density_kg_m3

    # dry: the ideal gas, 287.05 J/(kg K)
    assert humid_air_density_kg_m3(25.0, 101325.0, 0.0) == pytest.approx(
        101325.0 / (287.05 * 298.15)
    )
    # 50 %: the owner's August value
    assert humid_air_density_kg_m3(25.0, 101325.0, 50.0) == pytest.approx(1.1769, abs=5e-5)
    # moist air is lighter
    assert humid_air_density_kg_m3(25.0, 101325.0, 100.0) < humid_air_density_kg_m3(
        25.0, 101325.0, 50.0
    )


def test_the_normalized_reference_is_named_and_computed_not_typed():
    from openflight import flight

    assert flight.NORMALIZED_TEMPERATURE_C == 25.0
    assert flight.NORMALIZED_PRESSURE_PA == 101325.0
    assert flight.NORMALIZED_REFERENCE_RH_PCT == 50.0
    assert flight.AIR_DENSITY_NORMALIZED == flight.humid_air_density_kg_m3(25.0, 101325.0, 50.0)


def test_every_normalized_output_names_its_reference():
    payload = compute_flight(_fusion_shot(), substitutes=False).to_dict()
    for name, metric in payload["normalized"]["metrics"].items():
        if metric["value"] is None:
            continue
        for part in ("25.0 °C (77 °F)", "1013.25 hPa", "RH 50 %", "1.1769 kg/m3"):
            assert part in metric["source"], (name, part)


def test_the_trackman_export_regression_flies_the_normalized_reference():
    import sys
    from pathlib import Path

    from openflight.flight import AIR_DENSITY_NORMALIZED

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "analysis"))
    import validate_ballistics  # noqa: PLC0415

    assert validate_ballistics.TM_FLAT_AIR_DENSITY == AIR_DENSITY_NORMALIZED


# ---- C8: the simulator provenance never says "measured" for a kiosk-computed value ------


@pytest.mark.parametrize(
    "carry_status, expected", [("calculated", "calculated"), ("measured", "calculated")]
)
def test_a_fusion_carry_is_at_most_calculated(carry_status, expected):
    from openflight.sim.resolver import resolve_shot
    from openflight.sim.types import PlayerState

    # flown on a measured spin, but a simulated carry is a model's output
    resolved = resolve_shot(_sim_shot(carry_status=carry_status), PlayerState(), substitutes=False)
    assert resolved.provenance["carry"] == expected


def test_a_model_geometry_fusion_carry_keeps_its_label(monkeypatch):
    from openflight.sim import resolver
    from openflight.sim.types import PlayerState

    monkeypatch.setattr(resolver, "SIMULATORS_ACCEPT_MODEL_GEOMETRY", True)
    resolved = resolver.resolve_shot(
        _sim_shot(carry_status="model_geometry"), PlayerState(), substitutes=False
    )
    assert resolved.provenance["carry"] == "model_geometry"


def test_an_ops_only_carry_stays_estimated():
    from openflight.sim.resolver import resolve_shot
    from openflight.sim.types import PlayerState

    resolved = resolve_shot(_sim_shot(carry_status="estimated"), PlayerState(), substitutes=True)
    assert resolved.provenance["carry"] == "estimated"


def test_table_filled_launch_angles_are_estimated():
    from openflight.sim.resolver import resolve_shot
    from openflight.sim.types import PlayerState

    shot = _sim_shot(
        launch_angle_vertical_source="estimated",
        launch_angle_horizontal=0.0,
        launch_angle_horizontal_source="estimated",
        carry_status="estimated",
    )
    resolved = resolve_shot(shot, PlayerState(), substitutes=True)
    assert resolved.vla == 12.0 and resolved.hla == 0.0  # the wire values are unchanged
    assert resolved.provenance["vla"] == "estimated"
    assert resolved.provenance["hla"] == "estimated"
    measured = resolve_shot(_sim_shot(), PlayerState(), substitutes=True)
    assert measured.provenance["vla"] == "measured"
    assert measured.provenance["hla"] == "measured"


def test_the_ops_only_server_path_fills_launch_and_labels_it_estimated(monkeypatch):
    import openflight.server as server_module
    from openflight.sim.resolver import resolve_shot
    from openflight.sim.types import PlayerState

    monkeypatch.setattr(server_module, "_fusion_unit", lambda: False)
    shot = Shot(ball_speed_mph=140.0, timestamp=datetime(2026, 10, 13), club=ClubType.IRON_7)
    server_module._ensure_user_facing_launch_angles(shot)  # pylint: disable=protected-access
    resolved = resolve_shot(shot, PlayerState(), substitutes=True)
    assert resolved.provenance["vla"] == "estimated"
    assert resolved.provenance["hla"] == "estimated"


def test_a_spin_axis_is_never_measured():
    from openflight.sim.resolver import resolve_shot
    from openflight.sim.types import PlayerState

    # no sensor measures the spin axis: an unlabelled one is the kiosk's computation
    resolved = resolve_shot(_sim_shot(spin_axis_source=None), PlayerState(), substitutes=True)
    assert resolved.provenance["spin_axis"] == "calculated"
    assert resolved.provenance["back_spin"] == "calculated"

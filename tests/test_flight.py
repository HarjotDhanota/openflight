"""E4: every flight output, labelled, as measured and normalized (Part E, Tier 6).

Carry, carry side, total, total side, apex, landing angle, hang time and curve,
each with a status, a source, a range and a reason. Nothing is filled in on a
fusion unit: without spin or spin axis there is no flight. The as-measured
flight uses the shot's own air density when one is supplied (the owner's hook)
and ISA otherwise, saying which; the normalized flight is TrackMan's
"no wind, 0 ft altitude, 77 F". Launch values are the same in both.
"""

import math
from datetime import datetime
from types import SimpleNamespace

import pytest

from openflight import metric_definitions, server as server_module
from openflight.ballistics import (
    AIR_DENSITY_STD,
    CLUB_TYPICAL_SPIN_RPM,
    LaunchConditions,
    resolve_launch,
    simulate,
)
from openflight.clubs import ClubType, physics as club_physics
from openflight.flight import (
    AIR_DENSITY_NORMALIZED,
    FLIGHT_METRICS,
    compute_flight,
)
from openflight.launch_monitor import Shot
from openflight.sim import resolver
from openflight.sim.resolver import resolve_shot
from openflight.sim.types import PlayerState

UNITS = {
    "carry": "yd",
    "carry_side": "yd",
    "total": "yd",
    "total_side": "yd",
    "apex": "yd",
    "landing_angle": "deg",
    "hang_time": "s",
    "curve": "yd",
}


def _shot(**overrides) -> Shot:
    values = dict(
        ball_speed_mph=150.0,
        timestamp=datetime(2026, 10, 12),
        club=ClubType.DRIVER,
        launch_angle_vertical=12.0,
        launch_angle_vertical_source="radar",
        launch_angle_horizontal=1.0,
        launch_angle_horizontal_source="radar",
        spin_rpm=2700.0,
        spin_confidence=0.9,
        spin_source="measured",
        spin_axis_deg=2.0,
        # a full ball speed: a flight on the line-of-sight speed is estimated (C1)
        ball_speed_contract="full",
    )
    values.update(overrides)
    return Shot(**values)


def _fusion(shot):
    return compute_flight(shot, substitutes=False).to_dict()


def test_flight_metrics_are_the_eight_tm4_outputs():
    assert set(FLIGHT_METRICS) == set(UNITS)


@pytest.mark.parametrize("block", ["as_measured", "normalized"])
def test_every_flight_output_is_present_with_its_label(block):
    flight = _fusion(_shot())
    metrics = flight[block]["metrics"]
    assert set(metrics) == set(UNITS)
    for name, metric in metrics.items():
        assert metric["unit"] == UNITS[name], name
        assert metric["status"] == "calculated", name
        assert metric["reason"] is None, name
        assert isinstance(metric["value"], float) and math.isfinite(metric["value"]), name
        assert metric["source"].startswith("calculated (model: "), name
        low, high = metric["range"]
        assert low <= metric["value"] <= high, name
    assert "Ferguson" in metrics["carry"]["source"]
    assert "USGA" in metrics["total"]["source"] and "R&A" in metrics["total"]["source"]
    assert "surface assumed: fairway, normal firmness" in metrics["total"]["source"]
    assert "surface assumed: fairway, normal firmness" in metrics["total_side"]["source"]
    assert "curve" in metrics["curve"]["source"]


def test_the_as_measured_flight_is_the_simulated_trajectory_at_isa():
    shot = _shot()
    metrics = _fusion(shot)["as_measured"]["metrics"]
    traj = simulate(resolve_launch(shot, substitutes=False), air_density=AIR_DENSITY_STD)
    assert metrics["carry"]["value"] == pytest.approx(traj.carry_yards)
    assert metrics["carry_side"]["value"] == pytest.approx(traj.carry_side_yards)
    assert metrics["apex"]["value"] == pytest.approx(traj.apex_yards)
    assert metrics["landing_angle"]["value"] == pytest.approx(traj.landing_angle_deg)
    assert metrics["hang_time"]["value"] == pytest.approx(traj.flight_time_s)
    assert metrics["total"]["value"] == pytest.approx(traj.total_yards)
    assert metrics["total_side"]["value"] == pytest.approx(traj.total_side_yards)


def test_curve_is_the_d37_definition_of_the_same_flight():
    shot = _shot(spin_axis_deg=-6.0, launch_angle_horizontal=3.0)
    metrics = _fusion(shot)["as_measured"]["metrics"]
    expected = metric_definitions.curve(
        metrics["carry"]["value"], metrics["carry_side"]["value"], 3.0
    )
    assert metrics["curve"]["value"] == pytest.approx(expected)
    assert metrics["curve"]["value"] < -1.0  # a draw axis curves left of the launch line


@pytest.mark.parametrize(
    "overrides, reason",
    [
        ({"spin_rpm": None, "spin_confidence": None, "spin_source": None}, "spin not measured"),
        ({"spin_confidence": 0.3}, "spin not measured"),
        ({"spin_axis_deg": None}, "spin axis not measured"),
        ({"spin_axis_deg": math.nan}, "spin axis not measured"),
        ({"launch_angle_horizontal": None}, "horizontal launch not measured"),
        ({"launch_angle_vertical": None}, "vertical launch not measured"),
    ],
)
def test_a_fusion_shot_missing_an_input_has_no_flight_and_says_why(overrides, reason):
    flight = _fusion(_shot(**overrides))
    for block in ("as_measured", "normalized"):
        for name, metric in flight[block]["metrics"].items():
            assert metric["status"] == "not_measured", (block, name)
            assert metric["value"] is None and metric["range"] is None, (block, name)
            assert reason in metric["reason"], (block, name)


def test_a_fusion_unit_never_uses_the_club_typical_spin():
    shot = _shot(spin_rpm=1500.0, spin_confidence=0.3)
    result = compute_flight(shot, substitutes=False)
    assert result.conditions is None and result.as_measured is None


def test_a_calculated_spin_and_axis_flow_through_under_their_labels():
    shot = _shot(spin_rpm=2600.0, spin_confidence=None, spin_source="calculated")
    shot.spin_axis_source = "calculated (model: D-plane tilt)"
    flight = _fusion(shot)
    assert flight["inputs"]["spin"]["label"].startswith("calculated")
    assert flight["inputs"]["spin_axis"]["label"] == "calculated (model: D-plane tilt)"
    assert flight["as_measured"]["metrics"]["carry"]["status"] == "calculated"


def test_an_unlabelled_spin_axis_is_named_as_the_kiosk_proxy():
    flight = _fusion(_shot())
    assert "proxy" in flight["inputs"]["spin_axis"]["label"]


def test_an_ops_only_flight_is_an_estimate_naming_its_substitutes():
    shot = _shot(
        spin_rpm=None,
        spin_confidence=None,
        spin_source=None,
        spin_axis_deg=None,
        launch_angle_horizontal=None,
    )
    flight = compute_flight(shot, substitutes=True).to_dict()
    carry = flight["as_measured"]["metrics"]["carry"]
    assert carry["status"] == "estimated"
    assert carry["source"].startswith("estimated (model: ")
    assert flight["inputs"]["spin"]["value"] == CLUB_TYPICAL_SPIN_RPM[ClubType.DRIVER]
    assert "club-typical estimate (source: " in flight["inputs"]["spin"]["label"]
    assert "substituted" in flight["inputs"]["spin_axis"]["label"]
    assert "substituted" in flight["inputs"]["launch_horizontal"]["label"]


def test_the_flight_model_disabled_says_so():
    flight = compute_flight(_shot(), substitutes=False, enabled=False).to_dict()
    carry = flight["as_measured"]["metrics"]["carry"]
    assert carry["status"] == "not_measured" and "flight model disabled" in carry["reason"]


# --- raw vs normalized -------------------------------------------------------------


def test_raw_and_normalized_share_their_launch_and_differ_only_in_the_air():
    flight = _fusion(_shot())
    raw, norm = flight["as_measured"], flight["normalized"]
    assert raw["launch"] == norm["launch"]
    assert raw["air_density_kg_m3"] == pytest.approx(AIR_DENSITY_STD)
    assert raw["air_density_source"] == "ISA default"
    assert norm["air_density_kg_m3"] == pytest.approx(AIR_DENSITY_NORMALIZED)
    assert "sea level" in norm["air_density_source"] and "25" in norm["air_density_source"]
    # thinner normalized air: the same launch carries farther
    assert norm["metrics"]["carry"]["value"] > raw["metrics"]["carry"]["value"]


def test_the_normalized_density_is_sea_level_25c_at_the_reference_humidity():
    # 50 % RH (V-formulas C7); dry air would be 101325 / (287.05 x 298.15) = 1.1839
    assert AIR_DENSITY_NORMALIZED == pytest.approx(1.1769, abs=5e-5)


def test_a_supplied_density_is_used_and_labelled():
    shot = _shot(air_density_kg_m3=AIR_DENSITY_NORMALIZED)
    flight = _fusion(shot)
    raw, norm = flight["as_measured"], flight["normalized"]
    assert raw["air_density_source"] == "supplied"
    for name in FLIGHT_METRICS:
        assert raw["metrics"][name]["value"] == pytest.approx(norm["metrics"][name]["value"])


@pytest.mark.parametrize("bad", [math.nan, math.inf, 0.0, -1.0, 5.0])
def test_an_invalid_supplied_density_is_not_replaced_by_isa(bad):
    flight = _fusion(_shot(air_density_kg_m3=bad))
    for metric in flight["as_measured"]["metrics"].values():
        assert metric["status"] == "not_measured"
        assert "supplied air density" in metric["reason"]
    assert flight["normalized"]["metrics"]["carry"]["status"] == "calculated"


# --- inputs ------------------------------------------------------------------------


def test_the_full_ball_speed_is_preferred_and_recorded():
    shot = _shot()
    shot.ball_speed_full_mph = 155.0
    flight = _fusion(shot)
    assert flight["inputs"]["ball_speed"]["value"] == 155.0
    assert flight["inputs"]["ball_speed"]["label"].startswith("full")
    straight = _fusion(_shot(ball_speed_contract="radial"))
    assert straight["inputs"]["ball_speed"]["label"].startswith("radial")
    # the line-of-sight speed stands in for the full speed: an estimate (V-formulas C1)
    assert straight["as_measured"]["metrics"]["carry"]["status"] == "estimated"
    assert (
        flight["as_measured"]["metrics"]["carry"]["value"]
        > straight["as_measured"]["metrics"]["carry"]["value"]
    )


def test_a_full_ball_speed_contract_marks_the_shots_speed_full():
    flight = _fusion(_shot(ball_speed_contract="full"))
    assert flight["inputs"]["ball_speed"]["label"].startswith("full")
    assert flight["inputs"]["ball_speed"]["value"] == 150.0


def test_each_shot_flies_on_its_own_inputs():
    a = _fusion(_shot(spin_rpm=2200.0))["as_measured"]["metrics"]["carry"]["value"]
    b = _fusion(_shot(spin_rpm=3200.0))["as_measured"]["metrics"]["carry"]["value"]
    assert a != b


# --- ranges ------------------------------------------------------------------------


def test_the_range_propagates_the_stated_input_uncertainties():
    flight = _fusion(_shot())
    carry = flight["as_measured"]["metrics"]["carry"]
    low, high = carry["range"]
    assert 1.0 < high - low < 40.0
    assert "ball speed" in flight["range_basis"] and "spin" in flight["range_basis"]


def test_a_landing_outside_the_bounce_data_gives_a_total_not_measured():
    # a lob wedge lands steeper than the model's 50 deg data range
    shot = _shot(
        ball_speed_mph=70.0,
        club=ClubType.LW,
        launch_angle_vertical=45.0,
        spin_rpm=10000.0,
        spin_axis_deg=0.0,
        launch_angle_horizontal=0.0,
    )
    metrics = _fusion(shot)["as_measured"]["metrics"]
    assert metrics["landing_angle"]["value"] > 50.0
    assert metrics["carry"]["status"] == "calculated"
    for name in ("total", "total_side"):
        assert metrics[name]["status"] == "not_measured"
        assert "landing angle" in metrics[name]["reason"]


# --- one club-typical spin table ---------------------------------------------------


def test_there_is_one_club_typical_spin_table():
    assert resolver.SPIN_MODEL_RPM is club_physics.CLUB_TYPICAL_SPIN_RPM
    assert CLUB_TYPICAL_SPIN_RPM is club_physics.CLUB_TYPICAL_SPIN_RPM
    for club, physics in club_physics.CLUB_PHYSICS.items():
        assert club_physics.CLUB_TYPICAL_SPIN_RPM[club] == physics.typical_spin_rpm


def test_the_club_typical_spin_table_is_labelled_an_estimate_with_its_source():
    assert club_physics.CLUB_TYPICAL_SPIN_LABEL.startswith("club-typical estimate (source: ")


def test_the_simulator_and_the_kiosk_estimate_the_same_spin():
    shot = Shot(
        ball_speed_mph=140.0,
        timestamp=datetime(2026, 10, 12),
        club=ClubType.IRON_7,
        launch_angle_vertical=18.0,
    )
    simulated = resolve_shot(shot, PlayerState())
    kiosk = resolve_launch(shot, substitutes=True)
    assert simulated.total_spin_rpm == kiosk.spin_rpm == CLUB_TYPICAL_SPIN_RPM[ClubType.IRON_7]


# --- server and payload -------------------------------------------------------------


@pytest.fixture
def fusion_finalize(monkeypatch):
    monkeypatch.setattr(server_module, "iwr6843_runtime", SimpleNamespace())
    monkeypatch.setattr(server_module, "ballistics_enabled", True)
    monkeypatch.setattr(server_module, "calculated_spin_enabled", False)
    monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
    monkeypatch.setattr(server_module.socketio, "emit", lambda *args, **kwargs: None)

    # E3's full speed stands in as the shot's own ball speed: a flight on the OPS
    # line-of-sight speed is "estimated" (V-formulas C1), and these tests are about
    # the other inputs' labels
    def full_speed(shot):
        shot.ball_speed_full = {"status": "calculated", "value_mph": shot.ball_speed_mph}
        shot.ball_speed_full_mph = shot.ball_speed_mph

    monkeypatch.setattr(server_module, "_attach_full_speeds", full_speed)


def _finalize(shot):
    server_module._finalize_shot_detected(shot, emit_event="shot")  # pylint: disable=protected-access


def test_finalization_keeps_every_flight_output_and_sends_it(fusion_finalize):
    shot = _shot(mode="rolling-buffer")
    _finalize(shot)
    payload = server_module.shot_to_dict(shot)
    assert payload["flight"] is not None
    carry = payload["flight"]["as_measured"]["metrics"]["carry"]
    assert carry["status"] == "calculated"
    assert shot.carry_spin_adjusted == pytest.approx(carry["value"])
    assert set(payload["flight"]["normalized"]["metrics"]) == set(UNITS)


def test_finalization_flies_the_supplied_density(fusion_finalize):
    shot = _shot(mode="rolling-buffer", air_density_kg_m3=1.0)
    _finalize(shot)
    raw = shot.flight["as_measured"]
    assert raw["air_density_source"] == "supplied"
    expected = simulate(
        LaunchConditions(150.0, 12.0, 1.0, 2700.0, 2.0, "measured"), air_density=1.0
    ).carry_yards
    assert shot.carry_spin_adjusted == pytest.approx(expected)


def test_finalization_of_a_fusion_shot_without_spin_axis_reports_no_flight(fusion_finalize):
    shot = _shot(mode="rolling-buffer", spin_axis_deg=None)
    _finalize(shot)
    assert shot.carry_status == "not_measured" and shot.carry_spin_adjusted is None
    carry = shot.flight["as_measured"]["metrics"]["carry"]
    assert carry["status"] == "not_measured" and "spin axis not measured" in carry["reason"]


def test_a_mock_shot_has_no_flight(fusion_finalize):
    shot = _shot(mode="mock")
    _finalize(shot)
    assert shot.flight is None

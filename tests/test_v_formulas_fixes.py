"""The V-formulas audit's fixes (C1-C5), each test first."""

from __future__ import annotations

from datetime import datetime

import pytest

from openflight import ballistics, collision_model as cm, flight as flight_module
from openflight.ballistics import Trajectory, bounce_and_roll_yards
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
        spin_axis_source=cm.SPIN_AXIS_SOURCE_TILT,
        ball_speed_full_mph=155.0,
    )
    values.update(overrides)
    return Shot(**values)


# ---- C3: Eq. 1 never rolls the ball backwards --------------------------------------

CLAMP_NOTE = "roll clamped at 0: Eq. 1 is negative above 49.4°"


@pytest.mark.parametrize("angle", [49.5, 49.9, 50.0])
def test_roll_is_clamped_at_zero_where_eq_1_goes_negative(angle):
    assert 79.1 - 1.6 * angle < 0.0
    assert bounce_and_roll_yards(angle) == 0.0


def test_roll_below_the_zero_crossing_is_eq_1_unchanged():
    assert bounce_and_roll_yards(49.0) == pytest.approx(79.1 - 1.6 * 49.0)
    assert bounce_and_roll_yards(50.1) is None  # outside the data range, as before


def _landing_at(angle):
    return Trajectory(
        points=[],
        carry_yards=150.0,
        apex_yards=30.0,
        lateral_yards=3.0,
        flight_time_s=6.0,
        landing_speed_mph=50.0,
        landing_angle_deg=angle,
        downrange_yards=(150.0**2 - 9.0) ** 0.5,
        landing_heading_deg=2.0,
    )


def test_a_clamped_roll_rests_at_the_landing_point_and_says_so(monkeypatch):
    traj = _landing_at(49.8)
    assert traj.total_yards == pytest.approx(150.0)
    assert traj.total_side_yards == pytest.approx(3.0)
    monkeypatch.setattr(flight_module, "simulate", lambda *args, **kwargs: _landing_at(49.8))

    payload = compute_flight(_fusion_shot(), substitutes=False).to_dict()

    for block in ("as_measured", "normalized"):
        metrics = payload[block]["metrics"]
        for name in ("total", "total_side"):
            assert metrics[name]["value"] is not None
            assert metrics[name]["note"] == CLAMP_NOTE
        assert metrics["carry"].get("note") is None


def test_an_unclamped_roll_has_no_note(monkeypatch):
    monkeypatch.setattr(flight_module, "simulate", lambda *args, **kwargs: _landing_at(40.0))
    payload = compute_flight(_fusion_shot(), substitutes=False).to_dict()
    assert payload["normalized"]["metrics"]["total"].get("note") is None


def test_the_clamp_constant_is_eq_1_s_zero_crossing():
    model = ballistics.USGA_RA_2021_BOUNCE_MODEL
    assert -model.intercept_yards / model.slope_yards_per_deg == pytest.approx(49.4375)


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

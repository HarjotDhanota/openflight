"""The spin chain's kinematic route is spin-refit-v1: its value, label and range on a shot.

The chain (``server._apply_fallback_spin``): an accepted OPS spin, the collision
model's spin, the kinematic route (spin-refit-v1, the best tier the shot's inputs
allow), the OPS's own reading below the confidence floor. These tests run the stage
on plain shots and read the result the way the kiosk does (``Shot.to_dict``).
"""

from __future__ import annotations

from datetime import datetime

import pytest

from openflight import server as server_module
from openflight.clubs.types import ClubType
from openflight.launch_monitor import Shot
from openflight.spin_estimate import TIER_CLUB, TIER_LAUNCH, refit_spin


def _shot(**values) -> Shot:
    base = {
        "ball_speed_mph": 100.0,
        "club_speed_mph": 75.0,
        "timestamp": datetime(2026, 10, 25, 9, 0, 0),
        "club": ClubType.IRON_7,
        "spin_rpm": 2300.0,
        "spin_confidence": 0.44,
        "spin_method": "multitaper_ungated",
    }
    base.update(values)
    return Shot(**base)


def _apply(shot: Shot) -> bool:
    return server_module._apply_fallback_spin(shot)  # pylint: disable=protected-access


def test_an_ops_only_shot_takes_the_club_tier_on_the_radial_speed_labelled_estimated():
    shot = _shot()

    assert _apply(shot) is True

    expected = refit_spin(club=ClubType.IRON_7, ball_speed_mph=100.0)
    trail = shot.input_sources["spin"]
    assert trail["route"] == "kinematic"
    assert trail["label"] == "estimated"
    assert trail["uncertainty"] == pytest.approx(expected.uncertainty_rpm)
    assert shot.spin_rpm == round(expected.spin_rpm)
    assert shot.spin_source == "estimated"
    assert shot.spin_method == expected.method
    assert shot.spin_method.startswith("estimated (model: spin refit v1, TM exports; club tier")
    assert shot.spin_rpm_range == pytest.approx(list(expected.range_rpm), abs=0.5)
    assert shot.spin_confidence is None
    # the OPS's own reading is kept for offline scoring
    assert shot.spin_rpm_measured == 2300.0
    assert shot.spin_method_measured == "multitaper_ungated"


def test_the_kiosk_record_carries_the_model_s_label_source_and_range():
    shot = _shot()
    _apply(shot)

    record = shot.to_dict()["metrics"]["spin_rate"]

    assert record["status"] == "estimated"
    assert record["value"] == shot.spin_rpm
    assert record["source"].startswith("estimated (model: spin refit v1, TM exports")
    assert record["range"] == pytest.approx(shot.spin_rpm_range)
    assert shot.to_dict()["spin_rpm_range"] == pytest.approx(shot.spin_rpm_range)


def test_a_calculated_full_ball_speed_makes_the_club_tier_calculated():
    shot = _shot(
        ball_speed_full={"status": "calculated", "value_mph": 104.0},
        ball_speed_full_mph=104.0,
    )

    _apply(shot)

    assert shot.spin_source == "calculated"
    assert shot.input_sources["spin"]["label"] == "calculated"
    assert "on the full ball speed" in shot.spin_method


def test_without_a_club_a_radar_launch_takes_the_launch_tier():
    shot = _shot(
        club=ClubType.UNKNOWN,
        launch_angle_vertical=18.0,
        launch_angle_vertical_source="radar",
    )

    _apply(shot)

    expected = refit_spin(club=None, ball_speed_mph=100.0, launch_vertical_deg=18.0)
    assert expected.tier == TIER_LAUNCH
    assert shot.input_sources["spin"]["route"] == "kinematic"
    assert shot.spin_rpm == round(expected.spin_rpm)
    assert "launch tier" in shot.spin_method


def test_an_accepted_ops_spin_still_wins_and_carries_no_model_range():
    shot = _shot(spin_rpm=6100.0, spin_confidence=0.9, spin_method="ops_fft")

    assert _apply(shot) is False

    assert shot.input_sources["spin"]["route"] == "ops"
    assert shot.spin_rpm == 6100.0
    assert shot.spin_rpm_range is None


def test_without_a_club_or_a_launch_the_ops_reading_keeps_its_own_label():
    shot = _shot(club=ClubType.UNKNOWN)

    assert _apply(shot) is False

    trail = shot.input_sources["spin"]
    assert trail["route"] == "ops_below_floor"
    refused = {entry["route"]: entry["reason"] for entry in trail["tried"]}
    assert "no club and no vertical launch" in refused["kinematic"]
    assert shot.spin_rpm == 2300.0
    assert shot.spin_rpm_range is None


def test_a_chip_below_the_fitted_speeds_is_refused_with_its_reason():
    shot = _shot(ball_speed_mph=28.0)

    _apply(shot)

    trail = shot.input_sources["spin"]
    assert trail["route"] == "ops_below_floor"
    refused = {entry["route"]: entry["reason"] for entry in trail["tried"]}
    assert "outside the refit's club tier" in refused["kinematic"]


def test_the_club_tier_takes_the_place_the_launch_formula_had():
    shot = _shot(launch_angle_vertical=17.0, launch_angle_vertical_source="radar")

    _apply(shot)

    assert shot.spin_method.split(";")[1].strip().startswith(f"{TIER_CLUB} tier")


def test_a_later_model_spin_does_not_carry_the_refit_s_range():
    shot = _shot()
    _apply(shot)
    assert shot.spin_rpm_range is not None

    # a later stage (the collision model, say) replaces the spin with its own
    shot.spin_rpm = 5200.0
    shot.spin_method = "impulse collision model"
    shot.spin_source = "calculated"

    data = shot.to_dict()
    assert data["spin_rpm_range"] is None
    assert data["metrics"]["spin_rate"]["range"] is None


def test_the_refit_method_predicate():
    from openflight.source_labels import is_spin_refit_method

    shot = _shot()
    _apply(shot)

    assert is_spin_refit_method(shot.spin_method)
    assert not is_spin_refit_method("impulse collision model")
    assert not is_spin_refit_method(None)

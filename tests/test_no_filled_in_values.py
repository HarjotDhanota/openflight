"""A fusion unit reports a missing value as missing (D17); OPS-only installs keep labelled estimates.

With the IWR or the camera running, the kiosk used to fill a missing vertical launch
from club tables, carry from an "optimal" spin and a club-typical one. On a fusion unit
a missing launch or spin stays missing, and carry is built only from measured inputs.
An OPS-only install has nothing to measure angles with, so it keeps its labelled
estimates for simulators. A calculated spin is a model's output: labelled calculated,
never high-confidence.
"""

from datetime import datetime
from types import SimpleNamespace

import pytest

from openflight import server as server_module
from openflight.ballistics import resolve_launch
from openflight.clubs import ClubType
from openflight.launch_monitor import SPIN_CONFIDENCE_HIGH, Shot
from openflight.sim.resolver import resolve_shot
from openflight.sim.types import PlayerState


def _shot(**overrides):
    values = {
        "ball_speed_mph": 150.0,
        "club_speed_mph": 105.0,
        "timestamp": datetime.now(),
        "club": ClubType.DRIVER,
    }
    values.update(overrides)
    return Shot(**values)


@pytest.fixture
def fusion_unit(monkeypatch):
    monkeypatch.setattr(server_module, "iwr6843_runtime", SimpleNamespace())


@pytest.fixture
def ops_only(monkeypatch):
    monkeypatch.setattr(server_module, "iwr6843_runtime", None)
    monkeypatch.setattr(server_module, "camera_capture_runtime", None)


@pytest.fixture
def quiet_finalize(monkeypatch):
    monkeypatch.setattr(server_module, "ballistics_enabled", True)
    monkeypatch.setattr(server_module, "calculated_spin_enabled", False)
    monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
    monkeypatch.setattr(server_module.socketio, "emit", lambda *args, **kwargs: None)


def _finalize(shot):
    server_module._finalize_shot_detected(shot, emit_event="shot")  # pylint: disable=protected-access


def test_a_fusion_unit_leaves_a_missing_vertical_launch_missing(fusion_unit):
    shot = _shot()

    server_module._ensure_user_facing_launch_angles(shot)  # pylint: disable=protected-access

    assert shot.launch_angle_vertical is None
    assert shot.launch_angle_vertical_source is None


def test_an_ops_only_install_keeps_its_labelled_launch_estimate(ops_only):
    shot = _shot()

    server_module._ensure_user_facing_launch_angles(shot)  # pylint: disable=protected-access

    assert shot.launch_angle_vertical is not None
    assert shot.launch_angle_vertical_source == "estimated"
    assert shot.launch_angle_horizontal == 0.0
    assert shot.launch_angle_horizontal_source == "estimated"


def test_a_fusion_unit_without_a_measured_launch_gives_no_carry(fusion_unit, quiet_finalize):
    shot = _shot(spin_rpm=2700, spin_confidence=0.85)

    _finalize(shot)

    assert shot.carry_spin_adjusted is None


def test_a_fusion_unit_without_a_measured_spin_gives_no_carry(fusion_unit, quiet_finalize):
    shot = _shot(
        launch_angle_vertical=12.0,
        launch_angle_vertical_source="radar",
        launch_angle_horizontal=1.0,
        launch_angle_horizontal_source="radar",
        spin_rpm=2700,
        spin_confidence=0.3,
    )

    _finalize(shot)

    assert shot.carry_spin_adjusted is None


def test_a_fusion_unit_with_measured_inputs_gets_ballistic_carry(fusion_unit, quiet_finalize):
    shot = _shot(
        launch_angle_vertical=12.0,
        launch_angle_vertical_source="radar",
        launch_angle_horizontal=1.0,
        launch_angle_horizontal_source="radar",
        spin_rpm=2700,
        spin_confidence=0.85,
        spin_axis_deg=2.0,
    )

    _finalize(shot)

    assert shot.carry_spin_adjusted is not None and shot.carry_spin_adjusted > 0


def test_an_ops_only_install_keeps_its_table_carry(ops_only, quiet_finalize):
    shot = _shot(spin_rpm=2700, spin_confidence=0.3)

    _finalize(shot)

    assert shot.carry_spin_adjusted is not None and shot.carry_spin_adjusted > 0


def test_ballistics_without_substitutes_needs_measured_spin_axis_and_both_angles():
    measured = _shot(
        launch_angle_vertical=12.0,
        launch_angle_horizontal=1.0,
        spin_rpm=2700,
        spin_confidence=0.85,
        spin_axis_deg=2.0,
    )

    assert resolve_launch(measured, substitutes=False).spin_source == "measured"
    measured.spin_axis_deg = None
    assert resolve_launch(measured, substitutes=False) is None
    assert (
        resolve_launch(
            _shot(
                launch_angle_vertical=12.0,
                launch_angle_horizontal=1.0,
                spin_rpm=2700,
                spin_confidence=0.3,
            ),
            substitutes=False,
        )
        is None
    )
    assert (
        resolve_launch(
            _shot(launch_angle_vertical=12.0, spin_rpm=2700, spin_confidence=0.85),
            substitutes=False,
        )
        is None
    )


# ---- a calculated spin is labelled calculated ------------------------------------------


def _calculated():
    shot = _shot(
        launch_angle_vertical=12.0,
        launch_angle_vertical_source="radar",
        spin_rpm=900,
        spin_confidence=0.2,
    )
    assert server_module._apply_calculated_spin(shot)  # pylint: disable=protected-access
    return shot


def test_a_calculated_spin_is_never_labelled_high_confidence():
    shot = _calculated()

    assert shot.spin_source == "calculated"
    assert shot.spin_confidence is None or shot.spin_confidence < SPIN_CONFIDENCE_HIGH
    assert shot.spin_rpm_measured == 900


def test_carry_and_simulators_use_a_calculated_spin_under_its_own_label():
    shot = _calculated()

    assert resolve_launch(shot).spin_source == "calculated"
    resolved = resolve_shot(shot, PlayerState())
    # simulators' provenance is measured or estimated: a model's spin is not measured
    assert resolved.provenance["total_spin"] == "estimated"
    assert resolved.total_spin_rpm == pytest.approx(shot.spin_rpm)

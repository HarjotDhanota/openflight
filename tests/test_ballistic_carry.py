"""Carry without a flight model extension: the drag + Magnus simulator (ballistics.simulate)."""

from __future__ import annotations

from datetime import datetime

import pytest

from openflight import kiosk_extensions, server as server_module
from openflight.ballistics import resolve_launch, simulate
from openflight.clubs import ClubType
from openflight.launch_monitor import CARRY_ESTIMATED, CARRY_NOT_MEASURED, Shot


@pytest.fixture(autouse=True)
def plain_kiosk(monkeypatch):
    """No extension registered, ballistics on, nothing logged or emitted."""
    monkeypatch.setattr(kiosk_extensions, "_extensions", [])
    monkeypatch.setattr(server_module, "ballistics_enabled", True)
    monkeypatch.setattr(server_module, "calculated_spin_enabled", False)
    monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
    monkeypatch.setattr(server_module.socketio, "emit", lambda *args, **kwargs: None)


@pytest.fixture
def fusion(monkeypatch):
    monkeypatch.setattr(server_module, "_fusion_unit", lambda: True)


@pytest.fixture
def ops_only(monkeypatch):
    monkeypatch.setattr(server_module, "_fusion_unit", lambda: False)


def _shot(**overrides) -> Shot:
    values = {
        "ball_speed_mph": 120.0,
        "club_speed_mph": 85.0,
        "timestamp": datetime(2026, 10, 12, 9, 0, 0),
        "club": ClubType.IRON_7,
        "launch_angle_vertical": 18.0,
        "launch_angle_horizontal": 1.0,
        "spin_rpm": 6800.0,
        "spin_confidence": 0.9,
        "spin_axis_deg": 2.0,
    }
    values.update(overrides)
    return Shot(**values)


def _finalize(shot: Shot) -> Shot:
    server_module._finalize_shot_detected(shot, emit_event="shot")  # pylint: disable=protected-access
    return shot


@pytest.mark.parametrize(
    ("unit", "overrides"), [("ops_only", {"spin_confidence": 0.2}), ("fusion", {})]
)
def test_the_unit_flies_the_simulator_on_the_inputs_it_trusts(request, unit, overrides):
    request.getfixturevalue(unit)

    shot = _finalize(_shot(**overrides))

    assert shot.carry_spin_adjusted == pytest.approx(simulate(resolve_launch(shot)).carry_yards)
    assert shot.carry_status == CARRY_ESTIMATED


@pytest.mark.parametrize(
    "missing",
    [
        {"spin_confidence": 0.2},
        {"launch_angle_horizontal": None},
        {"spin_axis_deg": float("nan")},
        {"launch_angle_vertical": None},
    ],
)
def test_a_fusion_unit_without_a_measured_input_has_no_carry(fusion, missing):
    shot = _finalize(_shot(**missing))

    assert shot.carry_spin_adjusted is None
    assert shot.carry_status == CARRY_NOT_MEASURED


def test_an_ops_only_unit_without_a_launch_angle_takes_the_table(ops_only):
    shot = _finalize(_shot(launch_angle_vertical=None))

    assert shot.carry_spin_adjusted is not None and shot.carry_spin_adjusted > 0
    assert shot.carry_status == CARRY_ESTIMATED


def test_disabled_ballistics_leave_the_fusion_unit_without_carry(fusion, monkeypatch):
    monkeypatch.setattr(server_module, "ballistics_enabled", False)

    shot = _finalize(_shot())

    assert shot.carry_spin_adjusted is None
    assert shot.carry_status == CARRY_NOT_MEASURED

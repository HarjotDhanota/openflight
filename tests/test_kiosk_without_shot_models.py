"""A kiosk without the shot models still measures, labels and publishes.

Nothing is registered: an OPS-only unit takes the table carry, a fusion unit has no carry,
and --calculated-spin uses its own formula."""

from datetime import datetime
from types import SimpleNamespace

import pytest

from openflight import kiosk_extensions, server
from openflight.clubs import ClubType
from openflight.launch_monitor import CARRY_ESTIMATED, CARRY_NOT_MEASURED, Shot


@pytest.fixture
def plain_kiosk(monkeypatch):
    monkeypatch.setattr(kiosk_extensions, "_extensions", [])
    monkeypatch.setattr(server, "iwr6843_runtime", None)
    monkeypatch.setattr(server, "camera_capture_runtime", None)
    monkeypatch.setattr(server, "ballistics_enabled", True)
    monkeypatch.setattr(server, "calculated_spin_enabled", False)
    monkeypatch.setattr(server, "get_session_logger", lambda: None)
    monkeypatch.setattr(server.socketio, "emit", lambda *args, **kwargs: None)
    return monkeypatch


def _shot(**overrides):
    fields = {
        "ball_speed_mph": 140.0,
        "club_speed_mph": 95.0,
        "timestamp": datetime(2026, 10, 19),
        "club": ClubType.IRON_7,
        "launch_angle_vertical": 18.0,
        "launch_angle_vertical_source": "radar",
        "mode": "rolling-buffer",
    }
    fields.update(overrides)
    return Shot(**fields)


def _finalize(shot):
    server._finalize_shot_detected(shot, emit_event="shot")  # pylint: disable=protected-access


@pytest.mark.parametrize("fusion", [False, True], ids=["ops_only", "fusion"])
def test_only_an_ops_only_unit_has_a_carry_and_no_model_output_is_attached(plain_kiosk, fusion):
    if fusion:
        plain_kiosk.setattr(server, "iwr6843_runtime", SimpleNamespace())
    shot = _shot()

    _finalize(shot)

    assert shot.flight is None
    if fusion:
        assert shot.carry_status == CARRY_NOT_MEASURED and shot.carry_spin_adjusted is None
    else:
        assert shot.carry_status == CARRY_ESTIMATED
        assert shot.carry_spin_adjusted is not None and shot.carry_spin_adjusted > 0
    assert shot.collision_model is None and shot.club_delivery is None
    assert shot.ball_speed_full is None and shot.smash_factor_full is None
    assert shot.ball_speed_contract == "radial"


def test_calculated_spin_uses_its_own_formula_without_a_collision_model(plain_kiosk):
    shot = _shot(spin_rpm_collision=5000.0)

    assert server._apply_calculated_spin(shot) is True  # pylint: disable=protected-access

    assert shot.spin_rpm == shot.spin_rpm_calculated_empirical != 5000.0
    assert shot.spin_method == server.EMPIRICAL_SPIN_METHOD

"""An extension's late result reaches the kiosk as an update to a shot already shown."""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from openflight import kiosk_extensions, server as server_module
from openflight.launch_monitor import Shot
from openflight.session_logger import SessionLogger


def _shot(number: int = 4) -> Shot:
    return Shot(ball_speed_mph=120.0, timestamp=datetime(2026, 10, 22, 9, 0), shot_number=number)


@pytest.fixture
def emitted(monkeypatch):
    sent = []
    monkeypatch.setattr(
        server_module.socketio,
        "emit",
        lambda event, payload=None, **_: sent.append((event, payload)),
    )
    return sent


@pytest.fixture
def session(tmp_path, monkeypatch):
    log = SessionLogger(log_dir=tmp_path, enabled=True)
    log.start_session(mode="rolling-buffer", trigger_type="sound")
    monkeypatch.setattr(server_module, "get_session_logger", lambda: log)
    yield log
    log.end_session()


def _entries(log: SessionLogger) -> list[dict]:
    text = log.session_path.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines()]


def test_a_shot_update_is_emitted_and_logged_with_its_update_record(emitted, session):
    shot = _shot()
    shot.impact_location = {"status": "rejected", "reason": "a named reason"}

    assert server_module.publish_shot_update(
        shot, update={"reason": "late_extension_result"}, session_uuid=session.active_session_uuid
    )

    assert [event for event, _ in emitted] == ["shot_update"]
    assert emitted[0][1]["shot"]["shot_number"] == 4
    assert emitted[0][1]["shot"]["metrics"]["impact_offset"]["reason"] == "a named reason"
    updates = [entry for entry in _entries(session) if entry["type"] == "shot_updated"]
    assert len(updates) == 1
    assert updates[0]["shot_number"] == 4
    assert updates[0]["update"] == {"reason": "late_extension_result", "simulators": "not_resent"}


def test_nothing_is_sent_once_the_shot_s_session_has_ended(emitted, session):
    assert not server_module.publish_shot_update(
        _shot(), update={"reason": "late_extension_result"}, session_uuid="an-older-session"
    )
    assert not emitted
    assert not [entry for entry in _entries(session) if entry["type"] == "shot_updated"]


@pytest.mark.parametrize("logger", [None, SessionLogger(enabled=False)])
def test_without_session_logging_the_update_is_still_shown(emitted, monkeypatch, logger):
    monkeypatch.setattr(server_module, "get_session_logger", lambda: logger)
    assert server_module.publish_shot_update(_shot(), update={"reason": "x"}, session_uuid=None)
    assert [event for event, _ in emitted] == ["shot_update"]


class _Recorder(kiosk_extensions.KioskExtension):
    def __init__(self, emitted):
        self.calls = []
        self.emitted = emitted

    def after_shot_published(self, shot, *, update_kind):
        self.calls.append((shot.shot_number, update_kind, [event for event, _ in self.emitted]))


@pytest.fixture
def recorder(emitted):
    extension = kiosk_extensions.register(_Recorder(emitted))
    yield extension
    kiosk_extensions.unregister(extension)


def test_extensions_hear_of_a_shot_after_its_first_result_went_out(recorder, monkeypatch):
    monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
    server_module._finalize_shot_detected(_shot(7), emit_event="shot")  # pylint: disable=protected-access
    assert recorder.calls == [(7, "first", ["shot"])]


def test_a_failing_extension_never_stops_the_shot(emitted, monkeypatch):
    class Broken(kiosk_extensions.KioskExtension):
        def after_shot_published(self, shot, *, update_kind):
            raise RuntimeError("broken extension")

    monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
    broken = kiosk_extensions.register(Broken())
    try:
        server_module._finalize_shot_detected(_shot(8), emit_event="shot")  # pylint: disable=protected-access
    finally:
        kiosk_extensions.unregister(broken)
    assert [event for event, _ in emitted] == ["shot"]

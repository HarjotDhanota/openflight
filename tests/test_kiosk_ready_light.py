"""A plain kiosk's ready light: only the sensors decide it (no tester setup causes)."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from openflight import kiosk_extensions, server
from openflight.ready_light import ReadyLight

ALIVE = SimpleNamespace(is_alive=lambda: True)


def _iwr(**overrides):
    return {
        "running": True,
        "armed": True,
        "worker_alive": True,
        "serial_open": True,
        "dumping": False,
        "queued": False,
        "dump_started_at": None,
        "typical_dump_s": 7.0,
        **overrides,
    }


def _camera(**overrides):
    return {
        "running": True,
        "latest_frame_age_s": 0.01,
        "requested_size": [1280, 800],
        "resolved_size": [1280, 800],
        "controls_purpose": "capture",
        "collecting_tail": False,
        "awaiting_handoff": False,
        "saving": False,
        "pending_saves": 0,
        "clip_started_at": None,
        "typical_clip_s": None,
        "buffered_frames": 24,
        "required_pre_frames": 24,
        "fps": 120.0,
        **overrides,
    }


class _Kiosk:
    """A plain kiosk: no extensions, a sound-triggered OPS, an IWR and a camera."""

    def __init__(self, monkeypatch):
        monkeypatch.setattr(kiosk_extensions, "_extensions", [])
        self.radar = SimpleNamespace(
            serial=SimpleNamespace(is_open=True), trigger_phase=("armed", 0.0)
        )
        self.monitor = SimpleNamespace(
            _running=True, _capture_thread=ALIVE, radar=self.radar, trigger_type="sound"
        )
        self.iwr = _iwr()
        self.camera = _camera()
        monkeypatch.setattr(server, "ready_light", ReadyLight())
        monkeypatch.setattr(server, "monitor", self.monitor)
        monkeypatch.setattr(server, "mock_mode", False)
        monkeypatch.setattr(
            server,
            "iwr6843_runtime",
            SimpleNamespace(capture_monitor=SimpleNamespace(ready_snapshot=lambda: self.iwr)),
        )
        monkeypatch.setattr(server, "iwr6843_runtime_config", {"enabled": True})
        monkeypatch.setattr(
            server,
            "camera_capture_runtime",
            SimpleNamespace(ready_snapshot=lambda: self.camera),
        )
        monkeypatch.setattr(server, "camera_capture_config", {"enabled": True})
        monkeypatch.setattr(server, "setup_service", None)
        monkeypatch.setattr(server, "ball_at_address_monitor", None)

    def light(self):
        return server.ready_light.update(server._ready_light_states())


@pytest.fixture
def kiosk(monkeypatch):
    return _Kiosk(monkeypatch)


def test_a_plain_kiosk_shows_the_light_green_when_every_sensor_is_ready(kiosk):
    light = kiosk.light()

    assert (light["state"], light["word"], light["cause"]) == (
        "green",
        "SWING",
        "Ready for a swing",
    )


def test_a_plain_kiosk_light_has_no_setup_causes(kiosk):
    states = server._ready_light_states()

    assert tuple(states.setup_problems) == ()
    assert states.camera_rule is None and states.green_note is None


def test_ops_not_armed_is_red_and_busy_is_amber(kiosk):
    kiosk.radar.trigger_phase = None
    assert kiosk.light()["cause"] == "OPS243 starting: not armed yet"

    kiosk.radar.trigger_phase = ("dumping", 0.0)
    light = kiosk.light()
    assert (light["state"], light["cause"]) == ("amber", "OPS243 dumping")


def test_an_iwr_dumping_is_amber(kiosk):
    kiosk.iwr = _iwr(dumping=True, dump_started_at=0.0)

    light = kiosk.light()

    assert (light["state"], light["cause"]) == ("amber", "IWR6843 dumping")


def test_a_camera_refilling_its_ring_is_amber(kiosk):
    kiosk.camera = _camera(buffered_frames=12)

    light = kiosk.light()

    assert (light["state"], light["cause"]) == ("amber", "camera refilling its ring")


def test_a_kiosk_without_iwr_or_camera_is_judged_on_the_ops_alone(kiosk, monkeypatch):
    monkeypatch.setattr(server, "iwr6843_runtime", None)
    monkeypatch.setattr(server, "iwr6843_runtime_config", {"enabled": False})
    monkeypatch.setattr(server, "camera_capture_runtime", None)
    monkeypatch.setattr(server, "camera_capture_config", {"enabled": False})

    assert kiosk.light()["state"] == "green"


def test_the_light_is_off_in_mock_mode_and_on_the_streaming_trigger(kiosk, monkeypatch):
    monkeypatch.setattr(server, "mock_mode", True)
    assert kiosk.light()["state"] == "off"

    monkeypatch.setattr(server, "mock_mode", False)
    kiosk.monitor.trigger_type = "speed"
    assert kiosk.light()["state"] == "off"


def _snapshot(**sections):
    record = {
        "box": {"state": "confirmed"},
        "aim": {"state": "set"},
        "ball_range": {"state": "set"},
        "net_range": {"state": "default"},
        "scene": {"state": "not_set"},
    }
    record.update(sections)
    return {**record, "live": {"camera_available": True}, "unapplied": {}}


def test_the_setup_record_and_the_ball_look_reach_the_light(kiosk, monkeypatch):
    snapshot = _snapshot(ball_range={"state": "not_set"})
    monkeypatch.setattr(server, "setup_service", SimpleNamespace(snapshot=lambda: snapshot))
    assert kiosk.light()["cause"] == "setup incomplete: ball range not set"

    snapshot = _snapshot()
    look = {"state": "not_seen", "observed_at": time.time()}
    monkeypatch.setattr(server, "ball_at_address_monitor", SimpleNamespace(latest=lambda: look))
    light = kiosk.light()
    assert (light["state"], light["cause"]) == ("amber", "no ball in the box")


def test_an_extension_adds_setup_causes_from_the_snapshot(kiosk, monkeypatch):
    seen = []

    class Tester(kiosk_extensions.KioskExtension):
        def setup_ready_causes(self, snapshot):
            seen.append(snapshot)
            return [("amber", "free play paused")]

    snapshot = _snapshot()
    monkeypatch.setattr(server, "setup_service", SimpleNamespace(snapshot=lambda: snapshot))
    assert kiosk_extensions.setup_ready_causes(snapshot) == []
    kiosk_extensions.register(Tester())

    light = kiosk.light()

    assert (light["state"], light["cause"]) == ("amber", "free play paused")
    assert seen == [snapshot]

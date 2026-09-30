"""P7-14: the kiosk serves one ready light, on its socket and over HTTP."""

from __future__ import annotations

import logging
from datetime import datetime
from types import SimpleNamespace

import pytest

from openflight import server
from openflight.launch_monitor import Shot
from openflight.ready_light import ReadyLight
from openflight.rig_geometry import geometry_fingerprint

ALIVE = SimpleNamespace(is_alive=lambda: True)
SETUP_PROBLEMS = server._ready_light_setup_problems
DEAD = SimpleNamespace(is_alive=lambda: False)


def _iwr_snapshot(**overrides):
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


def _camera_snapshot(**overrides):
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


class _Sensors:
    """The kiosk's sensors as the ready light reads them; each test changes one."""

    def __init__(self, monkeypatch):
        self.monkeypatch = monkeypatch
        self.phase = ("armed", 0.0)
        self.iwr = _iwr_snapshot()
        self.camera = _camera_snapshot()
        self.radar = SimpleNamespace(serial=SimpleNamespace(is_open=True), trigger_phase=None)
        self.monitor = SimpleNamespace(
            _running=True, _capture_thread=ALIVE, radar=self.radar, trigger_type="sound"
        )
        self.emitted = []
        monkeypatch.setattr(server, "ready_light", ReadyLight())
        monkeypatch.setattr(server, "_ready_light_published", None)
        monkeypatch.setattr(server, "monitor", self.monitor)
        monkeypatch.setattr(server, "mock_mode", False)
        # a tester's plain swings kiosk: setup required, no study mode
        monkeypatch.setattr(server, "study_mode_enabled", False)
        monkeypatch.setattr(server, "tester_setup_required", True)
        monkeypatch.setattr(server, "_ready_light_setup_problems", lambda: [])
        monkeypatch.setattr(
            server,
            "iwr6843_runtime",
            SimpleNamespace(capture_monitor=SimpleNamespace(ready_snapshot=lambda: self.iwr)),
        )
        monkeypatch.setattr(server, "iwr6843_runtime_config", {"enabled": True})
        monkeypatch.setattr(
            server,
            "camera_capture_runtime",
            SimpleNamespace(
                ready_snapshot=lambda: self.camera,
                settings=SimpleNamespace(auto_exposure=False),
            ),
        )
        monkeypatch.setattr(server, "camera_capture_config", {"enabled": True})
        monkeypatch.setattr(
            server.socketio,
            "emit",
            lambda event, data=None, **_: self.emitted.append((event, data)),
        )
        self.set_phase("armed")

    def set_phase(self, phase):
        self.radar.trigger_phase = (phase, server.time.time())

    def light(self):
        response = server.app.test_client().get("/api/ready-light")
        assert response.status_code == 200
        return response.get_json()


@pytest.fixture
def sensors(monkeypatch):
    return _Sensors(monkeypatch)


def test_green_when_every_sensor_is_armed_and_idle(sensors):
    light = sensors.light()

    assert (light["state"], light["word"]) == ("green", "SWING")
    assert light["schema_version"] == 1


def test_each_sensor_busy_state_turns_the_light_amber(sensors):
    sensors.set_phase("dumping")
    assert sensors.light()["cause"] == "OPS243 dumping"
    sensors.set_phase("rearming")
    assert sensors.light()["cause"] == "OPS243 re-arming"
    sensors.set_phase("armed")

    sensors.iwr = _iwr_snapshot(dumping=True, dump_started_at=server.time.time())
    light = sensors.light()
    assert (light["state"], light["cause"]) == ("amber", "IWR6843 dumping")
    assert 6.0 < light["time_left_s"] <= 7.0
    sensors.iwr = _iwr_snapshot()

    sensors.camera = _camera_snapshot(saving=True)
    assert sensors.light()["cause"] == "camera saving a clip"
    sensors.camera = _camera_snapshot()

    assert sensors.light()["state"] == "green"


@pytest.mark.parametrize(
    ("break_it", "cause"),
    [
        (lambda s: setattr(s.monitor, "_capture_thread", DEAD), "OPS243 radar is not running"),
        (lambda s: setattr(s.radar, "serial", None), "OPS243 serial link is closed"),
        (lambda s: s.iwr.update(worker_alive=False), "IWR6843 radar is not running"),
        (lambda s: s.iwr.update(armed=False), "IWR6843 not armed yet"),
        (lambda s: s.camera.update(latest_frame_age_s=5.0), "camera frames stopped"),
    ],
)
def test_a_failed_sensor_is_red(sensors, break_it, cause):
    break_it(sensors)

    light = sensors.light()

    assert (light["state"], light["word"], light["cause"]) == ("red", "NOT READY", cause)


def test_an_iwr_that_failed_to_start_is_red(sensors, monkeypatch):
    monkeypatch.setattr(server, "iwr6843_runtime", None)
    monkeypatch.setattr(server, "iwr6843_runtime_config", {"enabled": False, "error": "no port"})

    assert sensors.light()["cause"] == "IWR6843 radar is not running"


def test_a_camera_that_failed_to_start_is_red(sensors, monkeypatch):
    monkeypatch.setattr(server, "camera_capture_runtime", None)
    monkeypatch.setattr(server, "camera_capture_config", {"enabled": False, "error": "no camera"})

    assert sensors.light()["cause"] == "camera is not running"


def test_the_light_is_off_outside_a_testers_kiosk(sensors, monkeypatch):
    monkeypatch.setattr(server, "tester_setup_required", False)
    assert sensors.light()["state"] == "off"

    monkeypatch.setattr(server, "study_mode_enabled", True)  # the ladder's kiosk
    assert sensors.light()["state"] == "green"


def test_the_light_is_off_without_the_sound_triggered_ops(sensors, monkeypatch):
    monkeypatch.setattr(server, "mock_mode", True)
    assert sensors.light()["state"] == "off"

    monkeypatch.setattr(server, "mock_mode", False)
    sensors.monitor.trigger_type = "speed"
    assert sensors.light()["state"] == "off"


def _admitted(monkeypatch, *, admitted_hash=None, stable=True):
    rig_hash = "a" * 64
    config_hash = geometry_fingerprint(
        {
            "rig_geometry_sha256": rig_hash,
            "inclinometer": {"i2c_bus": 1, "i2c_address": "0x18", "zero_offset_deg": 0.0},
        }
    )
    monkeypatch.setattr(server, "_ready_light_setup_problems", SETUP_PROBLEMS)
    monkeypatch.setattr(server, "tester_config_hash", admitted_hash or config_hash)
    monkeypatch.setattr(server, "rig_geometry_config", {"snapshot": {"sha256": rig_hash}})
    monkeypatch.setattr(
        server,
        "inclinometer_runtime_config",
        {"enabled": True, "i2c_bus": 1, "i2c_address": "0x18", "zero_offset_deg": 0.0},
    )
    monkeypatch.setattr(
        server,
        "inclinometer_service",
        SimpleNamespace(
            snapshot_for_impact=lambda _t: SimpleNamespace(
                status="stable" if stable else "moving",
                snapshot=(
                    SimpleNamespace(calibrated_pitch_deg=0.0, x_g=0.0, y_g=0.0, z_g=1.0)
                    if stable
                    else None
                ),
            )
        ),
    )
    monkeypatch.setattr(
        server,
        "get_session_logger",
        lambda: SimpleNamespace(log_dir=server.Path("logs"), active_session_uuid="uuid"),
    )


def test_tester_kiosk_is_green_with_its_admitted_setup(sensors, monkeypatch):
    _admitted(monkeypatch)

    assert sensors.light()["state"] == "green"


def test_no_admitted_setup_is_red(sensors, monkeypatch):
    _admitted(monkeypatch, admitted_hash="b" * 64)

    light = sensors.light()

    assert light["state"] == "red"
    assert light["cause"].startswith("no admitted setup")


def test_a_moving_tilt_sensor_is_red_for_the_tester(sensors, monkeypatch):
    _admitted(monkeypatch, stable=False)

    assert sensors.light()["cause"] == "tilt sensor reading is moving"


def test_hold_is_refused_outside_study_mode(sensors):
    response = server.app.test_client().post("/api/ready-light/hold", json={"cause": "x"})

    assert response.status_code == 404


def test_the_tester_page_holds_the_light_red_and_releases_it(sensors, monkeypatch):
    monkeypatch.setattr(server, "study_mode_enabled", True)
    client = server.app.test_client()

    held = client.post("/api/ready-light/hold", json={"cause": "ladder light check running"})
    assert held.status_code == 200
    assert (held.get_json()["state"], held.get_json()["cause"]) == (
        "red",
        "ladder light check running",
    )
    assert sensors.light()["hold"] == "ladder light check running"

    released = client.post("/api/ready-light/hold", json={"cause": None})
    assert released.get_json()["state"] == "green"


@pytest.mark.parametrize("body", [{"cause": 5}, {"cause": "  "}, {"cause": "x" * 200}, [1]])
def test_a_bad_hold_is_refused(sensors, monkeypatch, body):
    monkeypatch.setattr(server, "study_mode_enabled", True)

    response = server.app.test_client().post("/api/ready-light/hold", json=body)

    assert response.status_code == 400


def test_the_socket_carries_each_change_once(sensors):
    server._publish_ready_light()
    server._publish_ready_light()
    sensors.set_phase("dumping")
    server._publish_ready_light()

    events = [data for event, data in sensors.emitted if event == "ready_light"]
    assert [(data["state"], data["cause"]) for data in events] == [
        ("green", "Ready for a swing"),
        ("amber", "OPS243 dumping"),
    ]


def test_a_new_client_gets_the_light_on_connect(sensors, monkeypatch):
    monkeypatch.setattr(server, "power_monitor", None)
    monkeypatch.setattr(server, "monitor", sensors.monitor)
    monkeypatch.setattr(server, "_emit_sim_snapshot", lambda: None)
    monkeypatch.setattr(server, "_emit_profiles", lambda: None)
    monkeypatch.setattr(server, "_session_state_payload", lambda **_: {})
    monkeypatch.setattr(server, "_get_trigger_status", lambda: {})

    server.handle_connect()

    assert any(event == "ready_light" for event, _data in sensors.emitted)


def test_a_trigger_edge_and_the_ops_dump_flash_once(sensors):
    server._ready_light_edge(server.time.time())
    server.on_shot_processing("capturing")

    swing = sensors.light()["swing"]

    assert swing["id"] == 1 and swing["result"] is None


def test_the_shot_reaches_the_light(sensors, monkeypatch):
    # the ladder's kiosk, so the tester trigger-evidence gate is not in the way
    monkeypatch.setattr(server, "tester_setup_required", False)
    monkeypatch.setattr(server, "study_mode_enabled", True)
    monkeypatch.setattr(
        server,
        "get_profile_store",
        lambda: SimpleNamespace(get_active=lambda: SimpleNamespace(id="p1", name="Tester")),
    )
    monkeypatch.setattr(server, "_assign_shot_number", lambda shot: None)
    monkeypatch.setattr(server, "_has_slow_shot_enrichment", lambda shot: False)
    monkeypatch.setattr(server, "_register_shot_for_finalization", lambda *a, **k: None)
    monkeypatch.setattr(server, "_finish_shot_detected", lambda shot, **_kwargs: None)
    server._ready_light_edge(server.time.time())

    server._handle_shot_detected(Shot(ball_speed_mph=141.3, timestamp=datetime.now()))

    assert sensors.light()["swing"]["result"]["text"] == "141.3 mph"


@pytest.mark.parametrize(
    ("reason", "text"),
    [
        ("edge_without_dump", "No radar shot"),
        ("dump_discarded", "No radar shot"),
        ("no_outbound_speed", "Not a shot"),
        ("shot_validation_failed", "Not a shot"),
    ],
)
def test_a_rejected_trigger_says_why(sensors, reason, text):
    server._ready_light_edge(server.time.time())

    server._ready_light_trigger_outcome({"accepted": False, "reason": reason})

    assert sensors.light()["swing"]["result"]["text"] == text


def test_an_accepted_trigger_waits_for_its_shot(sensors):
    server._ready_light_edge(server.time.time())

    server._ready_light_trigger_outcome({"accepted": True, "reason": "accepted"})

    assert sensors.light()["swing"]["result"] is None


def test_state_changes_are_logged(sensors, caplog):
    caplog.set_level(logging.INFO, logger="openflight.ready_light")

    sensors.light()
    sensors.set_phase("dumping")
    sensors.light()

    messages = [record.getMessage() for record in caplog.records]
    assert any("SWING" in message for message in messages)
    assert any("WAIT" in message and "OPS243 dumping" in message for message in messages)


def test_a_shot_the_tester_gate_refuses_says_it_was_not_counted(sensors, monkeypatch):
    monkeypatch.setattr(
        server,
        "camera_capture_runtime",
        SimpleNamespace(
            # an unconnected sensor is a hard stop (D15)
            trigger_evidence_for_shot=lambda _impact: {
                "schema_version": 1,
                "ready": False,
                "blockers": [{"id": "iwr6843", "reason": "serial link unavailable"}],
            },
            ready_snapshot=lambda: sensors.camera,
            settings=SimpleNamespace(auto_exposure=False),
        ),
    )
    monkeypatch.setattr(server, "log_session_error", lambda *a, **k: None)
    server._ready_light_edge(server.time.time())

    server._handle_shot_detected(
        Shot(ball_speed_mph=99.0, timestamp=datetime.now(), impact_timestamp=server.time.time())
    )

    assert sensors.light()["swing"]["result"]["text"] == "Not counted: setup not ready"

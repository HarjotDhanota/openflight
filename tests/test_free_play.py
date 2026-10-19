"""Free play records continuously without adopting the exposure ladder's gates."""

import json
import sys
import threading
import time
from types import SimpleNamespace

import pytest
from flask import Flask

from openflight import free_play, server
from openflight.camera import tester_server as ts
from tests.test_tester_server import TESTER_SETUP, eligible_app, params, setup_saw_ball


def test_free_play_command_uses_automatic_exposure_and_opens_the_kiosk(tmp_path):
    commands, _ = ts.action_commands(
        "free_play",
        params(arm_id="arm5"),
        tmp_path,
        ts.DEFAULT_RIG_GEOMETRY,
        tester_setup=TESTER_SETUP,
        light_index_dn_per_us_gain=0.25,
        ball_box=(50, 100, 1000, 700),
    )
    command = commands[0]
    assert "--tester-free-play" in command
    assert "--camera-capture-manual-exposure" not in command
    assert "--study-mode" not in command
    assert "--no-browser" not in command
    assert command[command.index("--camera-light-index") + 1] == "0.25"
    assert "--tester-setup-required" in command
    assert "--camera-capture" in command and "--iwr6843" in command and "--debug" in command
    assert command[command.index("--camera-ball-box") + 1] == "50,100,1000,700"


class Jobs:
    def __init__(self):
        self.job = {"state": "idle", "action": None}
        self.calls = []
        self.done = None

    def status(self):
        return self.job

    def start(self, action, commands, _log, on_finish=None, **_kwargs):
        self.calls.append((action, commands))
        self.job = {"state": "running", "action": action}
        self.done = on_finish

    def cancel(self):
        self.job = {**self.job, "state": "stopped"}
        return True


@pytest.mark.parametrize("default_arm,width", [("arm5", "1280"), ("arm6", "640")])
def test_free_play_starts_without_a_gain_screen_or_ladder(
    tmp_path, monkeypatch, default_arm, width
):
    setup_saw_ball(tmp_path)
    catalogue = ts.lens_profiles.load_catalogue()
    settings_path = tmp_path / "lens-settings.json"
    store = ts.lens_profiles.LensSettingsStore(settings_path, catalogue)
    store.save_settings(
        catalogue.reference_lens_id, {"modes": ["arm5", "arm6"], "default_arm": default_arm}
    )
    monkeypatch.setattr(ts, "setup_light_warning", lambda _: {"light_index_dn_per_us_gain": 0.25})
    manager = Jobs()
    app = eligible_app(sessions_root=tmp_path, manager=manager, lens_settings_path=settings_path)
    response = app.test_client().post(
        "/api/tester/run",
        json={
            "action": "free_play",
            "tester_id": "20260922-name",
            "arm_id": "arm1",
            "environment": "indoors",
        },
    )
    assert response.status_code == 202, response.get_json()
    state = response.get_json()["free_play"]
    assert state["phase"] == "recording"
    assert state["run_id"]
    command = manager.calls[0][1][0]
    assert command[command.index("--camera-capture-width") + 1] == width
    assert command[command.index("--camera-light-index") + 1] == "0.25"
    assert "--tester-run-id" in command
    assert not list(tmp_path.rglob("ladder.json"))


def test_packaging_is_run_bound_and_duplicate_requests_start_one_job(tmp_path, monkeypatch):
    capture, packaging = Jobs(), Jobs()
    capture.job = {"state": "running", "action": "free_play"}
    run = tmp_path / "tester" / "arm5" / "paired" / "run-01"
    run.mkdir(parents=True)
    controller = free_play.FreePlayController(tmp_path, capture, packaging)
    controller.begin("tester", "run-one", run)
    with pytest.raises(ValueError, match="another run"):
        controller.package("tester", "old-run")
    assert not packaging.calls
    controller.package("tester", "run-one")
    controller.package("tester", "run-one")
    assert len(packaging.calls) == 1
    monkeypatch.setattr(
        free_play.review,
        "read_analysis",
        lambda *_: {"latest_bundle": {"name": "session.zip", "path": ".bundles/session.zip"}},
    )
    packaging.done("analyze", 0)
    assert controller.status()["phase"] == "ready"
    assert json.loads((tmp_path / "tester/free-play.json").read_text())["phase"] == "ready"
    controller.capture_finished("run-one", 0)
    assert controller.status()["phase"] == "closed"
    assert controller.status()["bundle"]["name"] == "session.zip"


@pytest.mark.parametrize("sensor", ["camera", "iwr6843"])
def test_optional_sensor_failure_does_not_reject_a_free_play_shot(monkeypatch, sensor):
    monkeypatch.setattr(server, "free_play_config", {"enabled": True})
    monkeypatch.setattr(
        server,
        "get_session_logger",
        lambda: SimpleNamespace(active_session_uuid="same", log_dir=server.Path("logs")),
    )
    evidence = {
        "blockers": [{"id": sensor, "reason": "disconnected"}],
        "observations": {"runtime": {"session_uuid": "same"}},
    }
    assert server._tester_trigger_hard_stop(evidence) is None
    evidence["observations"]["runtime"]["session_uuid"] = "different"
    assert "different logging session" in server._tester_trigger_hard_stop(evidence)


def test_session_start_records_the_free_play_identity(monkeypatch):
    monkeypatch.setattr(
        server,
        "free_play_config",
        {
            "enabled": True,
            "tester_id": "tester",
            "run_id": "run-one",
            "unit_id": "unit-one",
            "geometry_unverified": True,
        },
    )
    assert server._session_start_config()["free_play"]["geometry_unverified"] is True


def test_kiosk_stops_capture_before_packaging_and_retries_do_not_stop_it_twice(monkeypatch):
    app = Flask(__name__)
    config = {"enabled": True, "phase": "recording"}
    closed = threading.Event()
    calls = []

    def finish():
        calls.append("close")
        closed.set()

    def proxy(_config, path, payload=None):
        if path == "/progress":
            calls.append("stopping")
            assert not closed.is_set()
            return {"phase": "stopping"}
        assert closed.is_set()
        if path == "/package":
            calls.append("package")
            assert payload["capture_closed"] is True
            return {"phase": "packaging"}
        return {"phase": "ready", "bundle": {"name": "test.zip"}}

    monkeypatch.setattr(free_play, "tester_request", proxy)
    free_play.register_kiosk_routes(app, config, finish)
    client = app.test_client()
    assert client.post("/api/free-play", json={"action": "stop"}).status_code == 200
    assert closed.wait(1)
    end = time.monotonic() + 1
    while config["phase"] == "stopping" and time.monotonic() < end:
        closed.wait(0.001)
    assert client.post("/api/free-play", json={"action": "stop"}).get_json()["phase"] == "ready"
    assert calls == ["stopping", "close", "package"]


def test_free_play_refuses_to_package_before_pending_shots_finish(monkeypatch):
    monkeypatch.setattr(server, "monitor", SimpleNamespace(stop=lambda: None))
    monkeypatch.setattr(server, "shot_enrichment_task", object())
    with pytest.raises(RuntimeError, match="finishing"):
        server._finish_free_play_capture(timeout_s=0)


def test_free_play_refuses_to_close_logs_while_radar_thread_is_alive(monkeypatch):
    monkeypatch.setattr(
        server,
        "monitor",
        SimpleNamespace(stop=lambda: None, _capture_thread=SimpleNamespace(is_alive=lambda: True)),
    )
    with pytest.raises(RuntimeError, match="radar"):
        server._finish_free_play_capture(timeout_s=0)


def test_an_exited_kiosk_does_not_leave_the_next_run_blocked(tmp_path):
    (tmp_path / "tester").mkdir()
    controller = free_play.FreePlayController(tmp_path, Jobs(), Jobs())
    controller.begin("tester", "one", tmp_path / "tester")
    controller.capture_finished("one", 1)
    assert controller.status()["phase"] == "interrupted"
    controller.begin("tester", "two", tmp_path / "tester")
    assert controller.status()["run_id"] == "two"


def test_camera_writer_must_stop_before_the_log_is_closed(monkeypatch):
    monkeypatch.setattr(server, "monitor", None)
    monkeypatch.setattr(
        server,
        "camera_capture_runtime",
        SimpleNamespace(stop=lambda: None, _worker=SimpleNamespace(is_alive=lambda: True)),
    )
    monkeypatch.setattr(
        server, "get_session_logger", lambda: pytest.fail("log closed before camera save finished")
    )
    with pytest.raises(RuntimeError, match="camera"):
        server._finish_free_play_capture()


def test_free_play_starts_ops_even_if_the_distance_radar_cannot_connect(monkeypatch, tmp_path):
    class ReachedOps(Exception):
        pass

    def start_ops(**_kwargs):
        raise ReachedOps

    monkeypatch.setattr(server, "init_iwr6843", lambda **_kw: False)
    monkeypatch.setattr(server, "init_session_logger", lambda **_kw: None)
    monkeypatch.setattr(server, "start_monitor", start_ops)
    monkeypatch.setattr(server, "_cleanup_hardware_for_shutdown", lambda: None)
    monkeypatch.setattr(server, "free_play_config", {})
    for name in (
        "tester_setup_required",
        "tester_config_hash",
        "study_mode_enabled",
        "ball_speed_correction_enabled",
        "ballistics_enabled",
    ):
        monkeypatch.setattr(server, name, getattr(server, name))
    monkeypatch.setattr(server, "profile_store", None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "openflight-server",
            "--iwr6843",
            "--no-logging",
            "--rig-geometry",
            "config/enclosure_v3_rig_geometry.json",
            "--profiles-path",
            str(tmp_path / "profiles.json"),
            "--tester-free-play",
            "--tester-setup-required",
            "--tester-config-hash",
            "test-hash",
            "--tester-id",
            "tester",
            "--tester-run-id",
            "run-one",
        ],
    )
    with pytest.raises(ReachedOps):
        server.main()


def test_shot_geometry_warning_preserves_radial_speed_and_freezes_exposure():
    from datetime import datetime

    from openflight.launch_monitor import Shot

    shot = Shot(ball_speed_mph=150, club_speed_mph=100, timestamp=datetime.now())
    shot.free_play_run_id = "run-one"
    shot.geometry_unverified = True
    shot.free_play_exposure = {"status": "recorded", "decision": {"setting": {"exposure_us": 100}}}
    record = shot.to_dict()
    shot.free_play_exposure["decision"]["setting"]["exposure_us"] = 200
    assert record["exposure_decision"]["decision"]["setting"]["exposure_us"] == 100
    assert record["metrics"]["ball_speed"]["value"] == 150
    assert not record["metrics"]["ball_speed"].get("geometry_validation")


def test_progress_and_package_routes_reject_stale_controls_and_old_bundles(tmp_path, monkeypatch):
    (tmp_path / "tester").mkdir()
    capture, packaging = Jobs(), Jobs()
    capture.job = {"state": "running", "action": "free_play"}
    controller = free_play.FreePlayController(tmp_path, capture, packaging)
    controller.begin("tester", "one", tmp_path / "tester")
    app = Flask(__name__)
    controller.register(app)
    client = app.test_client()
    identity = {"tester_id": "tester", "run_id": "one"}
    assert client.post("/api/tester/free-play/package", json=identity).status_code == 409
    assert (
        client.post(
            "/api/tester/free-play/progress", json={**identity, "phase": "stopping"}
        ).status_code
        == 200
    )
    assert controller.status()["phase"] == "stopping"
    assert (
        client.post("/api/tester/free-play/stop", json={**identity, "run_id": "old"}).status_code
        == 409
    )
    assert client.post("/api/tester/free-play/close", json=identity).status_code == 409
    monkeypatch.setattr(
        free_play.review, "read_analysis", lambda *_: {"latest_bundle": {"name": "old.zip"}}
    )
    assert (
        client.post(
            "/api/tester/free-play/package", json={**identity, "capture_closed": True}
        ).status_code
        == 202
    )
    packaging.done("analyze", 0)
    assert controller.status()["phase"] == "error"


def test_kiosk_rejects_foreign_run_and_retries_a_failed_stop(monkeypatch):
    app = Flask(__name__)
    config = {"enabled": True, "phase": "recording", "tester_id": "tester", "run_id": "one"}

    def failed_stop():
        raise RuntimeError("camera still saving")

    monkeypatch.setattr(free_play, "tester_request", lambda *_: {})
    free_play.register_kiosk_routes(app, config, failed_stop)
    client = app.test_client()
    assert (
        client.post("/api/free-play", json={"action": "stop", "run_id": "old"}).status_code == 409
    )
    assert client.post("/api/free-play", json=[1]).status_code == 400
    client.post("/api/free-play", json={"action": "stop"})
    deadline = time.monotonic() + 1
    while config["phase"] == "stopping" and time.monotonic() < deadline:
        time.sleep(0.001)
    assert config["phase"] == "error" and "saving" in config["error"]
    assert not config.get("capture_closed")

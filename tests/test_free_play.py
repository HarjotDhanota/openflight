"""Free play records continuously without adopting the exposure ladder's gates."""

import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask

from openflight import free_play, server, tester_hooks
from openflight.camera import free_play_ladder as fpl, tester_server as ts
from tests.test_tester_server import (
    SETUP_BALL_PX,
    TESTER_SETUP,
    eligible_app,
    params,
    setup_saw_ball,
)


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
    store = ts.lens_settings.LensSettingsStore(settings_path, catalogue)
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
            "resolution": default_arm,
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
    controller = free_play.FreePlayController(tmp_path, capture, packaging, kiosk_port=8080)
    controller.begin("tester", "run-one", run)
    with pytest.raises(ValueError, match="another run"):
        controller.package("tester", "old-run")
    assert not packaging.calls
    controller.package("tester", "run-one")
    controller.package("tester", "run-one")
    assert len(packaging.calls) == 1
    monkeypatch.setattr(
        free_play.review, "read_analysis", lambda *_: analysis_with_bundle("session.zip")
    )
    packaging.done("analyze", 0)
    assert controller.status()["phase"] == "ready"
    assert json.loads((tmp_path / "tester/free-play.json").read_text())["phase"] == "ready"
    controller.capture_finished("run-one", 0)
    assert controller.status()["phase"] == "closed"
    assert controller.status()["bundle"]["name"] == "session.zip"


@pytest.mark.parametrize("sensor", ["camera", "iwr6843"])
def test_optional_sensor_failure_does_not_reject_a_free_play_shot(monkeypatch, sensor):
    monkeypatch.setattr(tester_hooks, "free_play_config", {"enabled": True})
    monkeypatch.setattr(
        server,
        "get_session_logger",
        lambda: SimpleNamespace(active_session_uuid="same", log_dir=server.Path("logs")),
    )
    evidence = {
        "blockers": [{"id": sensor, "reason": "disconnected"}],
        "observations": {"runtime": {"session_uuid": "same"}},
    }
    assert tester_hooks._tester_trigger_hard_stop(evidence) is None
    evidence["observations"]["runtime"]["session_uuid"] = "different"
    assert "different logging session" in tester_hooks._tester_trigger_hard_stop(evidence)


def test_session_start_records_the_free_play_identity(monkeypatch):
    monkeypatch.setattr(
        tester_hooks,
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
        tester_hooks._finish_free_play_capture(timeout_s=0)


def test_free_play_refuses_to_close_logs_while_radar_thread_is_alive(monkeypatch):
    monkeypatch.setattr(
        server,
        "monitor",
        SimpleNamespace(stop=lambda: None, _capture_thread=SimpleNamespace(is_alive=lambda: True)),
    )
    with pytest.raises(RuntimeError, match="radar"):
        tester_hooks._finish_free_play_capture(timeout_s=0)


def test_an_exited_kiosk_does_not_leave_the_next_run_blocked(tmp_path):
    (tmp_path / "tester").mkdir()
    controller = free_play.FreePlayController(tmp_path, Jobs(), Jobs(), kiosk_port=8080)
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
        tester_hooks._finish_free_play_capture()


def test_free_play_starts_ops_even_if_the_distance_radar_cannot_connect(monkeypatch, tmp_path):
    class ReachedOps(Exception):
        pass

    def start_ops(**_kwargs):
        raise ReachedOps

    monkeypatch.setattr(server, "init_iwr6843", lambda **_kw: False)
    monkeypatch.setattr(server, "init_session_logger", lambda **_kw: None)
    monkeypatch.setattr(server, "start_monitor", start_ops)
    monkeypatch.setattr(server, "_cleanup_hardware_for_shutdown", lambda: None)
    monkeypatch.setattr(tester_hooks, "free_play_config", {})
    for name in ("ball_speed_correction_enabled", "ballistics_enabled"):
        monkeypatch.setattr(server, name, getattr(server, name))
    for name in ("tester_setup_required", "tester_config_hash", "study_mode_enabled"):
        monkeypatch.setattr(tester_hooks, name, getattr(tester_hooks, name))
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
    controller = free_play.FreePlayController(tmp_path, capture, packaging, kiosk_port=8080)
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
    # an old bundle is listed, but this job made none: never claim the old one
    monkeypatch.setattr(
        free_play.review, "read_analysis", lambda *_: analysis_with_bundle("old.zip", made=False)
    )
    assert (
        client.post(
            "/api/tester/free-play/package", json={**identity, "capture_closed": True}
        ).status_code
        == 202
    )
    packaging.done("analyze", 0)
    assert controller.status()["phase"] == "error"
    assert controller.status()["bundle"] is None


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


def test_delayed_done_from_an_old_run_cannot_cancel_its_replacement(tmp_path, monkeypatch):
    (tmp_path / "tester").mkdir()
    capture, packaging = Jobs(), Jobs()
    capture.job = {"state": "running", "action": "free_play"}
    controller = free_play.FreePlayController(tmp_path, capture, packaging, kiosk_port=8080)
    controller.begin("tester", "old", tmp_path / "tester")
    controller.run["phase"] = "ready"
    timers = []

    class Timer:
        def __init__(self, _delay, callback):
            timers.append(callback)

        def start(self):
            pass

    monkeypatch.setattr(free_play.threading, "Timer", Timer)
    app = Flask(__name__)
    controller.register(app)
    response = app.test_client().post(
        "/api/tester/free-play/close",
        json={
            "tester_id": "tester",
            "run_id": "old",
        },
    )
    assert response.status_code == 200
    controller.capture_finished("old", 0)
    controller.begin("tester", "new", tmp_path / "tester")
    timers[0]()
    assert capture.status()["state"] == "running"


def analysis_with_bundle(name, *, made=True, reused=False):
    """``review.read_analysis`` as it reads a finished analysis job (job.json + bundles)."""
    listed = {"name": name, "size_bytes": 1234, "sha256": "a" * 64}
    built = {"name": name, "path": f".bundles/{name}", "reused": reused, "sha256": "a" * 64}
    return {
        "state": "complete",
        "reason": None,
        "job": {"state": "complete", "bundle": built if made else None},
        "review_ready": True,
        "bundles": [listed],
        "latest_bundle": listed,
    }


def test_repackaging_into_the_same_bundle_name_is_ready_not_failed(tmp_path, monkeypatch):
    """An unchanged session reuses its latest bundle: the same name, and a success."""
    (tmp_path / "tester").mkdir()
    capture, packaging = Jobs(), Jobs()
    capture.job = {"state": "running", "action": "free_play"}
    monkeypatch.setattr(
        free_play.review, "read_analysis", lambda *_: analysis_with_bundle("tester.zip")
    )
    controller = free_play.FreePlayController(tmp_path, capture, packaging, kiosk_port=8080)
    controller.begin("tester", "one", tmp_path / "tester")
    controller.package("tester", "one")
    monkeypatch.setattr(
        free_play.review,
        "read_analysis",
        lambda *_: analysis_with_bundle("tester.zip", reused=True),
    )
    packaging.done("analyze", 0)
    state = controller.status()
    assert state["phase"] == "ready", state
    assert state["bundle"]["name"] == "tester.zip"
    assert state["bundle"]["reused"] is True
    assert state["bundle"]["path"].endswith("tester.zip")


def test_a_failed_analysis_is_an_error_even_with_a_bundle_listed(tmp_path, monkeypatch):
    (tmp_path / "tester").mkdir()
    capture, packaging = Jobs(), Jobs()
    capture.job = {"state": "running", "action": "free_play"}
    monkeypatch.setattr(
        free_play.review, "read_analysis", lambda *_: analysis_with_bundle("tester.zip")
    )
    controller = free_play.FreePlayController(tmp_path, capture, packaging, kiosk_port=8080)
    controller.begin("tester", "one", tmp_path / "tester")
    controller.package("tester", "one")
    packaging.done("analyze", 1)
    assert controller.status()["phase"] == "error"


def test_package_does_not_hold_the_lock_while_the_job_spawns(tmp_path):
    """Status polls answer while the package job waits (up to 10 s) for its process."""
    (tmp_path / "tester").mkdir()
    capture = Jobs()
    capture.job = {"state": "running", "action": "free_play"}
    spawning, release = threading.Event(), threading.Event()

    class SlowSpawn(Jobs):
        def start(self, *args, **kwargs):
            spawning.set()
            assert release.wait(5)
            super().start(*args, **kwargs)

    packaging = SlowSpawn()
    controller = free_play.FreePlayController(tmp_path, capture, packaging, kiosk_port=8080)
    controller.begin("tester", "one", tmp_path / "tester")
    worker = threading.Thread(target=controller.package, args=("tester", "one"), daemon=True)
    worker.start()
    try:
        assert spawning.wait(5)
        answered = []
        poll = threading.Thread(
            target=lambda: answered.append(controller.status("tester", "one")), daemon=True
        )
        poll.start()
        poll.join(timeout=1.0)
        assert answered, "status blocked behind the package job's spawn wait"
        assert answered[0]["phase"] == "packaging"
        # a second Stop during the wait starts nothing new
        assert controller.package("tester", "one")["phase"] == "packaging"
    finally:
        release.set()
        worker.join(timeout=5)
    assert len(packaging.calls) == 1


def test_a_spawn_failure_releases_the_run_with_its_error(tmp_path):
    (tmp_path / "tester").mkdir()
    capture = Jobs()
    capture.job = {"state": "running", "action": "free_play"}

    class NoSpawn(Jobs):
        def start(self, *_args, **_kwargs):
            raise RuntimeError("the analyze process did not start within 10 s")

    controller = free_play.FreePlayController(tmp_path, capture, NoSpawn(), kiosk_port=8080)
    controller.begin("tester", "one", tmp_path / "tester")
    with pytest.raises(RuntimeError, match="did not start"):
        controller.package("tester", "one")
    assert controller.status()["phase"] == "error"
    assert "did not start" in controller.status()["error"]


def test_stop_reaches_the_kiosk_on_its_configured_port(tmp_path, monkeypatch):
    (tmp_path / "tester").mkdir()
    controller = free_play.FreePlayController(tmp_path, Jobs(), Jobs(), kiosk_port=9123)
    controller.begin("tester", "one", tmp_path / "tester")
    app = Flask(__name__)
    controller.register(app)
    seen = []

    class Reply:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def read(self, *_args):
            return b'{"phase": "stopping"}'

    def urlopen(query, timeout):
        seen.append((query.full_url, timeout))
        return Reply()

    monkeypatch.setattr(free_play.urllib.request, "urlopen", urlopen)
    response = app.test_client().post(
        "/api/tester/free-play/stop", json={"tester_id": "tester", "run_id": "one"}
    )
    assert response.status_code == 202, response.get_json()
    assert seen[0][0] == "http://127.0.0.1:9123/api/free-play"


def test_the_tester_server_points_free_play_at_the_kiosk_web_port(tmp_path):
    app = eligible_app(sessions_root=tmp_path, manager=Jobs())
    assert app.extensions["openflight_free_play"].kiosk_port == ts.KIOSK_WEB_PORT


# --- the resolution tap and free play's exposure ladder (19 Oct) ------------------------

LOCK = {
    "applied_exposure_us": 150.0,
    "applied_gain": 4.0,
    "observation": {"light_index_dn_per_us_gain": 0.25},
}


def setup_locked_ball(root, tester="20260922-name", arms=("arm5",)):
    """A finished setup whose camera locked the resting ball: its light is measured."""
    from openflight import tee_range, tee_range_setup  # pylint: disable=import-outside-toplevel

    candidates = [
        tee_range.TeeRangeCandidate(
            candidate_id=f"camera-setup-1-{arm}",
            source="camera_reference_ball_size_range",
            source_group="camera",
            radar_slant_range_m=None,
            uncertainty_m=None,
            selectable=False,
            evidence={
                "result": {"status": "selected", "selected": SETUP_BALL_PX},
                "static_exposure_lock": LOCK,
            },
        )
        for arm in arms
    ]
    solution = tee_range.TeeRangeSolution.unresolved(
        candidates, reason="qualification_artifact_missing"
    )
    epoch = tee_range_setup.TeeRangeEvidenceEpoch(
        epoch_id="setup-1", created_at_utc="2026-09-29T18:00:00Z", solution=solution
    )
    tee_range_setup.write_epoch(root / tester, epoch, make_current=True)


def free_play_app(tmp_path, modes=("arm5", "arm6"), default_arm="arm5", ladder=None):
    setup_locked_ball(tmp_path)
    catalogue = ts.lens_profiles.load_catalogue()
    settings_path = tmp_path / "lens-settings.json"
    store = ts.lens_settings.LensSettingsStore(settings_path, catalogue)
    settings = {"modes": list(modes), "default_arm": default_arm}
    if ladder is not None:
        settings["ladder"] = ladder
    store.save_settings(catalogue.reference_lens_id, settings)
    manager = Jobs()
    app = eligible_app(sessions_root=tmp_path, manager=manager, lens_settings_path=settings_path)
    return app, manager


def start_free_play(app, **extra):
    return app.test_client().post(
        "/api/tester/run",
        json={
            "action": "free_play",
            "tester_id": "20260922-name",
            "arm_id": "arm1",
            "environment": "indoors",
            **extra,
        },
    )


def test_free_play_waits_for_the_tester_to_tap_a_resolution(tmp_path):
    app, manager = free_play_app(tmp_path)

    response = start_free_play(app)

    assert response.status_code == 400
    assert "resolution" in response.get_json()["error"]
    assert not manager.calls


def test_a_resolution_the_lens_disables_is_refused(tmp_path):
    app, manager = free_play_app(tmp_path, modes=("arm5",))

    response = start_free_play(app, resolution="arm6")

    assert response.status_code == 400
    assert "640" in response.get_json()["error"]
    assert not manager.calls


@pytest.mark.parametrize("arm_id,width,first", [("arm5", "1280", 300), ("arm6", "640", 300)])
def test_the_tapped_resolution_runs_its_ladder_from_the_first_rung(tmp_path, arm_id, width, first):
    app, manager = free_play_app(tmp_path, ladder={"arm5": [300, 150, 75], "arm6": [300, 75]})

    response = start_free_play(app, resolution=arm_id)

    assert response.status_code == 202, response.get_json()
    command = manager.calls[0][1][0]
    assert command[command.index("--camera-capture-width") + 1] == width
    # the ladder holds each rung's controls: the kiosk starts at the first, not 8 us x 1
    assert "--camera-capture-manual-exposure" in command
    assert command[command.index("--camera-capture-exposure-us") + 1] == str(first)
    assert float(command[command.index("--camera-capture-gain") + 1]) >= 1.0
    plan_path = Path(command[command.index("--free-play-ladder") + 1])
    run_dir = Path(command[command.index("--log-dir") + 1])
    assert plan_path == run_dir / fpl.PLAN_NAME
    plan = json.loads(plan_path.read_text())
    assert plan["resolution"]["arm_id"] == arm_id
    assert plan["rungs"][0]["exposure_us"] == first
    assert plan["startup"]["source"] == "first_rung"
    assert plan["resolution_choice"]["confirmed_by"] == "tester_tap"
    assert plan["resolution_choice"]["lens_default_arm"] == "arm5"
    state = response.get_json()["free_play"]
    assert state["resolution"]["arm_id"] == arm_id
    assert state["exposure_study"]["phase"] == "starting"


def test_a_ladder_the_light_cannot_run_starts_automatic_from_the_policy_seed(tmp_path, monkeypatch):
    app, manager = free_play_app(tmp_path)
    monkeypatch.setattr(
        ts, "setup_light_warning", lambda _solution: {"light_index_dn_per_us_gain": 0.00001}
    )

    response = start_free_play(app, resolution="arm5")

    assert response.status_code == 202, response.get_json()
    command = manager.calls[0][1][0]
    assert "--camera-capture-manual-exposure" not in command
    exposure = int(command[command.index("--camera-capture-exposure-us") + 1])
    gain = float(command[command.index("--camera-capture-gain") + 1])
    assert exposure * gain > 8.0 * 1.0  # never the near-black startup
    plan = json.loads(Path(command[command.index("--free-play-ladder") + 1]).read_text())
    assert plan["status"] == "not_run"


def test_free_play_without_a_plan_never_starts_near_black(tmp_path):
    commands, _ = ts.action_commands(
        "free_play",
        params(arm_id="arm5"),
        tmp_path,
        ts.DEFAULT_RIG_GEOMETRY,
        tester_setup=TESTER_SETUP,
    )
    command = commands[0]
    exposure = int(command[command.index("--camera-capture-exposure-us") + 1])
    gain = float(command[command.index("--camera-capture-gain") + 1])
    assert (exposure, gain) != (8, 1.0)
    assert exposure == 294


def test_the_tester_status_shows_the_kiosks_exposure_study(tmp_path):
    run = tmp_path / "tester" / "arm5" / "paired" / "run-01"
    run.mkdir(parents=True)
    controller = free_play.FreePlayController(tmp_path, Jobs(), Jobs(), kiosk_port=8080)
    built = fpl.build_plan(
        "arm5",
        [300, 150],
        light_index=0.25,
        sensor_limits=None,
        gain_basis={"source": "setup_lock", "signal_us": 600.0},
    )
    fpl.write_json(run / fpl.PLAN_NAME, built)
    controller.begin("tester", "one", run, resolution=built["resolution"])
    assert controller.status()["exposure_study"]["text"] == "Exposure study: starting"

    study = fpl.FreePlayExposureStudy(
        built,
        run / fpl.STATE_NAME,
        apply_controls=lambda *_: None,
        begin_automatic=lambda: None,
        run_async=lambda work: work(),
        settle_s=0.0,
    )
    study.on_shot(1)
    study.record_verdict(1, {"status": "judged", "color": "green"})

    shown = controller.status()["exposure_study"]
    assert shown["text"] == "Exposure study: 300 µs · swing 2 of 5 · setting 1 of 2"
    assert shown["rungs"][0]["counts"]["green"] == 1
    assert controller.status()["resolution"]["label"] == "1280×800"


# --- sending the package (decision 3, 19 Oct) ----------------------------------------


def test_free_play_packages_into_the_send_folder_and_reports_its_copy(tmp_path, monkeypatch):
    (tmp_path / "tester").mkdir()
    capture, packaging = Jobs(), Jobs()
    capture.job = {"state": "running", "action": "free_play"}
    send = tmp_path / "OpenFlight-to-send"
    analysis = analysis_with_bundle("tester.zip")
    analysis["job"]["bundle"]["send_copy"] = {
        "name": "tester.zip",
        "folder": "~/OpenFlight-to-send",
        "size_bytes": 1234,
        "method": "link",
        "error": None,
    }
    monkeypatch.setattr(free_play.review, "read_analysis", lambda *_: analysis)
    controller = free_play.FreePlayController(
        tmp_path, capture, packaging, kiosk_port=8080, send_dir=send
    )
    controller.begin("tester", "one", tmp_path / "tester")
    controller.package("tester", "one")

    command = packaging.calls[0][1][0]
    assert command[command.index("--send-dir") + 1] == str(send)
    packaging.done("analyze", 0)
    bundle = controller.status()["bundle"]
    assert bundle["size_bytes"] == 1234
    assert bundle["send_copy"]["folder"] == "~/OpenFlight-to-send"


def test_the_tester_page_is_told_the_send_folder_and_the_bundle_copy(tmp_path):
    send = tmp_path / "OpenFlight-to-send"
    app = eligible_app(sessions_root=tmp_path, manager=Jobs(), send_dir=send)
    body = (
        app.test_client()
        .post(
            "/api/tester/status",
            json={"tester_id": "20260922-name", "arm_id": "arm5", "environment": "indoors"},
        )
        .get_json()
    )
    assert body["analysis"]["send_folder"] == ts.session_bundle.display_path(send)
    assert body["analysis"]["send_copy"] is None

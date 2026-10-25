"""The kiosk's half of free play's exposure ladder: shots, verdicts, the log and the band."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask

from openflight import free_play, kiosk_extensions, server, tester_hooks
from openflight.camera import free_play_ladder as fpl
from openflight.launch_monitor import Shot
from tests.test_free_play_ladder import BALL, _clip

LOCK = {"source": "setup_lock", "signal_us": 600.0}


def _plan(ladder_us=(300, 150), light_index=0.25):
    return fpl.build_plan(
        "arm5", list(ladder_us), light_index=light_index, sensor_limits=None, gain_basis=LOCK
    )


class Runtime:
    """The capture runtime's seams the kiosk's study drives."""

    def __init__(self, exposure_us=300, gain=2.0):
        self.settings = SimpleNamespace(
            exposure_us=exposure_us,
            gain=gain,
            setup_ball=BALL,
            hitting_zone=None,
            auto_exposure=False,
            width=1280,
            height=800,
        )
        self.sensor_limits = None
        self.applied = []
        self.automatic = 0

    def apply_held_controls(self, exposure_us, gain):
        self.applied.append((exposure_us, gain))
        self.settings.exposure_us, self.settings.gain = exposure_us, gain

    def begin_automatic_exposure(self):
        self.automatic += 1
        return {"status": "seeded"}


@pytest.fixture
def kiosk(monkeypatch, tmp_path):
    """A free-play kiosk with its plan loaded, as main() leaves it."""
    plan = _plan()
    plan_path = tmp_path / fpl.PLAN_NAME
    fpl.write_json(plan_path, plan)
    runtime = Runtime()
    monkeypatch.setattr(server, "camera_capture_runtime", runtime)
    monkeypatch.setattr(
        tester_hooks,
        "free_play_config",
        {"enabled": True, "tester_id": "tester", "run_id": "run-one", "phase": "recording"},
    )
    monkeypatch.setattr(tester_hooks, "free_play_plan", None)
    monkeypatch.setattr(tester_hooks, "free_play_study", None)
    tester_hooks.load_free_play_plan(plan_path)
    study = tester_hooks.init_free_play_study(run_async=lambda work: work(), settle_s=0.0)
    return SimpleNamespace(plan=plan, runtime=runtime, study=study, run_dir=tmp_path)


def _shot(number):
    shot = Shot(ball_speed_mph=120.0, club_speed_mph=85.0, timestamp=datetime.now())
    shot.shot_number = number
    shot.extras["free_play_run_id"] = "run-one"
    return shot


def test_the_plan_and_resolution_are_recorded_in_session_start(kiosk):
    config = server._session_start_config()["free_play"]

    assert config["resolution"]["label"] == "1280×800"
    assert config["exposure_plan"]["rungs"][0]["rung_id"] == "full-300"
    assert config["exposure_plan"]["schema"] == fpl.PLAN_SCHEMA


def test_the_study_is_written_beside_the_plan(kiosk):
    saved = json.loads((kiosk.run_dir / fpl.STATE_NAME).read_text())

    assert saved["phase"] == "ladder"
    assert saved["run_id"] == "run-one"
    assert kiosk.runtime.applied == []  # started at the first rung's controls


def test_each_real_shot_is_counted_and_carries_its_ladder_record(kiosk):
    shot = _shot(1)

    tester_hooks._count_free_play_shot(shot)

    record = shot.to_dict()["free_play"]["exposure_study"]
    assert record["schema"] == fpl.SHOT_SCHEMA
    assert record["ladder_phase"] == "ladder"
    assert record["rung_id"] == "full-300"
    assert (record["exposure_us"], record["gain"]) == (300, 2.0)
    assert record["resolution"]["arm_id"] == "arm5"
    assert record["verdict"]["status"] == "no_capture"


def test_the_fifth_shot_moves_the_camera_to_the_next_rung_live(kiosk):
    for number in range(1, 6):
        tester_hooks._count_free_play_shot(_shot(number))

    assert kiosk.runtime.applied == [(150, 4.0)]
    sixth = _shot(6)
    tester_hooks._count_free_play_shot(sixth)
    assert sixth.extras["free_play_exposure_study"]["rung_id"] == "full-150"


def test_after_the_last_rung_the_camera_runs_automatic(kiosk):
    for number in range(1, 11):
        tester_hooks._count_free_play_shot(_shot(number))

    assert kiosk.runtime.automatic == 1
    after = _shot(11)
    tester_hooks._count_free_play_shot(after)
    assert after.extras["free_play_exposure_study"]["ladder_phase"] == "automatic"
    assert after.extras["free_play_exposure_study"]["exposure_us"] == 150


def test_a_matched_clip_gets_the_picture_verdict_on_the_shot_and_its_rung(kiosk, tmp_path):
    shot = _shot(1)
    tester_hooks._count_free_play_shot(shot)
    clip = _clip(tmp_path / "camera" / "camera_001", exposure_us=300, gain=2.0)
    capture = SimpleNamespace(path=clip, error=None, metadata={}, valid=True)

    tester_hooks._judge_free_play_clip(shot, capture)

    verdict = shot.to_dict()["free_play"]["exposure_study"]["verdict"]
    assert verdict["status"] == "judged"
    assert verdict["color"] == "green"
    assert verdict["judged_against"]["rung_id"] == "full-300"
    saved = json.loads((kiosk.run_dir / fpl.STATE_NAME).read_text())
    assert saved["rungs"][0]["swings"][0]["verdict"]["color"] == "green"
    assert saved["rungs"][0]["counts"]["green"] == 1


def test_a_failed_clip_save_is_labelled_not_judged(kiosk):
    shot = _shot(1)
    tester_hooks._count_free_play_shot(shot)
    capture = SimpleNamespace(path=None, error="disk full", metadata={}, valid=False)

    tester_hooks._judge_free_play_clip(shot, capture)

    verdict = shot.extras["free_play_exposure_study"]["verdict"]
    assert verdict["status"] == "no_capture"
    assert "disk full" in verdict["reason"]


def test_the_kiosk_band_reads_the_study_progress(kiosk, monkeypatch):
    app = Flask(__name__)
    free_play.register_kiosk_routes(
        app, tester_hooks.free_play_config, lambda: None, progress=tester_hooks._free_play_progress
    )

    body = app.test_client().get("/api/free-play").get_json()

    assert (
        body["exposure_study"]["text"] == "Exposure study: 300 µs · swing 1 of 5 · setting 1 of 2"
    )
    assert body["phase"] == "recording"


def test_the_ready_light_hears_the_ladder(kiosk, monkeypatch):
    monkeypatch.setattr(tester_hooks, "tester_setup_required", True)
    monkeypatch.setattr(server, "monitor", None)
    monkeypatch.setattr(server, "iwr6843_runtime", None)
    kiosk.runtime.ready_snapshot = lambda: {"running": True}

    states = server._ready_light_states()

    assert states.camera["exposure_study"] == {"phase": "ladder", "changing": False}


def test_stop_and_package_records_a_partial_ladder(kiosk, monkeypatch):
    for number in range(1, 4):
        tester_hooks._count_free_play_shot(_shot(number))
    kiosk.runtime.stop = lambda: None
    kiosk.runtime._worker = None
    monkeypatch.setattr(server, "monitor", None)
    monkeypatch.setattr(server, "iwr6843_runtime", None)
    monkeypatch.setattr(server, "inclinometer_service", None)
    monkeypatch.setattr(server, "get_session_logger", lambda: None)

    tester_hooks._finish_free_play_capture(timeout_s=0)

    saved = json.loads((kiosk.run_dir / fpl.STATE_NAME).read_text())
    assert saved["ladder_status"] == "partial"
    assert saved["rungs"][0]["status"] == "incomplete"
    assert saved["rungs"][1]["status"] == "not_reached"


def test_without_a_camera_the_shots_say_so(monkeypatch, tmp_path):
    plan_path = tmp_path / fpl.PLAN_NAME
    fpl.write_json(plan_path, _plan())
    monkeypatch.setattr(server, "camera_capture_runtime", None)
    monkeypatch.setattr(tester_hooks, "free_play_config", {"enabled": True, "run_id": "run-one"})
    monkeypatch.setattr(tester_hooks, "free_play_plan", None)
    monkeypatch.setattr(tester_hooks, "free_play_study", None)
    tester_hooks.load_free_play_plan(plan_path)
    tester_hooks.init_free_play_study()
    shot = _shot(1)

    tester_hooks._count_free_play_shot(shot)

    assert shot.extras["free_play_exposure_study"]["ladder_phase"] == "camera_unavailable"


def test_free_play_without_a_plan_records_no_study(monkeypatch):
    monkeypatch.setattr(tester_hooks, "free_play_config", {"enabled": True, "run_id": "run-one"})
    monkeypatch.setattr(tester_hooks, "free_play_plan", None)
    monkeypatch.setattr(tester_hooks, "free_play_study", None)
    shot = _shot(1)

    tester_hooks._count_free_play_shot(shot)

    assert shot.extras["free_play_exposure_study"] is None
    assert "exposure_study" not in shot.to_dict()["free_play"]


def test_an_unreadable_plan_refuses_to_start(tmp_path):
    path = tmp_path / fpl.PLAN_NAME
    path.write_text("{not json")

    with pytest.raises(ValueError, match="plan"):
        tester_hooks.load_free_play_plan(path)


def _function_source(module, name):
    source = Path(module.__file__).read_text(encoding="utf-8")
    body = source[source.index(f"def {name}(") :]
    return body[: body.index("\n\n\n")]  # up to the next top-level statement


def test_the_shot_and_clip_hooks_are_wired():
    # counted in the serialized shot callback, judged where the clip is matched
    assert 'call_all("on_shot_accepted", shot)' in _function_source(server, "_handle_shot_detected")
    assert "_log_camera_capture_match, shot, camera_capture" in _function_source(
        server, "_enrich_shot_from_optional_hardware"
    )
    hook = _function_source(server, "_log_camera_capture_match")
    assert '"on_camera_capture",' in hook and "kiosk_extensions.call_all," in hook
    assert "_count_free_play_shot(shot)" in _function_source(tester_hooks, "_on_shot_accepted")
    assert "_judge_free_play_clip(shot, camera_capture)" in _function_source(
        tester_hooks, "_on_camera_capture"
    )
    assert tester_hooks.extension in kiosk_extensions.registered()


def test_main_loads_the_plan_and_starts_the_study_before_the_radar(monkeypatch, tmp_path):
    import sys

    class ReachedOps(Exception):
        pass

    seen = {}

    def start_ops(**_kwargs):
        seen["study"] = tester_hooks.free_play_study
        raise ReachedOps

    plan_path = tmp_path / "run-01" / fpl.PLAN_NAME
    fpl.write_json(plan_path, _plan())
    monkeypatch.setattr(server, "init_iwr6843", lambda **_kw: False)
    monkeypatch.setattr(server, "init_session_logger", lambda **_kw: None)
    monkeypatch.setattr(server, "start_monitor", start_ops)
    monkeypatch.setattr(server, "_cleanup_hardware_for_shutdown", lambda: None)
    monkeypatch.setattr(tester_hooks, "free_play_config", {})
    monkeypatch.setattr(tester_hooks, "free_play_plan", None)
    monkeypatch.setattr(tester_hooks, "free_play_study", None)
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
            "--free-play-ladder",
            str(plan_path),
        ],
    )
    with pytest.raises(ReachedOps):
        server.main()

    assert tester_hooks.free_play_plan["resolution"]["arm_id"] == "arm5"
    assert seen["study"] is not None
    # no camera was started in this test: the study says so instead of guessing
    assert seen["study"].progress()["phase"] == "camera_unavailable"
    assert (plan_path.parent / fpl.STATE_NAME).is_file()

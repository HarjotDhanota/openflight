from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pytest

from openflight import tee_range, tee_range_setup
from openflight.camera import tester_server as ts
from openflight.camera.reference_ball_range import (
    BallPlaneCamera,
    ReferenceBallRangeCandidate,
    ReferenceBallRangeResult,
)


class EligibleSetup:
    def evaluate(self, tester_id, _reading):
        return self.require(tester_id, _reading, "read")

    def require(self, tester_id, _reading, _action):
        return {
            "eligible": True,
            "tester_id": tester_id,
            "config_hash": "fixture",
            "operator_confirmation": {"confirmed": True},
            "checks": [],
            "blockers": [],
        }

    def confirmation_valid(self, _tester_id):
        return True

    def current_config_hash(self):
        return "fixture"


class FakeTilt:
    bus = 1
    address = 0x18
    zero_offset_deg = 0.0

    def reading(self):
        return {"status": "stable", "camera_pitch_deg": 0.0, "roll_deg": 0.0}

    def start(self):
        return None

    def stop(self):
        return None


class FakeLive:
    def __init__(self):
        self.running = False
        self.arm = None

    def start(self, arm, *_args):
        self.running = True
        self.arm = arm

    def stop(self):
        self.running = False

    def recent_frames(self):
        frames = np.full((3, self.arm.height, self.arm.width), 80, dtype=np.uint8)
        return self.arm, frames

    def snapshot(self):
        return None, {"running": self.running}


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def profile(capture: str, power: list[float], config_hash: str, rig_hash: str) -> dict:
    return {
        "capture_sha256": capture * 64,
        "radar_profile_sha256": config_hash,
        "radar_profile_qualified": False,
        "rig_geometry_sha256": rig_hash,
        "capture_config_sha256": "e" * 64,
        "range_bin_start": 0,
        "range_bin_count": 96,
        "range_resolution_m": 0.04,
        "power": power,
    }


class StaticManager:
    def __init__(
        self,
        *,
        config_hash: str,
        firmware_hash: str,
        rig_hash: str,
        calibration_hash: str,
        fail=False,
    ):
        self.config_hash = config_hash
        self.firmware_hash = firmware_hash
        self.rig_hash = rig_hash
        self.calibration_hash = calibration_hash
        self.fail = fail
        self._status = {"state": "idle", "action": None, "message": "Ready"}

    def status(self):
        return dict(self._status)

    def start(self, action, commands, _log_path, on_finish=None, **_kwargs):
        assert action == "tee_range"
        command = list(commands[0])

        def value(flag):
            return command[command.index(flag) + 1]

        kind = value("--kind")
        capture_id = value("--capture-id")
        output = Path(value("--output-dir"))
        output.mkdir(parents=True, exist_ok=True)
        empty = np.ones(96).tolist()
        present = (np.ones(96) + np.where(np.arange(96) == 30, 30.0, 0.0)).tolist()
        record = {
            "capture_id": capture_id,
            "capture_kind": kind,
            "status": "error" if self.fail else "usable",
            "usable": not self.fail,
            "inputs": {
                "firmware": {"sha256": self.firmware_hash},
                "radar_config": {"sha256": self.config_hash},
                "rig_geometry": {"sha256": self.rig_hash},
                "calibration": {"sha256": self.calibration_hash},
            },
            "profile": None
            if self.fail
            else profile(
                "a" if kind == "empty" else "b",
                empty if kind == "empty" else present,
                self.config_hash,
                self.rig_hash,
            ),
            "error": {"message": "fixture failure"} if self.fail else None,
        }
        (output / f"{capture_id}.json").write_text(json.dumps(record), encoding="utf-8")
        if on_finish:
            on_finish(action, 1 if self.fail else 0)

    def cancel(self):
        return False


def camera_result(value: float) -> ReferenceBallRangeResult:
    candidate = ReferenceBallRangeCandidate(
        x_px=640.0,
        y_px=500.0,
        diameter_px=24.0,
        area_px=450,
        floor_point_lfu_m=(0.0, value, 0.021),
        floor_radar_range_m=value,
        floor_camera_range_m=value,
        size_camera_range_m=value,
        floor_range_uncertainty_m=0.02,
        size_range_uncertainty_m=0.03,
        range_disagreement_m=0.0,
        consistency_sigma=0.0,
        source="calibrated_qualified",
        confidence="high",
        score=12.0,
        rejection_reason=None,
    )
    return ReferenceBallRangeResult("selected", "high", candidate, (candidate,), {})


def qualification(paths, *, residual=0.08):
    return tee_range.TeeRangeQualification(
        rig_geometry_sha256=file_hash(paths["rig"]),
        camera_calibration_sha256=file_hash(paths["camera"]),
        camera_arm_id="arm5",
        iwr_firmware_sha256=file_hash(paths["firmware"]),
        iwr_capture_config_sha256=file_hash(paths["config"]),
        iwr_profile_sha256="e" * 64,
        iwr_range_calibration_sha256=file_hash(paths["calibration"]),
        policy_version=tee_range.PROMOTION_POLICY_VERSION,
        scope="tester_setup",
        accuracy_qualified=True,
        plausible_range_m=(0.5, 4.0),
        max_camera_uncertainty_m=0.1,
        max_iwr_uncertainty_m=0.1,
        max_absolute_residual_m=residual,
        max_normalized_residual_sigma=4.0,
    )


@pytest.fixture
def inputs(tmp_path):
    paths = {
        "rig": tmp_path / "rig.json",
        "camera": tmp_path / "camera.json",
        "placement": tmp_path / "placement.json",
        "firmware": tmp_path / "firmware.bin",
        "config": tmp_path / "static.cfg",
        "calibration": tmp_path / "iwr-cal.json",
        "qualification": tmp_path / "qualification.json",
    }
    paths["rig"].write_text("{}", encoding="utf-8")
    paths["camera"].write_text("{}", encoding="utf-8")
    paths["placement"].write_text("{}", encoding="utf-8")
    paths["firmware"].write_bytes(b"firmware")
    paths["config"].write_text("profile", encoding="utf-8")
    paths["calibration"].write_text('{"range_bias_const_m": 0.0}', encoding="utf-8")
    artifact = qualification(paths)
    paths["qualification"].write_text(json.dumps(artifact.to_dict()), encoding="utf-8")
    return paths


def app_for(tmp_path, inputs, monkeypatch, *, qualified=True, camera_m=1.2, fail=False):
    def camera_model(arm, *_args):
        return BallPlaneCamera.nominal(
            focal_px=933.0 if arm.width == 1280 else 466.5,
            image_width_px=arm.width,
            image_height_px=arm.height,
            pitch_deg=0.0,
            roll_correction_deg=0.0,
            mirror_horizontal=False,
            camera_origin_lfu=(0.0, 0.0, 0.095),
            radar_origin_lfu=(0.0, -0.03, 0.051),
            angular_uncertainty_deg=0.1,
            focal_relative_uncertainty=0.01,
        )

    monkeypatch.setattr(ts, "_reference_ball_camera", camera_model)
    monkeypatch.setattr(
        ts, "estimate_reference_ball_range", lambda *_args, **_kwargs: camera_result(camera_m)
    )
    manager = StaticManager(
        config_hash=file_hash(inputs["config"]),
        firmware_hash=file_hash(inputs["firmware"]),
        rig_hash=file_hash(inputs["rig"]),
        calibration_hash=file_hash(inputs["calibration"]),
        fail=fail,
    )
    app = ts.create_app(
        sessions_root=tmp_path / "sessions",
        rig_geometry=inputs["rig"],
        manager=manager,
        live_view=FakeLive(),
        tilt=FakeTilt(),
        setup_policy=EligibleSetup(),
        optical_calibration=inputs["camera"],
        camera_placement=inputs["placement"],
        iwr_static_config=inputs["config"],
        iwr_firmware=inputs["firmware"],
        iwr_calibration=inputs["calibration"],
        tee_range_qualification=inputs["qualification"] if qualified else None,
        require_tee_range_flow=True,
    )
    tester = "guided-fixture"
    for arm_id in ("arm5", "arm6"):
        params = ts.TesterParameters(tester, arm_id, "indoors")
        ts.write_arm_state(
            tmp_path / "sessions", params, gain=4.0, gain_exposure_us=params.arm.exposure_us
        )
    return app, tester


def post(client, tester, action, request_id):
    return client.post(
        "/api/tester/tee-range",
        json={"tester_id": tester, "action": action, "request_id": request_id},
    )


def phase(client, tester):
    response = client.get("/api/tester/tee-range", query_string={"tester_id": tester})
    assert response.status_code == 200
    return response.get_json()["state"]


def drive(client, tester):
    actions = (
        "start",
        "capture_empty",
        "capture_ball",
        "start_camera_arm5",
        "evaluate_camera_arm5",
        "start_camera_arm6",
        "evaluate_camera_arm6",
    )
    for index, action in enumerate(actions):
        response = post(client, tester, action, f"request-{index}")
        assert response.status_code == 200, response.get_json()
    return phase(client, tester)


def test_guided_flow_resolves_and_survives_reload(tmp_path, inputs, monkeypatch):
    app, tester = app_for(tmp_path, inputs, monkeypatch)
    client = app.test_client()
    state = drive(client, tester)
    assert state["phase"] == "resolved"
    assert state["solution"]["selected_range_m"] == pytest.approx(1.2)
    assert "tee_mm" not in json.dumps(state)
    identity = state["evidence"]["camera_arm5_candidate"]["evidence"]["capture_identity"]
    assert identity["saved_frame_sha256"]
    assert identity["rig_geometry"]["sha256"] == file_hash(inputs["rig"])
    assert identity["optical_calibration"]["sha256"] == file_hash(inputs["camera"])
    assert identity["camera_placement"]["sha256"] == file_hash(inputs["placement"])
    assert identity["mode"]["arm"]["arm_id"] == "arm5"
    reloaded = (
        app.test_client()
        .get("/api/tester/tee-range", query_string={"tester_id": tester})
        .get_json()["state"]
    )
    assert reloaded == state
    solution = tee_range_setup.load_current_epoch(tmp_path / "sessions" / tester).solution
    assert ts._tee_range_cli_args(solution) == ["--iwr6843-tee-m", "1.2"]


def test_missing_qualification_and_disagreement_remain_raw_only(tmp_path, inputs, monkeypatch):
    app, tester = app_for(tmp_path, inputs, monkeypatch, qualified=False)
    missing = drive(app.test_client(), tester)
    assert missing["phase"] == "raw_only"
    assert missing["solution"]["reason"] == "qualification_artifact_missing"
    assert ts._tee_range_cli_args(tee_range.TeeRangeSolution.from_dict(missing["solution"])) == [
        "--iwr6843-tee-range-pending"
    ]
    other_root = tmp_path / "other"
    app, tester = app_for(other_root, inputs, monkeypatch, camera_m=1.5)
    disagreed = drive(app.test_client(), tester)
    assert disagreed["phase"] == "raw_only"
    assert disagreed["solution"]["reason"] == "absolute_residual_exceeds_policy"


def test_requests_are_idempotent_and_failures_retry_without_erasing_evidence(
    tmp_path, inputs, monkeypatch
):
    app, tester = app_for(tmp_path, inputs, monkeypatch, fail=True)
    client = app.test_client()
    first = post(client, tester, "start", "same").get_json()["state"]
    second = post(client, tester, "start", "same").get_json()["state"]
    assert second["epoch_id"] == first["epoch_id"]
    assert second["sequence"] == first["sequence"]
    post(client, tester, "capture_empty", "empty")
    failed = phase(client, tester)
    assert failed["phase"] == "retryable_failure"
    assert failed["evidence"]["empty_capture"]["usable"] is False
    retried = post(client, tester, "retry", "retry").get_json()["state"]
    assert retried["phase"] == "needs_empty"
    restarted = post(client, tester, "ball_moved", "new-epoch").get_json()["state"]
    assert restarted["epoch_id"] != first["epoch_id"]


def test_concurrent_arm_and_placement_writes_keep_both_updates(tmp_path):
    sessions = tmp_path / "sessions"
    params = ts.TesterParameters("concurrent", "arm5", "indoors")
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(ts.write_arm_state, sessions, params, first="one")
        second = pool.submit(ts.write_arm_state, sessions, params, second="two")
        first.result()
        second.result()
    state = ts.read_arm_state(sessions, params.tester_id, params.arm_id)
    assert state["first"] == "one"
    assert state["second"] == "two"
    frame = np.zeros((20, 30), dtype=np.uint8)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(ts.record_placement, sessions, params, {}, frame) for _ in range(2)]
        saved = sorted(future.result() for future in futures)
    assert saved == [1, 2]
    placements = sessions / "concurrent" / "calibration" / "placements.jsonl"
    rows = [json.loads(line) for line in placements.read_text(encoding="utf-8").splitlines()]
    assert [row["placement"] for row in rows] == [1, 2]
    assert len({row["frame"] for row in rows}) == 2
    assert all(row["frame_sha256"] for row in rows)

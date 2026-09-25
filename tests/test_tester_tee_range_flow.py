from __future__ import annotations

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from openflight import tee_range, tee_range_setup
from openflight.camera import tee_range_flow, tester_server as ts
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
        self.start_count = 0
        self.stop_count = 0
        self.error = None
        self.analyzer = None

    def start(self, arm, *_args, analyzer=None):
        self.running = True
        self.arm = arm
        self.analyzer = analyzer
        self.start_count += 1
        if analyzer is not None:
            frames = self.recent_frames()[1]
            for observed_at in (1.0, 1.5, 2.0):
                analyzer.observe(frames, observed_at=observed_at)

    def stop(self):
        self.running = False
        self.stop_count += 1

    def recent_frames(self):
        frames = np.full((3, self.arm.height, self.arm.width), 80, dtype=np.uint8)
        return self.arm, frames

    def snapshot(self):
        return None, {
            "running": self.running,
            "error": self.error,
            "association": self.analyzer.snapshot() if self.analyzer is not None else None,
        }


class ChangingFakeLive(FakeLive):
    def __init__(self):
        super().__init__()
        self.value = 79

    def start(self, arm, *_args, **kwargs):
        super().start(arm, *_args, **kwargs)
        self.value += 1

    def recent_frames(self):
        frames = np.full((3, self.arm.height, self.arm.width), self.value, dtype=np.uint8)
        return self.arm, frames


class UnreadyFakeLive(FakeLive):
    def start(self, arm, *_args, analyzer=None):
        self.running = True
        self.arm = arm
        self.analyzer = analyzer
        self.start_count += 1


class IneligibleSetup(EligibleSetup):
    def require(self, tester_id, _reading, _action):
        result = super().require(tester_id, _reading, _action)
        return {**result, "eligible": False, "blockers": [{"id": "lis3dh"}]}


class MutableSetup(EligibleSetup):
    def __init__(self):
        self.eligible = True

    def require(self, tester_id, _reading, _action):
        result = super().require(tester_id, _reading, _action)
        return {
            **result,
            "eligible": self.eligible,
            "blockers": [] if self.eligible else [{"id": "lis3dh"}],
        }


class ReconfirmedSetup(EligibleSetup):
    def __init__(self):
        self.confirmed_at = "first-server"

    def require(self, tester_id, _reading, _action):
        result = super().require(tester_id, _reading, _action)
        return {
            **result,
            "operator_confirmation": {
                "confirmed": True,
                "confirmed_at": self.confirmed_at,
                "authority": "operator_physical_setup",
            },
        }


class MutableTilt(FakeTilt):
    def __init__(self):
        self.pitch = 0.0

    def reading(self):
        return {"status": "stable", "camera_pitch_deg": self.pitch, "roll_deg": 0.0}


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
        self.last_command = None
        self.start_count = 0
        self._status = {"state": "idle", "action": None, "message": "Ready"}

    def status(self):
        return dict(self._status)

    def start(self, action, commands, _log_path, on_finish=None, **_kwargs):
        self.start_count += 1
        if action == "preflight":
            if on_finish:
                on_finish(action, 0)
            return
        assert action == "tee_range"
        command = list(commands[0])
        self.last_command = command

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
            "error": {
                "stage": "connect",
                "type": "RuntimeError",
                "message": "no IWR6843 CLI found — board on, flashed, single-port fw?",
            }
            if self.fail
            else None,
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
        camera_placement_sha256=file_hash(paths["placement"]),
        camera_mode_profile_sha256=ts._camera_mode_profile_sha256(ts.ARMS["arm5"], paths["camera"]),
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
    paths["calibration"].write_text(
        '{"range_bias_const_m": 0.0, "range_bias_uncertainty_m": 0.01}',
        encoding="utf-8",
    )
    artifact = qualification(paths)
    paths["qualification"].write_text(json.dumps(artifact.to_dict()), encoding="utf-8")
    return paths


def app_for(
    tmp_path,
    inputs,
    monkeypatch,
    *,
    qualified=True,
    camera_m=1.2,
    fail=False,
    setup_policy=None,
    live_view=None,
    tilt=None,
    require_iwr_preflight=False,
):
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
        live_view=live_view or FakeLive(),
        tilt=tilt or FakeTilt(),
        setup_policy=setup_policy or EligibleSetup(),
        optical_calibration=inputs["camera"],
        camera_placement=inputs["placement"],
        iwr_static_config=inputs["config"],
        iwr_firmware=inputs["firmware"],
        iwr_calibration=inputs["calibration"],
        tee_range_qualification=inputs["qualification"] if qualified else None,
        require_tee_range_flow=True,
        iwr_static_port="/dev/serial/by-id/iwr-if00-port0",
        require_iwr_preflight=require_iwr_preflight,
    )
    app.config["TEST_STATIC_MANAGER"] = manager
    tester = "guided-fixture"
    for arm_id in ("arm5", "arm6"):
        params = ts.TesterParameters(tester, arm_id, "indoors")
        ts.write_arm_state(
            tmp_path / "sessions", params, gain=4.0, gain_exposure_us=params.arm.exposure_us
        )
    return app, tester


def test_guided_static_capture_uses_the_configured_iwr_port(tmp_path, inputs, monkeypatch):
    app, tester = app_for(tmp_path, inputs, monkeypatch)
    client = app.test_client()
    assert post(client, tester, "start", "start").status_code == 200
    assert post(client, tester, "capture_empty", "empty").status_code == 200

    command = app.config["TEST_STATIC_MANAGER"].last_command
    assert command[command.index("--port") + 1] == "/dev/serial/by-id/iwr-if00-port0"


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
    camera_evidence = state["evidence"]["camera_arm5_candidate"]["evidence"]
    assert identity["saved_frame_sha256"]
    assert identity["rig_geometry"]["sha256"] == file_hash(inputs["rig"])
    assert identity["optical_calibration"]["sha256"] == file_hash(inputs["camera"])
    assert identity["camera_placement"]["sha256"] == file_hash(inputs["placement"])
    assert identity["mode"]["arm"]["arm_id"] == "arm5"
    assert camera_evidence["live_readiness"]["save_eligible"] is True
    assert camera_evidence["save_camera_only_analysis"]["dependency_facts"] == {
        "iwr_range_used": False,
        "manual_range_used": False,
        "prior_canonical_range_used": False,
    }
    assert (
        camera_evidence["live_readiness"]["selected"]
        == camera_evidence["save_camera_only_analysis"]["selected"]
    )
    iwr = state["evidence"]["iwr_candidate"]
    assert iwr["evidence"]["bias_uncertainty"] == {
        "value_m": 0.01,
        "source": "hashed_range_calibration",
    }
    assert iwr["uncertainty_m"] > 0.01
    reloaded = (
        app.test_client()
        .get("/api/tester/tee-range", query_string={"tester_id": tester})
        .get_json()["state"]
    )
    assert reloaded == state
    solution = tee_range_setup.load_current_epoch(tmp_path / "sessions" / tester).solution
    assert ts._tee_range_cli_args(solution) == ["--iwr6843-tee-m", "1.2"]


def test_start_over_stops_only_the_guided_camera_owner(tmp_path, inputs, monkeypatch):
    live = FakeLive()
    app, tester = app_for(tmp_path, inputs, monkeypatch, live_view=live)
    client = app.test_client()
    for index, action in enumerate(("start", "capture_empty", "capture_ball", "start_camera_arm5")):
        assert post(client, tester, action, f"request-{index}").status_code == 200
    assert live.running is True
    assert client.get("/api/tester/live").get_json()["owner"]["kind"] == "guided_tee_range"

    response = post(client, tester, "start_over", "restart")

    assert response.status_code == 200
    assert response.get_json()["state"]["phase"] == "needs_empty"
    assert live.running is False
    assert live.stop_count == 1
    assert client.get("/api/tester/live").get_json()["owner"] is None


def test_backend_refuses_save_before_camera_only_selection_is_stable(tmp_path, inputs, monkeypatch):
    live = UnreadyFakeLive()
    app, tester = app_for(tmp_path, inputs, monkeypatch, live_view=live)
    client = app.test_client()
    for index, action in enumerate(("start", "capture_empty", "capture_ball", "start_camera_arm5")):
        assert post(client, tester, action, f"request-{index}").status_code == 200

    response = post(client, tester, "evaluate_camera_arm5", "too-early")

    assert response.status_code == 409
    assert "not ready to save" in response.get_json()["error"]
    assert phase(client, tester)["phase"] == "camera_arm5_capturing"
    assert live.running is True


def test_latest_ambiguous_frames_are_preserved_and_withheld_after_live_readiness(
    tmp_path, inputs, monkeypatch
):
    app, tester = app_for(tmp_path, inputs, monkeypatch)
    client = app.test_client()
    for index, action in enumerate(("start", "capture_empty", "capture_ball", "start_camera_arm5")):
        assert post(client, tester, action, f"request-{index}").status_code == 200
    first = camera_result(1.2).candidates[0]
    second = replace(first, x_px=700.0)
    monkeypatch.setattr(
        ts,
        "estimate_reference_ball_range",
        lambda *_args, **_kwargs: ReferenceBallRangeResult(
            "ambiguous", "withheld", None, (first, second), {"plausible_candidate_count": 2}
        ),
    )

    response = post(client, tester, "evaluate_camera_arm5", "became-ambiguous")

    assert response.status_code == 200
    state = response.get_json()["state"]
    assert state["phase"] == "retryable_failure"
    assert state["retry_phase"] == "needs_camera_arm5"
    attempt = next(
        value for key, value in state["evidence"].items() if key.startswith("camera_arm5_attempt_")
    )
    assert attempt["status"] == "association_withheld"
    assert attempt["frame_sha256"]
    assert attempt["camera_only_analysis"]["status"] == "ambiguous"
    assert state["evidence"]["camera_capture_failure"]["stage"] == "camera-only association"


def test_new_flow_does_not_stop_an_unrelated_standalone_live_view(tmp_path, inputs, monkeypatch):
    live = FakeLive()
    app, tester = app_for(tmp_path, inputs, monkeypatch, live_view=live)
    client = app.test_client()
    body = {
        "tester_id": tester,
        "arm_id": "arm5",
        "environment": "indoors",
        "exposure_us": 300,
        "gain": 4,
    }
    assert client.post("/api/tester/live", json=body).status_code == 200
    assert live.running is True

    response = post(client, tester, "start", "start")

    assert response.status_code == 200
    assert live.running is True
    assert live.stop_count == 0
    assert client.get("/api/tester/live").get_json()["owner"] is None


def test_general_stop_releases_the_guided_camera_owner(tmp_path, inputs, monkeypatch):
    live = FakeLive()
    app, tester = app_for(tmp_path, inputs, monkeypatch, live_view=live)
    client = app.test_client()
    for index, action in enumerate(("start", "capture_empty", "capture_ball", "start_camera_arm5")):
        assert post(client, tester, action, f"request-{index}").status_code == 200

    response = client.post("/api/tester/stop")

    assert response.status_code == 200
    assert response.get_json()["stopped"] is True
    assert live.running is False
    assert client.get("/api/tester/live").get_json()["owner"] is None


@pytest.mark.parametrize(
    "calibration",
    [
        '{"range_bias_const_m": 0.0}',
        '{"range_bias_const_m": 0.0, "range_bias_uncertainty_m": 0.0}',
        '{"range_bias_const_m": 0.0, "range_bias_uncertainty_m": -0.01}',
        '{"range_bias_const_m": 0.0, "range_bias_uncertainty_m": "NaN"}',
    ],
)
def test_invalid_bias_uncertainty_can_never_promote_static_iwr(
    tmp_path, inputs, monkeypatch, calibration
):
    inputs["calibration"].write_text(calibration, encoding="utf-8")
    artifact = qualification(inputs)
    inputs["qualification"].write_text(json.dumps(artifact.to_dict()), encoding="utf-8")

    app, tester = app_for(tmp_path, inputs, monkeypatch)
    state = drive(app.test_client(), tester)

    assert state["phase"] == "raw_only"
    assert state["solution"]["reason"] == "qualified_static_iwr_candidate_missing"
    iwr = state["evidence"]["iwr_candidate"]
    assert iwr["evidence"]["qualification"]["accuracy_qualified"] is False
    assert iwr["evidence"]["bias_uncertainty"] == "unavailable"


def test_changed_camera_placement_cannot_match_a_qualified_artifact(tmp_path, inputs, monkeypatch):
    inputs["placement"].write_text('{"changed": true}', encoding="utf-8")

    app, tester = app_for(tmp_path, inputs, monkeypatch)
    state = drive(app.test_client(), tester)

    assert state["phase"] == "raw_only"
    assert state["solution"]["reason"] == "qualified_camera_candidate_missing"
    camera = state["evidence"]["camera_arm5_candidate"]
    assert camera["evidence"]["qualification"]["status"] == "rejected"


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
    assert failed["evidence"]["capture_failure"] == {
        "capture_kind": "empty",
        "stage": "connect",
        "type": "RuntimeError",
        "message": "no IWR6843 CLI found — board on, flashed, single-port fw?",
        "remedy": (
            "Power the IWR6843, set its switches to functional mode, press RESET, and verify "
            "the CP2105 Enhanced/UARTA interface (if00) is present. If auto-detection still "
            "misses it, restart the tester with --iwr-static-port set to its stable "
            "/dev/serial/by-id/...-if00-port0 path."
        ),
    }
    retried = post(client, tester, "retry", "retry").get_json()["state"]
    assert retried["phase"] == "needs_empty"
    assert app.config["TEST_STATIC_MANAGER"].start_count == 1
    restarted = post(client, tester, "ball_moved", "new-epoch").get_json()["state"]
    assert restarted["epoch_id"] != first["epoch_id"]


def test_unusable_static_capture_invalidates_the_required_iwr_preflight(
    tmp_path, inputs, monkeypatch
):
    app, tester = app_for(
        tmp_path,
        inputs,
        monkeypatch,
        fail=True,
        require_iwr_preflight=True,
    )
    client = app.test_client()
    preflight = client.post(
        "/api/tester/run",
        json={
            "tester_id": tester,
            "arm_id": "arm5",
            "environment": "indoors",
            "action": "preflight",
        },
    )
    assert preflight.status_code == 202
    assert post(client, tester, "start", "start").status_code == 200
    assert post(client, tester, "capture_empty", "empty").status_code == 200

    eligibility = client.get(
        "/api/tester/setup-eligibility", query_string={"tester_id": tester}
    ).get_json()
    assert eligibility["eligible"] is False
    assert eligibility["blockers"][-1]["id"] == "iwr6843_cli"
    retry = post(client, tester, "retry", "retry")
    assert retry.status_code == 409
    assert retry.get_json()["setup_eligibility"]["blockers"][-1]["id"] == "iwr6843_cli"


def test_direct_range_api_fails_closed_when_setup_is_not_eligible(tmp_path, inputs, monkeypatch):
    app, tester = app_for(tmp_path, inputs, monkeypatch, setup_policy=IneligibleSetup())

    response = post(app.test_client(), tester, "start", "blocked")

    assert response.status_code == 409
    assert response.get_json()["setup_eligibility"]["eligible"] is False


def test_direct_range_api_revalidates_setup_before_new_or_idempotent_evidence(
    tmp_path, inputs, monkeypatch
):
    setup = MutableSetup()
    app, tester = app_for(tmp_path, inputs, monkeypatch, setup_policy=setup)
    client = app.test_client()
    assert post(client, tester, "start", "same-request").status_code == 200
    setup.eligible = False

    new_evidence = post(client, tester, "capture_empty", "capture")
    repeated = post(client, tester, "start", "same-request")

    assert new_evidence.status_code == 409
    assert new_evidence.get_json()["setup_eligibility"]["blockers"] == [{"id": "lis3dh"}]
    assert repeated.status_code == 409
    assert repeated.get_json()["setup_eligibility"]["eligible"] is False


def test_direct_range_api_requires_start_over_after_orientation_changes(
    tmp_path, inputs, monkeypatch
):
    tilt = MutableTilt()
    app, tester = app_for(tmp_path, inputs, monkeypatch, tilt=tilt)
    client = app.test_client()
    assert post(client, tester, "start", "start").status_code == 200
    tilt.pitch = ts.TEE_RANGE_ORIENTATION_DRIFT_DEG + 0.1

    response = post(client, tester, "capture_empty", "capture")

    assert response.status_code == 409
    assert response.get_json()["start_over_required"] is True
    assert "orientation changed" in response.get_json()["error"]


def test_reconfirmation_persists_start_over_required_in_the_guided_state(
    tmp_path, inputs, monkeypatch
):
    setup = ReconfirmedSetup()
    app, tester = app_for(tmp_path, inputs, monkeypatch, setup_policy=setup)
    client = app.test_client()
    assert post(client, tester, "start", "start").status_code == 200
    setup.confirmed_at = "after-restart"

    response = post(client, tester, "capture_empty", "stale-epoch")

    body = response.get_json()
    assert response.status_code == 409
    assert body["start_over_required"] is True
    assert body["state"]["phase"] == "retryable_failure"
    assert body["state"]["retry_phase"] is None
    assert "admission changed" in body["state"]["reason"]
    assert phase(client, tester) == body["state"]


def test_start_over_persistence_that_loses_a_race_reports_the_stored_state(
    tmp_path, inputs, monkeypatch
):
    setup = ReconfirmedSetup()
    app, tester = app_for(tmp_path, inputs, monkeypatch, setup_policy=setup)
    client = app.test_client()
    assert post(client, tester, "start", "start").status_code == 200
    stored = phase(client, tester)
    setup.confirmed_at = "after-restart"

    def lost_race(*_args, **_kwargs):
        raise RuntimeError("tee-range setup state changed; reload and retry")

    monkeypatch.setattr(tee_range_flow.FlowStore, "transition", lost_race)
    response = post(client, tester, "capture_empty", "stale-epoch")

    assert response.status_code == 409
    assert response.get_json()["start_over_required"] is True
    assert response.get_json()["state"] == stored


def test_retry_after_a_repassed_hardware_check_is_state_only(tmp_path, inputs, monkeypatch):
    app, tester = app_for(tmp_path, inputs, monkeypatch, fail=True, require_iwr_preflight=True)
    client = app.test_client()
    manager = app.config["TEST_STATIC_MANAGER"]
    body = {"tester_id": tester, "arm_id": "arm5", "environment": "indoors", "action": "preflight"}
    assert client.post("/api/tester/run", json=body).status_code == 202
    assert post(client, tester, "start", "start").status_code == 200
    assert post(client, tester, "capture_empty", "empty").status_code == 200
    assert post(client, tester, "retry", "blocked").status_code == 409
    assert client.post("/api/tester/run", json=body).status_code == 202
    hardware_starts = manager.start_count

    retried = post(client, tester, "retry", "retry")

    assert retried.status_code == 200
    assert retried.get_json()["state"]["phase"] == "needs_empty"
    assert manager.start_count == hardware_starts
    manager.fail = False
    assert post(client, tester, "capture_empty", "empty-again").status_code == 200
    assert manager.start_count == hardware_starts + 1
    command = manager.last_command
    assert command[command.index("--port") + 1] == "/dev/serial/by-id/iwr-if00-port0"
    assert phase(client, tester)["phase"] == "needs_ball"


def test_incomplete_dump_failure_names_the_transfer_and_its_remedy():
    failure = ts._static_capture_failure(
        {"error": {"stage": "read_dump", "type": "IWR6843DumpRecoveryError", "message": "ended"}},
        "empty",
    )

    assert failure["stage"] == "read_dump"
    assert "did not complete" in failure["remedy"]
    assert "press reset" in failure["remedy"].lower()


def test_finalize_publishes_terminal_state_only_after_epoch_pointer(tmp_path, monkeypatch):
    store = tee_range_flow.FlowStore(tmp_path / "tester")
    state = store.start("start", setup_admission={"identity_sha256": "a" * 64})
    state = store.transition(state, phase="evaluating", reason="ready")
    solution = tee_range.TeeRangeSolution.unresolved(reason="raw_only")
    original = tee_range_flow.write_epoch
    calls = 0

    def interrupted(*args, **kwargs):
        nonlocal calls
        calls += 1
        reference = original(*args, **kwargs)
        if calls == 1:
            raise OSError("simulated crash after current pointer")
        return reference

    monkeypatch.setattr(tee_range_flow, "write_epoch", interrupted)
    with pytest.raises(OSError, match="simulated crash"):
        store.finalize(state, solution)

    assert store.load().phase == "evaluating"
    finished = store.finalize(store.load(), solution)
    assert finished.phase == "raw_only"
    assert finished.evidence["final_reference"]["epoch_id"] == state.epoch_id


def test_admission_refuses_a_current_pointer_from_another_epoch(tmp_path, inputs, monkeypatch):
    app, tester = app_for(tmp_path, inputs, monkeypatch)
    client = app.test_client()
    state = drive(client, tester)
    assert state["phase"] == "resolved"
    other = tee_range_setup.TeeRangeEvidenceEpoch(
        epoch_id="stale-other-epoch",
        created_at_utc="2026-09-25T18:00:00Z",
        solution=tee_range.TeeRangeSolution.unresolved(reason="stale"),
    )
    tee_range_setup.write_epoch(tmp_path / "sessions" / tester, other, make_current=True)

    response = client.post(
        "/api/tester/ladder/start",
        json={"tester_id": tester, "arm_id": "arm5", "environment": "indoors"},
    )

    assert response.status_code == 409
    assert "retryable_failure" in response.get_json()["error"]
    assert "does not match" in phase(client, tester)["reason"]


def test_restart_waits_for_a_live_detached_static_capture_and_reconciles_late_result(
    tmp_path, inputs, monkeypatch
):
    app, tester = app_for(tmp_path, inputs, monkeypatch)
    root = tmp_path / "sessions" / tester
    store = tee_range_flow.FlowStore(root)
    binding = ts._tee_range_setup_binding(
        EligibleSetup().require(tester, {}, "start"), FakeTilt().reading()
    )
    state = store.start("start", setup_admission=binding)
    state = store.transition(
        state,
        phase="empty_capturing",
        reason="capturing_empty",
        request_id="capture",
        evidence={"empty_capture_id": "empty-detached"},
    )
    output = store.epoch_dir(state.epoch_id) / "iwr"
    output.mkdir(parents=True)
    (output / ".empty-detached.reserve").write_text(
        f"pid={os.getpid()} capture_id=empty-detached\n", encoding="utf-8"
    )

    client = app.test_client()
    assert phase(client, tester)["phase"] == "empty_capturing"

    record = {
        "capture_id": "empty-detached",
        "capture_kind": "empty",
        "status": "usable",
        "usable": True,
        "profile": profile(
            "a", np.ones(96).tolist(), file_hash(inputs["config"]), file_hash(inputs["rig"])
        ),
    }
    (output / "empty-detached.json").write_text(json.dumps(record), encoding="utf-8")
    assert phase(client, tester)["phase"] == "needs_ball"


def test_restart_marks_a_dead_detached_static_capture_retryable(tmp_path, inputs, monkeypatch):
    app, tester = app_for(tmp_path, inputs, monkeypatch)
    store = tee_range_flow.FlowStore(tmp_path / "sessions" / tester)
    binding = ts._tee_range_setup_binding(
        EligibleSetup().require(tester, {}, "start"), FakeTilt().reading()
    )
    state = store.start("start", setup_admission=binding)
    state = store.transition(
        state,
        phase="empty_capturing",
        reason="capturing_empty",
        request_id="capture",
        evidence={"empty_capture_id": "empty-dead"},
    )
    output = store.epoch_dir(state.epoch_id) / "iwr"
    output.mkdir(parents=True)
    (output / ".empty-dead.reserve").write_text(
        "pid=12345 capture_id=empty-dead\n", encoding="utf-8"
    )
    monkeypatch.setattr(ts, "_process_is_alive", lambda _pid: False)

    restarted = phase(app.test_client(), tester)

    assert restarted["phase"] == "retryable_failure"
    assert restarted["retry_phase"] == "needs_empty"
    assert restarted["evidence"]["empty_capture_id"] == "empty-dead"


def test_restart_records_an_interrupted_camera_evaluation_attempt(tmp_path, inputs, monkeypatch):
    app, tester = app_for(tmp_path, inputs, monkeypatch)
    store = tee_range_flow.FlowStore(tmp_path / "sessions" / tester)
    binding = ts._tee_range_setup_binding(
        EligibleSetup().require(tester, {}, "start"), FakeTilt().reading()
    )
    state = store.start("start", setup_admission=binding)
    capture_id = "arm5-000002"
    state = store.transition(
        state,
        phase="camera_arm5_evaluating",
        reason="evaluating_camera_arm5",
        evidence={"camera_arm5_capture_setup": {"capture_id": capture_id}},
    )
    frame = store.epoch_dir(state.epoch_id) / f"camera-{capture_id}.pgm"
    frame.write_bytes(b"P5\n1 1\n255\n\x80")

    restarted = phase(app.test_client(), tester)

    attempt = restarted["evidence"][f"camera_arm5_attempt_{capture_id}"]
    assert restarted["phase"] == "retryable_failure"
    assert restarted["retry_phase"] == "needs_camera_arm5"
    assert attempt["status"] == "evaluation_interrupted"
    assert attempt["frame"] == frame.name
    assert attempt["frame_sha256"] == file_hash(frame)


def test_camera_evaluation_retry_uses_a_new_immutable_frame_attempt(tmp_path, inputs, monkeypatch):
    live = ChangingFakeLive()
    app, tester = app_for(tmp_path, inputs, monkeypatch, live_view=live)
    client = app.test_client()
    for index, action in enumerate(("start", "capture_empty", "capture_ball", "start_camera_arm5")):
        assert post(client, tester, action, f"initial-{index}").status_code == 200

    monkeypatch.setattr(
        ts,
        "estimate_reference_ball_range",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("transient")),
    )
    assert post(client, tester, "evaluate_camera_arm5", "bad-evaluation").status_code == 200
    assert phase(client, tester)["phase"] == "retryable_failure"
    assert live.running is False
    assert client.get("/api/tester/live").get_json()["owner"] is None
    assert (
        post(client, tester, "retry", "retry-camera").get_json()["state"]["phase"]
        == "needs_camera_arm5"
    )
    monkeypatch.setattr(
        ts, "estimate_reference_ball_range", lambda *_args, **_kwargs: camera_result(1.2)
    )
    assert post(client, tester, "start_camera_arm5", "new-camera").status_code == 200

    retried = post(client, tester, "evaluate_camera_arm5", "good-evaluation")

    assert retried.status_code == 200
    assert retried.get_json()["state"]["phase"] == "needs_camera_arm6"
    assert live.running is False
    assert client.get("/api/tester/live").get_json()["owner"] is None
    root = tmp_path / "sessions" / tester
    store = tee_range_flow.FlowStore(root)
    state = store.load()
    frames = list(store.epoch_dir(state.epoch_id).glob("camera-arm5-*.pgm"))
    assert len(frames) == 2


def test_interrupted_guided_camera_preserves_its_live_error(tmp_path, inputs, monkeypatch):
    live = FakeLive()
    app, tester = app_for(tmp_path, inputs, monkeypatch, live_view=live)
    client = app.test_client()
    for index, action in enumerate(("start", "capture_empty", "capture_ball", "start_camera_arm5")):
        assert post(client, tester, action, f"request-{index}").status_code == 200
    live.error = "camera cable disconnected"
    live.running = False

    state = phase(client, tester)

    assert state["phase"] == "retryable_failure"
    assert state["retry_phase"] == "needs_camera_arm5"
    assert state["evidence"]["camera_capture_failure"] == {
        "arm_id": "arm5",
        "stage": "live_view",
        "message": "camera cable disconnected",
        "remedy": "Check the camera connection, then retry this camera step.",
    }
    assert client.get("/api/tester/live").get_json()["owner"] is None


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

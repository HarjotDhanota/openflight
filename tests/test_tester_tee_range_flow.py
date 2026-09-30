from __future__ import annotations

import hashlib
import json
import math
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
from openflight.iwr6843.range_evidence import STATIC_PROFILE_V2_SCHEMA


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


def ball_pixels(width: int, height: int) -> tuple[float, float, float]:
    """Where the fake camera draws the reference ball, and its diameter."""
    return width / 2.0, height * 0.625, 24.0 * width / 1280.0


class FakeLive:
    """A live camera whose ball brightness follows exposure x gain and echoes its controls."""

    background = 40.0
    ball_per_signal = 0.03
    max_observations = 80

    def __init__(self):
        self.running = False
        self.arm = None
        self.start_count = 0
        self.stop_count = 0
        self.error = None
        self.analyzer = None
        self.frame_sequence = 0
        self.latest_frame_at = None
        self.context_generation = 0
        self.controls = None
        self.applied_offset_us = 0.0
        self.requested_history = []
        self.other_bright_objects = []

    def start(self, arm, exposure_us, gain, *_args, analyzer=None):
        self.running = True
        self.arm = arm
        self.analyzer = analyzer
        self.start_count += 1
        self.context_generation += 1
        self.controls = (exposure_us, gain)
        self.requested_history.append(self.controls)
        if analyzer is not None:
            self.pump()

    def change_controls(self, exposure_us, gain, owner=None):
        if owner is not None and owner is not self.analyzer:
            return
        self.controls = (exposure_us, gain)
        self.requested_history.append(self.controls)
        self.context_generation += 1

    def applied(self):
        exposure_us, gain = self.controls
        return exposure_us + self.applied_offset_us, gain

    def pump(self):
        for _ in range(self.max_observations):
            generation = self.context_generation
            self.frame_sequence += 1
            self.analyzer.observe(
                self.recent_frames()[1],
                self.frame_sequence,
                observed_at=self.frame_sequence * 0.5,
                applied_controls=[self.applied()] * 3,
            )
            self.latest_frame_at = ts.time.monotonic()
            status = self.analyzer.status()
            snapshot = self.analyzer.snapshot() or {}
            if generation == self.context_generation and (
                status["status"] == "lighting_required"
                or (status["locked_and_passing"] and snapshot.get("save_eligible"))
            ):
                return

    def stop(self):
        self.running = False
        self.stop_count += 1
        self.context_generation += 1

    def ball_level(self):
        exposure_us, gain = self.applied()
        return min(255.0, self.background + self.ball_per_signal * exposure_us * gain)

    def recent_frames(self):
        height, width = self.arm.height, self.arm.width
        x, y, diameter = ball_pixels(width, height)
        yy, xx = np.ogrid[:height, :width]
        image = np.full((height, width), self.background, dtype=np.float32)
        for object_x, object_y, object_diameter in [(x, y, diameter), *self.other_bright_objects]:
            image[np.hypot(xx - object_x, yy - object_y) <= object_diameter / 2.0] = (
                self.ball_level()
            )
        frames = np.repeat(np.clip(image, 0, 255).astype(np.uint8)[None], 3, axis=0)
        return self.arm, frames

    def recent_frames_context(self):
        arm, frames = self.recent_frames()
        return arm, frames, self.frame_sequence, self.latest_frame_at

    def capture_context_snapshot(self):
        arm, frames = self.recent_frames()
        return {
            "running": self.running,
            "error": self.error,
            "arm": arm,
            "frames": frames,
            "applied_controls": [self.applied()] * 3,
            "frame_sequence": self.frame_sequence,
            "latest_frame_at": self.latest_frame_at,
            "analyzer": self.analyzer,
            "run_generation": self.context_generation,
            "context_generation": self.context_generation,
        }

    def capture_context_is_current(self, context):
        return bool(
            self.running
            and self.error is None
            and self.arm == context.get("arm")
            and self.analyzer is context.get("analyzer")
            and self.context_generation == context.get("context_generation")
        )

    def snapshot(self):
        return None, {
            "running": self.running,
            "error": self.error,
            "association": self.analyzer.snapshot() if self.analyzer is not None else None,
        }


class ChangingFakeLive(FakeLive):
    def __init__(self):
        super().__init__()
        self.background = 39.0

    def start(self, arm, *args, **kwargs):
        self.background += 1.0
        super().start(arm, *args, **kwargs)


class UnreadyFakeLive(FakeLive):
    def start(self, arm, exposure_us, gain, *_args, analyzer=None):
        self.running = True
        self.arm = arm
        self.analyzer = analyzer
        self.start_count += 1
        self.controls = (exposure_us, gain)
        self.frame_sequence = 1
        self.latest_frame_at = ts.time.monotonic()


class StaleFakeLive(FakeLive):
    def start(self, arm, *args, analyzer=None):
        super().start(arm, *args, analyzer=analyzer)
        self.latest_frame_at = ts.time.monotonic() - ts.LIVE_FRAME_STALE_S - 1.0


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


def profile(
    capture: str, power: list[float], config_hash: str, rig_hash: str, *, legacy=False
) -> dict:
    payload = {
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
    if legacy:
        return payload
    return {
        **payload,
        "schema": STATIC_PROFILE_V2_SCHEMA,
        "frame_mad_fraction": [0.0] * 96,
        "frame_count": 24,
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
        legacy_profile=False,
    ):
        self.config_hash = config_hash
        self.firmware_hash = firmware_hash
        self.rig_hash = rig_hash
        self.calibration_hash = calibration_hash
        self.fail = fail
        self.legacy_profile = legacy_profile
        self.ball_return = 30.0
        self.ball_bins: tuple[int, ...] = (30,)
        # (bin, extra power) for something else that appeared with the ball
        self.other_return: tuple[int, float] | None = None
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
        if action in {"swings", "ladder"}:
            # the kiosk command is what the swing server would be started with
            self.last_command = list(commands[0])
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
        present = np.ones(96) + np.where(
            np.isin(np.arange(96), self.ball_bins), self.ball_return, 0.0
        )
        if self.other_return is not None:
            present[self.other_return[0]] += self.other_return[1]
        present = present.tolist()
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
                legacy=self.legacy_profile,
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


def camera_result(value: float, width: int = 1280, height: int = 800) -> ReferenceBallRangeResult:
    x, y, diameter = ball_pixels(width, height)
    candidate = ReferenceBallRangeCandidate(
        x_px=x,
        y_px=y,
        diameter_px=diameter,
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
        camera_range_estimator_sha256=ts._camera_range_estimator_sha256(),
        camera_exposure_policy_sha256=ts._static_exposure_policy_sha256(),
        camera_exposure_policy_purpose="static_reference_ball",
        camera_arm_id="arm5",
        iwr_firmware_sha256=file_hash(paths["firmware"]),
        iwr_capture_config_sha256=file_hash(paths["config"]),
        iwr_profile_sha256="e" * 64,
        iwr_range_calibration_sha256=file_hash(paths["calibration"]),
        iwr_static_estimator_sha256=ts._iwr_static_estimator_sha256(),
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
    legacy_profile=False,
    held_radar=False,
    black_floor_dn=None,
    use_unqualified=False,
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
        ts,
        "estimate_reference_ball_range",
        lambda _frames, camera, **_kwargs: camera_result(
            camera_m, camera.image_width_px, camera.image_height_px
        ),
    )
    manager = StaticManager(
        config_hash=file_hash(inputs["config"]),
        firmware_hash=file_hash(inputs["firmware"]),
        rig_hash=file_hash(inputs["rig"]),
        calibration_hash=file_hash(inputs["calibration"]),
        fail=fail,
        legacy_profile=legacy_profile,
    )
    held = (
        HeldRadarDouble(
            config_hash=manager.config_hash,
            firmware_hash=manager.firmware_hash,
            rig_hash=manager.rig_hash,
            calibration_hash=manager.calibration_hash,
            fail=fail,
            legacy_profile=legacy_profile,
        )
        if held_radar
        else None
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
        static_radar=held,
        use_unqualified_tee_range=use_unqualified,
    )
    app.config["TEST_STATIC_MANAGER"] = manager
    app.config["TEST_HELD_RADAR"] = held
    tester = "guided-fixture"
    for arm_id in ("arm5", "arm6"):
        params = ts.TesterParameters(tester, arm_id, "indoors")
        # The fake camera's background does not scale with exposure x gain, so by
        # default it is that camera's black level, as a gain screen would have
        # measured it; a test about a lit background passes its own black level.
        ts.write_arm_state(
            tmp_path / "sessions",
            params,
            gain=4.0,
            gain_exposure_us=params.arm.exposure_us,
            black_floor_dn=FakeLive.background if black_floor_dn is None else black_floor_dn,
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


class HeldRadarDouble(StaticManager):
    """The held radar session: runs setup captures and records releases."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.releases = 0
        self.cancels = 0

    def release(self):
        self.releases += 1

    def cancel(self):
        self.cancels += 1
        return False


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


def start_arm5(client, tester, prefix="exposure"):
    for index, action in enumerate(("start", "capture_empty", "capture_ball", "start_camera_arm5")):
        assert post(client, tester, action, f"{prefix}-{index}").status_code == 200


def test_static_exposure_locks_the_lowest_passing_setting_before_camera_save(
    tmp_path, inputs, monkeypatch
):
    live = FakeLive()
    app, tester = app_for(tmp_path, inputs, monkeypatch, live_view=live)

    state = drive(app.test_client(), tester)

    exposure = state["evidence"]["camera_arm5_static_exposure"]
    lock = exposure["lock"]
    arm = ts.ARMS["arm5"]
    x, y, diameter = ball_pixels(arm.width, arm.height)
    probe = FakeLive()
    probe.arm = arm

    def passes(step):
        probe.controls = (step.exposure_us, step.gain)
        return ts.assess_static_exposure(
            probe.recent_frames()[1],
            {
                "status": "selected",
                "selected": {"x_px": x, "y_px": y, "diameter_px": diameter},
                "stable_count": 3,
            },
            requested=step,
            applied_exposure_us=step.exposure_us,
            applied_gain=step.gain,
            black_floor_dn=FakeLive.background,  # the black level app_for records
        ).acceptable

    lowest_passing = next(
        step for step in sorted(ts.exposure_steps_for_fps(arm.fps)) if passes(step)
    )
    facts = state["evidence"]["camera_arm5_candidate"]["evidence"]["qualification"]
    controls = state["evidence"]["camera_arm5_candidate"]["evidence"]["capture_identity"]["mode"][
        "controls"
    ]
    assert state["phase"] == "resolved"
    assert exposure["status"] == "locked"
    assert exposure["locked_and_passing"] is True
    assert (lock["exposure_us"], lock["gain"]) == (
        lowest_passing.exposure_us,
        lowest_passing.gain,
    )
    assert exposure["attempts"][0]["stage"] == "bootstrap"
    assert facts["static_exposure_lock_verified"] is True
    assert controls["applied_exposure_us"] == lock["applied_exposure_us"]
    assert controls["applied_gain"] == lock["applied_gain"]


def test_a_static_exposure_lock_never_reaches_swing_capture(tmp_path, inputs, monkeypatch):
    monkeypatch.setattr(ts.Path, "home", classmethod(lambda _cls: tmp_path / "home"))
    app, tester = app_for(tmp_path, inputs, monkeypatch)

    state = drive(app.test_client(), tester)

    for arm_id in ("arm5", "arm6"):
        lock = state["evidence"][f"camera_{arm_id}_static_exposure"]["lock"]
        params = ts.TesterParameters(tester, arm_id, "indoors")
        swing_gain, swing_exposure_us = ts.resolve_gain(tmp_path / "sessions", params)
        arm_state = ts.read_arm_state(tmp_path / "sessions", tester, arm_id)
        assert (swing_exposure_us, swing_gain) == (params.arm.exposure_us, 4.0)
        assert (lock["exposure_us"], lock["gain"]) != (swing_exposure_us, swing_gain)
        assert "static_exposure" not in json.dumps(arm_state)
    assert not list((tmp_path / "home").rglob("camera-exposure.json"))


def _search_evidence(tmp_path, client, tester):
    state = phase(client, tester)
    name = state["evidence"]["camera_arm5_capture_setup"]["exposure_search_file"]
    store = tee_range_flow.FlowStore(ts.tester_root(tmp_path / "sessions", tester))
    return json.loads((store.epoch_dir(state["epoch_id"]) / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("ball_per_signal", "ambiguous", "status", "detector"),
    [
        (0.03, False, "locked", "selected"),
        (0.0001, False, "lighting_required", "selected"),
        (0.03, True, "ball_not_identified", "ambiguous"),
    ],
)
def test_the_exposure_search_is_saved_to_disk_without_any_save(
    tmp_path, inputs, monkeypatch, ball_per_signal, ambiguous, status, detector
):
    live = FakeLive()
    live.ball_per_signal = ball_per_signal
    # the ambiguous case is a lit scene: its background is light above a black of 0
    app, tester = app_for(
        tmp_path, inputs, monkeypatch, live_view=live, black_floor_dn=0.0 if ambiguous else None
    )
    if ambiguous:
        result = replace(camera_result(1.2), status="ambiguous", selected=None)
        monkeypatch.setattr(ts, "estimate_reference_ball_range", lambda *_args, **_kwargs: result)
    client = app.test_client()
    start_arm5(client, tester)

    saved = _search_evidence(tmp_path, client, tester)

    assert saved["status"] == status
    assert saved["attempts"]
    assert saved["last_detection"]["status"] == detector
    assert saved["last_detection"]["candidates"]
    assert saved["policy_sha256"] == ts._static_exposure_policy_sha256()


def test_dark_scene_requires_light_and_blocks_camera_save(tmp_path, inputs, monkeypatch):
    live = FakeLive()
    live.ball_per_signal = 0.0001
    app, tester = app_for(tmp_path, inputs, monkeypatch, live_view=live)
    client = app.test_client()
    start_arm5(client, tester)

    response = post(client, tester, "evaluate_camera_arm5", "dark-save")
    association = live.analyzer.snapshot()

    assert response.status_code == 409
    assert "no visible setting passed the ball-pixel gates" in response.get_json()["error"]
    assert association["static_exposure"]["status"] == "lighting_required"
    assert association["save_eligible"] is False
    assert phase(client, tester)["phase"] == "camera_arm5_capturing"


def test_a_dark_camera_view_can_be_kept_as_unqualified_raw_evidence(tmp_path, inputs, monkeypatch):
    live = FakeLive()
    live.ball_per_signal = 0.0001
    app, tester = app_for(tmp_path, inputs, monkeypatch, live_view=live)
    client = app.test_client()
    start_arm5(client, tester)

    response = post(client, tester, "save_camera_arm5_diagnostic", "dark-diagnostic")
    state = phase(client, tester)

    saved = state["evidence"]["camera_arm5_diagnostic_capture"]
    path = (
        tee_range_flow.FlowStore(ts.tester_root(tmp_path / "sessions", tester)).epoch_dir(
            state["epoch_id"]
        )
        / saved["file"]
    )
    assert response.status_code == 200
    assert state["phase"] == "raw_only"
    assert state["solution"]["status"] == "unresolved"
    assert state["solution"]["reason"] == "camera_arm5_lighting_required_raw_evidence_only"
    assert saved["qualified"] is False
    assert saved["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert "camera_arm5_candidate" not in state["evidence"]
    assert live.running is False
    phases = [
        json.loads(item.read_text(encoding="utf-8"))["phase"]
        for item in path.parent.glob("state-*.json")
    ]
    assert "evaluating" not in phases


def test_an_ambiguous_ball_is_reported_as_unidentified_not_as_dark(tmp_path, inputs, monkeypatch):
    live = FakeLive()
    # a lit scene: the background is light above a black level of 0
    app, tester = app_for(tmp_path, inputs, monkeypatch, live_view=live, black_floor_dn=0.0)
    ambiguous = replace(camera_result(1.2), status="ambiguous", selected=None)
    monkeypatch.setattr(ts, "estimate_reference_ball_range", lambda *_args, **_kwargs: ambiguous)
    client = app.test_client()
    start_arm5(client, tester)

    exposure = live.analyzer.status()
    refused = post(client, tester, "evaluate_camera_arm5", "ambiguous-save")
    kept = post(client, tester, "save_camera_arm5_diagnostic", "ambiguous-diagnostic")
    state = phase(client, tester)

    assert exposure["status"] == "ball_not_identified"
    assert len(exposure["attempts"]) <= 8
    assert refused.status_code == 409
    assert "ball not identified" in refused.get_json()["error"]
    assert kept.status_code == 200
    assert state["phase"] == "raw_only"
    assert state["solution"]["reason"] == "camera_arm5_ball_not_identified_raw_evidence_only"


def test_the_diagnostic_save_is_refused_when_the_light_is_usable(tmp_path, inputs, monkeypatch):
    app, tester = app_for(tmp_path, inputs, monkeypatch)
    client = app.test_client()
    start_arm5(client, tester)

    response = post(client, tester, "save_camera_arm5_diagnostic", "usable-diagnostic")

    assert response.status_code == 409
    assert "only for a lighting or ball-identification failure" in response.get_json()["error"]
    assert phase(client, tester)["phase"] == "camera_arm5_capturing"


def test_controls_the_camera_ignores_never_lock(tmp_path, inputs, monkeypatch):
    live = FakeLive()
    live.applied_offset_us = 500.0
    app, tester = app_for(tmp_path, inputs, monkeypatch, live_view=live)
    client = app.test_client()
    start_arm5(client, tester)

    response = post(client, tester, "evaluate_camera_arm5", "ignored-save")
    exposure = live.analyzer.status()

    assert response.status_code == 409
    assert exposure["status"] == "lighting_required"
    assert exposure["lock"] is None
    assert {item["reason"] for item in exposure["attempts"]} == {"controls_not_applied"}


def test_save_frames_not_at_the_locked_controls_are_withheld(tmp_path, inputs, monkeypatch):
    live = FakeLive()
    app, tester = app_for(tmp_path, inputs, monkeypatch, live_view=live)
    client = app.test_client()
    start_arm5(client, tester)
    assert live.analyzer.status()["locked_and_passing"] is True
    live.applied_offset_us = 300.0

    state = post(client, tester, "evaluate_camera_arm5", "moved-save").get_json()["state"]

    attempt = next(
        value for key, value in state["evidence"].items() if key.startswith("camera_arm5_attempt_")
    )
    assert state["phase"] == "retryable_failure"
    assert attempt["reason"] == "Save frames were not captured at the locked static exposure"
    assert "camera_arm5_candidate" not in state["evidence"]


def test_save_rechecks_the_ball_pixels_on_the_save_frames(tmp_path, inputs, monkeypatch):
    live = FakeLive()
    app, tester = app_for(tmp_path, inputs, monkeypatch, live_view=live)
    client = app.test_client()
    start_arm5(client, tester)
    assert live.analyzer.status()["locked_and_passing"] is True
    live.ball_per_signal = 0.0001

    state = post(client, tester, "evaluate_camera_arm5", "dimmed-save").get_json()["state"]

    attempt = next(
        value for key, value in state["evidence"].items() if key.startswith("camera_arm5_attempt_")
    )
    assert state["phase"] == "retryable_failure"
    assert attempt["reason"].startswith("Save frames failed the ball-pixel gates")
    assert "camera_arm5_candidate" not in state["evidence"]


def test_the_estimator_is_not_run_until_the_camera_applies_the_controls(
    tmp_path, inputs, monkeypatch
):
    live = FakeLive()
    live.applied_offset_us = 500.0
    app, tester = app_for(tmp_path, inputs, monkeypatch, live_view=live)
    calls = []

    def counting(_frames, camera, **_kwargs):
        calls.append(1)
        return camera_result(1.2, camera.image_width_px, camera.image_height_px)

    monkeypatch.setattr(ts, "estimate_reference_ball_range", counting)
    start_arm5(app.test_client(), tester)

    assert live.analyzer.status()["status"] == "lighting_required"
    assert calls == []


def test_losing_light_after_the_lock_restarts_the_search(tmp_path, inputs, monkeypatch):
    live = FakeLive()
    app, tester = app_for(tmp_path, inputs, monkeypatch, live_view=live)
    client = app.test_client()
    start_arm5(client, tester)
    locked = live.analyzer.status()["lock"]
    live.ball_per_signal = 0.0001

    for _ in range(2):
        live.frame_sequence += 1
        live.analyzer.observe(
            live.recent_frames()[1],
            live.frame_sequence,
            observed_at=live.frame_sequence * 0.5,
            applied_controls=[live.applied()] * 3,
        )
    exposure = live.analyzer.status()

    assert exposure["status"] == "searching"
    assert exposure["lock"] is None
    assert exposure["invalidations"][0]["lock"] == locked
    assert live.controls == (
        exposure["current_step"]["exposure_us"],
        exposure["current_step"]["gain"],
    )
    assert post(client, tester, "evaluate_camera_arm5", "dim-save").status_code == 409


def test_range_display_reports_accepted_values_only_when_qualified(tmp_path, inputs, monkeypatch):
    app, tester = app_for(tmp_path, inputs, monkeypatch)
    client = app.test_client()
    drive(client, tester)

    display = client.get("/api/tester/tee-range", query_string={"tester_id": tester}).get_json()[
        "display"
    ]

    assert display["iwr"]["state"] == "accepted"
    assert display["iwr"]["range_m"] == pytest.approx(1.2)
    assert display["iwr"]["diagnostic_range_m"] is None
    assert display["iwr"]["label"] == "bias-corrected IWR slant range"
    assert display["camera"]["arm5"]["state"] == "accepted"
    assert display["canonical"] == {
        "state": "resolved",
        "range_m": pytest.approx(1.2),
        "reason": None,
    }


def test_range_display_marks_unqualified_values_as_diagnostics(tmp_path, inputs, monkeypatch):
    app, tester = app_for(tmp_path, inputs, monkeypatch, qualified=False)
    client = app.test_client()
    drive(client, tester)

    display = client.get("/api/tester/tee-range", query_string={"tester_id": tester}).get_json()[
        "display"
    ]

    assert display["iwr"]["state"] == "unqualified"
    assert display["iwr"]["range_m"] is None
    assert display["iwr"]["diagnostic_range_m"] == pytest.approx(1.2)
    assert display["camera"]["arm5"]["state"] == "unqualified"
    assert display["canonical"]["state"] == "withheld"
    assert display["canonical"]["reason"] == "qualification_artifact_missing"


def test_range_display_never_presents_a_rejected_radar_number_as_a_range(
    tmp_path, inputs, monkeypatch
):
    app, tester = app_for(tmp_path, inputs, monkeypatch)
    app.config["TEST_STATIC_MANAGER"].ball_return = 0.0
    client = app.test_client()
    for index, action in enumerate(("start", "capture_empty", "capture_ball")):
        assert post(client, tester, action, f"rejected-{index}").status_code == 200

    display = client.get("/api/tester/tee-range", query_string={"tester_id": tester}).get_json()[
        "display"
    ]

    assert display["iwr"]["state"] == "rejected"
    assert display["iwr"]["range_m"] is None
    assert display["iwr"]["reason"].startswith("rejected_no_ball")
    assert display["canonical"]["state"] == "withheld"


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
    assert identity["analyzed_frame_window_sha256"]
    assert "iwr_camera_search_hint" not in identity["mode"]["controls"]
    guidance = state["evidence"]["camera_arm5_guidance"]
    live = guidance["live_readiness"]
    save = guidance["save_camera_only_analysis"]
    ranking = guidance["camera_to_iwr_ranking"]
    assert live["save_eligible"] is True
    assert live["timing"]["clock"] == "host_performance_counter_duration"
    # 1280x800 searches during the radar ball capture, before any radar candidate exists.
    assert guidance["search_hint"]["reason_code"] == "static_iwr_candidate_missing"
    assert live["dependency_facts"]["iwr_range_used"] is False
    hinted = state["evidence"]["camera_arm6_guidance"]["live_readiness"]
    hint = state["evidence"]["camera_arm6_guidance"]["search_hint"]
    assert hinted["independent"] is False
    assert hinted["promotion_eligible"] is False
    assert hinted["dependency_facts"]["iwr_range_used"] is True
    assert hint["status"] == "usable"
    assert hint["input_identity"]["active_epoch_id"] == state["epoch_id"]
    assert hint["input_identity"]["source_epoch_id"] == state["epoch_id"]
    assert hint["input_identity"]["source_inputs"]["empty_capture_sha256"] == "a" * 64
    assert hint["input_identity"]["source_inputs"]["present_capture_sha256"] == "b" * 64
    assert hint["input_identity"]["camera_projection"]["artifacts"][
        "rig_geometry_sha256"
    ] == file_hash(inputs["rig"])
    assert hint["source_uncertainty_m"] > 0.0
    assert hint["uncertainty"]["source_standard_uncertainty_m"] == hint["source_uncertainty_m"]
    assert camera_evidence["save_camera_only_analysis"]["dependency_facts"] == {
        "iwr_range_used": False,
        "manual_range_used": False,
        "prior_canonical_range_used": False,
    }
    assert save["independent"] is True
    assert save["promotion_eligible"] is True
    assert save["search_region_px"] is None
    assert (
        save["input_identity"]["analyzed_frame_window_sha256"]
        == identity["analyzed_frame_window_sha256"]
    )
    assert live["selected"] == save["selected"]
    assert ranking["role"] == "diagnostic_ranking_only"
    assert ranking["promotion_eligible"] is False
    assert ranking["independent_confirmation_eligible"] is False
    assert ranking["input_identity"]["saved_frame_sha256"] == identity["saved_frame_sha256"]
    assert ranking["hypotheses"][0]["camera_uncertainty_m"] > 0.0
    assert "camera_to_iwr_static_ranking" not in json.dumps(state["solution"]["candidates"])
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
    assert ts._tee_range_cli_args(solution) == [
        "--iwr6843-tee-m",
        "1.2",
        "--iwr6843-ball-height-m",
        "0.021335",
        "--iwr6843-tee-range-source",
        "qualified_static_iwr",
        "--iwr6843-tee-range-candidate",
        solution.selected_candidate_id,
    ]


def test_conditioned_static_object_must_match_broad_save_before_promotion(
    tmp_path, inputs, monkeypatch
):
    """The radar hint only exists once the radar has finished, so 640x400 is conditioned."""
    live = FakeLive()
    live.other_bright_objects = [(260.0, 300.0, 12.0)]
    app, tester = app_for(tmp_path, inputs, monkeypatch, live_view=live)

    def estimate(_frames, camera, **kwargs):
        ball = camera_result(1.2, camera.image_width_px, camera.image_height_px)
        roi = kwargs.get("roi")
        # a follow look (one fit) sees whatever is inside its small region
        if roi is None or (
            kwargs.get("max_fits") == 1
            and not (roi[0] <= 260 <= roi[2] and roi[1] <= 300 <= roi[3])
        ):
            return ball
        return replace(
            ball,
            selected=replace(ball.selected, x_px=260.0, y_px=300.0),
            candidates=(replace(ball.candidates[0], x_px=260.0, y_px=300.0),),
        )

    monkeypatch.setattr(ts, "estimate_reference_ball_range", estimate)
    client = app.test_client()
    actions = (
        "start",
        "capture_empty",
        "capture_ball",
        "start_camera_arm5",
        "evaluate_camera_arm5",
        "start_camera_arm6",
    )
    for index, action in enumerate(actions):
        assert post(client, tester, action, f"hinge-{index}").status_code == 200

    response = post(client, tester, "evaluate_camera_arm6", "hinge-save")

    assert response.status_code == 200
    state = response.get_json()["state"]
    assert state["phase"] == "retryable_failure"
    assert "camera_arm5_candidate" in state["evidence"]
    assert "camera_arm6_candidate" not in state["evidence"]
    attempt = next(
        value for key, value in state["evidence"].items() if key.startswith("camera_arm6_attempt_")
    )
    assert attempt["status"] == "association_withheld"
    assert attempt["live_guidance"]["dependency_facts"]["iwr_range_used"] is True
    assert attempt["live_guidance"]["promotion_eligible"] is False
    assert attempt["camera_only_analysis"]["independent"] is True
    assert attempt["camera_only_analysis"]["promotion_eligible"] is False
    assert attempt["camera_only_analysis"]["search_region_px"] is None
    assert attempt["reason"] == (
        "broad independent camera search does not confirm the stable provisional selection"
    )
    assert state["evidence"]["iwr_candidate"]["radar_slant_range_m"] == pytest.approx(1.2)


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


def test_backend_refuses_save_when_latest_camera_frame_is_stale(tmp_path, inputs, monkeypatch):
    live = StaleFakeLive()
    app, tester = app_for(tmp_path, inputs, monkeypatch, live_view=live)
    client = app.test_client()
    for index, action in enumerate(("start", "capture_empty", "capture_ball", "start_camera_arm5")):
        assert post(client, tester, action, f"request-{index}").status_code == 200

    response = post(client, tester, "evaluate_camera_arm5", "stale-camera")

    assert response.status_code == 409
    assert "latest frame is stale" in response.get_json()["error"]
    assert phase(client, tester)["phase"] == "camera_arm5_capturing"


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


def test_stop_during_save_preserves_frame_and_withholds_changed_context(
    tmp_path, inputs, monkeypatch
):
    live = FakeLive()
    app, tester = app_for(tmp_path, inputs, monkeypatch, live_view=live)
    client = app.test_client()
    for index, action in enumerate(("start", "capture_empty", "capture_ball", "start_camera_arm5")):
        assert post(client, tester, action, f"request-{index}").status_code == 200

    def stop_during_analysis(*_args, **_kwargs):
        live.stop()
        return camera_result(1.2)

    monkeypatch.setattr(ts, "estimate_reference_ball_range", stop_during_analysis)
    response = post(client, tester, "evaluate_camera_arm5", "context-race")

    assert response.status_code == 200
    state = response.get_json()["state"]
    assert state["phase"] == "retryable_failure"
    attempt = next(
        value for key, value in state["evidence"].items() if key.startswith("camera_arm5_attempt_")
    )
    assert attempt["status"] == "association_withheld"
    assert attempt["frame_sha256"]
    assert attempt["reason"] == "guided camera context changed during Save"
    assert state["evidence"]["camera_capture_failure"]["message"] == attempt["reason"]


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
    assert missing["evidence"]["camera_arm5_guidance"]["search_hint"]["reason_code"] == (
        "static_iwr_candidate_missing"
    )
    fallback = missing["evidence"]["camera_arm6_guidance"]
    assert fallback["search_hint"]["status"] == "rejected"
    assert fallback["search_hint"]["reason_code"] == "static_iwr_candidate_rejected"
    # after the first full-frame find, live looks follow the ball
    assert fallback["live_readiness"]["discovery_mode"] in {
        "broad_full_frame_unconditioned",
        "follow_last_selection",
    }
    assert fallback["live_readiness"]["independent"] is True
    assert fallback["live_readiness"]["fallback"]["used"] is True
    assert fallback["live_readiness"]["fallback"]["reason_code"] == (
        "static_iwr_candidate_rejected"
    )
    assert ts._tee_range_cli_args(tee_range.TeeRangeSolution.from_dict(missing["solution"])) == [
        "--iwr6843-tee-range-pending",
        "--iwr6843-ball-height-m",
        "0.021335",
        "--iwr6843-tee-range-source",
        "pending",
    ]
    other_root = tmp_path / "other"
    app, tester = app_for(other_root, inputs, monkeypatch, camera_m=1.5)
    disagreed = drive(app.test_client(), tester)
    assert disagreed["phase"] == "raw_only"
    assert disagreed["solution"]["reason"] == "absolute_residual_exceeds_policy"


@pytest.mark.parametrize(
    ("artifact", "reason"),
    [
        ("{not json", "qualification_artifact_invalid"),
        ('["not", "an", "object"]', "qualification_artifact_invalid"),
        (None, "qualification_artifact_legacy_schema"),
    ],
)
def test_legacy_or_invalid_qualification_starts_and_stays_raw_only(
    tmp_path, inputs, monkeypatch, artifact, reason
):
    if artifact is None:
        legacy = json.loads(inputs["qualification"].read_text(encoding="utf-8"))
        legacy["schema"] = "openflight.tee_range_qualification.v2"
        legacy["schema_version"] = 2
        for field in (
            "camera_range_estimator_sha256",
            "camera_exposure_policy_sha256",
            "camera_exposure_policy_purpose",
            "iwr_static_estimator_sha256",
        ):
            legacy["identities"].pop(field)
        artifact = json.dumps(legacy)
    inputs["qualification"].write_text(artifact, encoding="utf-8")

    app, tester = app_for(tmp_path, inputs, monkeypatch)
    client = app.test_client()
    status = client.get("/api/tester/tee-range", query_string={"tester_id": tester}).get_json()
    state = drive(client, tester)

    assert status["qualification_available"] is False
    assert status["qualification_status"]["reason"].startswith(reason)
    assert state["phase"] == "raw_only"
    assert state["solution"]["status"] == "unresolved"
    assert state["solution"]["reason"].startswith(reason)


def test_resolved_epoch_is_withdrawn_without_a_configured_qualification(
    tmp_path, inputs, monkeypatch
):
    app, tester = app_for(tmp_path, inputs, monkeypatch)
    assert drive(app.test_client(), tester)["phase"] == "resolved"
    epoch = tee_range_setup.load_current_epoch(ts.tester_root(tmp_path / "sessions", tester))

    configured = tee_range_setup.validate_epoch_solution(
        epoch, required_qualification=epoch.qualification
    )
    withdrawn = tee_range_setup.validate_epoch_solution(epoch)

    assert configured.status == "resolved"
    assert withdrawn.status == "unresolved"
    assert withdrawn.reason == "resolved_range_requires_qualification_context"
    assert withdrawn.selected_range_m is None
    assert all("promotion" not in item.evidence for item in withdrawn.candidates)


def test_legacy_v1_static_profiles_never_promote_under_v3_qualification(
    tmp_path, inputs, monkeypatch
):
    app, tester = app_for(tmp_path, inputs, monkeypatch, legacy_profile=True)
    state = drive(app.test_client(), tester)

    iwr = state["evidence"]["iwr_candidate"]["evidence"]["qualification"]
    assert state["phase"] == "raw_only"
    assert state["solution"]["status"] == "unresolved"
    assert iwr["status"] == "rejected"
    assert iwr["accuracy_qualified"] is False
    assert iwr["iwr_static_estimator_sha256"] is None


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
        "sequence": failed["sequence"],
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
        "sequence": state["sequence"],
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


class DeferredStaticManager(StaticManager):
    """A radar job that keeps running until the test releases it."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.deferred = None

    def start(self, action, commands, log_path, on_finish=None, **kwargs):
        kind = commands[0][commands[0].index("--kind") + 1] if action == "tee_range" else None
        if kind != "ball_present":
            return super().start(action, commands, log_path, on_finish=on_finish, **kwargs)
        self.deferred = (action, commands, log_path, on_finish)
        self._status = {"state": "running", "action": "tee_range", "message": "capturing"}
        return None

    def release(self, *, fail=False):
        action, commands, log_path, on_finish = self.deferred
        self.deferred = None
        self._status = {"state": "idle", "action": None, "message": "Ready"}
        self.fail = fail
        super().start(action, commands, log_path, on_finish=on_finish)


def deferred_app(tmp_path, inputs, monkeypatch, live):
    app, tester = app_for(tmp_path, inputs, monkeypatch, live_view=live)
    original = app.config["TEST_STATIC_MANAGER"]
    manager = DeferredStaticManager(
        config_hash=original.config_hash,
        firmware_hash=original.firmware_hash,
        rig_hash=original.rig_hash,
        calibration_hash=original.calibration_hash,
    )
    monkeypatch.setattr(original, "start", manager.start)
    monkeypatch.setattr(original, "status", manager.status)
    return app, tester, manager


def test_the_camera_finds_its_exposure_while_the_radar_records_the_ball(
    tmp_path, inputs, monkeypatch
):
    live = FakeLive()
    app, tester, manager = deferred_app(tmp_path, inputs, monkeypatch, live)
    client = app.test_client()
    for index, action in enumerate(("start", "capture_empty", "capture_ball")):
        assert post(client, tester, action, f"parallel-{index}").status_code == 200

    during = phase(client, tester)
    setup = during["evidence"]["camera_arm5_capture_setup"]
    display = client.get("/api/tester/live").get_json()["guided_display"]
    assert during["phase"] == "ball_capturing"
    assert setup["started_during_radar_capture_id"] == during["evidence"]["ball_present_capture_id"]
    assert live.running is True
    assert display["state"] == "exposure_locked"
    assert post(client, tester, "evaluate_camera_arm5", "too-early").status_code == 409

    manager.release()

    after = phase(client, tester)
    assert after["phase"] == "camera_arm5_capturing"
    assert after["evidence"]["iwr_candidate"]
    assert live.start_count == 1
    assert post(client, tester, "start_camera_arm5", "no-op").status_code == 200
    assert live.start_count == 1
    assert post(client, tester, "evaluate_camera_arm5", "save").status_code == 200
    assert phase(client, tester)["phase"] == "needs_camera_arm6"


def test_a_failed_ball_capture_stops_the_parallel_camera_search(tmp_path, inputs, monkeypatch):
    live = FakeLive()
    app, tester, manager = deferred_app(tmp_path, inputs, monkeypatch, live)
    client = app.test_client()
    for index, action in enumerate(("start", "capture_empty", "capture_ball")):
        assert post(client, tester, action, f"parallel-{index}").status_code == 200
    assert live.running is True

    manager.release(fail=True)

    failed = phase(client, tester)
    assert failed["phase"] == "retryable_failure"
    assert failed["retry_phase"] == "needs_ball"
    assert live.running is False
    assert client.get("/api/tester/live").get_json()["owner"] is None
    assert post(client, tester, "retry", "retry").status_code == 200
    assert post(client, tester, "capture_ball", "again").status_code == 200
    assert live.running is True


def test_the_last_verified_lock_is_tried_first_next_time(tmp_path, inputs, monkeypatch):
    live = FakeLive()
    app, tester = app_for(tmp_path, inputs, monkeypatch, live_view=live)
    client = app.test_client()
    first = drive(client, tester)
    remembered = first["evidence"]["camera_arm5_static_exposure"]["lock"]

    assert post(client, tester, "start_over", "again").status_code == 200
    start_arm5(client, tester, prefix="second")

    setup = phase(client, tester)["evidence"]["camera_arm5_capture_setup"]
    saved = _search_evidence(tmp_path, client, tester)
    assert setup["warm_start"] == {
        "exposure_us": remembered["exposure_us"],
        "gain": remembered["gain"],
    }
    assert setup["initial_step"] == setup["warm_start"]
    assert saved["status"] == "locked"
    assert [attempt["stage"] for attempt in saved["attempts"]] == ["warm_start"]
    assert (saved["lock"]["exposure_us"], saved["lock"]["gain"]) == (
        remembered["exposure_us"],
        remembered["gain"],
    )


def test_a_remembered_lock_from_another_policy_is_not_used(tmp_path, inputs, monkeypatch):
    live = FakeLive()
    app, tester = app_for(tmp_path, inputs, monkeypatch, live_view=live)
    client = app.test_client()
    drive(client, tester)
    memory_path = ts.tester_root(tmp_path / "sessions", tester) / ts.STATIC_EXPOSURE_MEMORY_FILE
    memory = json.loads(memory_path.read_text(encoding="utf-8"))
    memory["arms"]["arm5"]["policy_sha256"] = "0" * 64
    memory_path.write_text(json.dumps(memory), encoding="utf-8")

    assert post(client, tester, "start_over", "again").status_code == 200
    start_arm5(client, tester, prefix="second")

    setup = phase(client, tester)["evidence"]["camera_arm5_capture_setup"]
    assert setup["warm_start"] is None
    assert _search_evidence(tmp_path, client, tester)["attempts"][0]["stage"] == "bootstrap"


def test_an_unqualified_range_reaches_swings_only_when_explicitly_enabled(
    tmp_path, inputs, monkeypatch
):
    app, tester = app_for(tmp_path, inputs, monkeypatch, qualified=False)
    state = drive(app.test_client(), tester)
    solution = tee_range.TeeRangeSolution.from_dict(state["solution"])
    iwr = state["evidence"]["iwr_candidate"]

    assert state["phase"] == "raw_only"
    assert ts._tee_range_cli_args(solution) == [
        "--iwr6843-tee-range-pending",
        "--iwr6843-ball-height-m",
        "0.021335",
        "--iwr6843-tee-range-source",
        "pending",
    ]
    assert ts._tee_range_cli_args(solution, use_unqualified=True) == [
        "--iwr6843-tee-m",
        f"{iwr['radar_slant_range_m']:.9g}",
        "--iwr6843-ball-height-m",
        "0.021335",
        "--iwr6843-tee-range-source",
        "unqualified_static_iwr",
        "--iwr6843-tee-range-candidate",
        iwr["candidate_id"],
    ]
    assert ts.tee_range_display(state)["swings"]["state"] == "pending"
    swings = ts.tee_range_display(state, use_unqualified=True)["swings"]
    assert swings["state"] == "unqualified"
    assert swings["range_m"] == pytest.approx(iwr["radar_slant_range_m"])


def test_without_an_accepted_radar_swings_start_with_the_range_pending(
    tmp_path, inputs, monkeypatch
):
    # wiring audit S2 (D4): the camera's own range never stands in for the radar's
    app, tester = app_for(tmp_path, inputs, monkeypatch, qualified=False)
    app.config["TEST_STATIC_MANAGER"].ball_return = 0.0
    state = drive(app.test_client(), tester)
    solution = tee_range.TeeRangeSolution.from_dict(state["solution"])

    assert state["evidence"]["camera_arm5_candidate"]["radar_slant_range_m"] is not None
    assert ts.unqualified_tee_range_choice(solution) is None
    assert ts._tee_range_cli_args(solution, use_unqualified=True)[0] == (
        "--iwr6843-tee-range-pending"
    )
    swings = ts.tee_range_display(state, use_unqualified=True)["swings"]
    assert swings["state"] == "pending"
    assert swings["range_m"] is None
    assert "withheld" in swings["message"]


def test_a_radar_that_stops_answering_mid_setup_is_told_to_replug_its_usb():
    record = {
        "error": {
            "stage": "configure",
            "type": "RuntimeError",
            "message": "IWR6843 did not acknowledge 'dfeDataOutputMode 1'; the firmware may be "
            "wedged (press RESET and retry); no reply within the command window",
        }
    }

    failure = ts._static_capture_failure(record, "empty")

    assert "unplug the radar's USB cable" in failure["remedy"]
    assert "RESET does not clear" in failure["remedy"]


def test_the_setup_solves_the_lens_height_from_the_radar_and_hands_it_to_swings(
    tmp_path, inputs, monkeypatch
):
    app, tester = app_for(tmp_path, inputs, monkeypatch, qualified=False)
    monkeypatch.setattr(
        ts,
        "estimate_reference_ball_range",
        lambda _frames, camera, **_kwargs: (
            lambda result: replace(
                result,
                selected=replace(
                    result.selected, camera_height_m=0.081, camera_height_uncertainty_m=0.03
                ),
            )
        )(camera_result(1.2, camera.image_width_px, camera.image_height_px)),
    )
    state = drive(app.test_client(), tester)
    solution = tee_range.TeeRangeSolution.from_dict(state["solution"])
    height = state["evidence"]["camera_arm5_candidate"]["evidence"]["camera_height"]

    assert height["size_solved_m"] == pytest.approx(0.081)
    assert height["radar_solved_m"] is not None
    assert height["radar_uncertainty_m"] < height["size_uncertainty_m"]
    assert height["reference"] == "hitting_surface"
    args = ts._tee_range_cli_args(solution, use_unqualified=True)
    if height["source"] == "static_iwr_range":
        assert args[args.index("--solved-camera-height-m") + 1] == f"{height['height_m']:.6g}"
    else:
        assert height["source"] == "rig_nominal"
        assert "--solved-camera-height-m" not in args
    # a pending range still carries a height the setup had to override (wiring audit S6)
    assert ("--solved-camera-height-m" in ts._tee_range_cli_args(solution)) == (
        height["source"] == "static_iwr_range"
    )


def test_a_radar_height_outside_the_plausible_band_is_not_used():
    camera = BallPlaneCamera.nominal(
        focal_px=933.3333,
        image_width_px=1280,
        image_height_px=800,
        pitch_deg=0.0,
        roll_correction_deg=0.0,
        mirror_horizontal=False,
        camera_origin_lfu=(0.0, 0.0, 0.095),
        radar_origin_lfu=(0.0, -0.03, 0.051),
        angular_uncertainty_deg=1.0,
        focal_relative_uncertainty=0.08,
    )
    # a "ball" far above the horizon row: no floor ball could sit there
    result = replace(
        camera_result(1.2),
        selected=replace(
            camera_result(1.2).selected,
            y_px=120.0,
            camera_height_m=0.09,
            camera_height_uncertainty_m=0.02,
        ),
    )
    iwr = {
        "radar_slant_range_m": 1.2,
        "uncertainty_m": 0.02,
        "evidence": {"difference": {"status": "accepted"}},
    }

    solved = ts._solved_camera_height(result, camera, iwr)

    assert solved["source"] == "rig_nominal"
    assert solved["height_m"] == pytest.approx(0.095)
    assert "the selected ball may be wrong" in solved["radar_rejected"]


def _level_camera():
    return BallPlaneCamera.nominal(
        focal_px=933.3333,
        image_width_px=1280,
        image_height_px=800,
        pitch_deg=0.0,
        roll_correction_deg=0.0,
        mirror_horizontal=False,
        camera_origin_lfu=(0.0, 0.0, 0.095),
        radar_origin_lfu=(0.0, -0.03, 0.051),
        angular_uncertainty_deg=1.0,
        focal_relative_uncertainty=0.08,
    )


def _setup_ball_seen_from(lens_height_m, distance_m=1.4):
    """Pixel and radar range of a surface ball when the lens is truly this high."""
    camera = _level_camera()
    radius = 0.021335
    down = (lens_height_m - radius) / distance_m
    y_px = 400.0 + 933.3333 * down
    ray = np.asarray(camera.ray_model.rays(np.array([640.0, y_px])), dtype=float)
    # along the ray to the ball's centre, which sits one radius above the surface
    t = (lens_height_m - radius) / -ray[2]
    lens = np.array([0.0, 0.0, lens_height_m])
    radar = lens + (np.asarray(camera.radar_origin_lfu) - np.asarray(camera.camera_origin_lfu))
    radar_range = float(np.linalg.norm(lens + t * ray - radar))
    result = replace(
        camera_result(1.2),
        selected=replace(
            camera_result(1.2).selected,
            x_px=640.0,
            y_px=y_px,
            camera_height_m=lens_height_m,
            camera_height_uncertainty_m=0.05,
        ),
    )
    iwr = {
        "radar_slant_range_m": radar_range,
        "uncertainty_m": 0.01,
        "evidence": {"difference": {"status": "accepted"}},
    }
    return camera, result, iwr


def test_a_unit_on_the_hitting_surface_keeps_the_rig_lens_height():
    # the unit stands on the same surface as the ball, 12 mm lower than nominal:
    # inside what one ball can tell apart, so the rig file's height stands
    camera, result, iwr = _setup_ball_seen_from(0.083)

    solved = ts._solved_camera_height(result, camera, iwr)

    assert solved["source"] == "rig_nominal"
    assert solved["height_m"] == pytest.approx(0.095)
    assert solved["nominal_m"] == pytest.approx(0.095)
    assert solved["radar_solved_m"] == pytest.approx(0.083, abs=0.002)
    assert solved["check"] == "consistent"
    assert solved["reference"] == "hitting_surface"


def test_a_unit_standing_on_a_box_uses_the_radar_solved_lens_height():
    camera, result, iwr = _setup_ball_seen_from(0.40)

    solved = ts._solved_camera_height(result, camera, iwr)

    assert solved["source"] == "static_iwr_range"
    assert solved["height_m"] == pytest.approx(0.40, abs=0.003)
    assert solved["check"] == "unit_raised"
    assert "above the hitting surface" in solved["note"]


def test_a_lens_height_below_the_radar_is_refused():
    # the radar sits 44 mm below the lens, so a lens 30 mm up would bury it
    camera, result, iwr = _setup_ball_seen_from(0.030, distance_m=2.0)

    solved = ts._solved_camera_height(result, camera, iwr)

    assert solved["source"] == "rig_nominal"
    assert solved["height_m"] == pytest.approx(0.095)
    assert "below the hitting surface" in solved["radar_rejected"]


def test_only_a_radar_solved_height_is_handed_to_swings():
    camera, result, iwr = _setup_ball_seen_from(0.40)
    boxed = ts._solved_camera_height(result, camera, iwr)
    camera, result, iwr = _setup_ball_seen_from(0.090)
    level = ts._solved_camera_height(result, camera, iwr)

    def solution(height):
        candidate = tee_range.TeeRangeCandidate(
            candidate_id="camera-setup-20260929-000000000001-arm5",
            source="camera_reference_ball",
            source_group="camera",
            radar_slant_range_m=1.4,
            uncertainty_m=0.03,
            evidence={"camera_height": height},
        )
        return tee_range.TeeRangeSolution.unresolved((candidate,), reason="test")

    assert ts._setup_camera_height_m(solution(boxed)) == pytest.approx(0.40, abs=0.003)
    assert ts._setup_camera_height_m(solution(level)) is None


def _accepted_iwr(range_m=1.4, **evidence):
    return tee_range.TeeRangeCandidate(
        candidate_id="iwr-static-setup-1",
        source="iwr_static_profile_difference",
        source_group="iwr",
        radar_slant_range_m=range_m,
        uncertainty_m=0.03,
        selectable=False,
        evidence={
            "method": "pre_mti_empty_vs_ball_present",
            "difference": {"status": "accepted"},
            **evidence,
        },
    )


def _camera_candidate(arm_id, height, range_m=1.4):
    return tee_range.TeeRangeCandidate(
        candidate_id=f"camera-setup-1-{arm_id}",
        source="camera_reference_ball",
        source_group="camera",
        radar_slant_range_m=range_m,
        uncertainty_m=0.03,
        selectable=False,
        evidence={"camera_height": height},
    )


def _heights():
    """The lens-height evidence of a unit on a box (0.40 m) and of one on the surface."""

    def solved(lens_height_m):
        camera, result, iwr = _setup_ball_seen_from(lens_height_m)
        return ts._solved_camera_height(result, camera, iwr)

    return solved(0.40), solved(0.090)


def test_a_rejected_radar_with_a_camera_range_leaves_swings_pending():
    # wiring audit S2 (D4): the camera's size-derived range (sigma ~21 %) is never
    # handed to swings as the radar tee range
    rejected = replace(_accepted_iwr(), evidence={"difference": {"status": "rejected_clutter"}})
    _boxed, level = _heights()
    solution = tee_range.TeeRangeSolution.unresolved(
        (rejected, _camera_candidate("arm5", level), _camera_candidate("arm6", level)),
        reason="test",
    )

    args = ts._tee_range_cli_args(solution, use_unqualified=True)

    assert ts.unqualified_tee_range_choice(solution) is None
    assert "--iwr6843-tee-range-pending" in args
    assert "--iwr6843-tee-m" not in args


def test_a_camera_steered_radar_range_is_still_an_unqualified_choice():
    steered = _accepted_iwr(
        1.2, camera_window={"outcome": "reselected", "camera_window_m": [0.72, 1.68]}
    )
    solution = tee_range.TeeRangeSolution.unresolved((steered,), reason="test")

    assert ts.unqualified_tee_range_choice(solution).candidate_id == steered.candidate_id


@pytest.mark.parametrize("outcome", ["not_rechecked", "camera_window_disjoint"])
def test_a_radar_pick_the_camera_could_not_recheck_is_unusable(outcome):
    # wiring audit S5: the radar's own pick lies outside the camera's window and
    # nothing inside the window replaced it
    camera, result, _iwr = _setup_ball_seen_from(0.40)
    iwr = _accepted_iwr(camera_window={"outcome": outcome, "camera_window_m": [0.8, 1.2]})
    solution = tee_range.TeeRangeSolution.unresolved((iwr,), reason="test")

    solved = ts._solved_camera_height(result, camera, iwr.to_dict())

    assert ts.unqualified_tee_range_choice(solution) is None
    assert solved["source"] == "rig_nominal"
    assert solved["radar_solved_m"] is None


def test_a_qualification_interval_disjoint_from_the_camera_window_rejects(
    tmp_path, inputs, monkeypatch
):
    # a person 2.8 m out is the only change inside a 2.0-4.0 m qualification window,
    # while the camera puts the ball at 1.2 m (window 0.72-1.68 m)
    artifact = replace(qualification(inputs), plausible_range_m=(2.0, 4.0))
    inputs["qualification"].write_text(json.dumps(artifact.to_dict()), encoding="utf-8")
    app, tester = app_for(tmp_path, inputs, monkeypatch)
    app.config["TEST_STATIC_MANAGER"].other_return = (70, 300.0)

    state = drive(app.test_client(), tester)

    iwr = state["evidence"]["iwr_candidate"]
    assert iwr["evidence"]["camera_window"]["outcome"] == "camera_window_disjoint"
    solution = tee_range.TeeRangeSolution.from_dict(state["solution"])
    assert ts.unqualified_tee_range_choice(solution) is None
    display = ts.tee_range_display(state)
    assert display["iwr"]["state"] == "rejected"
    assert "camera" in display["iwr"]["reason"]


def test_the_lens_height_comes_from_the_1280x800_search_only():
    # wiring audit S4: arm5 found the rig height consistent; arm6's coarser solve
    # is a check, not an override
    boxed, level = _heights()
    solution = tee_range.TeeRangeSolution.unresolved(
        (_camera_candidate("arm5", level), _camera_candidate("arm6", boxed)), reason="test"
    )

    assert level["check"] == "consistent"
    assert boxed["check"] == "unit_raised"
    assert ts._setup_camera_height_m(solution) is None


def _arm6_at(monkeypatch, arm6_m):
    """The 640x400 validation sees the ball at another range than 1280x800 (1.2 m)."""
    monkeypatch.setattr(
        ts,
        "estimate_reference_ball_range",
        lambda _frames, camera, **_kwargs: camera_result(
            1.2 if camera.image_width_px == 1280 else arm6_m,
            camera.image_width_px,
            camera.image_height_px,
        ),
    )


def test_an_agreeing_640x400_validation_is_recorded(tmp_path, inputs, monkeypatch):
    app, tester = app_for(tmp_path, inputs, monkeypatch)
    _arm6_at(monkeypatch, 1.22)

    state = drive(app.test_client(), tester)

    agreement = state["evidence"]["validation_agreement"]
    assert agreement["status"] == "agrees"
    assert agreement["residual_m"] == pytest.approx(0.02)
    assert agreement["combined_uncertainty_m"] == pytest.approx(math.hypot(0.02, 0.02))
    assert ts.tee_range_display(state)["validation"]["state"] == "agrees"


def test_a_disagreeing_640x400_validation_flags_the_setup_without_blocking_it(
    tmp_path, inputs, monkeypatch
):
    # wiring audit S9: the 640x400 validation only had to exist
    app, tester = app_for(tmp_path, inputs, monkeypatch)
    _arm6_at(monkeypatch, 1.5)

    state = drive(app.test_client(), tester)

    agreement = state["evidence"]["validation_agreement"]
    assert agreement["status"] == "validation_disagrees"
    assert agreement["normalized_sigma"] > ts.ARM6_VALIDATION_SIGMAS
    assert agreement["blocking"] is False
    assert state["phase"] == "resolved"
    validation = ts.tee_range_display(state)["validation"]
    assert validation["state"] == "validation_disagrees"
    assert "1.500" in validation["message"] and "1.200" in validation["message"]


def test_a_validation_without_a_range_is_not_compared():
    arm5 = _camera_candidate("arm5", None, range_m=1.2)
    arm6 = tee_range.TeeRangeCandidate(
        candidate_id="camera-setup-1-arm6",
        source="camera_reference_ball",
        source_group="camera",
        radar_slant_range_m=None,
        uncertainty_m=None,
        selectable=False,
        evidence={},
    )

    assert ts.camera_validation_agreement(arm5, arm6)["status"] == "not_compared"


def test_a_pending_range_still_hands_swings_the_solved_lens_height():
    # wiring audit S6: a unit on a box without a range for swings
    boxed, _level = _heights()
    solution = tee_range.TeeRangeSolution.unresolved(
        (_accepted_iwr(), _camera_candidate("arm5", boxed)), reason="test"
    )

    args = ts._tee_range_cli_args(solution)

    assert args[0] == "--iwr6843-tee-range-pending"
    assert "--iwr6843-tee-m" not in args
    assert float(args[args.index("--solved-camera-height-m") + 1]) == pytest.approx(0.40, abs=0.003)
    assert args[args.index("--iwr6843-ball-height-m") + 1] == "0.021335"


def test_setup_captures_go_through_the_held_radar_session(tmp_path, inputs, monkeypatch):
    app, tester = app_for(tmp_path, inputs, monkeypatch, held_radar=True, qualified=False)
    client = app.test_client()

    for index, action in enumerate(("start", "capture_empty", "capture_ball")):
        assert post(client, tester, action, f"held-{index}").status_code == 200

    assert app.config["TEST_HELD_RADAR"].start_count == 2
    assert app.config["TEST_STATIC_MANAGER"].start_count == 0
    assert phase(client, tester)["phase"] in {"needs_camera_arm5", "camera_arm5_capturing"}


def test_a_hardware_job_releases_the_held_radar_first(tmp_path, inputs, monkeypatch):
    app, tester = app_for(tmp_path, inputs, monkeypatch, held_radar=True)
    body = {"tester_id": tester, "arm_id": "arm5", "environment": "indoors", "action": "preflight"}

    assert app.test_client().post("/api/tester/run", json=body).status_code == 202

    assert app.config["TEST_HELD_RADAR"].releases == 1


def test_stop_also_cancels_a_held_radar_capture(tmp_path, inputs, monkeypatch):
    app, _tester = app_for(tmp_path, inputs, monkeypatch, held_radar=True)

    assert app.test_client().post("/api/tester/stop").status_code == 200

    assert app.config["TEST_HELD_RADAR"].cancels == 1


def test_the_camera_window_is_its_range_plus_minus_two_sigma_with_a_20_percent_floor():
    selected = camera_result(1.2).selected

    low, high = ts.camera_radar_window(selected)

    # floor uncertainty is 0.02 m, below the 20 % floor, so the window is +-0.48 m
    assert low == pytest.approx(1.2 - 0.48)
    assert high == pytest.approx(1.2 + 0.48)
    assert ts.camera_radar_window(replace(selected, floor_radar_range_m=None)) is None


def test_the_camera_steers_the_radar_away_from_a_person_behind_the_ball(
    tmp_path, inputs, monkeypatch
):
    app, tester = app_for(tmp_path, inputs, monkeypatch, qualified=False)
    radar = app.config["TEST_STATIC_MANAGER"]
    # a still person 2.8 m out adds ten times the ball's echo
    radar.other_return = (70, 300.0)

    state = drive(app.test_client(), tester)

    iwr = state["evidence"]["iwr_candidate"]
    window = iwr["evidence"]["camera_window"]
    assert window["outcome"] == "reselected"
    assert window["full_window"]["range_m"] == pytest.approx(2.8, abs=0.05)
    assert iwr["radar_slant_range_m"] == pytest.approx(1.2, abs=0.05)
    assert iwr["evidence"]["qualification"]["camera_range_used"] is True
    assert iwr["evidence"]["qualification"]["accuracy_qualified"] is False


def test_the_camera_rescues_a_three_bin_ball_at_one_metre(tmp_path, inputs, monkeypatch):
    # wiring audit S1: re-selecting inside the 0.6-1.4 m camera window judged clutter
    # over that window alone, where the ball's three bins are 3 of 21
    app, tester = app_for(tmp_path, inputs, monkeypatch, qualified=False, camera_m=1.0)
    radar = app.config["TEST_STATIC_MANAGER"]
    radar.ball_bins = (24, 25, 26)
    radar.other_return = (70, 300.0)

    state = drive(app.test_client(), tester)

    iwr = state["evidence"]["iwr_candidate"]
    assert iwr["evidence"]["camera_window"]["outcome"] == "reselected"
    assert iwr["evidence"]["difference"]["status"] == "accepted"
    assert iwr["radar_slant_range_m"] == pytest.approx(1.0, abs=0.03)
    # the search stays the whole qualification window; the camera only picks the cluster
    assert iwr["evidence"]["search_window_m"] == [0.5, 4.0]


def test_a_radar_reading_inside_the_camera_window_is_kept_as_is(tmp_path, inputs, monkeypatch):
    app, tester = app_for(tmp_path, inputs, monkeypatch, qualified=False)

    state = drive(app.test_client(), tester)

    iwr = state["evidence"]["iwr_candidate"]
    assert iwr["evidence"]["camera_window"]["outcome"] == "consistent"
    assert iwr["radar_slant_range_m"] == pytest.approx(1.2, abs=0.05)
    assert iwr["evidence"]["qualification"]["camera_range_used"] is False


class HandedOverTilt(MutableTilt):
    """The LIS3DH as the kiosk takes it: "off" once stopped, warming up after a restart."""

    def __init__(self, warmup_readings=0):
        super().__init__()
        self.running = True
        self.warmup_readings = warmup_readings
        self._warming = 0

    def stop(self):
        self.running = False

    def start(self):
        self.running = True
        self._warming = self.warmup_readings

    def reading(self):
        if not self.running:
            return {"status": "off", "error": None}
        if self._warming > 0:
            self._warming -= 1
            return {"status": "unstable", "camera_pitch_deg": self.pitch, "roll_deg": 0.0}
        return super().reading()


def test_a_finished_setup_survives_the_kiosk_taking_the_tilt_sensor(tmp_path, inputs, monkeypatch):
    # 29 Sept (Outdoors-test-1 and -3): the page's poll re-validated the finished
    # setup against the LIS3DH the ladder had just handed to the kiosk, and saved
    # "start over required"
    tilt = HandedOverTilt()
    app, tester = app_for(tmp_path, inputs, monkeypatch, tilt=tilt)
    client = app.test_client()
    finished = drive(client, tester)
    assert finished["phase"] in ts.TERMINAL_PHASES

    tilt.stop()
    states = [phase(client, tester) for _ in range(5)]

    assert all(state["phase"] == finished["phase"] for state in states)
    assert all(state["sequence"] == finished["sequence"] for state in states)


def _gain_both_arms(sessions, tester):
    for arm_id in ("arm5", "arm6"):
        ts.write_arm_state(
            sessions,
            ts.TesterParameters(tester, arm_id, "indoors"),
            gain=4.0,
            gain_exposure_us=ts.ARMS[arm_id].exposure_us,
        )


def test_a_real_rig_move_is_still_refused_when_capture_starts(tmp_path, inputs, monkeypatch):
    tilt = HandedOverTilt()
    app, tester = app_for(tmp_path, inputs, monkeypatch, tilt=tilt)
    client = app.test_client()
    drive(client, tester)
    _gain_both_arms(tmp_path / "sessions", tester)
    tilt.pitch = ts.TEE_RANGE_ORIENTATION_DRIFT_DEG + 0.5

    response = client.post(
        "/api/tester/ladder/start",
        json={"tester_id": tester, "arm_id": "arm5", "environment": "indoors"},
    )

    assert response.status_code == 409
    assert "orientation changed" in response.get_json()["error"]


def test_a_tilt_reading_is_given_time_to_settle_after_a_restart():
    readings = iter([{"status": "unstable"}, {"status": "unstable"}, {"status": "stable", "x": 1}])
    now = [0.0]

    reading = ts.settle_reading(
        lambda: next(readings),
        timeout_s=3.0,
        sleep=lambda s: now.__setitem__(0, now[0] + s),
        clock=lambda: now[0],
    )

    assert reading == {"status": "stable", "x": 1}


def test_a_tilt_reading_that_never_settles_is_returned_as_it_is():
    now = [0.0]

    reading = ts.settle_reading(
        lambda: {"status": "off"},
        timeout_s=3.0,
        sleep=lambda s: now.__setitem__(0, now[0] + s),
        clock=lambda: now[0],
    )

    assert reading == {"status": "off"}
    assert now[0] >= 3.0


def _session_start_for(command, rig_path, tmp_path, monkeypatch):
    """Start the swing server on the tester's hand-off; return its session_start config."""
    from openflight import server  # noqa: PLC0415

    class StopAfterIwrInit(Exception):
        pass

    def stop(**_kwargs):
        raise StopAfterIwrInit

    # everything the tester hands over sits between --iwr6843 and --inclinometer
    handoff = command[command.index("--iwr6843") + 1 : command.index("--inclinometer")]
    monkeypatch.setattr(server, "init_iwr6843", stop)
    monkeypatch.setattr(server, "init_session_logger", lambda **_kwargs: None)
    monkeypatch.setattr(server, "ball_speed_correction_enabled", None)
    monkeypatch.setattr(server, "profile_store", None)
    for name in ("rig_geometry", "rig_geometry_config", "setup_handoff_config"):
        monkeypatch.setattr(server, name, getattr(server, name))
    monkeypatch.setattr(
        server.sys,
        "argv",
        [
            "openflight-server",
            "--iwr6843",
            "--no-logging",
            "--profiles-path",
            str(tmp_path / "profiles.json"),
            "--rig-geometry",
            str(rig_path),
            *handoff,
        ],
    )
    with pytest.raises(StopAfterIwrInit):
        server.main()
    return server._session_start_config()


def _solving_camera(monkeypatch):
    """A camera that also solves a lens height from the ball's size, as the real one does."""
    monkeypatch.setattr(
        ts,
        "estimate_reference_ball_range",
        lambda _frames, camera, **_kwargs: (
            lambda result: replace(
                result,
                selected=replace(
                    result.selected, camera_height_m=0.1, camera_height_uncertainty_m=0.05
                ),
            )
        )(camera_result(1.2, camera.image_width_px, camera.image_height_px)),
    )


def test_every_record_and_session_start_say_what_swings_used(tmp_path, inputs, monkeypatch):
    """Wiring audit S3 with C3, C4, C5: an unqualified run's tee value, candidate,
    heights and their bases are in arm.json, setup_admission.json, the run's
    tee_range.json and session_start, and both rig hashes join setup to session."""
    inputs["rig"].write_bytes(Path(ts.DEFAULT_RIG_GEOMETRY).read_bytes())
    app, tester = app_for(tmp_path, inputs, monkeypatch, qualified=False, use_unqualified=True)
    _solving_camera(monkeypatch)
    client = app.test_client()
    state = drive(client, tester)
    body = {"tester_id": tester, "arm_id": "arm5", "environment": "indoors", "action": "swings"}

    response = client.post("/api/tester/run", json=body)

    assert response.status_code == 202, response.get_json()
    command = app.config["TEST_STATIC_MANAGER"].last_command
    run_dir = Path(command[command.index("--log-dir") + 1])
    arm = ts.read_arm_state(tmp_path / "sessions", tester, "arm5")
    admission = json.loads((run_dir / "setup_admission.json").read_text(encoding="utf-8"))
    contract = json.loads((run_dir / "tee_range.json").read_text(encoding="utf-8"))
    handed = arm["handed_to_swings"]
    assert admission["handed_to_swings"] == handed
    assert contract["handed_to_swings"] == handed
    iwr = state["evidence"]["iwr_candidate"]
    assert handed["epoch_id"] == state["epoch_id"]
    assert handed["tee_range_status"] == "unqualified"
    assert handed["tee_range_source"] == "unqualified_static_iwr"
    assert handed["tee_m"] == pytest.approx(iwr["radar_slant_range_m"])
    assert handed["candidate_id"] == iwr["candidate_id"]
    assert handed["qualified"] is False
    assert handed["camera_window"]["outcome"] == "consistent"
    assert arm["tee_range_m"] == handed["tee_m"]
    assert arm["tee_range_source"] == handed["tee_range_source"]
    scene = handed["scene"]
    assert scene["lens_height_solved_m"] is not None
    assert scene["lens_height_used_source"] in {"rig_nominal", "range_setup"}
    assert scene["ball_height_m"] == pytest.approx(0.021335)
    assert scene["ball_height_basis"] == "assumed_on_surface"
    assert (
        handed["cli_args"]
        == command[command.index("--iwr6843") + 1 : command.index("--inclinometer")]
    )
    # the contract file still loads as a tee-range solution
    assert tee_range.load_solution(run_dir / "tee_range.json").status == "unresolved"

    session = _session_start_for(command, inputs["rig"], tmp_path, monkeypatch)

    assert session["tee_range_handoff"] == {
        "tee_slant_range_m": pytest.approx(handed["tee_m"]),
        "status": "configured",
        "source": handed["tee_range_source"],
        "candidate_id": handed["candidate_id"],
    }
    for key in (
        "reference",
        "lens_height_used_m",
        "lens_height_used_source",
        "lens_height_solved_m",
        "lens_height_solved_uncertainty_m",
        "rig_lens_height_m",
        "ball_height_m",
        "ball_height_basis",
    ):
        # heights cross the command line to the micrometre
        assert session["scene"][key] == pytest.approx(scene[key], abs=1e-6), key
    hashes = {
        "rig_geometry_file_sha256": file_hash(inputs["rig"]),
        "rig_geometry_params_sha256": session["rig_geometry"]["snapshot"]["sha256"],
    }
    assert {key: session["rig_geometry"][key] for key in hashes} == hashes
    assert {key: handed[key] for key in hashes} == hashes
    assert state["evidence"]["rig_geometry"] == hashes
    assert state["evidence"]["scene"] == scene


def test_a_pending_hand_off_is_recorded_as_pending():
    boxed, _level = _heights()
    solution = tee_range.TeeRangeSolution.unresolved(
        (_accepted_iwr(), _camera_candidate("arm5", boxed)), reason="test"
    )

    args, handed = ts.tee_range_handoff(solution)

    assert handed["tee_range_status"] == handed["tee_range_source"] == "pending"
    assert handed["tee_m"] is None
    assert handed["candidate_id"] is None
    assert handed["qualified"] is False
    assert handed["solved_camera_height_m"] == pytest.approx(0.40, abs=0.003)
    assert handed["scene"]["lens_height_used_source"] == "range_setup"
    assert handed["scene"]["lens_height_check"] == "unit_raised"
    assert args[args.index("--iwr6843-tee-range-source") + 1] == "pending"
    assert "--iwr6843-tee-range-candidate" not in args
    assert handed["cli_args"] == args


def test_a_rig_consistent_setup_still_records_the_solved_lens_height():
    # wiring audit C4: the rig height is used, and the solve is kept as the scene's
    _boxed, level = _heights()
    solution = tee_range.TeeRangeSolution.unresolved(
        (_accepted_iwr(), _camera_candidate("arm5", level)), reason="test"
    )

    args, handed = ts.tee_range_handoff(solution, use_unqualified=True)

    scene = handed["scene"]
    assert scene["lens_height_check"] == "consistent"
    assert scene["lens_height_used_source"] == "rig_nominal"
    assert scene["lens_height_solved_m"] == pytest.approx(0.090, abs=0.002)
    assert scene["lens_height_solved_uncertainty_m"] > 0.0
    assert "--solved-camera-height-m" not in args
    assert float(args[args.index("--scene-lens-height-solved-m") + 1]) == pytest.approx(
        0.090, abs=0.002
    )


def test_a_camera_steered_hand_off_says_so():
    steered = _accepted_iwr(
        1.2, camera_window={"outcome": "reselected", "camera_window_m": [0.72, 1.68]}
    )
    solution = tee_range.TeeRangeSolution.unresolved((steered,), reason="test")

    args, handed = ts.tee_range_handoff(solution, use_unqualified=True)

    assert handed["tee_range_source"] == "unqualified_static_iwr_camera_steered"
    assert handed["camera_window"] == {"outcome": "reselected", "camera_window_m": [0.72, 1.68]}
    assert args[args.index("--iwr6843-tee-range-candidate") + 1] == steered.candidate_id


def test_a_failure_message_belongs_to_the_transition_that_set_it(tmp_path, inputs, monkeypatch):
    """Wiring audit T8: fail, retry and succeed, then a different failure; the page
    shows a failure only when it was set in the current transition."""
    live = FakeLive()
    app, tester = app_for(tmp_path, inputs, monkeypatch, live_view=live)
    manager = app.config["TEST_STATIC_MANAGER"]
    client = app.test_client()
    for index, action in enumerate(("start", "capture_empty")):
        assert post(client, tester, action, f"t8-{index}").status_code == 200
    manager.fail = True
    assert post(client, tester, "capture_ball", "t8-ball-fails").status_code == 200
    failed = phase(client, tester)
    assert failed["evidence"]["capture_failure"]["sequence"] == failed["sequence"]

    manager.fail = False
    for index, action in enumerate(("retry", "capture_ball", "start_camera_arm5")):
        assert post(client, tester, action, f"t8-again-{index}").status_code == 200
    live.error = "camera cable disconnected"
    live.running = False
    state = phase(client, tester)

    assert state["phase"] == "retryable_failure"
    assert state["evidence"]["camera_capture_failure"]["sequence"] == state["sequence"]
    # the radar failure is still on record, but it is not this transition's
    assert state["evidence"]["capture_failure"]["sequence"] < state["sequence"]


def test_a_late_empty_capture_callback_during_the_ball_capture_changes_nothing(
    tmp_path, inputs, monkeypatch
):
    """Wiring audit T9: a capture result is finished once, by its own capture."""
    live = FakeLive()
    app, tester, manager = deferred_app(tmp_path, inputs, monkeypatch, live)
    original = app.config["TEST_STATIC_MANAGER"]
    callbacks = []

    def recording_start(action, commands, log_path, on_finish=None, **kwargs):
        callbacks.append(on_finish)
        return manager.start(action, commands, log_path, on_finish=on_finish, **kwargs)

    monkeypatch.setattr(original, "start", recording_start)
    client = app.test_client()
    for index, action in enumerate(("start", "capture_empty", "capture_ball")):
        assert post(client, tester, action, f"t9-{index}").status_code == 200
    during = phase(client, tester)
    assert during["phase"] == "ball_capturing"

    callbacks[0]("tee_range", 0)  # the empty capture's completion, delivered again

    after = phase(client, tester)
    assert after["phase"] == "ball_capturing"
    assert after["sequence"] == during["sequence"]
    manager.release()
    assert phase(client, tester)["phase"] == "camera_arm5_capturing"


class StoppableStaticManager(StaticManager):
    """A radar setup capture that runs until Stop (or its timeout) cancels it."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.mirror = self  # the manager the app holds, which reports the cancel
        self.running = None

    def start(self, action, commands, log_path, on_finish=None, **kwargs):
        if action != "tee_range":
            return super().start(action, commands, log_path, on_finish=on_finish, **kwargs)
        self.mirror.cancel_requested = False
        self.running = (action, on_finish)
        self._status = {"state": "running", "action": "tee_range", "message": "capturing"}
        return None

    def cancel(self):
        if self.running is None:
            return False
        action, on_finish = self.running
        self.running = None
        self.mirror.cancel_requested = True
        self._status = {"state": "idle", "action": None, "message": "Ready"}
        on_finish(action, -15)  # the capture process was stopped, so it wrote no result
        return True


def test_stopping_a_radar_capture_keeps_the_hardware_check(tmp_path, inputs, monkeypatch):
    """Wiring audit T10: Stop or a timeout is not a hardware failure."""
    app, tester = app_for(tmp_path, inputs, monkeypatch, require_iwr_preflight=True)
    original = app.config["TEST_STATIC_MANAGER"]
    manager = StoppableStaticManager(
        config_hash=original.config_hash,
        firmware_hash=original.firmware_hash,
        rig_hash=original.rig_hash,
        calibration_hash=original.calibration_hash,
    )
    for name in ("start", "status", "cancel"):
        monkeypatch.setattr(original, name, getattr(manager, name))
    manager.mirror = original
    client = app.test_client()
    body = {"tester_id": tester, "arm_id": "arm5", "environment": "indoors"}
    assert client.post("/api/tester/run", json={**body, "action": "preflight"}).status_code == 202
    assert post(client, tester, "start", "t10-start").status_code == 200
    assert post(client, tester, "capture_empty", "t10-empty").status_code == 200

    assert client.post("/api/tester/stop").get_json()["stopped"] is True

    state = phase(client, tester)
    assert state["phase"] == "retryable_failure"
    assert state["reason"] == "empty_capture_stopped"
    eligibility = client.get(
        "/api/tester/setup-eligibility", query_string={"tester_id": tester}
    ).get_json()
    assert eligibility["eligible"] is True
    assert "iwr6843_cli" not in [blocker["id"] for blocker in eligibility["blockers"]]

"""Tests for the browser-driven OV9281 study runner."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from openflight.camera.study_server import (
    StudyParameters,
    action_commands,
    create_app,
    study_directory,
)


def valid_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "study_id": "20260917-test-enclosure",
        "preset": "binned-crop-320-450",
        "exposure_us": 150,
        "gain": 4,
        "exposure_sweep_us": "75,100,150",
        "gain_sweep": "1,2,4",
        "captures": 5,
        "square_mm": 24.8,
        "rotate_180": True,
    }
    payload.update(overrides)
    return payload


class FakeManager:
    def __init__(self) -> None:
        self.started: tuple[str, list[list[str]], Path] | None = None
        self.running = False

    def status(self) -> dict[str, object]:
        return {
            "state": "running" if self.running else "idle",
            "action": self.started[0] if self.started else None,
            "message": "Ready",
            "output": [],
        }

    def start(self, action: str, commands: list[list[str]], log_path: Path) -> None:
        if self.running:
            raise RuntimeError("another study action is already running")
        self.started = (action, commands, log_path)
        self.running = True

    def cancel(self) -> bool:
        was_running = self.running
        self.running = False
        return was_running


def test_root_serves_the_guided_runner(tmp_path: Path) -> None:
    response = create_app(sessions_root=tmp_path, manager=FakeManager()).test_client().get("/")

    assert response.status_code == 200
    assert b'data-study-action="exposure"' in response.data
    assert b"Package entire study" in response.data


def test_parameters_reject_paths_and_exposure_outside_frame_period() -> None:
    with pytest.raises(ValueError, match="study_id"):
        StudyParameters.from_payload(valid_payload(study_id="../escape"))

    with pytest.raises(ValueError, match="frame period"):
        StudyParameters.from_payload(valid_payload(exposure_us=3000))

    with pytest.raises(ValueError, match="true or false"):
        StudyParameters.from_payload(valid_payload(rotate_180="sometimes"))


def test_exposure_action_is_allowlisted_and_confined(tmp_path: Path) -> None:
    params = StudyParameters.from_payload(valid_payload())

    commands, log_path = action_commands("exposure", params, tmp_path)

    assert len(commands) == 1
    assert Path(commands[0][1]).name == "calibrate_camera_exposure.py"
    assert commands[0][commands[0].index("--exposures-us") + 1] == "75,100,150"
    assert "--no-prompt" in commands[0]
    assert commands[0][-1] == "--rotate-180"
    assert tmp_path.resolve() in log_path.resolve().parents

    with pytest.raises(ValueError, match="unknown study action"):
        action_commands("shell", params, tmp_path)


def test_run_endpoint_records_config_and_starts_selected_action(tmp_path: Path) -> None:
    manager = FakeManager()
    client = create_app(sessions_root=tmp_path, manager=manager).test_client()

    response = client.post(
        "/api/camera-study/run", json={**valid_payload(), "action": "checkerboard"}
    )

    assert response.status_code == 202
    assert manager.started is not None
    assert manager.started[0] == "checkerboard"
    params = StudyParameters.from_payload(valid_payload())
    config = json.loads((study_directory(tmp_path, params) / "study.json").read_text())
    assert config["preset"] == "binned-crop-320-450"
    assert config["mode"] == {"width": 320, "height": 200, "fps": 450.0}

    conflict = client.post("/api/camera-study/run", json={**valid_payload(), "action": "preflight"})
    assert conflict.status_code == 409


def test_status_counts_saved_outputs(tmp_path: Path) -> None:
    params = StudyParameters.from_payload(valid_payload())
    root = study_directory(tmp_path, params)
    (root / "intrinsics" / "captures").mkdir(parents=True)
    (root / "intrinsics" / "captures" / "view_01.png").write_bytes(b"png")
    (root / "exposure" / "run-1").mkdir(parents=True)
    (root / "exposure" / "run-1" / "results.json").write_text("[]")
    (root / "swings" / "run-1" / "capture_001").mkdir(parents=True)
    (root / "swings" / "run-1" / "capture_001" / "frames.npz").write_bytes(b"npz")
    client = create_app(sessions_root=tmp_path, manager=FakeManager()).test_client()

    response = client.post("/api/camera-study/status", json=valid_payload())

    assert response.status_code == 200
    assert response.json["artifacts"]["calibration_views"] == 1
    assert response.json["artifacts"]["exposure_runs"] == 1
    assert response.json["artifacts"]["swing_captures"] == 1


def test_package_endpoint_creates_one_downloadable_archive(tmp_path: Path) -> None:
    params = StudyParameters.from_payload(valid_payload())
    root = study_directory(tmp_path, params)
    root.mkdir(parents=True)
    (root / "capture.txt").write_text("camera data", encoding="utf-8")
    client = create_app(sessions_root=tmp_path, manager=FakeManager()).test_client()

    created = client.post("/api/camera-study/package", json=valid_payload())

    assert created.status_code == 200
    assert created.json["artifacts"]["package_ready"] is True
    downloaded = client.get(
        "/api/camera-study/package",
        query_string={"study_id": params.study_id, "preset": params.preset},
    )
    assert downloaded.status_code == 200
    archive = tmp_path / f"{params.study_id}-openflight-study.zip"
    with zipfile.ZipFile(archive) as bundle:
        assert bundle.read(f"{params.study_id}/{params.preset}/capture.txt") == b"camera data"
        assert f"{params.study_id}/{params.preset}/study.json" in bundle.namelist()

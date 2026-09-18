"""Tests for the tester pilot capture runner."""

from __future__ import annotations

import json
import zipfile

import pytest

from openflight.camera import tester_server


def params(**overrides):
    payload = {
        "tester_id": "20260918-name",
        "preset": "binned-320-450",
        "exposure_us": 150,
        "gain": 4,
        "geometry": {"camera_to_rx_x_mm": 0, "camera_height_mm": 210},
    }
    payload.update(overrides)
    return tester_server.TesterParameters.from_payload(payload)


def test_tester_id_rejects_path_traversal():
    with pytest.raises(ValueError, match="tester_id"):
        params(tester_id="../../etc")


def test_unknown_preset_is_rejected():
    with pytest.raises(ValueError, match="unknown camera mode"):
        params(preset="made-up")


def test_exposure_must_fit_inside_the_frame_period():
    with pytest.raises(ValueError, match="frame period"):
        params(exposure_us=5000)


def test_unknown_action_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="unknown tester action"):
        tester_server.action_commands("rm -rf /", params(), tmp_path)


def test_paired_action_drives_the_kiosk_with_radar_and_debug(tmp_path):
    commands, log_path = tester_server.action_commands("paired", params(), tmp_path)
    assert len(commands) == 1
    command = commands[0]
    assert command[1].endswith("start-kiosk.sh")
    assert "--iwr6843" in command
    assert "--camera-capture" in command
    assert "--debug" in command
    assert command[command.index("--camera-capture-width") + 1] == "320"
    assert command[command.index("--camera-capture-fps") + 1] == "450.0"
    assert log_path.name == "paired.log"


def test_exposure_action_does_not_wait_for_a_terminal(tmp_path):
    commands, _ = tester_server.action_commands("exposure", params(), tmp_path)
    assert "--no-prompt" in commands[0]


def test_verify_reports_missing_radar_dumps(tmp_path):
    settings = params()
    root = tester_server.tester_directory(tmp_path, settings)
    shot = root / "paired" / "tester" / "camera" / "camera_1_001"
    shot.mkdir(parents=True)
    (shot / "frames.npz").write_bytes(b"frames")
    report = tester_server.verify_capture(tmp_path, settings)
    assert report["camera_captures"] == 1
    assert report["radar_dumps"] == 0
    assert report["ready_to_send"] is False
    assert any("l3dump" in problem for problem in report["problems"])


def test_verify_flags_an_empty_camera_capture(tmp_path):
    settings = params()
    root = tester_server.tester_directory(tmp_path, settings)
    (root / "paired" / "tester" / "camera" / "camera_1_001").mkdir(parents=True)
    report = tester_server.verify_capture(tmp_path, settings)
    assert report["camera_captures"] == 0
    assert any("no frames.npz" in problem for problem in report["problems"])


def test_verify_passes_when_both_sensors_paired(tmp_path):
    settings = params()
    root = tester_server.tester_directory(tmp_path, settings)
    camera = root / "paired" / "tester" / "camera"
    dumps = root / "paired" / "iwr6843"
    camera.mkdir(parents=True)
    dumps.mkdir(parents=True)
    for index in range(3):
        shot = camera / f"camera_1_00{index}"
        shot.mkdir()
        (shot / "frames.npz").write_bytes(b"frames")
        (dumps / f"iwr6843_1_00{index}.l3dump").write_bytes(b"dump")
    report = tester_server.verify_capture(tmp_path, settings)
    assert report["ready_to_send"] is True
    assert report["problems"] == []


def test_verify_requires_recorded_measurements(tmp_path):
    settings = params(geometry={})
    root = tester_server.tester_directory(tmp_path, settings)
    camera = root / "paired" / "tester" / "camera"
    dumps = root / "paired" / "iwr6843"
    camera.mkdir(parents=True)
    dumps.mkdir(parents=True)
    shot = camera / "camera_1_001"
    shot.mkdir()
    (shot / "frames.npz").write_bytes(b"frames")
    (dumps / "iwr6843_1_001.l3dump").write_bytes(b"dump")
    report = tester_server.verify_capture(tmp_path, settings)
    assert any("measurements" in problem for problem in report["problems"])


def test_calibration_views_match_the_clap_script_layout(tmp_path):
    """test_camera_clap_buffer.py writes <outdir>/<timestamp>/capture_NNN/frames.npz."""
    settings = params()
    root = tester_server.tester_directory(tmp_path, settings)
    session = root / "calibration" / "20260918_101500"
    for index in range(1, 4):
        shot = session / f"capture_{index:03d}"
        shot.mkdir(parents=True)
        (shot / "frames.npz").write_bytes(b"frames")
    report = tester_server.verify_capture(tmp_path, settings)
    assert report["calibration_views"] == 3


def test_package_carries_the_recorded_measurements(tmp_path):
    settings = params()
    root = tester_server.tester_directory(tmp_path, settings)
    tester_server._write_tester_config(root, settings)
    archive = tester_server.package_capture(tmp_path, settings)
    with zipfile.ZipFile(archive) as bundle:
        name = next(n for n in bundle.namelist() if n.endswith("tester.json"))
        saved = json.loads(bundle.read(name))
    assert saved["geometry"]["camera_height_mm"] == 210
    assert saved["mode"] == {"width": 320, "height": 200, "fps": 450.0}


def test_page_and_status_are_served(tmp_path):
    client = tester_server.create_app(sessions_root=tmp_path).test_client()
    assert client.get("/").status_code == 200
    response = client.post(
        "/api/tester/status",
        json={
            "tester_id": "20260918-name",
            "preset": "binned-320-450",
        },
    )
    assert response.status_code == 200
    assert response.get_json()["available"] is True


def test_invalid_status_request_returns_400(tmp_path):
    client = tester_server.create_app(sessions_root=tmp_path).test_client()
    response = client.post("/api/tester/status", json={"tester_id": "../x"})
    assert response.status_code == 400


def test_run_rejects_a_second_concurrent_action(tmp_path):
    class BusyManager(tester_server.TesterJobManager):
        def status(self):
            return {"state": "running", "action": "paired", "message": "busy", "output": []}

        def start(self, action, commands, log_path):
            raise RuntimeError("another action is already running")

    client = tester_server.create_app(sessions_root=tmp_path, manager=BusyManager()).test_client()
    response = client.post(
        "/api/tester/run",
        json={
            "tester_id": "20260918-name",
            "preset": "binned-320-450",
            "action": "preflight",
        },
    )
    assert response.status_code == 409

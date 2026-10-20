"""The kiosk's study mode: the tester page sets exposure and gain live and reads raw frames."""

import io
import json
import sys
from types import SimpleNamespace

import numpy as np
import pytest

import openflight.server as server_module
from openflight import tester_hooks


class FakeRuntime:
    def __init__(self, auto_exposure=False):
        self.settings = SimpleNamespace(auto_exposure=auto_exposure, fps=120.0)
        self.applied = []
        self.purposes = []

    def update_image_controls(self, *, exposure_us, gain, purpose="capture"):
        self.applied.append((exposure_us, gain))
        self.purposes.append(purpose)
        return {"exposure_us": exposure_us, "gain": gain}

    def recent_frames(self, count, *, timeout_s=2.0):
        del timeout_s
        return [
            SimpleNamespace(
                image=np.full((8, 10), 40 + i, np.uint8),
                exposure_us=300,
                analogue_gain=4.0,
                sensor_timestamp_ns=1000 + i,
            )
            for i in range(count)
        ]


@pytest.fixture(name="client")
def fixture_client(monkeypatch):
    runtime = FakeRuntime()
    monkeypatch.setattr(server_module, "camera_capture_runtime", runtime)
    monkeypatch.setattr(tester_hooks, "study_mode_enabled", True, raising=False)
    return server_module.app.test_client(), runtime


def test_the_page_sets_exposure_and_gain_live(client):
    test_client, runtime = client
    response = test_client.post(
        "/api/camera/study/controls", json={"exposure_us": 150, "gain": 8.0}
    )
    assert response.status_code == 200
    assert response.get_json() == {"exposure_us": 150, "gain": 8.0}
    assert runtime.applied == [(150, 8.0)]
    assert runtime.purposes == ["capture"]


def test_a_still_photo_request_is_forwarded_with_its_purpose(client):
    test_client, runtime = client

    response = test_client.post(
        "/api/camera/study/controls",
        json={"exposure_us": 4000, "gain": 2.0, "purpose": "still_photo"},
    )

    assert response.status_code == 200
    assert runtime.purposes == ["still_photo"]


def test_raw_frames_come_back_with_their_controls(client):
    test_client, _runtime = client
    response = test_client.get("/api/camera/study/frames?n=3")
    assert response.status_code == 200
    data = np.load(io.BytesIO(response.data))
    assert data["frames"].shape == (3, 8, 10)
    assert list(data["exposure_us"]) == [300, 300, 300]
    assert list(data["sensor_timestamp_ns"]) == [1000, 1001, 1002]


def test_nothing_exists_without_study_mode(monkeypatch):
    monkeypatch.setattr(server_module, "camera_capture_runtime", FakeRuntime())
    monkeypatch.setattr(tester_hooks, "study_mode_enabled", False, raising=False)
    test_client = server_module.app.test_client()
    response = test_client.post(
        "/api/camera/study/controls", json={"exposure_us": 150, "gain": 8.0}
    )
    assert response.status_code == 404
    assert test_client.get("/api/camera/study/frames").status_code == 404


def test_automatic_exposure_refuses_the_page(monkeypatch):
    monkeypatch.setattr(server_module, "camera_capture_runtime", FakeRuntime(auto_exposure=True))
    monkeypatch.setattr(tester_hooks, "study_mode_enabled", True, raising=False)
    response = server_module.app.test_client().post(
        "/api/camera/study/controls", json={"exposure_us": 150, "gain": 8.0}
    )
    assert response.status_code == 409


def test_a_bad_body_is_refused(client):
    test_client, runtime = client
    assert test_client.post("/api/camera/study/controls", json={"gain": 8.0}).status_code == 400
    assert runtime.applied == []


def test_recent_frames_are_distinct():
    from openflight.camera.capture_runtime import (  # pylint: disable=import-outside-toplevel
        CameraCaptureRuntime,
    )

    class Ring:
        def __init__(self):
            self._stamps = iter([1, 1, 2, 2, 3, 4])

        @property
        def latest_frame(self):
            return SimpleNamespace(sensor_timestamp_ns=next(self._stamps, 4))

    runtime = CameraCaptureRuntime.__new__(CameraCaptureRuntime)
    runtime._ring = Ring()  # pylint: disable=protected-access
    frames = runtime.recent_frames(3, timeout_s=1.0)
    assert [f.sensor_timestamp_ns for f in frames] == [1, 2, 3]


def test_study_mode_capture_is_diagnostic_under_an_armed_profile(monkeypatch, tmp_path):
    path = tmp_path / "armed.json"
    path.write_text(
        json.dumps(
            {
                "schema": "openflight.camera.armed_exposure_profile.v1",
                "qualified": True,
                "exposure_ceiling_us": 400,
                "gain_ceiling": 12.0,
            }
        ),
        encoding="utf-8",
    )
    received = {}
    monkeypatch.setattr(tester_hooks, "study_mode_enabled", tester_hooks.study_mode_enabled)

    class StopAfterCameraInit(Exception):
        pass

    def fake_init_camera_capture(**kwargs):
        received.update(kwargs)
        raise StopAfterCameraInit

    monkeypatch.setattr(server_module, "init_camera_capture", fake_init_camera_capture)
    monkeypatch.setattr(server_module, "init_session_logger", lambda **kwargs: None)
    monkeypatch.setattr(server_module, "profile_store", None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "openflight-server",
            "--camera-capture",
            "--rig-geometry",
            "config/enclosure_v3_rig_geometry.json",
            "--study-mode",
            "--no-logging",
            "--profiles-path",
            str(tmp_path / "profiles.json"),
            "--camera-capture-exposure-us",
            "1250",
            "--camera-armed-profile",
            str(path),
        ],
    )

    with pytest.raises(StopAfterCameraInit):
        server_module.main()

    assert received["diagnostic_capture"] is True
    assert received["armed_profile"]["exposure_ceiling_us"] == 400

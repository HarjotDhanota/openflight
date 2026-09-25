"""Capture-time camera mode provenance tests."""

import sys
from dataclasses import replace
from types import SimpleNamespace

import numpy as np

from openflight.camera.capture_runtime import (
    CameraCaptureRuntime,
    CameraCaptureSettings,
    resolved_camera_config,
)
from openflight.camera.triggered_buffer import CameraFrame, TriggeredCapture


class FakeRequest:
    def __init__(self, arrays, metadata):
        self.arrays = arrays
        self.metadata = metadata

    def get_metadata(self):
        return self.metadata

    def make_array(self, stream):
        return self.arrays[stream]


def snapshot(runtime, *, stream="raw", rotate_180=False, mirror_horizontal=False):
    runtime.settings = replace(
        runtime.settings,
        stream=stream,
        rotate_180=rotate_180,
        mirror_horizontal=mirror_horizontal,
    )
    runtime._resolved_config = {"raw": {"size": [3, 2], "format": "R8"}}
    runtime._camera = type("Camera", (), {"camera_properties": {"Model": "ov9281"}})()
    runtime._startup_capture_mode = runtime._capture_mode_startup_snapshot()


def raw_request(value, *, crop=None, duration=None):
    raw = np.zeros((2, 6), dtype=np.uint8)
    raw[:, 1::2] = value
    metadata = {"SensorTimestamp": value, "ExposureTime": 100, "AnalogueGain": 2.0}
    if crop is not None:
        metadata["ScalerCrop"] = crop
    if duration is not None:
        metadata["FrameDuration"] = duration
    return FakeRequest({"raw": raw}, metadata)


def test_callbacks_freeze_per_frame_metadata_and_settings_before_save(tmp_path):
    runtime = CameraCaptureRuntime(
        output_dir=tmp_path,
        settings=CameraCaptureSettings(width=3, height=2, fps=1000, pre_ms=1, post_ms=1),
        vertical_offset_path=tmp_path / "missing",
    )
    snapshot(runtime)
    runtime._on_frame(raw_request(1, crop=(1, 2, 3, 4), duration=1000))
    assert runtime._ring.trigger(50)
    runtime._on_frame(raw_request(2))
    capture = runtime._ready.get_nowait()
    runtime.settings = replace(runtime.settings, width=99, stream="main-y", rotate_180=True)

    saved = runtime._save_capture(1, 1.0, capture)
    mode = saved.metadata["capture_mode"]

    assert mode["context_status"] == "uniform"
    assert mode["frames"]["scaler_crop"] == [[1, 2, 3, 4], None]
    assert mode["frames"]["frame_duration_us"] == [1000, None]
    assert mode["frames"]["saved_width"] == [3, 3]
    assert saved.metadata["settings"]["width"] == 3
    assert saved.metadata["settings"]["stream"] == "raw"


def test_malformed_optional_request_metadata_does_not_drop_frame(tmp_path):
    runtime = CameraCaptureRuntime(
        output_dir=tmp_path,
        settings=CameraCaptureSettings(width=3, height=2),
        vertical_offset_path=tmp_path / "missing",
    )
    snapshot(runtime)

    runtime._on_frame(raw_request(1, crop=(1, 2, -3, 4), duration=float("inf")))
    frame = runtime._ring.latest_frame

    assert frame is not None
    assert frame.scaler_crop is None
    assert frame.frame_duration_us is None


def test_rectangle_resolved_control_is_json_safe_and_saveable(tmp_path):
    rectangle = type("Rectangle", (), {"x": 1, "y": 2, "width": 3, "height": 4})()
    camera = type(
        "Camera",
        (),
        {
            "camera_configuration": lambda _self: {
                "controls": {"ScalerCrop": rectangle},
            }
        },
    )()
    runtime = CameraCaptureRuntime(output_dir=tmp_path)
    runtime.settings = replace(runtime.settings, width=999)
    runtime._resolved_config = resolved_camera_config(camera)
    runtime._camera = type("Properties", (), {"camera_properties": {}})()
    runtime._startup_capture_mode = runtime._capture_mode_startup_snapshot()
    frame = CameraFrame(
        np.zeros((2, 3), dtype=np.uint8),
        1,
        1,
        1,
        1.0,
        capture_mode=runtime._startup_capture_mode,
    )

    saved = runtime._save_capture(1, 1.0, TriggeredCapture((frame,), 1, 1))

    assert saved.metadata["resolved"]["controls"]["ScalerCrop"] == [1, 2, 3, 4]


def test_driver_readback_is_nullable_and_never_defaults_to_zero(tmp_path):
    missing = CameraCaptureRuntime(output_dir=tmp_path, vertical_offset_path=tmp_path / "missing")
    assert missing._driver_offset_readback() == {
        "value_px": None,
        "scope": "startup",
        "status": "unavailable",
    }

    malformed_path = tmp_path / "strip_y_offset"
    malformed_path.write_text("unknown\n", encoding="ascii")
    malformed = CameraCaptureRuntime(output_dir=tmp_path, vertical_offset_path=malformed_path)
    assert malformed._driver_offset_readback() == {
        "value_px": None,
        "scope": "startup",
        "status": "malformed",
    }


def test_callback_records_main_stream_and_applied_orientation(tmp_path):
    runtime = CameraCaptureRuntime(
        output_dir=tmp_path,
        settings=CameraCaptureSettings(width=3, height=2),
        vertical_offset_path=tmp_path / "missing",
    )
    snapshot(runtime, stream="main-y", rotate_180=True, mirror_horizontal=True)
    main = np.array([[1, 2, 3], [4, 5, 6], [0, 0, 0]], dtype=np.uint8)

    runtime._on_frame(
        FakeRequest(
            {"main": main},
            {"SensorTimestamp": 1, "ExposureTime": 100, "AnalogueGain": 2.0},
        )
    )
    frame = runtime._ring.latest_frame

    assert frame.capture_mode["settings"]["stream"] == "main-y"
    assert frame.capture_mode["settings"]["rotate_180"] is True
    assert frame.capture_mode["settings"]["mirror_horizontal"] is True
    assert frame.image.tolist() == [[4, 5, 6], [1, 2, 3]]


def test_mixed_restart_contexts_are_explicit_per_frame(tmp_path):
    first = {"settings": {"stream": "raw", "width": 3, "height": 2}}
    second = {"settings": {"stream": "main-y", "width": 3, "height": 2}}
    frames = (
        CameraFrame(np.zeros((2, 3)), 1, 1, 1, 1.0, capture_mode=first),
        CameraFrame(np.zeros((2, 3)), 2, 2, 1, 1.0, capture_mode=second),
    )
    runtime = CameraCaptureRuntime(output_dir=tmp_path)

    saved = runtime._save_capture(1, 1.0, TriggeredCapture(frames, 1, 1))
    mode = saved.metadata["capture_mode"]

    assert mode["context_status"] == "mixed_or_incomplete"
    assert mode["frames"]["context_index"] == [0, 1]
    assert len(mode["contexts"]) == 2
    assert saved.metadata["resolved"] is None
    assert saved.metadata["settings"] == {}
    assert saved.metadata["settings_scope"] == "unavailable"


def test_start_snapshots_after_controls_and_refreshes_on_restart(tmp_path, monkeypatch):
    events = []

    class FakeCamera:
        generation = 0

        def __init__(self):
            type(self).generation += 1
            self.value = type(self).generation
            self.camera_properties = {"Model": f"camera-{self.value}"}

        @staticmethod
        def create_video_configuration(**_kwargs):
            return {}

        def configure(self, _config):
            events.append((self.value, "configure"))

        def set_controls(self, _controls):
            events.append((self.value, "set_controls"))

        def camera_configuration(self):
            events.append((self.value, "read_config"))
            return {"controls": {"ScalerCrop": (self.value, 0, 10, 10)}}

        @staticmethod
        def start():
            return None

        @staticmethod
        def stop():
            return None

        @staticmethod
        def close():
            return None

    monkeypatch.setitem(sys.modules, "picamera2", SimpleNamespace(Picamera2=FakeCamera))
    runtime = CameraCaptureRuntime(
        output_dir=tmp_path,
        settings=CameraCaptureSettings(auto_exposure=False, scaler_crop=(1, 0, 10, 10)),
        use_gpio_trigger=False,
        vertical_offset_path=tmp_path / "missing",
    )
    monkeypatch.setattr(runtime, "_wait_for_prebuffer", lambda: None)

    runtime.start()
    first = runtime._startup_capture_mode
    runtime.stop()
    runtime._reset_capture_state()
    runtime.settings = replace(runtime.settings, scaler_crop=(2, 0, 10, 10))
    runtime.start()
    second = runtime._startup_capture_mode
    runtime.stop()

    assert events[:3] == [(1, "configure"), (1, "set_controls"), (1, "read_config")]
    assert first["camera_properties"]["Model"] == "camera-1"
    assert second["camera_properties"]["Model"] == "camera-2"
    assert first["settings"]["scaler_crop"] == [1, 0, 10, 10]
    assert second["settings"]["scaler_crop"] == [2, 0, 10, 10]

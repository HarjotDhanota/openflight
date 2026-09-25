"""Exports preserve runtime-produced camera mode sidecars exactly."""

from dataclasses import replace

import numpy as np
from test_export_session import _capture_tree, export_session

from openflight.camera.capture_runtime import CameraCaptureRuntime
from openflight.camera.triggered_buffer import CameraFrame, TriggeredCapture


def write_runtime_sidecar(camera_dir, *, stream, crop):
    runtime = CameraCaptureRuntime(output_dir=camera_dir.parent)
    runtime.settings = replace(runtime.settings, stream=stream, scaler_crop=crop)
    runtime._resolved_config = {"raw": {"size": [3, 2], "format": "R8"}}
    runtime._camera = type("Camera", (), {"camera_properties": {"Model": "ov9281"}})()
    runtime._startup_capture_mode = runtime._capture_mode_startup_snapshot()
    frame = CameraFrame(
        np.zeros((2, 3), dtype=np.uint8),
        1,
        1,
        100,
        2.0,
        scaler_crop=crop,
        frame_duration_us=2000,
        capture_mode=runtime._startup_capture_mode,
    )
    existing = camera_dir
    existing.rename(camera_dir.with_name(f"{camera_dir.name}_fixture"))
    saved = runtime._save_capture(1, 1.0, TriggeredCapture((frame,), 1, 1))
    saved.path.rename(camera_dir)
    return (camera_dir / "metadata.json").read_bytes()


def test_complete_exports_preserve_distinct_capture_contexts_byte_exact(tmp_path):
    source = _capture_tree(tmp_path, [{"n": 1}, {"n": 2}])
    first_dir = next(source.rglob("camera_20260922_001"))
    second_dir = next(source.rglob("camera_20260922_002"))
    first = write_runtime_sidecar(first_dir, stream="raw", crop=(1, 2, 3, 4))
    second = write_runtime_sidecar(second_dir, stream="main-y", crop=(5, 6, 7, 8))

    report = export_session.export_session(source, tmp_path / "out")

    assert (report.out / "shots/shot_001_7-iron/camera_metadata.json").read_bytes() == first
    assert (report.out / "shots/shot_002_7-iron/camera_metadata.json").read_bytes() == second
    assert first != second


def test_partial_export_preserves_capture_context_byte_exact(tmp_path):
    source = _capture_tree(tmp_path, [{"n": 1, "radar": False}])
    camera_dir = next(source.rglob("camera_20260922_001"))
    original = write_runtime_sidecar(camera_dir, stream="raw", crop=(1, 2, 3, 4))

    report = export_session.export_session(source, tmp_path / "out")
    partial = report.out / "partial_captures/shot_001_7-iron/camera_metadata.json"

    assert partial.read_bytes() == original

"""Every missing camera clip is recorded with why it is missing."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from openflight import server as server_module
from openflight.camera import capture_runtime
from openflight.camera.capture_runtime import CameraCaptureRuntime, SavedCameraCapture
from openflight.camera.triggered_buffer import TriggeredFrameBuffer
from openflight.clubs import ClubType
from openflight.launch_monitor import Shot


def _runtime(tmp_path, seen=None):
    runtime = CameraCaptureRuntime(
        output_dir=tmp_path, on_trigger_rejected=seen.append if seen is not None else None
    )
    runtime._running = True
    runtime._ring = SimpleNamespace(trigger=lambda _timestamp: True)
    return runtime


def test_each_refused_trigger_is_reported_with_its_own_reason(tmp_path):
    seen = []
    runtime = _runtime(tmp_path, seen)
    for _ in range(capture_runtime.MAX_PENDING_SAVES):
        runtime._ready.put(object())
    assert runtime.notify_trigger(10.0) is False

    runtime._ready = capture_runtime.queue.Queue()
    ring = TriggeredFrameBuffer(pre_trigger_frames=2, post_trigger_frames=1)
    runtime._ring = ring
    assert runtime.notify_trigger(20.0) is False

    runtime._running = False
    assert runtime.notify_trigger(30.0) is False

    assert [(item["reason"], item["trigger_timestamp"]) for item in seen] == [
        ("save_backlog_full", 10.0),
        ("ring_busy", 20.0),
        ("camera_stopped", 30.0),
    ]
    assert seen[0]["pending_saves"] == capture_runtime.MAX_PENDING_SAVES
    assert seen[1]["detail"] == "prebuffer_filling"


def test_a_shot_claims_the_refused_trigger_nearest_its_impact_once(tmp_path):
    runtime = _runtime(tmp_path)
    for _ in range(capture_runtime.MAX_PENDING_SAVES):
        runtime._ready.put(object())
    runtime.notify_trigger(10.0)
    runtime.notify_trigger(50.0)
    assert runtime.trigger_rejection_for_shot(10.2)["reason"] == "save_backlog_full"
    assert runtime.trigger_rejection_for_shot(10.2) is None
    assert runtime.trigger_rejection_for_shot(30.0) is None
    assert runtime.trigger_rejection_for_shot(None) is None


def test_equal_distance_triggers_are_not_consumed_as_a_match(tmp_path):
    runtime = CameraCaptureRuntime(output_dir=tmp_path)
    runtime._captures = [
        SavedCameraCapture(i, stamp, stamp + 0.1, tmp_path, {"trigger": stamp})
        for i, stamp in enumerate((100.0, 100.5))
    ]

    assert runtime.capture_for_shot(100.25, timeout_s=0.0) is None
    assert runtime.capture_for_shot(100.5, timeout_s=0.0).trigger_timestamp == 100.5


def test_capture_matching_keeps_future_clips_and_timestamp_free_fifo(tmp_path):
    runtime = CameraCaptureRuntime(output_dir=tmp_path)
    runtime._captures = [
        SavedCameraCapture(i, stamp, stamp + 0.1, tmp_path, {})
        for i, stamp in enumerate((99.0, 101.0, 102.0))
    ]

    assert runtime.capture_for_shot(100.0, timeout_s=0.0) is None
    assert runtime.capture_for_shot(None, timeout_s=0.0).trigger_timestamp == 101.0
    assert runtime.capture_for_shot(None, timeout_s=0.0).trigger_timestamp == 102.0
    assert runtime.capture_for_shot(None, timeout_s=0.0) is None


class _SessionLog:
    def __init__(self):
        self.captures = []
        self.stats = {}

    def log_camera_capture(self, **entry):
        self.captures.append(entry)


@pytest.mark.parametrize(
    ("capture", "rejection", "error"),
    [
        (
            None,
            {"reason": "save_backlog_full", "trigger_timestamp": 100.0},
            "camera_trigger_rejected:save_backlog_full",
        ),
        (
            None,
            {"reason": "ring_busy", "trigger_timestamp": 100.0},
            "camera_trigger_rejected:ring_busy",
        ),
        (
            None,
            {"reason": "camera_stopped", "trigger_timestamp": 100.0},
            "camera_trigger_rejected:camera_stopped",
        ),
        (None, None, "no_matching_camera_capture"),
        (
            SimpleNamespace(
                trigger_timestamp=100.0,
                path=None,
                metadata={},
                error="disk full",
                valid=False,
                sequence=1,
            ),
            None,
            "camera_save_failed: disk full",
        ),
    ],
)
def test_the_session_log_names_why_a_shot_has_no_clip(monkeypatch, capture, rejection, error):
    session_log = _SessionLog()

    class Runtime:
        camera_analysis_eligible = False

        @staticmethod
        def capture_for_shot(_impact, timeout_s):
            del timeout_s
            return capture

        @staticmethod
        def trigger_rejection_for_shot(_impact):
            return rejection

    for name in (
        "_snapshot_inclinometer_for_shot",
        "_attach_camera_replay",
        "_fuse_camera_measurements",
    ):
        monkeypatch.setattr(server_module, name, lambda *_args: None)
    monkeypatch.setattr(server_module, "_process_iwr6843_angle", lambda _shot: None)
    monkeypatch.setattr(server_module, "kld7_vertical", None)
    monkeypatch.setattr(server_module, "kld7_horizontal", None)
    monkeypatch.setattr(server_module, "camera_capture_runtime", Runtime())
    monkeypatch.setattr(server_module, "get_session_logger", lambda: session_log)
    shot = Shot(
        ball_speed_mph=100.0,
        timestamp=datetime(2026, 9, 25, 12, 0, 0),
        impact_timestamp=100.0,
        club=ClubType.IRON_7,
    )
    shot.shot_number = 4
    server_module._enrich_shot_from_optional_hardware(shot)
    assert session_log.captures[-1]["capture_error"] == error
    if rejection:
        assert session_log.captures[-1]["metadata"] == {"trigger_rejection": rejection}

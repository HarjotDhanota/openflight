"""Synthetic event clocks exercise the real camera save/replay boundary."""

import json
from types import SimpleNamespace

import numpy as np
import pytest

from openflight.camera import capture_runtime
from openflight.camera.capture_runtime import CameraCaptureRuntime
from openflight.camera.head_track import head_track_context
from openflight.camera.triggered_buffer import TriggeredCapture, exposure_trigger_index
from openflight.capture_trigger import CaptureRequest, CaptureTriggerRouter
from tests.test_camera_trigger_timing import _frames


@pytest.mark.parametrize("source", ["sound_gate", "ops_speed_trigger"])
def test_routed_camera_request_retains_source_clock_bounds_and_shared_identity(
    tmp_path, monkeypatch, source
):
    ticks = iter([30_000_000, 30_000_100])
    monkeypatch.setattr(capture_runtime.time, "monotonic_ns", lambda: next(ticks))
    monkeypatch.setattr(capture_runtime, "_boottime_ns", lambda: 30_000_750)
    seen = {}
    runtime = CameraCaptureRuntime(output_dir=tmp_path)
    runtime._running = True
    runtime._ring = SimpleNamespace(
        trigger=lambda stamp, **kwargs: seen.update(stamp=stamp, **kwargs) or True
    )
    iwr_requests = []
    router = CaptureTriggerRouter(
        source,
        "boot-a",
        {
            "iwr": lambda event: iwr_requests.append(event) or True,
            "camera": runtime.notify_capture_request,
        },
    )
    arm_id = router.arm(1_000_000)
    event = CaptureRequest(
        "event-a", source, "boot-a", arm_id, (8_000_000, 8_100_000), 9_000_000, 10.0
    )
    result = router.dispatch(event)
    assert result["consumers"]["camera"]["status"] == "accepted"
    assert iwr_requests == [event]
    assert seen == {"stamp": 30_000_000, "sensor_timestamp_ns": 8_100_750}
    clocks = runtime._trigger_clocks.get_nowait()
    assert clocks["capture_request"] == event.to_dict()
    assert clocks["capture_event_boottime_interval_ns"] == [8_000_650, 8_100_750]
    assert clocks["capture_to_camera_callback_ns"] == [21_900_000, 22_000_000]
    assert clocks["trigger_source"] == source
    if source == "ops_speed_trigger":
        assert (
            not {"gate_event", "gate_boottime_interval_ns", "gate_to_camera_callback_ns"}
            & clocks.keys()
        )
    else:
        assert clocks["gate_boottime_interval_ns"] == [8_000_650, 8_100_750]
    monkeypatch.setattr(capture_runtime.time, "monotonic_ns", lambda: 40_000_000)
    capture = TriggeredCapture(_frames(4, start_ns=0), 2, 30_000_000, 8_100_750)
    saved = runtime._save_capture(1, 10.0, capture, trigger_clocks=clocks)
    metadata = json.loads((saved.path / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["trigger_clocks"]["capture_request"]["event_id"] == event.event_id
    assert metadata["trigger_clocks"]["capture_request"]["arm_id"] == arm_id
    with np.load(saved.path / "frames.npz", allow_pickle=False) as archive:
        assert archive["trigger_source"].item() == source
        assert archive["pre_trigger_count_by_exposure"].item() == 4
        assert archive["pre_trigger_count_by_capture_event"].item() == 1
        assert exposure_trigger_index(archive, 4) == 0
        assert archive["capture_event_boottime_interval_ns"].tolist() == [8_000_650, 8_100_750]
        context = head_track_context(archive)
        assert context.trigger_source == source
        if source == "ops_speed_trigger":
            assert context.contact_window_ns is None
            assert context.gate_interval_ns is None
            assert context.legacy_sound_model is False
    assert router.dispatch(event)["reason"] == "capture_already_requested"
    assert len(iwr_requests) == 1


@pytest.mark.parametrize(
    "source,interval",
    [
        ("ops_capture_end", (100, 200)),
        ("ops_speed_trigger", None),
        ("sound_gate", None),
    ],
)
def test_invalid_capture_notification_does_not_freeze_the_ring(tmp_path, source, interval):
    runtime = CameraCaptureRuntime(output_dir=tmp_path)
    runtime._running = True
    runtime._ring = SimpleNamespace(trigger=lambda *_a, **_k: pytest.fail("must not freeze"))
    event = CaptureRequest("event-a", source, "boot-a", "arm-a", interval, 300, 10.0)
    with pytest.raises(ValueError):
        runtime.notify_capture_request(event)
    assert runtime._trigger_clocks.empty()


@pytest.mark.parametrize(
    "count", [np.array([1]), np.array(1.5), np.array(True), np.array(-1), np.array(5)]
)
def test_invalid_event_exposure_count_cannot_fall_back_to_callback_count(count):
    archive = {
        "pre_trigger_count_by_capture_event": count,
        "pre_trigger_count_by_exposure": np.int32(3),
    }
    assert exposure_trigger_index(archive, 4) is None


def test_future_receipt_clock_cannot_freeze_camera(tmp_path, monkeypatch):
    monkeypatch.setattr(capture_runtime.time, "monotonic_ns", lambda: 100)
    monkeypatch.setattr(capture_runtime, "_boottime_ns", lambda: 100)
    runtime = CameraCaptureRuntime(output_dir=tmp_path)
    runtime._running = True
    runtime._ring = SimpleNamespace(trigger=lambda *_a, **_k: pytest.fail("must not freeze"))
    event = CaptureRequest("event-a", "ops_speed_trigger", "boot-a", "arm-a", (10, 20), 200, 10.0)
    with pytest.raises(ValueError, match="receipt"):
        runtime.notify_capture_request(event)

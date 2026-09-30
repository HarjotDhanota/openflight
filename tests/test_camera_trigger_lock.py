"""The trigger holds the frame lock only to freeze the ring (P6-7).

In Outdoors-test-5 ``notify_trigger`` ran the evidence provider, its deepcopy,
``auto_exposure_status()`` and a second deepcopy under the lock ``_on_frame``
needs, so frame delivery stalled 14-31 ms at every trigger.
"""

from __future__ import annotations

import threading
from dataclasses import replace

import numpy as np

from openflight.camera.capture_runtime import CameraCaptureRuntime, CameraCaptureSettings


class FakeRequest:
    def __init__(self, value):
        raw = np.zeros((2, 6), dtype=np.uint8)
        raw[:, 1::2] = value % 256
        self.raw = raw
        self.metadata = {"SensorTimestamp": value, "ExposureTime": 100, "AnalogueGain": 2.0}

    def get_metadata(self):
        return self.metadata

    def make_array(self, _stream):
        return self.raw


def _runtime(tmp_path, provider, **settings):
    runtime = CameraCaptureRuntime(
        output_dir=tmp_path,
        settings=CameraCaptureSettings(
            width=3, height=2, fps=1000.0, pre_ms=2.0, post_ms=2.0, **settings
        ),
        vertical_offset_path=tmp_path / "missing",
        trigger_evidence_provider=provider,
    )
    runtime._running = True
    return runtime


def _deliver(runtime, first, count):
    for value in range(first, first + count):
        runtime._on_frame(FakeRequest(value))


def _run(target):
    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread


def test_frames_keep_arriving_while_the_evidence_is_gathered(tmp_path):
    entered, release = threading.Event(), threading.Event()
    result = {}

    def slow_provider(_timestamp):
        entered.set()
        assert release.wait(2.0)
        return {"ready": True}

    runtime = _runtime(tmp_path, slow_provider)
    _deliver(runtime, 1, 2)
    trigger = _run(lambda: result.setdefault("accepted", runtime.notify_trigger(10.0)))
    assert entered.wait(1.0)

    delivery = _run(lambda: _deliver(runtime, 3, 2))
    delivery.join(0.5)

    assert not delivery.is_alive(), "a frame waited for the trigger's evidence"
    capture = runtime._ready.get_nowait()
    assert [frame.sensor_timestamp_ns for frame in capture.frames] == [1, 2, 3, 4]
    assert capture.pre_trigger_count == 2
    release.set()
    trigger.join(1.0)
    assert result["accepted"] is True
    assert runtime._trigger_evidence.get_nowait() == {"ready": True}


def test_a_slow_trigger_keeps_its_records_ahead_of_the_next_one(tmp_path):
    """The save loop pairs the n-th capture with the n-th records, so order must hold."""
    entered, release = threading.Event(), threading.Event()
    calls = []

    def provider(_timestamp):
        calls.append(len(calls) + 1)
        if calls[-1] == 1:
            entered.set()
            assert release.wait(2.0)
        return {"trigger": calls[-1]}

    runtime = _runtime(tmp_path, provider)
    _deliver(runtime, 1, 2)
    first = _run(lambda: runtime.notify_trigger(10.0))
    assert entered.wait(1.0)
    _deliver(runtime, 3, 4)  # completes the first clip and refills the pre-trigger ring
    second = _run(lambda: runtime.notify_trigger(11.0))
    second.join(0.2)
    release.set()
    first.join(1.0)
    second.join(1.0)

    assert [runtime._trigger_epochs.get_nowait() for _ in range(2)] == [10.0, 11.0]
    assert [runtime._trigger_evidence.get_nowait()["trigger"] for _ in range(2)] == [1, 2]
    clocks = [runtime._trigger_clocks.get_nowait()["host_monotonic_ns"] for _ in range(2)]
    assert clocks[0] <= clocks[1]  # Windows ticks every ~15 ms
    assert runtime._trigger_auto_exposure.qsize() == 2


def test_a_refused_trigger_gathers_nothing_and_queues_nothing(tmp_path):
    calls = []
    runtime = _runtime(tmp_path, lambda timestamp: calls.append(timestamp) or {"ready": True})
    _deliver(runtime, 1, 1)  # the pre-trigger ring is not full yet

    assert runtime.notify_trigger(10.0) is False

    assert calls == []
    for records in (
        runtime._trigger_epochs,
        runtime._trigger_auto_exposure,
        runtime._trigger_evidence,
        runtime._trigger_clocks,
    ):
        assert records.empty()
    assert runtime._admission_evidence == []


def test_the_recorded_controls_are_those_in_force_when_the_ring_froze(tmp_path):
    """A rung change while the evidence is gathered must not rewrite the trigger's controls."""
    holder = {}

    def provider(_timestamp):
        runtime = holder["runtime"]
        runtime.settings = replace(runtime.settings, exposure_us=30, gain=9.0)
        return {"ready": True}

    runtime = _runtime(tmp_path, provider, auto_exposure=False, exposure_us=300, gain=2.0)
    holder["runtime"] = runtime
    _deliver(runtime, 1, 2)

    assert runtime.notify_trigger(10.0) is True

    recorded = runtime._trigger_auto_exposure.get_nowait()
    assert (recorded["exposure_us"], recorded["gain"]) == (300, 2.0)
    assert recorded["status"] == "manual"
    assert "observation" in recorded


def test_the_admission_copy_is_independent_of_the_queued_evidence(tmp_path):
    runtime = _runtime(tmp_path, lambda _timestamp: {"warnings": [{"id": "lis3dh"}]})
    _deliver(runtime, 1, 2)

    assert runtime.notify_trigger(10.0) is True
    queued = runtime._trigger_evidence.get_nowait()
    queued["warnings"].clear()

    assert runtime.trigger_evidence_for_shot(10.0) == {"warnings": [{"id": "lis3dh"}]}

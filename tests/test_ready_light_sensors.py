"""P7-14: each sensor reports its own ready-light state, read without its locks."""

from __future__ import annotations

import json
import threading
import time

import numpy as np

from openflight.camera.capture_runtime import (
    CameraCaptureRuntime,
    CameraCaptureSettings,
    SavedCameraCapture,
)
from openflight.camera.triggered_buffer import CameraFrame, TriggeredFrameBuffer
from openflight.iwr6843.dump import pack_dump
from openflight.iwr6843.monitor import IWR6843CaptureMonitor
from openflight.ops243 import OPS243Radar
from openflight.ready_light import IWR_TYPICAL_DUMP_S

# ---------------------------------------------------------------- OPS243


def _dump() -> bytes:
    lines = [
        '{"sample_time": "964.003"}',
        '{"trigger_time": "964.105"}',
        json.dumps({"I": [2048] * 20}),
        json.dumps({"Q": [2048] * 20}),
    ]
    return "\r\n".join(lines).encode("ascii")


class PhaseSerial:
    """An OPS243 UART that answers after a few idle polls, noting the radar's phase."""

    def __init__(self, data: bytes, idle_polls: int = 3):
        self.is_open = True
        self.radar = None
        self.data = bytearray(data)
        self.idle_polls = idle_polls
        self.seen: list[str | None] = []

    def _note(self):
        phase = getattr(self.radar, "trigger_phase", None)
        name = phase[0] if phase else None
        if not self.seen or self.seen[-1] != name:
            self.seen.append(name)

    @property
    def in_waiting(self):
        self._note()
        if self.idle_polls:
            self.idle_polls -= 1
            return 0
        return len(self.data)

    def read(self, size):
        chunk = bytes(self.data[:size])
        del self.data[:size]
        return chunk

    def write(self, data):
        self._note()
        return len(data)

    def flush(self):
        pass


def _radar(serial_obj) -> OPS243Radar:
    radar = OPS243Radar.__new__(OPS243Radar)
    radar.serial = serial_obj
    radar.last_hardware_trigger_first_byte_timestamp = None
    serial_obj.radar = radar
    return radar


def test_ops_phase_walks_armed_dumping_draining_rearming_rearmed():
    port = PhaseSerial(_dump())
    radar = _radar(port)

    before = time.time()
    assert radar.wait_for_hardware_trigger(timeout=2.0, dump_grace=2.0, on_first_byte=port._note)
    assert radar.trigger_phase[0] == "draining"
    assert radar.trigger_phase[1] >= before
    radar.rearm_rolling_buffer(16)

    assert port.seen == ["armed", "dumping", "rearming"]
    assert radar.trigger_phase[0] == "rearmed"


def test_ops_wait_is_armed_again_after_a_rearm():
    port = PhaseSerial(b"", idle_polls=10**6)
    radar = _radar(port)
    radar.trigger_phase = ("rearmed", time.time())

    assert radar.wait_for_hardware_trigger(timeout=0.1, dump_grace=0.1) == ""

    assert "armed" in port.seen


def test_ops_wait_after_an_unfinished_rearm_stays_rearming():
    """A re-arm whose writes timed out left the board unconfirmed: amber, not green."""
    port = PhaseSerial(b"", idle_polls=10**6)
    radar = _radar(port)
    radar.trigger_phase = ("rearming", time.time())

    radar.wait_for_hardware_trigger(timeout=0.1, dump_grace=0.1)

    assert "armed" not in port.seen


def test_ops_cancelled_wait_is_stopped():
    port = PhaseSerial(b"", idle_polls=10**6)
    radar = _radar(port)
    cancel = threading.Event()
    cancel.set()

    radar.wait_for_hardware_trigger(timeout=5.0, cancel_event=cancel)

    assert radar.trigger_phase[0] == "stopped"


# ---------------------------------------------------------------- IWR6843


class _Serial:
    is_open = True


class BlockingIwr:
    port = "/dev/fake-iwr6843"

    def __init__(self, raw: bytes):
        self.raw = raw
        self.ser = _Serial()
        self.read_started = threading.Event()
        self.release = threading.Event()

    def send_config(self, _path):
        pass

    def read_dump(self):
        self.read_started.set()
        self.release.wait(timeout=2.0)
        return self.raw

    def close(self):
        pass

    def stop_sensor(self):
        pass


class FakeButton:
    def __init__(self, pin, pull_up, bounce_time):
        self.when_pressed = None

    def close(self):
        pass


def _iwr_monitor(tmp_path, radar):
    config = tmp_path / "radar.cfg"
    config.write_text("sensorStart\n", encoding="utf-8")
    return IWR6843CaptureMonitor(
        config_path=config, output_dir=tmp_path / "dumps", radar=radar, button_factory=FakeButton
    )


def _wait_for(predicate, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_iwr_snapshot_reports_idle_armed_then_its_dump(tmp_path):
    cube = np.zeros((2, 4, 4, 8), dtype=complex)
    radar = BlockingIwr(pack_dump(cube, n_tx=2, version=3, frame_period_us=6000))
    monitor = _iwr_monitor(tmp_path, radar)

    monitor.start(armed=False)
    unarmed = monitor.ready_snapshot()
    assert unarmed["running"] and unarmed["worker_alive"] and not unarmed["armed"]
    monitor.arm()
    idle = monitor.ready_snapshot()
    assert idle["armed"] and idle["serial_open"] and not idle["dumping"] and not idle["queued"]
    assert idle["typical_dump_s"] == IWR_TYPICAL_DUMP_S

    edge = time.time()
    assert monitor.notify_trigger(edge)
    assert radar.read_started.wait(timeout=1.0)
    dumping = monitor.ready_snapshot()
    assert dumping["dumping"]
    assert dumping["dump_started_at"] >= edge

    radar.release.set()
    assert _wait_for(lambda: not monitor.ready_snapshot()["dumping"])
    done = monitor.ready_snapshot()
    # the next rough time left is this dump's length
    assert done["typical_dump_s"] < 2.5
    monitor.stop()
    assert not monitor.ready_snapshot()["running"]


# ---------------------------------------------------------------- camera


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


def _camera(tmp_path):
    runtime = CameraCaptureRuntime(
        output_dir=tmp_path,
        settings=CameraCaptureSettings(width=3, height=2, fps=1000.0, pre_ms=2.0, post_ms=2.0),
        vertical_offset_path=tmp_path / "missing",
    )
    runtime._running = True
    return runtime


def _deliver(runtime, first, count):
    for value in range(first, first + count):
        runtime._on_frame(FakeRequest(value))


def test_camera_snapshot_walks_ring_tail_and_pending_save(tmp_path):
    runtime = _camera(tmp_path)

    empty = runtime.ready_snapshot()
    assert empty["running"] and empty["latest_frame_age_s"] is None
    assert empty["requested_size"] == [3, 2]
    _deliver(runtime, 1, 2)
    ready = runtime.ready_snapshot()
    assert ready["buffered_frames"] == ready["required_pre_frames"] == 2
    assert ready["latest_frame_age_s"] < 1.0
    assert ready["controls_purpose"] == "capture"
    assert not (ready["collecting_tail"] or ready["awaiting_handoff"] or ready["pending_saves"])

    assert runtime.notify_trigger(time.time())
    tail = runtime.ready_snapshot()
    assert tail["collecting_tail"]
    assert tail["clip_started_at"] is not None

    _deliver(runtime, 3, 2)
    handed = runtime.ready_snapshot()
    assert not handed["collecting_tail"]
    assert handed["pending_saves"] == 1
    assert handed["buffered_frames"] == 0


def test_camera_snapshot_reports_a_save_in_progress_and_learns_its_length(tmp_path, monkeypatch):
    runtime = _camera(tmp_path)
    entered, release = threading.Event(), threading.Event()

    def slow_save(sequence, trigger_epoch, _capture, **_kwargs):
        entered.set()
        assert release.wait(2.0)
        return SavedCameraCapture(
            sequence=sequence,
            trigger_timestamp=trigger_epoch,
            completed_timestamp=time.time(),
            path=tmp_path,
            metadata={},
        )

    monkeypatch.setattr(runtime, "_save_capture", slow_save)
    _deliver(runtime, 1, 2)
    trigger_epoch = time.time()
    assert runtime.notify_trigger(trigger_epoch)
    _deliver(runtime, 3, 2)
    saver = threading.Thread(target=runtime._save_loop, daemon=True)
    saver.start()
    assert entered.wait(1.0)

    assert runtime.ready_snapshot()["saving"]
    time.sleep(0.05)
    release.set()
    assert _wait_for(lambda: not runtime.ready_snapshot()["saving"])
    assert runtime.ready_snapshot()["typical_clip_s"] >= 0.05
    runtime._running = False
    runtime._ready.put(None)
    saver.join(1.0)


def test_camera_snapshot_never_waits_on_the_frame_or_ring_locks(tmp_path):
    runtime = _camera(tmp_path)
    _deliver(runtime, 1, 2)
    result = {}

    with runtime._trigger_exposure_lock, runtime._ring._condition:
        reader = threading.Thread(
            target=lambda: result.setdefault("snapshot", runtime.ready_snapshot()), daemon=True
        )
        reader.start()
        reader.join(0.5)
        assert not reader.is_alive(), "the ready light waited on a camera lock"
    assert result["snapshot"]["buffered_frames"] == 2


def test_ring_state_nowait_matches_its_locked_view():
    ring = TriggeredFrameBuffer(2, 1)

    def frame(value):
        return CameraFrame(
            image=np.zeros((1, 1), dtype=np.uint8),
            sensor_timestamp_ns=value,
            host_timestamp_ns=value,
            exposure_us=100,
            analogue_gain=1.0,
        )

    ring.add_frame(frame(1))
    assert ring.state_nowait() == (False, False, 1)
    ring.add_frame(frame(2))
    assert ring.trigger()
    assert ring.state_nowait() == (True, False, 0)
    ring.add_frame(frame(3))
    assert ring.state_nowait() == (False, True, 0)

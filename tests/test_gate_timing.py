"""One GPIO observation survives delayed consumers and capture persistence."""

import json
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from openflight import gate_timing
from openflight.camera import capture_runtime
from openflight.camera.capture_runtime import CameraCaptureRuntime
from openflight.camera.triggered_buffer import TriggeredCapture
from openflight.gate_timing import GateEvent
from openflight.iwr6843.monitor import IWR6843CaptureMonitor
from tests.test_camera_trigger_timing import _frames
from tests.test_iwr6843_monitor import FakeButton, FakeRadar, _raw_dump


def _reading(**overrides):
    return {
        "mono_before_ns": 1_000_000,
        "mono_after_ns": 1_000_100,
        "age_ns": 200_000,
        "backend": "LGPIOFactory",
        **overrides,
    }


def test_event_keeps_a_bracket_and_cannot_be_changed():
    event = GateEvent.observe(lambda: _reading(), 10.0)
    assert event.kernel_mono_interval_ns == (800_000, 800_100)
    assert event.to_dict()["physical_edge_uncertainty_ns"] is None
    assert event.to_dict()["status"] == "observed_kernel_tick"
    with pytest.raises(FrozenInstanceError):
        event.event_id = "replacement"


@pytest.mark.parametrize(
    "overrides,previous",
    [
        ({"backend": "RPiGPIOFactory"}, None),
        ({"age_ns": None}, None),
        ({"age_ns": -1}, None),
        ({"mono_before_ns": 1_000_101}, None),
        ({"age_ns": 200_000}, 800_000),
    ],
)
def test_unusable_readings_never_become_an_event_time(overrides, previous):
    event = GateEvent.observe(
        lambda: _reading(**overrides), 10.0, previous_callback_mono_ns=previous
    )
    assert event.kernel_mono_interval_ns is None
    assert event.to_dict()["status"] == "unobserved"


def test_raising_reader_leaves_missing_evidence():
    def broken():
        raise RuntimeError("pin unavailable")

    event = GateEvent.observe(broken, 10.0)
    assert event.kernel_mono_interval_ns is None
    assert event.to_dict()["reading_error"] == "RuntimeError"


def test_camera_records_shared_event_beside_its_later_callback(tmp_path, monkeypatch):
    event = GateEvent.observe(lambda: _reading(), 10.0)
    monkeypatch.setattr(capture_runtime.time, "monotonic_ns", lambda: 30_000_000)
    monkeypatch.setattr(capture_runtime, "_boottime_ns", lambda: 30_000_750)
    runtime = CameraCaptureRuntime(output_dir=tmp_path)
    runtime._running = True
    runtime._ring = SimpleNamespace(trigger=lambda _stamp, **_kwargs: True)
    assert runtime.notify_gate_event(event)
    clocks = runtime._trigger_clocks.get_nowait()
    assert clocks["host_monotonic_ns"] == 30_000_000
    assert clocks["gate_event"] == event.to_dict()
    assert clocks["gate_to_camera_callback_ns"] == [29_199_900, 29_200_000]
    capture = TriggeredCapture(
        frames=_frames(4), pre_trigger_count=2, trigger_host_timestamp_ns=30_000_000
    )
    saved = runtime._save_capture(1, 10.0, capture, trigger_clocks=clocks)
    written = json.loads((saved.path / "metadata.json").read_text(encoding="utf-8"))
    assert written["trigger_clocks"]["gate_event"] == event.to_dict()
    assert written["trigger_clocks"]["gate_to_camera_callback_ns"] == [29_199_900, 29_200_000]


def test_camera_only_gpio_uses_the_same_event_reader(tmp_path, monkeypatch):
    runtime = CameraCaptureRuntime(output_dir=tmp_path, button_factory=FakeButton)
    runtime._running = True
    runtime._ring = SimpleNamespace(trigger=lambda _stamp: True)
    monkeypatch.setattr(gate_timing, "read_edge_tick", lambda _button: _reading())
    runtime._start_gpio_trigger()
    runtime._button.when_pressed()
    event = runtime._trigger_clocks.get_nowait()["gate_event"]
    assert event["kernel_mono_interval_ns"] == [800_000, 800_100]
    runtime._button.close()


def test_camera_keeps_callback_time_without_inventing_a_missing_event_time(tmp_path):
    runtime = CameraCaptureRuntime(output_dir=tmp_path)
    runtime._running = True
    runtime._ring = SimpleNamespace(trigger=lambda _stamp: True)
    event = GateEvent.observe(None, 10.0)
    assert runtime.notify_gate_event(event)
    clocks = runtime._trigger_clocks.get_nowait()
    assert clocks["host_monotonic_ns"] > 0
    assert clocks["gate_to_camera_callback_ns"] is None
    assert clocks["gate_event"]["kernel_mono_interval_ns"] is None


def test_a_delayed_observer_does_not_cause_the_gpio_to_be_read_again(tmp_path, monkeypatch):
    heard, readings = [], [_reading()]

    def delay(_event):
        readings.append(_reading(age_ns=30_200_000))

    monitor = IWR6843CaptureMonitor(
        config_path=tmp_path / "unused.cfg",
        output_dir=tmp_path,
        radar=object(),
        gate_event_observers=[delay, heard.append],
    )
    monitor._running = monitor._armed = monitor._capture_active = True
    monkeypatch.setattr(monitor, "edge_tick_reading", lambda: readings.pop(0))
    assert not monitor.notify_trigger(10.0)
    assert heard[0].kernel_mono_interval_ns == (800_000, 800_100)
    assert readings == [_reading(age_ns=30_200_000)]


def test_one_read_is_shared_with_capture_and_camera(tmp_path, monkeypatch):
    config = tmp_path / "radar.cfg"
    config.write_text("sensorStart\n", encoding="utf-8")
    camera = CameraCaptureRuntime(output_dir=tmp_path / "camera")
    camera._running = True
    camera._ring = SimpleNamespace(trigger=lambda _stamp: True)
    monitor = IWR6843CaptureMonitor(
        config_path=config,
        output_dir=tmp_path,
        radar=FakeRadar(_raw_dump()),
        button_factory=FakeButton,
        gate_event_observers=[camera.notify_gate_event],
    )
    reads = []
    monkeypatch.setattr(monitor, "edge_tick_reading", lambda: reads.append(1) or _reading())
    monitor.start()
    try:
        assert monitor.notify_trigger(10.0)
        capture = monitor.capture_for_shot(10.0, timeout_s=1.0)
        camera_event = camera._trigger_clocks.get_nowait()["gate_event"]
        assert capture.gate_event.to_dict() == camera_event
        assert reads == [1]
    finally:
        monitor.stop()


def test_busy_iwr_fans_out_new_events_and_debounce_hides_them(tmp_path, monkeypatch):
    heard, legacy = [], []
    monitor = IWR6843CaptureMonitor(
        config_path=tmp_path / "unused.cfg",
        output_dir=tmp_path,
        radar=object(),
        gate_event_observers=[heard.append],
        trigger_observers=[legacy.append],
    )
    monitor._running = monitor._armed = monitor._capture_active = True
    monkeypatch.setattr(monitor, "edge_tick_reading", lambda: _reading())
    assert not monitor.notify_trigger(10.0)
    assert not monitor.notify_trigger(10.01)
    assert not monitor.notify_trigger(11.0)
    assert legacy == [10.0, 11.0]
    assert [event.observation.observer_wall_s for event in heard] == legacy
    assert heard[0].event_id != heard[1].event_id


def test_failed_event_observer_does_not_hide_edge_from_others(tmp_path):
    heard = []

    def broken(_event):
        raise RuntimeError("consumer failed")

    monitor = IWR6843CaptureMonitor(
        config_path=tmp_path / "unused.cfg",
        output_dir=tmp_path,
        radar=object(),
        gate_event_observers=[broken, heard.append],
    )
    monitor._running = monitor._armed = monitor._capture_active = True
    assert not monitor.notify_trigger(10.0)
    assert len(heard) == 1

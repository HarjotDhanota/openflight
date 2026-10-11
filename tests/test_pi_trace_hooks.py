"""Trace hooks in the non-shared files (no hardware)."""

import json
from datetime import datetime

import pytest
import serial

from openflight import pi_trace
from openflight.iwr6843.monitor import IWR6843CaptureMonitor
from openflight.launch_monitor import Shot
from openflight.ops243 import OPS243Radar
from openflight.rolling_buffer import monitor as monitor_module
from openflight.rolling_buffer.monitor import RollingBufferMonitor
from openflight.rolling_buffer.processor import RollingBufferProcessor
from openflight.rolling_buffer.trigger import SoundTrigger
from openflight.rolling_buffer.types import IQCapture, ProcessedCapture, SpeedTimeline
from openflight.timing import OpsCaptureTimer
from tests.pi_trace_helpers import read_marks
from tests.spin_synth import synth_capture


class _ClockAndQuietSerial:
    """Answers C? with a fractional clock; otherwise silent, so re-arm drains at once."""

    def __init__(self, *, write_timeout_on=None):
        self.is_open = True
        self._pending = b""
        self.writes = []
        self._write_timeout_on = write_timeout_on

    def reset_input_buffer(self):
        self._pending = b""

    def arrive(self, data: bytes) -> None:
        """Bytes the radar sends unprompted (a dump an edge started)."""
        self._pending += data

    def write(self, data):
        if self._write_timeout_on is not None and data.startswith(self._write_timeout_on):
            raise serial.SerialTimeoutException("write timeout")
        self.writes.append(data)
        if data == b"C?":
            self._pending = b'{"Clock":"137.429"}'
        return len(data)

    def flush(self):
        pass

    @property
    def in_waiting(self):
        return len(self._pending)

    def read(self, n):
        chunk, self._pending = self._pending[:n], self._pending[n:]
        return chunk


def _radar(monkeypatch, **serial_kwargs):
    monkeypatch.setattr("openflight.ops243.time.sleep", lambda _s: None)
    radar = OPS243Radar.__new__(OPS243Radar)
    radar.serial = _ClockAndQuietSerial(**serial_kwargs)
    radar.trigger_phase = None
    radar.baud = OPS243Radar.DEFAULT_BAUD
    return radar


def _dump_bytes(i_samples, q_samples) -> bytes:
    lines = [
        '{"sample_time": "964.003"}',
        '{"trigger_time": "964.105"}',
        json.dumps({"I": [int(v) for v in i_samples]}),
        json.dumps({"Q": [int(v) for v in q_samples]}),
    ]
    return ("\n".join(lines) + "\n").encode("ascii")


def _named(marks, name):
    return [m for m in marks if m["name"] == name]


# --- OPS capture timer (timing.py) -----------------------------------------------------


def test_ops_timer_marks_one_attempt_through_first_byte_and_dump(recorder):
    recorder.note_edge(age_ns=100)
    timer = OpsCaptureTimer.start()
    timer.first_marker_callback()
    timer.first_marker_callback()  # a second marker never re-marks the first byte
    timer.response_completed()
    marks = read_marks(recorder)
    ops = [m for m in marks if m["name"].startswith("ops_")]
    assert [m["name"] for m in ops] == ["ops_wait_started", "ops_first_byte", "ops_dump_complete"]
    assert {m["attempt_id"] for m in ops} == {timer.attempt_id}
    assert timer.attempt_id is not None
    assert ops[1]["monotonic_ns"] == timer.first_marker_ns
    assert ops[1]["last_edge_seq"] == 1
    assert ops[2]["monotonic_ns"] == timer.response_completed_ns


def test_ops_timer_first_byte_before_any_edge_has_no_edge_seq(recorder):
    timer = OpsCaptureTimer.start()
    timer.first_marker_callback()
    assert _named(read_marks(recorder), "ops_first_byte")[0]["last_edge_seq"] is None


def test_ops_timer_without_recorder_has_no_attempt():
    pi_trace.set_recorder(None)
    timer = OpsCaptureTimer.start()
    assert timer.attempt_id is None
    timer.first_marker_callback()
    timer.response_completed()
    assert timer.first_marker_ns is not None and timer.response_completed_ns is not None


def test_ops_timer_still_times_and_forwards_when_the_recorder_raises(recorder, monkeypatch):
    def broken(*_a, **_k):
        raise RuntimeError("trace bug")

    monkeypatch.setattr(recorder, "open_ops_attempt", broken)
    monkeypatch.setattr(recorder, "mark", broken)
    forwarded = []
    timer = OpsCaptureTimer.start()
    timer.first_marker_callback(lambda: forwarded.append(True))
    timer.response_completed()
    assert timer.attempt_id is None
    assert forwarded == [True]
    assert timer.first_marker_ns is not None and timer.response_completed_ns is not None


def test_hook_mark_never_raises_into_the_caller(recorder, monkeypatch, caplog):
    def broken(*_a, **_k):
        raise RuntimeError("trace bug")

    monkeypatch.setattr(recorder, "mark", broken)
    pi_trace.mark("anything", "a-00001")  # must not raise
    assert "trace bug" in caplog.text


# --- OPS243 clock sync, re-arm and armed S# (ops243.py) ---------------------------------


def test_clock_sync_and_rearm_are_marked_on_the_current_attempt(recorder, monkeypatch):
    radar = _radar(monkeypatch)
    attempt = recorder.open_ops_attempt()
    summary = radar.read_clock_sync(samples=2)
    radar.rearm_rolling_buffer(16)
    marks = read_marks(recorder)
    names = [(m["name"], m["attempt_id"]) for m in marks]
    assert names[1:] == [
        ("ops_clock_sync_started", attempt),
        ("ops_clock_sync_done", attempt),
        ("ops_rearm_started", attempt),
        ("ops_rearm_done", attempt),
    ]
    done = _named(marks, "ops_clock_sync_done")[0]
    assert done["method"] == summary["clock_sync_method"] == "fractional_clock"
    assert done["usable"] is True
    rearm_started, rearm_done = (
        _named(marks, "ops_rearm_started")[0],
        _named(marks, "ops_rearm_done")[0],
    )
    assert rearm_started["pre_trigger_segments"] == 16
    assert rearm_done["outcome"] == "rearmed"


def test_rearm_write_timeout_is_marked_and_leaves_the_armed_split_unknown(recorder, monkeypatch):
    radar = _radar(monkeypatch, write_timeout_on=b"PA")
    recorder.open_ops_attempt()
    radar.rearm_rolling_buffer(12)
    radar.wait_for_hardware_trigger(timeout=0.0)
    marks = read_marks(recorder)
    assert _named(marks, "ops_rearm_done")[0]["outcome"] == "write_timeout"
    armed = _named(marks, "ops_wait_armed")[0]
    assert armed["pre_trigger_segments"] is None
    assert armed["pre_trigger_segments_reason"] == "rearm_write_timed_out"


def test_rearm_exception_is_marked_and_still_raised(recorder, monkeypatch):
    radar = _radar(monkeypatch)
    radar.serial.is_open = False
    recorder.open_ops_attempt()
    with pytest.raises(ConnectionError):
        radar.rearm_rolling_buffer(16)
    assert _named(read_marks(recorder), "ops_rearm_done")[0]["outcome"] == "exception"


def test_rearm_returns_none_as_before(monkeypatch):
    pi_trace.set_recorder(None)
    radar = _radar(monkeypatch)
    assert radar.rearm_rolling_buffer(16) is None
    assert b"S#16\r" in radar.serial.writes


def test_wait_marks_the_pre_trigger_split_the_last_rearm_wrote(recorder, monkeypatch):
    """R-F9: the summary's segment allowance needs the S# in effect for this dump."""
    radar = _radar(monkeypatch)
    radar.rearm_rolling_buffer(40)  # clamped to 32, as the board is told
    attempt = recorder.open_ops_attempt()
    radar.wait_for_hardware_trigger(timeout=0.0)
    armed = _named(read_marks(recorder), "ops_wait_armed")[0]
    assert armed["attempt_id"] == attempt
    assert armed["pre_trigger_segments"] == 32
    assert armed["pre_trigger_segments_reason"] is None
    assert armed["arm_confirmed"] is True


def test_wait_before_any_split_was_written_reports_it_unknown(recorder, monkeypatch):
    radar = _radar(monkeypatch)
    recorder.open_ops_attempt()
    radar.wait_for_hardware_trigger(timeout=0.0)
    armed = _named(read_marks(recorder), "ops_wait_armed")[0]
    assert armed["pre_trigger_segments"] is None
    assert armed["pre_trigger_segments_reason"] == "not_written_this_session"


def test_entering_rolling_buffer_mode_records_the_split_it_wrote(recorder, monkeypatch):
    radar = _radar(monkeypatch)
    radar.enter_rolling_buffer_mode(pre_trigger_segments=6)
    recorder.open_ops_attempt()
    radar.wait_for_hardware_trigger(timeout=0.0)
    assert _named(read_marks(recorder), "ops_wait_armed")[0]["pre_trigger_segments"] == 6


def test_one_sound_trigger_capture_is_one_attempt_in_serial_order(recorder, monkeypatch):
    """Through the unchanged SoundTrigger: wait, dump, clock sync, then re-arm last."""
    i_samples, q_samples = synth_capture(rpm=3000, ball_speed_mph=80.0, amplitude=400.0)
    radar = _radar(monkeypatch)
    radar.rearm_rolling_buffer(12)  # the previous attempt's re-arm (no attempt yet)
    radar.serial.arrive(_dump_bytes(i_samples, q_samples))
    capture = SoundTrigger(pre_trigger_segments=12).wait_for_trigger(
        radar, RollingBufferProcessor(), timeout=1.0
    )
    assert capture is not None
    marks = [m for m in read_marks(recorder) if m["name"].startswith("ops_")]
    attempt = marks[2]["attempt_id"]
    assert attempt is not None
    assert [m["name"] for m in marks if m["attempt_id"] == attempt] == [
        "ops_wait_started",
        "ops_wait_armed",
        "ops_first_byte",
        "ops_dump_complete",
        "ops_clock_sync_started",
        "ops_clock_sync_done",
        "ops_rearm_started",
        "ops_rearm_done",
    ]
    assert [m["attempt_id"] for m in marks[:2]] == [None, None]
    assert _named(marks, "ops_wait_armed")[0]["pre_trigger_segments"] == 12


# --- Shot (launch_monitor.py) -----------------------------------------------------------


def test_shot_attempt_id_is_transient():
    shot = Shot(ball_speed_mph=100.0, timestamp=datetime.now(), trace_attempt_id="abc-00001")
    assert "trace_attempt_id" not in shot.to_dict()
    assert "abc-00001" not in repr(shot)
    assert shot == Shot(ball_speed_mph=100.0, timestamp=shot.timestamp)


# --- Phase A join invariant (rolling_buffer/monitor.py, unchanged) -----------------------


def test_phase_a_join_invariant_shot_callback_runs_before_the_next_wait(monkeypatch):
    """Pins the serial-loop join until Task 12 replaces it with an explicit ID."""
    events = []
    monitor = RollingBufferMonitor(port="/dev/null-stage1b", trigger_type="sound")

    class FakeTrigger:
        pre_trigger_segments = 16

        def __init__(self):
            self.calls = 0

        def wait_for_trigger(self, **_kwargs):
            self.calls += 1
            events.append(("wait", self.calls))
            if self.calls == 1:
                return IQCapture(1.0, 1.068, [2048] * 4096, [2048] * 4096, timestamp=0.0)
            monitor._running = False  # pylint: disable=protected-access
            return None

        def drain_diagnostics(self):
            return []

        def reset(self):
            pass

    monkeypatch.setattr(monitor_module, "get_session_logger", lambda: None)
    monitor.trigger = FakeTrigger()
    processed = ProcessedCapture(
        timeline=SpeedTimeline([], 937.5), ball_speed_mph=100.0, ball_timestamp_ms=80.0
    )
    monkeypatch.setattr(monitor.processor, "process_capture", lambda _c, **_k: processed)
    monitor._shot_callback = lambda _shot: events.append(  # pylint: disable=protected-access
        ("callback", monitor.trigger.calls)
    )
    monitor._running = True  # pylint: disable=protected-access
    monitor._capture_loop()  # pylint: disable=protected-access
    assert events == [("wait", 1), ("callback", 1), ("wait", 2)]


# --- IWR6843 edge reading and observer order (iwr6843/monitor.py) -----------------------


def test_edge_tick_reading_brackets_active_time_and_names_the_backend():
    capture_monitor = IWR6843CaptureMonitor.__new__(IWR6843CaptureMonitor)

    class LGPIOFactory:
        pass

    class Button:
        active_time = 0.00025
        pin_factory = LGPIOFactory()

    capture_monitor._button = Button()  # pylint: disable=protected-access
    reading = capture_monitor.edge_tick_reading()
    assert reading["age_ns"] == 250_000 and reading["backend"] == "LGPIOFactory"
    assert reading["mono_before_ns"] <= reading["mono_after_ns"]
    assert set(reading) == {
        "mono_before_ns",
        "age_ns",
        "mono_after_ns",
        "backend",
        "gpiozero_version",
        "lgpio_version",
    }
    Button.active_time = None
    assert capture_monitor.edge_tick_reading()["age_ns"] is None
    capture_monitor._button = None  # pylint: disable=protected-access
    no_button = capture_monitor.edge_tick_reading()
    assert no_button["age_ns"] is None and no_button["backend"] is None


def test_edge_tick_reading_survives_a_raising_active_time():
    capture_monitor = IWR6843CaptureMonitor.__new__(IWR6843CaptureMonitor)

    class Button:
        pin_factory = None

        @property
        def active_time(self):
            raise RuntimeError("pin closed")

    capture_monitor._button = Button()  # pylint: disable=protected-access
    assert capture_monitor.edge_tick_reading()["age_ns"] is None


def test_prepended_observer_runs_first_and_is_added_once(tmp_path):
    capture_monitor = IWR6843CaptureMonitor(
        config_path=tmp_path / "unused.cfg", output_dir=tmp_path, radar=object()
    )
    order = []

    def camera(_wall):
        order.append("camera")

    def trace(_wall):
        order.append("trace")

    capture_monitor.add_trigger_observer(camera)
    capture_monitor.prepend_trigger_observer(trace)
    capture_monitor.prepend_trigger_observer(trace)
    capture_monitor._running = capture_monitor._armed = True  # pylint: disable=protected-access
    capture_monitor._last_observed_edge_timestamp = 0.0  # pylint: disable=protected-access
    capture_monitor._capture_active = True  # pylint: disable=protected-access
    capture_monitor.notify_trigger(1_790_000_000.0)
    assert order == ["trace", "camera"]


@pytest.mark.parametrize("bad_age", [float("nan"), float("inf"), "soon"])
def test_edge_tick_reading_reports_an_unusable_age_as_none(bad_age):
    capture_monitor = IWR6843CaptureMonitor.__new__(IWR6843CaptureMonitor)

    class Button:
        active_time = bad_age
        pin_factory = None

    capture_monitor._button = Button()  # pylint: disable=protected-access
    assert capture_monitor.edge_tick_reading()["age_ns"] is None

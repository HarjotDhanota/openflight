"""Trace hooks in the non-shared files (no hardware)."""

import json
import math
import queue
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
    assert armed["arm_writes_complete"] is True


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
        # this fake's dump is already in the buffer when the wait begins (A-I2)
        "ops_dump_pending_at_wait_start",
        "ops_first_byte",
        "ops_dump_complete",
        "ops_clock_sync_started",
        "ops_clock_sync_done",
        "ops_rearm_started",
        "ops_rearm_done",
    ]
    assert [m["attempt_id"] for m in marks[:2]] == [None, None]
    assert _named(marks, "ops_wait_armed")[0]["pre_trigger_segments"] == 12


def test_switching_to_rolling_buffer_records_the_s0_split_it_wrote(recorder, monkeypatch):
    """M3: every S# write records the split, S#0 in the speed-triggered switch too."""
    radar = _radar(monkeypatch)
    radar.rearm_rolling_buffer(16)
    radar.switch_to_rolling_buffer()
    assert b"S#0" in b"".join(radar.serial.writes)
    recorder.open_ops_attempt()
    radar.wait_for_hardware_trigger(timeout=0.0)
    armed = _named(read_marks(recorder), "ops_wait_armed")[0]
    assert (armed["pre_trigger_segments"], armed["pre_trigger_segments_reason"]) == (0, None)


@pytest.mark.parametrize("write", ["enter", "switch"])
def test_an_s_hash_write_that_never_returns_leaves_the_split_unknown(recorder, monkeypatch, write):
    """M4: a split is recorded as written only once its write returns (no readback)."""
    radar = _radar(monkeypatch, write_timeout_on=b"S#")
    with pytest.raises(serial.SerialTimeoutException):
        if write == "enter":
            radar.enter_rolling_buffer_mode(pre_trigger_segments=6)
        else:
            radar.switch_to_rolling_buffer()
    recorder.open_ops_attempt()
    radar.wait_for_hardware_trigger(timeout=0.0)
    armed = _named(read_marks(recorder), "ops_wait_armed")[0]
    assert armed["pre_trigger_segments"] is None
    assert armed["pre_trigger_segments_reason"] == "split_write_incomplete"


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


# --- server.py hooks ---------------------------------------------------------------------


class _EdgeCaptureMonitor:
    @staticmethod
    def edge_tick_reading():
        return {
            "mono_before_ns": 1_000,
            "age_ns": 300_000,
            "mono_after_ns": 1_400,
            "backend": "LGPIOFactory",
            "gpiozero_version": "2.0.1",
            "lgpio_version": "0.2.2.0",
        }


def _iwr_runtime(capture_monitor):
    class Runtime:
        pass

    runtime = Runtime()
    runtime.capture_monitor = capture_monitor
    return runtime


def test_server_edge_observer_uses_its_own_bracket_not_the_observer_argument(recorder, monkeypatch):
    from openflight import server

    monkeypatch.setattr(server, "iwr6843_runtime", _iwr_runtime(_EdgeCaptureMonitor()))
    server._trace_gate_edge(1_790_000_000.25)  # pylint: disable=protected-access
    edge = _named(read_marks(recorder), "gate_edge_callback")[0]
    assert (edge["mono_before_ns"], edge["age_ns"], edge["mono_after_ns"]) == (
        1_000,
        300_000,
        1_400,
    )
    assert edge["monotonic_ns"] == 1_400
    assert edge["edge_seq"] == 1
    assert edge["wall_minus_mono_low_ns"] <= edge["wall_minus_mono_high_ns"]
    assert edge["observer_wall_s"] == 1_790_000_000.25
    assert (edge["backend"], edge["gpiozero_version"], edge["lgpio_version"]) == (
        "LGPIOFactory",
        "2.0.1",
        "0.2.2.0",
    )
    assert edge["reading_error"] is None


def test_server_edge_observer_still_notes_the_edge_when_the_reading_fails(recorder, monkeypatch):
    """An edge happened: it is noted age-less (an ambiguity-maker), never invented."""
    from openflight import server

    class Broken:
        @staticmethod
        def edge_tick_reading():
            raise RuntimeError("pin gone")

    monkeypatch.setattr(server, "iwr6843_runtime", _iwr_runtime(Broken()))
    server._trace_gate_edge(1_790_000_000.25)  # pylint: disable=protected-access
    edge = _named(read_marks(recorder), "gate_edge_callback")[0]
    assert edge["age_ns"] is None and edge["mono_before_ns"] is None
    assert edge["mono_after_ns"] is None
    assert edge["reading_error"] == "RuntimeError"


def test_server_edge_observer_never_raises_into_the_gpio_callback(recorder, monkeypatch):
    from openflight import server

    def broken(**_kwargs):
        raise RuntimeError("trace bug")

    monkeypatch.setattr(server, "iwr6843_runtime", _iwr_runtime(_EdgeCaptureMonitor()))
    monkeypatch.setattr(recorder, "note_edge", broken)
    server._trace_gate_edge(1_790_000_000.25)  # pylint: disable=protected-access


def test_server_edge_observer_is_a_no_op_without_a_recorder(monkeypatch):
    from openflight import server

    class Counting:
        calls = 0

        def edge_tick_reading(self):
            Counting.calls += 1
            return {}

    pi_trace.set_recorder(None)
    monkeypatch.setattr(server, "iwr6843_runtime", _iwr_runtime(Counting()))
    server._trace_gate_edge(1.0)  # pylint: disable=protected-access
    assert Counting.calls == 0


def test_trace_tag_carries_eligibility_and_update_kind_separately_from_final(recorder):
    from openflight import server

    shot = Shot(ball_speed_mph=100.0, timestamp=datetime.now(), trace_attempt_id="t-00001")
    tag = server._trace_tag(  # pylint: disable=protected-access
        shot,
        revision=2,
        final=True,
        update_kind="late_update",
        core_eligible=False,
        core_fields=["ball_speed_mph"],
    )
    assert tag == {
        "attempt_id": "t-00001",
        "revision": 2,
        "final": True,
        "update_kind": "late_update",
        "core_eligible": False,
        "core_fields": ["ball_speed_mph"],
    }
    untraced = Shot(ball_speed_mph=100.0, timestamp=datetime.now())
    assert (
        server._trace_tag(  # pylint: disable=protected-access
            untraced, revision=1, final=False, update_kind="first", core_eligible=False
        )
        is None
    )


def test_trace_tag_is_absent_when_tracing_is_off():
    from openflight import server

    pi_trace.set_recorder(None)
    shot = Shot(ball_speed_mph=100.0, timestamp=datetime.now(), trace_attempt_id="t-00001")
    assert (
        server._trace_tag(  # pylint: disable=protected-access
            shot, revision=1, final=False, update_kind="first", core_eligible=False
        )
        is None
    )


def test_spin_and_analysis_are_traced_on_the_instance_and_results_unchanged(recorder):
    from openflight import server

    plain, traced = RollingBufferProcessor(), RollingBufferProcessor()
    server._trace_processor(traced)  # pylint: disable=protected-access
    server._trace_processor(traced)  # pylint: disable=protected-access  (wrapped once)
    i_samples, q_samples = synth_capture(rpm=3000, ball_speed_mph=80.0, amplitude=400.0)
    capture = IQCapture(
        1.0, 1.068, [int(v) for v in i_samples], [int(v) for v in q_samples], timestamp=0.0
    )
    recorder.bind_thread("t-00001")
    expected = plain.process_capture(capture)
    assert expected is not None and traced.process_capture(capture) == expected
    assert "process_capture" not in vars(RollingBufferProcessor()), "class must be untouched"
    config = {
        "club_type": monitor_module.ClubType.IRON_7,
        "spin_prior_policy": "x",
        "resolved_spin_prior_rpm": None,
    }
    assert traced.replay_config(**config)["sha256"] == plain.replay_config(**config)["sha256"]
    marks = [m for m in read_marks(recorder) if m["name"].startswith("ops_")]
    assert [m["name"] for m in marks] == [
        "ops_analysis_started",
        "ops_spin_started",
        "ops_spin_done",
        "ops_core_ready",
    ]
    assert {m["attempt_id"] for m in marks} == {"t-00001"}
    assert {m["error"] for m in marks if m["name"].endswith(("_done", "_ready"))} == {None}


def test_install_hooks_wraps_the_processor_and_puts_the_edge_observer_first(
    recorder, monkeypatch, tmp_path
):
    from openflight import server

    capture_monitor = IWR6843CaptureMonitor(
        config_path=tmp_path / "unused.cfg", output_dir=tmp_path, radar=object()
    )
    capture_monitor.add_trigger_observer(lambda _wall: None)  # the camera's, registered earlier

    class Monitor:
        processor = RollingBufferProcessor()

    monkeypatch.setattr(server, "monitor", Monitor())
    monkeypatch.setattr(server, "iwr6843_runtime", _iwr_runtime(capture_monitor))
    server._install_pi_trace_hooks()  # pylint: disable=protected-access
    assert "process_capture" in vars(Monitor.processor)
    assert "detect_spin_multitaper" in vars(Monitor.processor)
    observers = capture_monitor._trigger_observers  # pylint: disable=protected-access
    assert observers[0] is server._trace_gate_edge  # pylint: disable=protected-access


def test_install_hooks_does_nothing_without_a_recorder(monkeypatch):
    from openflight import server

    pi_trace.set_recorder(None)

    class Monitor:
        processor = RollingBufferProcessor()

    monkeypatch.setattr(server, "monitor", Monitor())
    monkeypatch.setattr(server, "iwr6843_runtime", None)
    server._install_pi_trace_hooks()  # pylint: disable=protected-access
    assert "process_capture" not in vars(Monitor.processor)


def _setup_rejecting_server(monkeypatch):
    """Tester setup required, a camera readiness that exists and hard-stops the shot."""
    from openflight import server

    class Camera:
        @staticmethod
        def trigger_evidence_for_shot(_impact):
            return {"schema_version": 1, "required": True, "ready": False, "blockers": []}

    swing_results = []

    class Light:
        @staticmethod
        def swing_result(result, **_kwargs):
            swing_results.append(result)

    assigned = []
    monkeypatch.setattr(server, "tester_setup_required", True)
    monkeypatch.setattr(server, "camera_capture_runtime", Camera())
    monkeypatch.setattr(server, "_tester_trigger_evidence_problem", lambda _r: "blocked")
    monkeypatch.setattr(server, "_tester_trigger_hard_stop", lambda _r: "hard")
    monkeypatch.setattr(server, "log_session_error", lambda *a, **k: None)
    monkeypatch.setattr(server, "ready_light", Light())
    monkeypatch.setattr(server, "_assign_shot_number", assigned.append)
    return server, swing_results, assigned


def test_setup_rejection_is_marked_and_not_accepted(recorder, monkeypatch):
    """R-F3: the rejection mark, and no sign the shot was accepted."""
    server, swing_results, assigned = _setup_rejecting_server(monkeypatch)
    attempt = recorder.open_ops_attempt()
    server._handle_shot_detected(  # pylint: disable=protected-access
        Shot(ball_speed_mph=100.0, timestamp=datetime.now())
    )
    marks = read_marks(recorder)
    rejected = _named(marks, "server_shot_rejected")
    assert [(m["attempt_id"], m["reason"]) for m in rejected] == [
        (attempt, "tester_setup_readiness")
    ]
    assert _named(marks, "server_callback_started")[0]["join"] == "serial_capture_loop"
    assert not _named(marks, "server_shot_accepted")
    assert not _named(marks, "server_emit_invoked")
    assert swing_results == ["not_counted"] and assigned == []


def test_callback_joins_the_current_attempt_and_marks_acceptance(recorder, monkeypatch):
    from openflight import server

    attempt = recorder.open_ops_attempt()
    shot = Shot(ball_speed_mph=100.0, timestamp=datetime.now())
    monkeypatch.setattr(server, "tester_setup_required", False)
    monkeypatch.setattr(server, "_has_slow_shot_enrichment", lambda _shot: False)
    monkeypatch.setattr(server, "_register_shot_for_finalization", lambda *a, **k: None)
    monkeypatch.setattr(server, "_finish_shot_detected", lambda *a, **k: None)
    monkeypatch.setattr(server.ready_light, "swing_result", lambda *a, **k: None)
    server._handle_shot_detected(shot)  # pylint: disable=protected-access
    assert shot.trace_attempt_id == attempt
    marks = read_marks(recorder)
    started = _named(marks, "server_callback_started")[0]
    assert started["attempt_id"] == attempt
    assert started["monotonic_ns"] == shot.server_callback_started_monotonic_ns
    accepted = _named(marks, "server_shot_accepted")[0]
    assert accepted["attempt_id"] == attempt and accepted["shot_number"] == shot.shot_number


def test_untraced_callback_leaves_the_shot_without_an_attempt(monkeypatch):
    from openflight import server

    pi_trace.set_recorder(None)
    shot = Shot(ball_speed_mph=100.0, timestamp=datetime.now())
    monkeypatch.setattr(server, "tester_setup_required", False)
    monkeypatch.setattr(server, "_has_slow_shot_enrichment", lambda _shot: False)
    monkeypatch.setattr(server, "_register_shot_for_finalization", lambda *a, **k: None)
    monkeypatch.setattr(server, "_finish_shot_detected", lambda *a, **k: None)
    monkeypatch.setattr(server.ready_light, "swing_result", lambda *a, **k: None)
    server._handle_shot_detected(shot)  # pylint: disable=protected-access
    assert shot.trace_attempt_id is None


def _initial_emit_server(monkeypatch):
    from openflight import server

    emitted = []
    monkeypatch.setattr(server, "monitor", None)
    monkeypatch.setattr(server, "iwr6843_runtime", object())
    monkeypatch.setattr(server, "camera_capture_runtime", None)
    monkeypatch.setattr(
        server.socketio, "emit", lambda event, payload: emitted.append((event, payload))
    )
    return server, emitted


def _traced_shot(attempt_id):
    return Shot(
        ball_speed_mph=120.0,
        timestamp=datetime.now(),
        launch_angle_vertical=12.0,
        launch_angle_vertical_source="radar",
        trace_attempt_id=attempt_id,
    )


def test_initial_emit_is_tagged_and_bracketed_by_invoked_and_returned(recorder, monkeypatch):
    server, emitted = _initial_emit_server(monkeypatch)
    attempt = recorder.open_ops_attempt()
    assert server._emit_initial_ops_shot(_traced_shot(attempt)) is True  # pylint: disable=protected-access
    ((event, payload),) = emitted
    assert event == "shot" and payload["pending"] == {"iwr6843": True}
    assert payload["trace"] == {
        "attempt_id": attempt,
        "revision": 1,
        "final": False,
        "update_kind": "first",
        "core_eligible": False,
        "core_fields": list(payload["trace"]["core_fields"]),
    }
    assert "ball_speed_mph" in payload["trace"]["core_fields"]
    marks = read_marks(recorder)
    invoked, returned = (
        _named(marks, "server_emit_invoked")[0],
        _named(marks, "server_emit_returned")[0],
    )
    assert invoked["seq"] < returned["seq"]
    for mark in (invoked, returned):
        assert mark["attempt_id"] == attempt
        assert (mark["revision"], mark["final"], mark["event"]) == (1, False, "shot")
        assert mark["update_kind"] == "first"
        assert mark["core_eligible"] is False
        assert mark["core_reasons"] == [
            "launch_horizontal_not_measured",
            "distance_not_in_payload",
        ]
        assert mark["core"]["ball_speed_mph"] == payload["shot"]["ball_speed_mph"]
        assert mark["contract"] == "pre_w13_v1"
    assert returned["error"] is None


def test_status_emit_is_tagged_status_and_never_eligible(recorder, monkeypatch):
    server, emitted = _initial_emit_server(monkeypatch)
    attempt = recorder.open_ops_attempt()
    shot = _traced_shot(attempt)
    server._emit_initial_ops_shot(shot)  # pylint: disable=protected-access
    server._emit_ops_enrichment_status(  # pylint: disable=protected-access
        shot, status="pending", reason="queue_full"
    )
    status_payload = emitted[1][1]
    assert status_payload["enrichment"]["status"] == "pending"
    tag = status_payload["trace"]
    assert (tag["update_kind"], tag["revision"], tag["final"], tag["core_eligible"]) == (
        "status",
        1,
        False,
        False,
    )
    status_mark = _named(read_marks(recorder), "server_emit_invoked")[1]
    assert status_mark["update_kind"] == "status" and status_mark["core_eligible"] is False
    assert status_mark["core_reasons"][0] == "status_update"


def test_untraced_emit_has_no_trace_key(monkeypatch):
    server, emitted = _initial_emit_server(monkeypatch)
    pi_trace.set_recorder(None)
    server._emit_initial_ops_shot(_traced_shot("stale-00001"))  # pylint: disable=protected-access
    assert "trace" not in emitted[0][1]


def test_a_raising_recorder_never_drops_or_blocks_the_emit(recorder, monkeypatch):
    """R-F16: a trace error is logged and ignored; the emit happens."""
    server, emitted = _initial_emit_server(monkeypatch)
    attempt = recorder.open_ops_attempt()

    def broken(*_a, **_k):
        raise RuntimeError("trace bug")

    monkeypatch.setattr(recorder, "mark", broken)
    monkeypatch.setattr(server.core_eligibility, "evaluate", broken)
    assert server._emit_initial_ops_shot(_traced_shot(attempt)) is True  # pylint: disable=protected-access
    assert [event for event, _payload in emitted] == ["shot"]
    assert "trace" not in emitted[0][1]


def test_a_failing_emit_is_marked_returned_with_its_error(recorder, monkeypatch):
    server, _emitted = _initial_emit_server(monkeypatch)

    def failing(_event, _payload):
        raise ConnectionError("socket gone")

    monkeypatch.setattr(server.socketio, "emit", failing)
    monkeypatch.setattr(server, "log_session_error", lambda *a, **k: None)
    attempt = recorder.open_ops_attempt()
    assert server._emit_initial_ops_shot(_traced_shot(attempt)) is False  # pylint: disable=protected-access
    assert _named(read_marks(recorder), "server_emit_returned")[0]["error"] == "ConnectionError"


def test_non_finite_core_values_cannot_break_the_trace_file(recorder, monkeypatch):
    server, emitted = _initial_emit_server(monkeypatch)
    attempt = recorder.open_ops_attempt()
    payload = {"shot": {"ball_speed_mph": 120.0, "launch_angle_vertical": float("nan")}}
    server._traced_emit(  # pylint: disable=protected-access
        _traced_shot(attempt), "shot", payload, final=False, update_kind="first"
    )
    assert math.isnan(emitted[0][1]["shot"]["launch_angle_vertical"]), "payload untouched"
    marks = read_marks(recorder)
    assert _named(marks, "server_emit_invoked")[0]["core"]["launch_angle_vertical"] == "nan"
    assert recorder.counts()["write_failures"] == 0


def test_unmatched_late_result_is_marked(recorder, monkeypatch):
    from openflight import server

    monkeypatch.setattr(server, "get_session_logger", lambda: None)
    server._reset_shot_sequence()  # pylint: disable=protected-access
    shot = _traced_shot("t-00007")
    shot.shot_number = 99
    pending = server._PendingShotFinalization(  # pylint: disable=protected-access
        shot=shot,
        emit_event="shot_update",
        initial_ui_ms=None,
        enrichment=server._ShotEnrichmentResult(),  # pylint: disable=protected-access
    )
    server._queue_ordered_shot_finalization(pending, source_shot=shot)  # pylint: disable=protected-access
    mark = _named(read_marks(recorder), "server_late_result_unmatched")[0]
    assert mark["attempt_id"] == "t-00007" and mark["reason"] == "shot_not_awaiting_enrichment"


def test_enrichment_marks_iwr_and_camera_stages_on_the_shot_attempt(recorder, monkeypatch):
    from openflight import server

    class Capture:
        valid = True
        path = None
        sequence = 3
        error = None
        trigger_timestamp = 1_790_000_000.0
        metadata = {"trigger_host_timestamp_ns": 5_000}

    class Camera:
        @staticmethod
        def capture_for_shot(_impact, timeout_s):
            del timeout_s
            return Capture()

    monkeypatch.setattr(server, "camera_capture_runtime", Camera())
    monkeypatch.setattr(server, "kld7_vertical", None)
    monkeypatch.setattr(server, "kld7_horizontal", None)
    monkeypatch.setattr(server, "get_session_logger", lambda: None)
    monkeypatch.setattr(server, "_snapshot_inclinometer_for_shot", lambda _shot: None)
    monkeypatch.setattr(server, "_process_iwr6843_angle", lambda _shot: 12.5)
    monkeypatch.setattr(
        server, "_load_camera_capture_archive", lambda _c: {"host_timestamp_ns": [6_000, 7_000]}
    )
    monkeypatch.setattr(server, "_attach_camera_replay", lambda *_a: None)
    monkeypatch.setattr(server, "_fuse_camera_measurements", lambda *_a: None)
    monkeypatch.setattr(server, "_attach_experimental_face_angle", lambda _shot: None)
    shot = _traced_shot("t-00003")
    shot.mode = "rolling-buffer"
    server._enrich_shot_from_optional_hardware(shot)  # pylint: disable=protected-access
    marks = [m for m in read_marks(recorder) if m["attempt_id"] == "t-00003"]
    names = [m["name"] for m in marks]
    for name in (
        "enrichment_started",
        "iwr_process_started",
        "iwr_process_done",
        "camera_trigger",
        "camera_last_frame",
        "camera_wait_started",
        "camera_ready",
        "camera_analysis_done",
    ):
        assert name in names, name
    assert names[0] == "enrichment_started"
    assert names.index("iwr_process_started") < names.index("iwr_process_done")
    assert names.index("camera_wait_started") < names.index("camera_ready")
    assert names.index("camera_ready") < names.index("camera_analysis_done")
    assert _named(marks, "camera_trigger")[0]["monotonic_ns"] == 5_000
    assert _named(marks, "camera_last_frame")[0]["monotonic_ns"] == 7_000
    assert _named(marks, "camera_ready")[0]["matched"] is True


def test_camera_marks_are_skipped_when_the_clip_carries_no_host_times(recorder, monkeypatch):
    from openflight import server

    class Camera:
        @staticmethod
        def capture_for_shot(_impact, timeout_s):
            del timeout_s

    monkeypatch.setattr(server, "camera_capture_runtime", Camera())
    shot = _traced_shot("t-00004")
    server._prefetch_camera_capture(shot)  # pylint: disable=protected-access
    names = [m["name"] for m in read_marks(recorder)]
    assert "camera_trigger" not in names and "camera_last_frame" not in names


def test_start_and_stop_trace_beside_the_session_logs(tmp_path):
    from openflight import server

    path = server._start_pi_trace(tmp_path)  # pylint: disable=protected-access
    try:
        assert path.parent == tmp_path
        assert path.name.startswith("pi_trace_") and path.suffix == ".jsonl"
        assert pi_trace.get_recorder() is not None
    finally:
        server._stop_pi_trace()  # pylint: disable=protected-access
    assert pi_trace.get_recorder() is None
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert lines[0]["type"] == "trace_start" and lines[-1]["type"] == "trace_end"
    assert lines[-1]["complete"] is True


def test_shutdown_cleanup_closes_the_trace(tmp_path, monkeypatch):
    from openflight import server

    for name in ("kld7_vertical", "kld7_horizontal", "inclinometer_service", "iwr6843_runtime"):
        monkeypatch.setattr(server, name, None)
    monkeypatch.setattr(server, "power_monitor", None)
    monkeypatch.setattr(server, "camera_capture_runtime", None)
    monkeypatch.setattr(server, "sim_connectors", [])
    monkeypatch.setattr(server, "stop_monitor", lambda: None)
    monkeypatch.setattr(server, "shutdown_cleanup_started", False)
    path = server._start_pi_trace(tmp_path)  # pylint: disable=protected-access
    assert server._cleanup_hardware_for_shutdown() is True  # pylint: disable=protected-access
    assert pi_trace.get_recorder() is None
    assert json.loads(path.read_text(encoding="utf-8").splitlines()[-1])["type"] == "trace_end"


def _admission_server(monkeypatch, defer):
    """_handle_shot_detected up to enrichment admission, with the downstream stubbed."""
    from openflight import server

    statuses, queued = [], []
    monkeypatch.setattr(server, "tester_setup_required", False)
    monkeypatch.setattr(server, "_has_slow_shot_enrichment", lambda _shot: True)
    monkeypatch.setattr(server, "_emit_initial_ops_shot", lambda _shot: True)
    monkeypatch.setattr(server, "_register_shot_for_finalization", lambda *a, **k: None)
    monkeypatch.setattr(server, "_defer_shot_enrichment", defer)
    monkeypatch.setattr(
        server, "_emit_ops_enrichment_status", lambda _shot, **kw: statuses.append(kw)
    )
    monkeypatch.setattr(server, "_queue_shot_finalization", lambda *a, **k: queued.append(k))
    monkeypatch.setattr(server, "_log_shot_enrichment_lifecycle", lambda *a, **k: None)
    monkeypatch.setattr(server, "log_session_error", lambda *a, **k: None)
    monkeypatch.setattr(server, "get_session_logger", lambda: None)
    monkeypatch.setattr(server.ready_light, "swing_result", lambda *a, **k: None)
    return server, statuses, queued


def test_queue_full_deferral_is_marked_and_the_enrichment_still_kept(recorder, monkeypatch):
    server, statuses, queued = _admission_server(monkeypatch, lambda *a, **k: False)
    attempt = recorder.open_ops_attempt()
    server._handle_shot_detected(  # pylint: disable=protected-access
        Shot(ball_speed_mph=100.0, timestamp=datetime.now())
    )
    deferred = _named(read_marks(recorder), "server_enrichment_deferred")
    assert [(m["attempt_id"], m["reason"]) for m in deferred] == [(attempt, "queue_full")]
    assert statuses == [{"status": "pending", "reason": "queue_full"}]
    assert queued[0]["awaits_late_enrichment"] is True


def test_backlog_full_abandonment_is_marked(recorder, monkeypatch):
    def full(*_a, **_k):
        raise queue.Full("backlog full")

    server, statuses, _queued = _admission_server(monkeypatch, full)
    attempt = recorder.open_ops_attempt()
    server._handle_shot_detected(  # pylint: disable=protected-access
        Shot(ball_speed_mph=100.0, timestamp=datetime.now())
    )
    abandoned = _named(read_marks(recorder), "server_enrichment_abandoned")
    assert [(m["attempt_id"], m["reason"]) for m in abandoned] == [
        (attempt, "enrichment_backlog_full")
    ]
    assert statuses == [{"status": "skipped", "reason": "enrichment_backlog_full"}]


# --- Debounced GATE edges stay visible (coordinator ruling, 11 Oct) ---------------------


def _armed_capture_monitor(tmp_path, observer):
    capture_monitor = IWR6843CaptureMonitor(
        config_path=tmp_path / "unused.cfg", output_dir=tmp_path, radar=object()
    )

    class LGPIOFactory:
        pass

    class Button:
        active_time = 0.0004
        pin_factory = LGPIOFactory()

    capture_monitor._button = Button()  # pylint: disable=protected-access
    capture_monitor.add_trigger_observer(observer)
    capture_monitor._running = capture_monitor._armed = True  # pylint: disable=protected-access
    capture_monitor._capture_active = True  # pylint: disable=protected-access  (no IWR queue)
    return capture_monitor


def test_a_debounced_edge_is_marked_and_the_debounce_is_unchanged(recorder, tmp_path):
    heard = []
    capture_monitor = _armed_capture_monitor(tmp_path, heard.append)
    capture_monitor.notify_trigger(1_790_000_000.00)
    assert capture_monitor.notify_trigger(1_790_000_000.05) is False  # inside 0.1 s
    assert heard == [1_790_000_000.00], "debounce still hides the edge from observers"
    assert capture_monitor._last_observed_edge_timestamp == 1_790_000_000.00  # pylint: disable=protected-access
    marks = read_marks(recorder)
    debounced = _named(marks, "gate_edge_debounced")
    assert len(debounced) == 1
    edge = debounced[0]
    assert edge["attempt_id"] is None
    assert edge["observer_wall_s"] == 1_790_000_000.05
    assert edge["since_last_observed_s"] == pytest.approx(0.05)
    assert edge["age_ns"] == 400_000 and edge["backend"] == "LGPIOFactory"
    assert edge["mono_before_ns"] <= edge["mono_after_ns"] == edge["monotonic_ns"]
    assert edge["wall_minus_mono_low_ns"] <= edge["wall_minus_mono_high_ns"]
    assert edge["reading_error"] is None


def test_a_debounced_edge_is_marked_even_when_its_reading_fails(recorder, tmp_path):
    capture_monitor = _armed_capture_monitor(tmp_path, lambda _wall: None)

    class Broken:
        pin_factory = None

        @property
        def active_time(self):
            raise RuntimeError("pin gone")

    capture_monitor.notify_trigger(10.0)
    capture_monitor._button = Broken()  # pylint: disable=protected-access
    capture_monitor.notify_trigger(10.01)
    edge = _named(read_marks(recorder), "gate_edge_debounced")[0]
    assert edge["age_ns"] is None


def test_a_debounced_edge_with_a_raising_recorder_still_returns_false(
    recorder, tmp_path, monkeypatch
):
    capture_monitor = _armed_capture_monitor(tmp_path, lambda _wall: None)

    def broken(*_a, **_k):
        raise RuntimeError("trace bug")

    capture_monitor.notify_trigger(10.0)
    monkeypatch.setattr(recorder, "mark", broken)
    assert capture_monitor.notify_trigger(10.01) is False


def test_debounced_edges_are_not_read_when_tracing_is_off(tmp_path, monkeypatch):
    pi_trace.set_recorder(None)
    capture_monitor = _armed_capture_monitor(tmp_path, lambda _wall: None)
    reads = []
    monkeypatch.setattr(capture_monitor, "edge_tick_reading", lambda: reads.append(1) or {})
    capture_monitor.notify_trigger(10.0)
    assert capture_monitor.notify_trigger(10.01) is False
    assert reads == []


def test_edge_reading_looks_package_versions_up_once(monkeypatch):
    from openflight.iwr6843 import monitor as iwr_monitor

    calls = []
    real_version = iwr_monitor.importlib.metadata.version
    iwr_monitor._package_version.cache_clear()  # pylint: disable=protected-access

    def counting(name):
        calls.append(name)
        return real_version(name)

    monkeypatch.setattr(iwr_monitor.importlib.metadata, "version", counting)
    capture_monitor = IWR6843CaptureMonitor.__new__(IWR6843CaptureMonitor)
    capture_monitor._button = None  # pylint: disable=protected-access
    for _ in range(3):
        capture_monitor.edge_tick_reading()
    assert sorted(calls) == ["gpiozero", "lgpio"]
    iwr_monitor._package_version.cache_clear()  # pylint: disable=protected-access


# --- No trace accessor can raise into a hooked path (coordinator audit, 11 Oct) ---------


class _RaisingAccessorsRecorder(pi_trace.TraceRecorder):
    """A recorder whose read accessors all fail, as a trace bug would."""

    @property
    def current_ops_attempt(self):
        raise RuntimeError("trace bug")

    @property
    def last_edge_seq(self):
        raise RuntimeError("trace bug")

    def bound_attempt(self):
        raise RuntimeError("trace bug")


@pytest.fixture
def raising_recorder(tmp_path):
    rec = _RaisingAccessorsRecorder(tmp_path / "r.jsonl", temp_reader=lambda: None)
    pi_trace.set_recorder(rec)
    yield rec
    pi_trace.set_recorder(None)
    rec.close()


def test_module_accessors_never_raise(raising_recorder):
    del raising_recorder
    assert pi_trace.current_ops_attempt() is None
    assert pi_trace.bound_attempt() is None
    assert pi_trace.last_edge_seq() is None


def test_capture_rearm_and_analysis_survive_raising_accessors(raising_recorder, monkeypatch):
    del raising_recorder
    forwarded = []
    timer = OpsCaptureTimer.start()
    timer.first_marker_callback(lambda: forwarded.append(True))
    assert forwarded == [True] and timer.first_marker_ns is not None
    radar = _radar(monkeypatch)
    radar.rearm_rolling_buffer(16)
    assert b"S#16\r" in radar.serial.writes
    radar.read_clock_sync(samples=2)
    processor = RollingBufferProcessor()
    pi_trace.trace_method(processor, "detect_spin_multitaper", "a", "b")
    i_samples, q_samples = synth_capture(rpm=3000, ball_speed_mph=80.0, amplitude=400.0)
    capture = IQCapture(
        1.0, 1.068, [int(v) for v in i_samples], [int(v) for v in q_samples], timestamp=0.0
    )
    assert processor.process_capture(capture) is not None


def test_shot_callback_survives_raising_accessors(raising_recorder, monkeypatch):
    del raising_recorder
    from openflight import server

    assigned = []
    monkeypatch.setattr(server, "tester_setup_required", False)
    monkeypatch.setattr(server, "_has_slow_shot_enrichment", lambda _shot: False)
    monkeypatch.setattr(server, "_register_shot_for_finalization", lambda *a, **k: None)
    monkeypatch.setattr(server, "_finish_shot_detected", lambda *a, **k: None)
    monkeypatch.setattr(server, "_assign_shot_number", assigned.append)
    monkeypatch.setattr(server.ready_light, "swing_result", lambda *a, **k: None)
    shot = Shot(ball_speed_mph=100.0, timestamp=datetime.now())
    server._handle_shot_detected(shot)  # pylint: disable=protected-access
    assert assigned == [shot] and shot.trace_attempt_id is None


def test_a_raising_trace_tag_still_emits_untagged(recorder, monkeypatch):
    server, emitted = _initial_emit_server(monkeypatch)

    def broken(*_a, **_k):
        raise RuntimeError("trace bug")

    monkeypatch.setattr(server, "_trace_tag", broken)
    attempt = recorder.open_ops_attempt()
    assert server._emit_initial_ops_shot(_traced_shot(attempt)) is True  # pylint: disable=protected-access
    assert len(emitted) == 1 and "trace" not in emitted[0][1]
    # A-M1: the summary must be able to tell an untagged emit from no emit at all.
    invoked = [m for m in read_marks(recorder) if m["name"] == "server_emit_invoked"]
    assert len(invoked) == 1
    assert invoked[0]["tagging_failed"] is True
    assert invoked[0]["update_kind"] == "first" and invoked[0]["event"] == "shot"
    assert "revision" not in invoked[0]


def test_a_trace_that_cannot_start_leaves_the_kiosk_running_untraced(tmp_path, caplog):
    from openflight import server

    blocker = tmp_path / "not_a_dir"
    blocker.write_text("x", encoding="utf-8")
    assert server._start_pi_trace(blocker) is None  # pylint: disable=protected-access
    assert pi_trace.get_recorder() is None
    assert "Pi trace could not start" in caplog.text


def test_installing_the_hooks_warms_the_version_lookup_so_the_first_edge_does_none(
    recorder, monkeypatch, tmp_path
):
    """M5: no metadata lookup inside the GPIO callback, not even on the first edge."""
    from openflight import server
    from openflight.iwr6843 import monitor as iwr_monitor

    del recorder
    iwr_monitor._package_version.cache_clear()  # pylint: disable=protected-access
    capture_monitor = IWR6843CaptureMonitor(
        config_path=tmp_path / "unused.cfg", output_dir=tmp_path, radar=object()
    )
    monkeypatch.setattr(server, "monitor", None)
    monkeypatch.setattr(server, "iwr6843_runtime", _iwr_runtime(capture_monitor))
    server._install_pi_trace_hooks()  # pylint: disable=protected-access

    lookups = []
    real_version = iwr_monitor.importlib.metadata.version

    def counting(name):
        lookups.append(name)
        return real_version(name)

    monkeypatch.setattr(iwr_monitor.importlib.metadata, "version", counting)
    capture_monitor.edge_tick_reading()
    assert lookups == []
    iwr_monitor._package_version.cache_clear()  # pylint: disable=protected-access


def test_rearm_records_the_split_only_after_its_trailing_pa(recorder, monkeypatch):
    """2d: S# returned but the trailing PA timed out: the split is not recorded."""
    radar = _radar(monkeypatch)
    pa_writes = []
    real_write = radar.serial.write

    def write(data):
        if data == b"PA":
            pa_writes.append(data)
            if len(pa_writes) == 2:
                raise serial.SerialTimeoutException("trailing PA timed out")
        return real_write(data)

    radar.serial.write = write
    assert radar.rearm_rolling_buffer(12) is None
    assert b"S#12\r" in radar.serial.writes
    recorder.open_ops_attempt()
    radar.wait_for_hardware_trigger(timeout=0.0)
    armed = _named(read_marks(recorder), "ops_wait_armed")[0]
    assert (armed["pre_trigger_segments"], armed["pre_trigger_segments_reason"]) == (
        None,
        "rearm_write_timed_out",
    )


def test_browser_trace_routes_start_on_the_kiosk_socket_when_tracing(recorder, monkeypatch):
    from openflight import server

    del recorder
    registered = []
    monkeypatch.setattr(
        server, "register_trace_routes", lambda sock: registered.append(sock) or "pinger"
    )
    assert server._start_browser_trace() == "pinger"  # pylint: disable=protected-access
    assert registered == [server.socketio]


def test_browser_trace_routes_that_fail_leave_the_kiosk_running(recorder, monkeypatch, caplog):
    from openflight import server

    del recorder

    def _boom(_sock):
        raise RuntimeError("no socket")

    monkeypatch.setattr(server, "register_trace_routes", _boom)
    assert server._start_browser_trace() is None  # pylint: disable=protected-access
    assert "browser trace routes could not start" in caplog.text


# --- A-I2: a dump already arriving when the wait began -------------------------------------


def test_a_dump_already_pending_at_wait_start_is_marked_with_its_byte_count(recorder, monkeypatch):
    """Its first byte arrived before the wait: the first-byte time is late (fail-closed)."""
    radar = _radar(monkeypatch)
    dump = _dump_bytes([2048] * 8, [2048] * 8)
    radar.serial.arrive(dump)  # an edge raced the re-arm
    attempt = recorder.open_ops_attempt()
    radar.wait_for_hardware_trigger(timeout=1.0, dump_grace=1.0)
    marks = read_marks(recorder)
    late = _named(marks, "ops_dump_pending_at_wait_start")
    assert len(late) == 1
    assert late[0]["attempt_id"] == attempt
    assert late[0]["pending_bytes"] == len(dump)
    assert late[0]["marker_offset"] == 0


def test_stale_noise_pending_at_wait_start_marks_no_late_dump(recorder, monkeypatch):
    """Bytes pending at the start that hold no dump marker say nothing about the dump."""
    radar = _radar(monkeypatch)
    radar.serial.arrive(b'{"Clock":"1.0"}\r\n')
    recorder.open_ops_attempt()
    radar.wait_for_hardware_trigger(timeout=0.0)
    assert _named(read_marks(recorder), "ops_dump_pending_at_wait_start") == []


def test_a_dump_whose_marker_starts_inside_the_pending_bytes_is_marked(recorder, monkeypatch):
    """Noise then the dump's marker, all before the wait: still late, at its offset."""
    radar = _radar(monkeypatch)
    noise = b'{"Clock":"1.0"}\r\n'
    dump = _dump_bytes([2048] * 8, [2048] * 8)
    radar.serial.arrive(noise + dump)
    recorder.open_ops_attempt()
    radar.wait_for_hardware_trigger(timeout=1.0, dump_grace=1.0)
    late = _named(read_marks(recorder), "ops_dump_pending_at_wait_start")
    assert len(late) == 1
    assert late[0]["pending_bytes"] == len(noise + dump)
    assert late[0]["marker_offset"] == len(noise)


def test_no_pending_bytes_marks_no_late_dump(recorder, monkeypatch):
    radar = _radar(monkeypatch)
    recorder.open_ops_attempt()
    radar.wait_for_hardware_trigger(timeout=0.0)
    assert _named(read_marks(recorder), "ops_dump_pending_at_wait_start") == []

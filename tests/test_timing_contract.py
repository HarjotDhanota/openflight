"""Regression tests for the versioned shot timing contract."""

from __future__ import annotations

import json
import queue
from datetime import datetime
from types import SimpleNamespace

import pytest

from openflight.iwr6843.monitor import IWR6843Capture, IWR6843CaptureMonitor
from openflight.iwr6843.runtime import IWR6843Runtime
from openflight.launch_monitor import Shot
from openflight.rolling_buffer.monitor import RollingBufferMonitor
from openflight.rolling_buffer.trigger import HardwareTriggeredCapture, SoundTrigger
from openflight.rolling_buffer.types import IQCapture, SpeedReading, SpeedTimeline
from openflight.session_logger import SessionLogger
from openflight.timing import (
    SCHEMA_NAME,
    SCHEMA_VERSION,
    measured_stage,
    new_stage_timing,
)


class _FakeClock:
    def __init__(self) -> None:
        self.monotonic_ns = 1_000_000_000
        self.wall_time = 1_700_000_000.0

    def advance_ns(self, duration_ns: int) -> None:
        self.monotonic_ns += duration_ns


def test_contract_marks_unobservable_boundaries_unavailable():
    timing = new_stage_timing()

    assert timing["schema"] == SCHEMA_NAME
    assert timing["version"] == SCHEMA_VERSION
    assert timing["clock"] == {
        "domain": "host_monotonic",
        "source": "time.monotonic_ns",
        "unit": "ns",
    }
    assert timing["ops"]["physical_trigger_to_observation"]["duration_ns"] is None
    assert timing["ops"]["acquisition_window"]["duration_ns"] is None
    assert timing["browser"]["receive"]["duration_ns"] is None
    assert timing["browser"]["paint"]["duration_ns"] is None


def test_reversed_monotonic_boundaries_fail_closed():
    stage = measured_stage(20, 10, "start", "end")

    assert stage["status"] == "unavailable"
    assert stage["duration_ns"] is None
    assert stage["reason"] == "monotonic_boundary_order_invalid"


def test_sound_trigger_uses_monotonic_first_marker_and_response_boundaries(monkeypatch):
    """A discontinuous wall clock must not affect new OPS durations."""
    from openflight import timing as timing_module
    from openflight.rolling_buffer import trigger as trigger_module

    clock = _FakeClock()
    monkeypatch.setattr(timing_module.time, "monotonic_ns", lambda: clock.monotonic_ns)
    monkeypatch.setattr(trigger_module.time, "monotonic_ns", lambda: clock.monotonic_ns)
    monkeypatch.setattr(trigger_module.time, "time", lambda: clock.wall_time)

    callback_calls = []

    class Radar:
        last_hardware_trigger_first_byte_timestamp = 1234.5
        last_clock_sync = None

        @staticmethod
        def wait_for_hardware_trigger(*, timeout, cancel_event, on_first_byte):
            assert timeout == 30.0
            assert cancel_event is None
            clock.advance_ns(30_000_000_000)
            on_first_byte()
            clock.wall_time -= 86_400.0
            clock.advance_ns(7_550_000_000)
            return '{"sample_time": 0.0}'

        @staticmethod
        def rearm_rolling_buffer(_segments):
            clock.advance_ns(50_000_000)

    capture = IQCapture(
        sample_time=0.0,
        trigger_time=0.068,
        i_samples=[2048],
        q_samples=[2048],
    )

    class Processor:
        @staticmethod
        def parse_capture(_response, *, first_byte_timestamp):
            assert first_byte_timestamp == 1234.5
            clock.advance_ns(100_000_000)
            return capture

        @staticmethod
        def process_standard(_capture):
            clock.advance_ns(200_000_000)
            return SpeedTimeline(
                readings=[SpeedReading(100.0, 900.0, 68.0, "outbound")],
                sample_rate_hz=937.5,
            )

    trigger = SoundTrigger()
    result = trigger.wait_for_trigger(
        Radar(),
        Processor(),
        capture_started_callback=lambda: callback_calls.append(clock.monotonic_ns),
    )

    assert result is capture
    assert callback_calls == [31_000_000_000]
    ops = trigger.drain_diagnostics()[0]["ops_timing"]
    assert ops["trigger_observation_wait"]["duration_ns"] == 30_000_000_000
    assert ops["trigger_observation_wait"]["includes_golfer_idle"] is True
    assert ops["uart_transport"]["duration_ns"] == 7_550_000_000
    assert ops["post_transport_processing"]["duration_ns"] == 350_000_000
    assert ops["physical_trigger_to_observation"]["status"] == "unavailable"
    assert ops["acquisition_window"]["status"] == "unavailable"


def test_hardware_trigger_preserves_legacy_parse_rearm_value_and_splits_transport(monkeypatch):
    from openflight import timing as timing_module
    from openflight.rolling_buffer import trigger as trigger_module

    clock = _FakeClock()
    monkeypatch.setattr(timing_module.time, "monotonic_ns", lambda: clock.monotonic_ns)
    monkeypatch.setattr(trigger_module.time, "monotonic_ns", lambda: clock.monotonic_ns)
    monkeypatch.setattr(trigger_module.time, "time", lambda: clock.wall_time)

    class Radar:
        last_hardware_trigger_first_byte_timestamp = 1234.5
        _internal_trigger_firmware_version = "test"

        @staticmethod
        def wait_for_hardware_trigger(*, timeout, cancel_event, on_first_byte):
            del timeout, cancel_event
            clock.advance_ns(10_000_000_000)
            on_first_byte()
            clock.advance_ns(7_550_000_000)
            return '{"sample_time": 0.0}'

        @staticmethod
        def rearm_internal_speed_trigger(_sample_rate):
            clock.advance_ns(50_000_000)
            clock.wall_time += 0.05
            return True

    capture = IQCapture(0.0, 0.068, [2048], [2048])

    class Processor:
        @staticmethod
        def parse_capture(_response, *, first_byte_timestamp):
            assert first_byte_timestamp == 1234.5
            clock.advance_ns(100_000_000)
            clock.wall_time += 0.1
            return capture

        @staticmethod
        def process_standard(_capture):
            clock.advance_ns(200_000_000)
            return SpeedTimeline(
                [SpeedReading(100.0, 900.0, 68.0, "outbound")],
                937.5,
            )

    trigger = HardwareTriggeredCapture()
    assert trigger.wait_for_trigger(Radar(), Processor()) is capture

    diagnostic = trigger.drain_diagnostics()[0]
    assert diagnostic["trigger_latency_ms"] == pytest.approx(150.0)
    assert diagnostic["ops_timing"]["uart_transport"]["duration_ns"] == 7_550_000_000
    assert diagnostic["ops_timing"]["post_transport_processing"]["duration_ns"] == 350_000_000


def test_missing_first_marker_is_unavailable_not_zero():
    radar = type(
        "Radar",
        (),
        {
            "last_hardware_trigger_first_byte_timestamp": None,
            "_internal_trigger_firmware_version": "test",
            "wait_for_hardware_trigger": staticmethod(lambda **_kwargs: "response"),
            "rearm_internal_speed_trigger": staticmethod(lambda _rate: True),
        },
    )()
    capture = IQCapture(0.0, 0.068, [2048], [2048])
    processor = type(
        "Processor",
        (),
        {
            "parse_capture": staticmethod(lambda *_args, **_kwargs: capture),
            "process_standard": staticmethod(
                lambda _capture: SpeedTimeline(
                    [SpeedReading(100.0, 900.0, 68.0, "outbound")],
                    937.5,
                )
            ),
        },
    )()

    trigger = HardwareTriggeredCapture()
    assert trigger.wait_for_trigger(radar, processor) is capture
    ops = trigger.drain_diagnostics()[0]["ops_timing"]
    assert ops["trigger_observation_wait"]["status"] == "unavailable"
    assert ops["uart_transport"]["status"] == "unavailable"


def test_monitor_preserves_mode_dependent_legacy_values_and_provenance():
    class HardwareDiagnostics:
        @staticmethod
        def drain_diagnostics():
            return [
                {
                    "accepted": True,
                    "reason": "accepted",
                    "trigger_latency_ms": 3.5,
                }
            ]

    monitor = RollingBufferMonitor(port=None, trigger_type="hardware")
    monitor.trigger = HardwareDiagnostics()
    diagnostic = monitor._emit_diagnostics(30_000.0, ensure_accepted=True)

    assert diagnostic["trigger_latency_ms"] == 3.5
    assert diagnostic["latency_ms"] == 3.5
    legacy = diagnostic["stage_timing"]["legacy_fields"]
    assert "parse and radar re-arm" in legacy["trigger_latency_ms"]["semantics"]
    assert "not physical trigger-edge" in legacy["trigger_latency_ms"]["ambiguity"]

    class SpeedDiagnostics:
        @staticmethod
        def drain_diagnostics():
            return []

    monitor.trigger_type = "speed"
    monitor.trigger = SpeedDiagnostics()
    fallback = new_stage_timing()["ops"]
    fallback["blocking_operation"] = measured_stage(
        1,
        12_250_001,
        "trigger_wait_started",
        "trigger_strategy_returned",
        includes_golfer_idle=True,
    )
    diagnostic = monitor._emit_diagnostics(
        12.25,
        fallback_ops_timing=fallback,
        ensure_accepted=True,
    )

    assert diagnostic["latency_ms"] == 12.25
    assert diagnostic["stage_timing"]["ops"]["blocking_operation"]["duration_ns"] == 12_250_000
    assert diagnostic["stage_timing"]["ops"]["trigger_observation_wait"]["status"] == (
        "unavailable"
    )
    assert diagnostic["stage_timing"]["ops"]["uart_transport"]["status"] == "unavailable"


def test_trigger_event_override_labels_whole_wait_legacy_value(monkeypatch):
    from openflight.rolling_buffer import monitor as monitor_module

    monitor = RollingBufferMonitor(port=None, trigger_type="hardware")
    monitor._diagnostic_callback = lambda event: observed.append(event)
    monkeypatch.setattr(monitor_module, "get_session_logger", lambda: None)
    observed = []
    timing = new_stage_timing()
    timing["legacy_fields"]["latency_ms"] = {"semantics": "parse and re-arm"}

    monitor._record_trigger_event(
        {"stage_timing": timing},
        accepted=True,
        reason="accepted",
        latency_ms=30_000.0,
    )

    provenance = observed[0]["stage_timing"]["legacy_fields"]["latency_ms"]
    assert "golfer idle wait" in provenance["semantics"]
    assert observed[0]["latency_ms"] == 30_000.0


def test_session_records_keep_legacy_numbers_and_add_contract(tmp_path):
    logger = SessionLogger(log_dir=tmp_path, enabled=True)
    logger.start_session(mode="rolling-buffer", trigger_type="sound")
    stage_timing = new_stage_timing()

    logger.log_trigger_event(
        trigger_type="sound",
        accepted=True,
        latency_ms=123.456,
        stage_timing=stage_timing,
    )
    trigger_entry = json.loads(logger.session_path.read_text().splitlines()[-1])
    assert trigger_entry["latency_ms"] == 123.456
    assert trigger_entry["stage_timing"]["version"] == 1

    logger.log_rolling_buffer_capture(
        shot_number=1,
        sample_time=1.0,
        trigger_time=1.1,
        i_samples=[1],
        q_samples=[2],
        trigger_latency_ms=9876.543,
        stage_timing=stage_timing,
    )
    capture_entry = json.loads(logger.session_path.read_text().splitlines()[-1])
    assert capture_entry["trigger_latency_ms"] == 9876.543
    assert capture_entry["stage_timing"]["version"] == 1


def test_iwr_capture_separates_uart_transport_from_dump_bookkeeping(monkeypatch, tmp_path):
    from openflight.iwr6843 import monitor as monitor_module

    clock = _FakeClock()
    monkeypatch.setattr(monitor_module.time, "monotonic_ns", lambda: clock.monotonic_ns)
    monkeypatch.setattr(monitor_module.time, "time", lambda: clock.wall_time)

    class Radar:
        port = "test"

        @staticmethod
        def read_dump():
            clock.advance_ns(7_550_000_000)
            clock.wall_time -= 10.0
            return b"dump"

    capture_monitor = IWR6843CaptureMonitor(
        config_path=tmp_path / "unused.cfg",
        output_dir=tmp_path,
        radar=Radar(),
    )

    def validate(_raw):
        clock.advance_ns(200_000_000)
        return {}

    monkeypatch.setattr(capture_monitor, "_validate_dump", validate)
    capture_monitor._running = True
    capture_monitor._events = queue.Queue()
    capture_monitor._events.put(123.0)
    capture_monitor._events.put(None)
    capture_monitor._capture_loop()

    capture = capture_monitor.capture_for_shot(123.0, timeout_s=0.0)
    assert capture is not None
    assert capture.uart_transport_duration_ns == 7_550_000_000
    assert capture.dump_duration_ns == 7_750_000_000
    assert capture.dump_duration_s == -10.0


def test_iwr_capture_preserves_reversed_monotonic_transport_for_contract_rejection(
    monkeypatch,
    tmp_path,
):
    from openflight.iwr6843 import monitor as monitor_module

    monotonic_ticks = iter((1_000, 900, 800, 700))
    wall_ticks = iter((10.0, 12.0))
    monkeypatch.setattr(monitor_module.time, "monotonic_ns", lambda: next(monotonic_ticks))
    monkeypatch.setattr(monitor_module.time, "time", lambda: next(wall_ticks))

    radar = SimpleNamespace(port="test", read_dump=lambda: b"dump")
    capture_monitor = IWR6843CaptureMonitor(
        config_path=tmp_path / "unused.cfg",
        output_dir=tmp_path,
        radar=radar,
    )
    monkeypatch.setattr(capture_monitor, "_validate_dump", lambda _raw: {})
    capture_monitor._running = True
    capture_monitor._events = queue.Queue()
    capture_monitor._events.put(123.0)
    capture_monitor._events.put(None)

    capture_monitor._capture_loop()

    capture = capture_monitor.capture_for_shot(123.0, timeout_s=0.0)
    assert capture is not None
    assert capture.uart_transport_duration_ns == -100
    assert capture.dump_duration_ns == -300
    assert capture.dump_duration_s == 2.0


def test_iwr_runtime_separates_capture_wait_and_estimator_analysis(monkeypatch):
    from openflight.iwr6843 import runtime as runtime_module

    clock = _FakeClock()
    monkeypatch.setattr(runtime_module.time, "monotonic_ns", lambda: clock.monotonic_ns)
    capture = IWR6843Capture(
        sequence=1,
        trigger_timestamp=1.0,
        completed_timestamp=2.0,
        dump_duration_s=7.55,
        raw=b"capture",
        path=None,
        uart_transport_duration_ns=7_550_000_000,
    )

    class CaptureMonitor:
        @staticmethod
        def capture_for_shot(_timestamp, *, timeout_s):
            assert timeout_s == 12.0
            clock.advance_ns(7_600_000_000)
            return capture

    measurement = SimpleNamespace(accepted=False)

    def process_raw(*_args, **_kwargs):
        clock.advance_ns(425_000_000)
        return measurement, None

    monkeypatch.setattr(runtime_module, "process_raw_capture", process_raw)
    runtime = IWR6843Runtime(
        capture_monitor=CaptureMonitor(),
        calibration=SimpleNamespace(tee_range_m=1.0),
        net_range_m=4.0,
    )

    result = runtime.process_shot(
        impact_timestamp=1.0,
        ball_speed_mph=100.0,
        club="driver",
    )

    assert result.capture_wait_duration_ns == 7_600_000_000
    assert result.estimator_analysis_duration_ns == 425_000_000
    assert result.aggregate_duration_ns == 8_025_000_000
    assert result.capture.uart_transport_duration_ns == 7_550_000_000


def test_iwr_runtime_reversed_stages_are_rejected_by_server_contract(monkeypatch):
    from openflight import server
    from openflight.iwr6843 import runtime as runtime_module

    monotonic_ticks = iter((1_000, 900, 800, 700, 600))
    monkeypatch.setattr(runtime_module.time, "monotonic_ns", lambda: next(monotonic_ticks))
    capture = IWR6843Capture(
        sequence=1,
        trigger_timestamp=1.0,
        completed_timestamp=2.0,
        dump_duration_s=7.55,
        raw=b"capture",
        path=None,
        uart_transport_duration_ns=-50,
    )
    capture_monitor = SimpleNamespace(capture_for_shot=lambda _timestamp, *, timeout_s: capture)
    measurement = SimpleNamespace(accepted=False)
    monkeypatch.setattr(
        runtime_module,
        "process_raw_capture",
        lambda *_args, **_kwargs: (measurement, None),
    )
    runtime = IWR6843Runtime(
        capture_monitor=capture_monitor,
        calibration=SimpleNamespace(tee_range_m=1.0),
        net_range_m=4.0,
    )

    result = runtime.process_shot(
        impact_timestamp=1.0,
        ball_speed_mph=100.0,
        club="driver",
    )

    assert result.capture_wait_duration_ns == -100
    assert result.estimator_analysis_duration_ns == -100
    assert result.aggregate_duration_ns == -400

    monkeypatch.setattr(server, "iwr6843_runtime", SimpleNamespace(process_shot=lambda **_: result))
    monkeypatch.setattr(server, "get_session_logger", lambda: None)
    monkeypatch.setattr(server.socketio, "emit", lambda *_args, **_kwargs: None)
    shot = Shot(ball_speed_mph=100.0, timestamp=datetime.now())
    server._process_iwr6843_angle(shot)

    for stage_name in ("capture_wait", "uart_transport", "estimator_analysis", "aggregate"):
        stage = shot.stage_timing["iwr6843"][stage_name]
        assert stage["status"] == "unavailable"
        assert stage["duration_ns"] is None
        assert stage["reason"] == "monotonic_boundary_order_invalid"


def test_shot_serializes_contract_but_not_transient_callback_clock():
    shot = Shot(
        ball_speed_mph=100.0,
        timestamp=datetime.now(),
        stage_timing=new_stage_timing(),
        server_callback_started_monotonic_ns=123,
    )

    payload = shot.to_dict()

    assert payload["stage_timing"]["version"] == 1
    assert "server_callback_started_monotonic_ns" not in payload
    assert "server_callback_started_monotonic_ns" not in repr(shot)


def test_server_maps_iwr_transport_and_analysis_without_using_legacy_dump_seconds(
    monkeypatch,
):
    from openflight import server

    capture = SimpleNamespace(
        trigger_timestamp=1.0,
        path=None,
        raw=b"capture",
        dump_duration_s=99.0,
        uart_transport_duration_ns=7_550_000_000,
        error=None,
        valid=True,
        sequence=1,
        temperature_report=None,
    )
    result = SimpleNamespace(
        capture=capture,
        measurement=None,
        club_path=None,
        withheld_reason=None,
        capture_wait_duration_ns=7_600_000_000,
        estimator_analysis_duration_ns=425_000_000,
        aggregate_duration_ns=8_025_000_000,
    )
    monkeypatch.setattr(
        server,
        "iwr6843_runtime",
        SimpleNamespace(process_shot=lambda **_kwargs: result),
    )
    monkeypatch.setattr(server, "get_session_logger", lambda: None)
    monkeypatch.setattr(server.socketio, "emit", lambda *_args, **_kwargs: None)
    shot = Shot(ball_speed_mph=100.0, timestamp=datetime.now())

    legacy_aggregate_ms = server._process_iwr6843_angle(shot)

    assert legacy_aggregate_ms is not None
    iwr = shot.stage_timing["iwr6843"]
    assert iwr["capture_wait"]["duration_ns"] == 7_600_000_000
    assert iwr["uart_transport"]["duration_ns"] == 7_550_000_000
    assert iwr["estimator_analysis"]["duration_ns"] == 425_000_000
    assert iwr["aggregate"]["duration_ns"] == 8_025_000_000
    assert iwr["uart_transport"]["duration_ns"] != 99_000_000_000


def test_server_publication_is_callback_to_emit_invocation_not_browser_time(monkeypatch):
    from openflight import server

    clock = _FakeClock()
    clock.monotonic_ns = 5_000_000_000
    clock.wall_time = -50_000.0
    monkeypatch.setattr(server.time, "monotonic_ns", lambda: clock.monotonic_ns)
    monkeypatch.setattr(server.time, "time", lambda: clock.wall_time)
    monkeypatch.setattr(server, "monitor", None)
    monkeypatch.setattr(server, "iwr6843_runtime", object())
    monkeypatch.setattr(server, "camera_capture_runtime", None)
    emitted = []
    monkeypatch.setattr(
        server.socketio, "emit", lambda event, payload: emitted.append((event, payload))
    )
    shot = Shot(
        ball_speed_mph=100.0,
        timestamp=datetime.now(),
        impact_timestamp=1_700_000_000.0,
        stage_timing=new_stage_timing(),
        server_callback_started_monotonic_ns=1_000_000_000,
    )

    assert server._emit_initial_ops_shot(shot) is True

    timing = emitted[0][1]["shot"]["stage_timing"]
    assert timing["server"]["publication"]["duration_ns"] == 4_000_000_000
    assert timing["server"]["publication"]["end_event"] == "server_websocket_emit_invoked"
    assert timing["browser"]["receive"]["status"] == "unavailable"
    assert timing["browser"]["paint"]["status"] == "unavailable"
    shot.stage_timing["server"]["publication"]["duration_ns"] = 99
    assert emitted[0][1]["shot"]["stage_timing"]["server"]["publication"]["duration_ns"] == (
        4_000_000_000
    )

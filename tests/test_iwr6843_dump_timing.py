"""Each IWR dump keeps its host monotonic transfer marks and the GATE it answered."""

from __future__ import annotations

import json
import queue
from datetime import datetime
from types import SimpleNamespace

import numpy as np

from openflight import server
from openflight.clock_mapping import EdgeObservation
from openflight.gate_timing import GateEvent
from openflight.iwr6843 import driver
from openflight.iwr6843.driver import DumpTransferTiming, IWR6843DumpRecoveryError, IWR6843Radar
from openflight.iwr6843.dump import pack_dump
from openflight.iwr6843.monitor import IWR6843Capture, IWR6843CaptureMonitor
from openflight.launch_monitor import Shot
from openflight.session_logger import SessionLogger
from tests.iwr6843_firmware_fake import FakeClock, FakePort, FirmwareModel, NoLock

_AGE_NS = 5_000_000


def _dump() -> bytes:
    return pack_dump(np.ones((2, 6, 4, 7), dtype=complex), n_tx=3, version=3)


def _gate_event(low_ns: int, high_ns: int, backend: str = "LGPIOFactory") -> GateEvent:
    """A GATE whose kernel tick interval is [low_ns, high_ns] on the host monotonic clock."""
    return GateEvent(
        event_id="gate-1",
        observation=EdgeObservation(
            edge_seq=None,
            mono_before_ns=low_ns + _AGE_NS,
            mono_after_ns=high_ns + _AGE_NS,
            edge_age_ns=_AGE_NS,
            wall_minus_mono_low_ns=0,
            wall_minus_mono_high_ns=10,
            backend=backend,
            observer_wall_s=1_700_000_000.0,
        ),
    )


def _run_one_capture(tmp_path, radar, event: GateEvent, *, save_dumps: bool = False):
    monitor = IWR6843CaptureMonitor(
        config_path=tmp_path / "unused.cfg",
        output_dir=tmp_path / "dumps",
        radar=radar,
        save_dumps=save_dumps,
    )
    if save_dumps:
        monitor.output_dir.mkdir(parents=True, exist_ok=True)
    monitor._events = queue.Queue()  # pylint: disable=protected-access
    monitor._events.put(event)  # pylint: disable=protected-access
    monitor._events.put(None)  # pylint: disable=protected-access
    monitor._running = True  # pylint: disable=protected-access
    monitor._capture_loop()  # pylint: disable=protected-access
    capture = monitor.capture_for_shot(None, timeout_s=0.0)
    assert capture is not None
    return capture


def test_monitor_keeps_the_absolute_transfer_marks_and_gate_latency(monkeypatch, tmp_path):
    clock = FakeClock()
    firmware = FirmwareModel(_dump(), clock, restart_s=2.5)
    firmware.active = True
    monkeypatch.setattr(driver, "time", clock.module())
    monkeypatch.setattr(driver, "IWR6843DeviceLock", NoLock)
    monkeypatch.setattr(driver, "open_port", lambda *_args, **_kwargs: FakePort(firmware))
    radar = IWR6843Radar(port="/dev/serial/by-id/iwr-if00-port0")
    before_ns = clock.monotonic_ns()
    gate_mid_ns = before_ns - 3_000_000  # the sound edge, 3 ms before the request
    event = _gate_event(gate_mid_ns - 1_000, gate_mid_ns + 1_000)

    capture = _run_one_capture(tmp_path, radar, event, save_dumps=True)

    assert capture.valid
    assert capture.gate_event is event
    timing = capture.dump_transfer_timing
    assert isinstance(timing, DumpTransferTiming)
    marks = [
        timing.request_written_ns,
        timing.first_byte_ns,
        timing.header_complete_ns,
        timing.last_byte_ns,
        timing.cli_ready_ns,
    ]
    assert None not in marks
    assert marks == sorted(marks)
    # absolute host monotonic times, not durations
    assert timing.request_written_ns >= before_ns
    assert timing.cli_ready_ns <= clock.monotonic_ns()
    assert capture.gate_to_request_ns == timing.request_written_ns - gate_mid_ns
    assert capture.gate_to_request_ns >= 3_000_000
    assert capture.gate_to_request_uncertainty_ns == 1_000
    assert capture.path is not None and capture.path.read_bytes() == firmware.raw


class _TimedRadar:
    port = "/dev/fake-iwr6843"

    def __init__(self, raw: bytes = b"", timing=None, error: Exception | None = None):
        self.raw, self.timing, self.error = raw, timing, error
        self.untimed_reads = 0

    def read_dump(self):
        self.untimed_reads += 1
        return self.raw

    def read_dump_timed(self):
        if self.error is not None:
            raise self.error
        return self.raw, self.timing


def test_monitor_reads_the_timed_dump_when_the_radar_offers_it(tmp_path):
    timing = DumpTransferTiming(
        request_written_ns=10_000_000_000,
        first_byte_ns=10_020_000_000,
        header_complete_ns=10_021_000_000,
        last_byte_ns=17_400_000_000,
        cli_ready_ns=17_450_000_000,
    )
    radar = _TimedRadar(_dump(), timing)

    capture = _run_one_capture(tmp_path, radar, _gate_event(9_990_000_000, 9_990_000_101))

    assert radar.untimed_reads == 0
    assert capture.valid and capture.dump_transfer_timing == timing
    # midpoint 9_990_000_050, half-width 50.5 ns rounded up so it still covers the interval
    assert capture.gate_to_request_ns == 10_000_000_000 - 9_990_000_050
    assert capture.gate_to_request_uncertainty_ns == 51


def test_a_failed_transfer_keeps_the_marks_it_reached(tmp_path):
    timing = DumpTransferTiming(request_written_ns=10_000_000_000, first_byte_ns=10_020_000_000)
    radar = _TimedRadar(
        error=IWR6843DumpRecoveryError("IWR6843 dump ended early", _dump()[:100], timing)
    )

    capture = _run_one_capture(tmp_path, radar, _gate_event(9_999_000_000, 9_999_000_000))

    assert not capture.valid
    assert capture.dump_transfer_timing == timing
    assert capture.gate_to_request_ns == 1_000_000
    assert capture.gate_to_request_uncertainty_ns == 0


def test_an_unqualified_gate_gives_no_latency_but_keeps_the_marks(tmp_path):
    timing = DumpTransferTiming(request_written_ns=10_000_000_000)
    radar = _TimedRadar(_dump(), timing)

    capture = _run_one_capture(
        tmp_path, radar, _gate_event(9_999_000_000, 9_999_001_000, backend="MockFactory")
    )

    assert capture.dump_transfer_timing == timing
    assert capture.gate_to_request_ns is None
    assert capture.gate_to_request_uncertainty_ns is None


def test_a_radar_without_timed_reads_still_captures(tmp_path):
    radar = SimpleNamespace(port="test", read_dump=_dump)

    capture = _run_one_capture(tmp_path, radar, _gate_event(1, 2))

    assert capture.valid and capture.raw == _dump()
    assert capture.dump_transfer_timing is None
    assert capture.gate_to_request_ns is None


def _logged_capture_entry(monkeypatch, tmp_path, capture) -> tuple[dict, Shot]:
    logger = SessionLogger(log_dir=tmp_path, enabled=True)
    logger.start_session(mode="rolling-buffer", trigger_type="sound")
    result = SimpleNamespace(
        capture=capture,
        measurement=None,
        club_path=None,
        withheld_reason=None,
        capture_wait_duration_ns=7_600_000_000,
        estimator_analysis_duration_ns=425_000_000,
        aggregate_duration_ns=8_025_000_000,
    )
    monkeypatch.setattr(server, "iwr6843_runtime", SimpleNamespace(process_shot=lambda **_: result))
    monkeypatch.setattr(server, "get_session_logger", lambda: logger)
    monkeypatch.setattr(server.socketio, "emit", lambda *_args, **_kwargs: None)
    shot = Shot(ball_speed_mph=100.0, timestamp=datetime.now())
    shot.shot_number = 1
    server._process_iwr6843_angle(shot)  # pylint: disable=protected-access
    entries = [json.loads(line) for line in logger.session_path.read_text().splitlines()]
    (entry,) = [e for e in entries if e["type"] == "iwr6843_capture"]
    return entry, shot


def test_the_session_log_entry_carries_the_marks_and_the_gate_latency(monkeypatch, tmp_path):
    timing = DumpTransferTiming(
        request_written_ns=10_000_000_000,
        first_byte_ns=10_020_000_000,
        header_complete_ns=10_021_000_000,
        last_byte_ns=17_400_000_000,
        cli_ready_ns=17_450_000_000,
    )
    event = _gate_event(9_996_000_000, 9_996_002_000)
    capture = IWR6843Capture(
        sequence=1,
        trigger_timestamp=1_700_000_000.0,
        completed_timestamp=1_700_000_007.5,
        dump_duration_s=7.5,
        raw=b"capture",
        path=tmp_path / "iwr6843_shot.l3dump",
        uart_transport_duration_ns=7_550_000_000,
        dump_duration_ns=7_600_000_000,
        gate_event=event,
        dump_transfer_timing=timing,
    )

    entry, shot = _logged_capture_entry(monkeypatch, tmp_path, capture)

    assert entry["dump_transfer_timing"] == {
        "request_written_ns": 10_000_000_000,
        "first_byte_ns": 10_020_000_000,
        "header_complete_ns": 10_021_000_000,
        "last_byte_ns": 17_400_000_000,
        "cli_ready_ns": 17_450_000_000,
        "clock": "host_monotonic_ns",
    }
    assert entry["gate_to_request_ns"] == 3_999_000
    assert entry["gate_to_request_uncertainty_ns"] == 1_000
    assert entry["gate_event"]["kernel_mono_interval_ns"] == [9_996_000_000, 9_996_002_000]
    # the existing fields are unchanged
    assert entry["trigger_timestamp"] == 1_700_000_000.0
    assert entry["capture_path"] == str(tmp_path / "iwr6843_shot.l3dump")
    assert entry["capture_bytes"] == len(b"capture")
    assert entry["capture_complete"] is True
    assert entry["dump_duration_s"] == 7.5
    assert entry["capture_error"] is None
    assert shot.stage_timing["iwr6843"]["uart_transport"]["duration_ns"] == 7_550_000_000
    assert shot.stage_timing["iwr6843"]["capture_wait"]["duration_ns"] == 7_600_000_000


def test_an_entry_without_marks_writes_nulls(monkeypatch, tmp_path):
    capture = SimpleNamespace(
        trigger_timestamp=1.0,
        path=None,
        raw=b"capture",
        dump_duration_s=7.5,
        uart_transport_duration_ns=None,
        error=None,
        valid=True,
        sequence=1,
        temperature_report=None,
    )

    entry, _shot = _logged_capture_entry(monkeypatch, tmp_path, capture)

    assert entry["dump_transfer_timing"] is None
    assert entry["gate_to_request_ns"] is None
    assert entry["gate_to_request_uncertainty_ns"] is None
    assert entry["dump_duration_s"] == 7.5

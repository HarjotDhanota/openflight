"""Original serial bytes and host observations survive capture rejection."""

import base64
import hashlib
import json
import threading
from types import SimpleNamespace

import pytest
import serial

from openflight.ops243 import OPS243Radar
from openflight.ops_capture_evidence import OpsCaptureEvidence
from openflight.rolling_buffer.monitor import RollingBufferMonitor
from openflight.rolling_buffer.processor import RollingBufferProcessor
from openflight.rolling_buffer.trigger import SoundTrigger
from openflight.session_logger import SessionLogger

RAW = b'{"sample_time":"1.000"}\r\n{"trigger_time":"1.050"}\r\n{"I":[1,2]}\r\n{"Q":[3,4]}\r\n'
NOTICE = b'{"trigger_velocity_detected":"1.049"}\r\n'


class SerialChunks:
    """Reads expose fixed chunks without real hardware or sleeps."""

    is_open = True

    def __init__(self, chunks):
        self.chunks = list(chunks)

    @property
    def in_waiting(self):
        if self.chunks and isinstance(self.chunks[0], Exception):
            return 1
        return len(self.chunks[0]) if self.chunks else 0

    def read(self, _size):
        item = self.chunks.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def write(self, _data):
        pass

    def flush(self):
        pass


def radar_with(chunks):
    radar = OPS243Radar()
    radar.serial = SerialChunks(chunks)
    return radar


def test_split_notification_and_original_dump_are_retained(monkeypatch):
    monkeypatch.setattr("openflight.ops243.time.sleep", lambda _seconds: None)
    radar = radar_with([NOTICE[:13], NOTICE[13:] + RAW[:8], RAW[8:]])
    saved = []
    radar.capture_evidence_callback = saved.append
    assert radar.wait_for_hardware_trigger(timeout=0.1) == RAW.decode("ascii")
    assert len(saved) == 1
    row = saved[0]
    assert base64.b64decode(row["raw_capture_base64"]) == RAW
    assert row["raw_sha256"] == hashlib.sha256(RAW).hexdigest()
    assert row["notifications"][0]["raw_line"] == NOTICE.decode("ascii").strip()
    assert row["notifications"][0]["payload"] == {"trigger_velocity_detected": "1.049"}
    assert row["armed_mono_ns"] is None
    assert row["physical_timing_qualified"] is False
    assert row["dump_start_mono_ns"] <= row["response_completed_mono_ns"]
    assert json.loads(json.dumps(row)) == row


def test_original_non_ascii_bytes_are_not_replaced_by_legacy_decoding():
    raw = RAW.replace(b'"1.000"', b'"1.000\xff"')
    radar = radar_with([raw])
    assert "\xff" not in radar.wait_for_hardware_trigger(timeout=0.1)
    assert base64.b64decode(radar.last_hardware_capture_evidence["raw_capture_base64"]) == raw


def test_serial_failure_keeps_partial_dump_and_preserves_original_exception(monkeypatch):
    monkeypatch.setattr("openflight.ops243.time.sleep", lambda _seconds: None)
    partial = RAW[:40]
    radar = radar_with([partial, serial.SerialException("test read error")])
    saved = []
    radar.capture_evidence_callback = saved.append
    with pytest.raises(serial.SerialException, match="test read error"):
        radar.wait_for_hardware_trigger(timeout=0.1)
    assert saved[0]["outcome"] == "error"
    assert saved[0]["error"] == "SerialException"
    assert base64.b64decode(saved[0]["raw_capture_base64"]) == partial


def test_logging_failure_does_not_lose_capture(caplog):
    radar = radar_with([RAW])

    def fail(_row):
        raise OSError("log disk unavailable")

    radar.capture_evidence_callback = fail
    assert radar.wait_for_hardware_trigger(timeout=0.1) == RAW.decode("ascii")
    assert "Capture evidence logging failed" in caplog.text


def test_cancelled_wait_does_not_reuse_previous_capture_or_notifications():
    radar = radar_with([NOTICE + RAW])
    radar.wait_for_hardware_trigger(timeout=0.1)
    first_id = radar.last_hardware_capture_evidence["capture_id"]
    cancel = threading.Event()
    cancel.set()
    assert radar.wait_for_hardware_trigger(timeout=0.1, cancel_event=cancel) == ""
    row = radar.last_hardware_capture_evidence
    assert row["capture_id"] != first_id
    assert row["outcome"] == "cancelled"
    assert row["notifications"] == []
    assert row["raw_capture_base64"] == ""


def test_notification_receipt_uses_read_completion_not_radar_time(monkeypatch):
    ticks = iter([100, 120, 150, 180, 200])
    monkeypatch.setattr("openflight.ops_capture_evidence.time.monotonic_ns", lambda: next(ticks))
    evidence = OpsCaptureEvidence(None)
    evidence.prefix_read(NOTICE[:10])
    evidence.prefix_read(NOTICE[10:] + RAW)
    evidence.marker(len(NOTICE))
    evidence.dump_chunk(RAW)
    row = evidence.finish("capture")
    assert row["notifications"][0]["received_mono_ns"] == 150
    assert row["notifications"][0]["payload"]["trigger_velocity_detected"] == "1.049"
    assert row["dump_start_mono_ns"] == 180


@pytest.mark.parametrize(
    "limit,issue",
    [
        ("PREFIX_LIMIT", "preamble_truncated"),
        ("RAW_LIMIT", "raw_capture_truncated"),
        ("READ_LIMIT", "preamble_read_observations_truncated"),
    ],
)
def test_evidence_caps_are_explicit(monkeypatch, limit, issue):
    monkeypatch.setattr(OpsCaptureEvidence, limit, 1)
    evidence = OpsCaptureEvidence(None)
    evidence.prefix_read(NOTICE[:5])
    evidence.prefix_read(NOTICE[5:])
    evidence.marker(len(NOTICE))
    evidence.dump_chunk(RAW)
    assert issue in evidence.finish("capture")["evidence_issues"]


def test_missing_boot_id_does_not_fabricate_identity(monkeypatch):
    monkeypatch.setattr("openflight.ops_capture_evidence.boot_identity", lambda: None)
    assert OpsCaptureEvidence(None).finish("timeout")["boot_id"] is None


def test_rearm_observation_starts_before_first_pa_and_preserves_failed_writes(monkeypatch):
    monkeypatch.setattr("openflight.ops243.time.sleep", lambda _seconds: None)
    radar = radar_with([])
    observed = []

    def write(data):
        observed.append((data, dict(radar._hardware_arm_context)))

    radar.serial.write = write
    radar.rearm_rolling_buffer(12)
    assert observed[0][0] == b"PA"
    assert observed[0][1]["command_started_mono_ns"] is not None
    assert observed[0][1]["command_completed_mono_ns"] is None
    radar.wait_for_hardware_trigger(timeout=0)
    row = radar.last_hardware_capture_evidence
    assert (
        row["armed_mono_ns"] <= row["arm_command_completed_mono_ns"] <= row["wait_started_mono_ns"]
    )
    assert row["pre_trigger_segments"] == 12

    def fail(_data):
        raise serial.SerialTimeoutException("busy")

    radar.serial.write = fail
    radar.rearm_rolling_buffer(16)
    radar.wait_for_hardware_trigger(timeout=0)
    assert radar.last_hardware_capture_evidence["arm_command_error"] == "arm_commands_incomplete"
    assert radar.last_hardware_capture_evidence["arm_command_completed_mono_ns"] is None


def test_monitor_logs_even_a_rejected_capture_before_rearm(tmp_path, monkeypatch):
    session = SessionLogger(log_dir=tmp_path, provenance_collector=lambda *_args: {})
    session.start_session(trigger_type="sound")
    monkeypatch.setattr("openflight.rolling_buffer.monitor.get_session_logger", lambda: session)
    monitor = RollingBufferMonitor()
    monitor.radar.serial = SerialChunks([NOTICE + RAW])

    def rearm(_segments):
        entries = [json.loads(line) for line in session.session_path.read_text().splitlines()]
        evidence = [row for row in entries if row["type"] == "ops_capture_evidence"]
        assert len(evidence) == 1
        assert base64.b64decode(evidence[0]["raw_capture_base64"]) == RAW

    monkeypatch.setattr(monitor.radar, "rearm_rolling_buffer", rearm)
    # An empty speed timeline rejects this capture after its original bytes are logged.
    processor = RollingBufferProcessor()
    monkeypatch.setattr(
        processor, "process_standard", lambda _capture: SimpleNamespace(readings=[])
    )
    assert monitor.trigger.wait_for_trigger(monitor.radar, processor, timeout=0.1) is None
    assert monitor.trigger.drain_diagnostics()[0]["reason"] == "no_outbound_speed"
    assert session.stats["shots_detected"] == 0
    session.end_session()


def test_parsed_capture_gets_identity_before_rearm(monkeypatch):
    radar = radar_with([RAW])
    trigger = SoundTrigger()
    capture = RollingBufferProcessor().parse_capture(RAW.decode("ascii"))
    processor = SimpleNamespace(parse_capture=lambda *_args, **_kwargs: capture)
    monkeypatch.setattr(
        trigger,
        "_summarize_capture_activity",
        lambda *_args: {"valid_outbound_count": 0, "peak_outbound_mph": 0, "total_readings": 0},
    )
    monkeypatch.setattr(trigger, "_append_activity_diagnostic", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(radar, "rearm_rolling_buffer", lambda _segments: None)
    assert trigger.wait_for_trigger(radar, processor, timeout=0.1) is None
    assert capture.capture_id == radar.last_hardware_capture_evidence["capture_id"]


def test_repeated_and_malformed_notifications_are_retained():
    evidence = OpsCaptureEvidence(None)
    malformed = b'{"trigger_velocity_detected":bad}\r\n'
    evidence.prefix_read(NOTICE + NOTICE + malformed)
    row = evidence.finish("timeout")
    assert len(row["notifications"]) == 3
    assert row["notifications"][2]["payload"] is None
    assert "malformed_trigger_notification" in row["evidence_issues"]


def test_session_capture_can_join_original_evidence_by_id(tmp_path):
    session = SessionLogger(log_dir=tmp_path, provenance_collector=lambda *_args: {})
    session.start_session(trigger_type="sound")
    evidence = OpsCaptureEvidence(None)
    evidence.marker(0)
    evidence.dump_chunk(RAW)
    row = evidence.finish("capture")
    session.log_ops_capture_evidence(row)
    parsed = RollingBufferProcessor().parse_capture(RAW.decode("ascii"))
    session.log_rolling_buffer_capture(
        shot_number=1,
        sample_time=parsed.sample_time,
        trigger_time=parsed.trigger_time,
        i_samples=parsed.i_samples,
        q_samples=parsed.q_samples,
        capture_id=row["capture_id"],
    )
    entries = [json.loads(line) for line in session.session_path.read_text().splitlines()]
    assert entries[-1]["type"] == "rolling_buffer_capture"
    assert entries[-1]["capture_id"] == entries[-2]["capture_id"]
    session.end_session()


def test_session_disabled_does_not_write_evidence(tmp_path):
    session = SessionLogger(log_dir=tmp_path, enabled=False)
    session.log_ops_capture_evidence(OpsCaptureEvidence(None).finish("timeout"))
    assert list(tmp_path.iterdir()) == []

"""Contracts for the dedicated OPS spin diagnostic capture."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from openflight.launch_monitor import ClubType
from openflight.rolling_buffer.types import (
    ImpactEstimate,
    IQCapture,
    ProcessedCapture,
    SpeedReading,
    SpeedTimeline,
    SpinCandidate,
    SpinResult,
)

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "hardware-test" / "capture_ops_spin.py"
spec = importlib.util.spec_from_file_location("capture_ops_spin", SCRIPT)
capture_ops_spin = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = capture_ops_spin
spec.loader.exec_module(capture_ops_spin)


class MemoryWriter:
    def __init__(self):
        self.records = []

    def write(self, record):
        self.records.append(record)


class MalformedCaptureRadar:
    def __init__(self, response: str):
        self.response = response
        self.last_hardware_trigger_first_byte_timestamp = 1_700_000_000.25
        self.rearmed = []

    def wait_for_hardware_trigger(self, timeout):
        assert timeout == 0.1
        return self.response

    def rearm_rolling_buffer(self, pre_trigger_segments):
        self.rearmed.append(pre_trigger_segments)


class ValidCaptureRadar(MalformedCaptureRadar):
    def read_clock_sync(self, store=False):
        assert store is False
        return {
            "usable_for_trigger_timestamps": True,
            "best_offset_s": 1_699_999_990.0,
        }


class StubProcessor:
    def __init__(self, capture, processed, standard):
        self.capture = capture
        self.processed = processed
        self.standard = standard

    def parse_capture(self, response, first_byte_timestamp=None):
        assert response == "complete raw response"
        self.capture.first_byte_timestamp = first_byte_timestamp
        self.capture.apply_trigger_timestamp_from_first_byte()
        return self.capture

    def process_standard(self, capture):
        assert capture is self.capture
        return self.standard

    def process_capture(self, capture, club_type):
        assert capture is self.capture
        assert club_type is ClubType.IRON_7
        return self.processed

    def detect_spin(self, capture, ball_speed_mph, ball_timestamp_ms):
        assert capture is self.capture
        assert ball_speed_mph == 112.5
        assert ball_timestamp_ms == 60.0
        return SpinResult(
            spin_rpm=6300.0,
            confidence=0.5,
            snr=4.0,
            quality="medium",
        )


def _processed_capture(capture: IQCapture) -> tuple[ProcessedCapture, SpeedTimeline]:
    standard = SpeedTimeline(
        readings=[SpeedReading(112.5, 8.0, 60.0, "outbound")],
        sample_rate_hz=234.375,
        capture=capture,
    )
    overlapping = SpeedTimeline(
        readings=[
            SpeedReading(84.0, 5.0, 48.0, "inbound"),
            SpeedReading(112.5, 9.0, 60.0, "outbound"),
        ],
        sample_rate_hz=937.5,
        capture=capture,
    )
    spin = SpinResult(
        spin_rpm=6420.0,
        confidence=0.72,
        snr=6.4,
        quality="experimental",
        method="multitaper_ungated",
        modulation_depth=0.012,
        peak_freq_hz=107.0,
        seam_cycles=4.2,
        candidates=[
            SpinCandidate(
                rank=1,
                rpm=6420.0,
                freq_hz=107.0,
                relative_magnitude=1.0,
                snr=6.4,
                selected=True,
            )
        ],
    )
    processed = ProcessedCapture(
        timeline=overlapping,
        ball_speed_mph=112.5,
        ball_timestamp_ms=60.0,
        club_speed_mph=84.0,
        club_timestamp_ms=48.0,
        spin=spin,
        capture=capture,
        impact=ImpactEstimate(
            timestamp_ms=54.0,
            source="ops_transition",
            speed_delta_mph=28.5,
        ),
    )
    return processed, standard


def test_capture_record_preserves_raw_iq_and_all_processor_diagnostics():
    capture = IQCapture(
        sample_time=10.0,
        trigger_time=10.05,
        i_samples=[0, 100, 2048, 4095],
        q_samples=[4095, 3000, 2048, 0],
        first_byte_timestamp=1_700_000_000.25,
    )
    processed, standard = _processed_capture(capture)

    record = capture_ops_spin.build_capture_record(
        capture_number=3,
        capture=capture,
        processed=processed,
        standard_timeline=standard,
        club=ClubType.IRON_7,
        clock_sync={"usable_for_trigger_timestamps": True, "best_offset_s": 123.5},
    )

    assert record["type"] == "rolling_buffer_capture"
    assert record["capture_number"] == 3
    assert record["shot_number"] == 3
    assert record["club"] == "7-iron"
    assert record["i_samples"] == [0, 100, 2048, 4095]
    assert record["q_samples"] == [4095, 3000, 2048, 0]
    assert record["adc_summary"]["i"]["clipped_low"] == 1
    assert record["adc_summary"]["i"]["clipped_high"] == 1
    assert record["processing"]["spin"]["method"] == "multitaper_ungated"
    assert record["processing"]["spin"]["candidates"][0]["rpm"] == 6420.0
    assert record["processing"]["impact"]["source"] == "ops_transition"
    assert record["standard_timeline"][0]["magnitude"] == 8.0
    assert record["overlapping_timeline"][1]["speed_mph"] == 112.5
    assert record["clock_sync"]["best_offset_s"] == 123.5


def test_raw_record_keeps_verbatim_response_and_integrity_hash():
    response = '{"sample_time":"1.0"}\r\nnot-json\x00\xff'

    record = capture_ops_spin.build_raw_capture_record(
        capture_number=4,
        response=response,
        wait_started_at=1_700_000_000.0,
        response_received_at=1_700_000_002.0,
        first_byte_timestamp=1_700_000_001.0,
    )

    assert record["type"] == "ops_spin_raw_capture"
    assert record["raw_response"] == response
    assert record["response_characters"] == len(response)
    assert record["response_sha256"] == hashlib.sha256(response.encode("utf-8")).hexdigest()


def test_jsonl_writer_flushes_each_record_immediately(tmp_path):
    output = tmp_path / "ops-spin.jsonl"

    with capture_ops_spin.JsonlWriter(output) as writer:
        writer.write({"type": "first", "value": 1})
        lines = output.read_text(encoding="utf-8").splitlines()
        record = json.loads(lines[0])
        assert record["type"] == "first"
        assert record["value"] == 1
        assert record["schema_version"] == 1
        assert record["timestamp"].endswith("+00:00")


def test_parse_failure_is_saved_after_raw_response_and_radar_is_rearmed():
    raw_response = '{"sample_time":"12.0"}\n{"I":[1,2,3]}'
    radar = MalformedCaptureRadar(raw_response)
    writer = MemoryWriter()
    processor = capture_ops_spin.RollingBufferProcessor()

    outcome = capture_ops_spin.capture_once(
        radar=radar,
        processor=processor,
        writer=writer,
        capture_number=1,
        timeout=0.1,
        pre_trigger_segments=12,
        club=ClubType.DRIVER,
    )

    assert outcome == "parse_failed"
    assert [record["type"] for record in writer.records] == [
        "ops_spin_raw_capture",
        "ops_spin_capture_error",
    ]
    assert writer.records[0]["raw_response"] == raw_response
    assert writer.records[1]["reason"] == "parse_failed"
    assert radar.rearmed == [12]


def test_valid_capture_is_clock_synced_processed_and_replayable():
    capture = IQCapture(
        sample_time=10.0,
        trigger_time=10.05,
        i_samples=[0, 100, 2048, 4095],
        q_samples=[4095, 3000, 2048, 0],
    )
    processed, standard = _processed_capture(capture)
    radar = ValidCaptureRadar("complete raw response")
    processor = StubProcessor(capture, processed, standard)
    writer = MemoryWriter()

    outcome = capture_ops_spin.capture_once(
        radar=radar,
        processor=processor,
        writer=writer,
        capture_number=2,
        timeout=0.1,
        pre_trigger_segments=12,
        club=ClubType.IRON_7,
    )

    assert outcome == "processed"
    assert [record["type"] for record in writer.records] == [
        "ops_spin_raw_capture",
        "rolling_buffer_capture",
        "shot_detected",
    ]
    structured = writer.records[1]
    assert structured["trigger"] == "hardware"
    assert writer.records[0]["trigger"] == "hardware"
    assert structured["trigger_timestamp"] == 1_700_000_000.05
    assert structured["trigger_timestamp_source"] == "ops_clock_sync"
    assert structured["processing"]["spin"]["spin_rpm"] == 6420.0
    assert structured["envelope_spin"]["spin_rpm"] == 6300.0
    assert writer.records[2]["data"]["shot_number"] == 2
    assert writer.records[2]["data"]["spin_candidate_rpm"] == 6420.0
    assert radar.rearmed == [12]


def test_parser_defaults_match_production_sound_capture():
    args = capture_ops_spin.build_parser().parse_args([])

    assert capture_ops_spin.SAMPLE_RATE_KSPS == 30
    assert args.pre_trigger == 12
    assert args.club == "unknown"
    assert args.environment == "outdoor-range"


# --- software-triggered (S!) bench mode -------------------------------------


def _dump(samples: int = 4096, q_samples: int | None = None) -> str:
    """Return an OPS rolling-buffer response with the requested I/Q lengths."""
    q_count = samples if q_samples is None else q_samples
    i_values = [2048 + (index % 7) for index in range(samples)]
    q_values = [2048 - (index % 5) for index in range(q_count)]
    return (
        '{"sample_time": "964.003"}\r\n'
        '{"trigger_time": "964.105"}\r\n'
        f'{{"I": {json.dumps(i_values)}}}\r\n'
        f'{{"Q": {json.dumps(q_values)}}}\r\n'
    )


class FakeClock:
    """Monotonic clock advanced only by fake sleeps and fake dump transfers."""

    def __init__(self):
        self.now = 100.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        assert seconds >= 0
        self.sleeps.append(seconds)
        self.now += seconds


class SoftwareTriggerRadar:
    """OPS243 fake that answers each S! with a dump after ``dump_s`` seconds."""

    def __init__(self, clock, responses=None, dump_s=1.0):
        self.clock = clock
        self.responses = list(responses) if responses is not None else None
        self.dump_s = dump_s
        self.requests = []
        self.request_timeouts = []
        self.rearmed = []
        self.hardware_waits = 0

    def trigger_capture(self, timeout=None):
        self.requests.append(self.clock.now)
        self.request_timeouts.append(timeout)
        self.clock.now += self.dump_s
        if self.responses is None:
            return _dump()
        return self.responses.pop(0)

    def wait_for_hardware_trigger(self, timeout):
        self.hardware_waits += 1
        raise AssertionError("software mode must not wait for HOST_INT")

    def rearm_rolling_buffer(self, pre_trigger_segments):
        self.rearmed.append(pre_trigger_segments)

    def read_clock_sync(self, store=False):
        return None


BENCH_LABELS = {
    "ball_label": "25 mm foil patch",
    "rpm_label": "tach 3012",
    "notes": "2.0 m, dowel left-right",
}


def _software_loop(radar, clock, writer, *, max_captures, interval=3.0):
    return capture_ops_spin.software_capture_loop(
        radar=radar,
        processor=capture_ops_spin.RollingBufferProcessor(
            sample_rate=capture_ops_spin.SAMPLE_RATE_KSPS * 1000
        ),
        writer=writer,
        max_captures=max_captures,
        interval_s=interval,
        timeout=10.0,
        pre_trigger_segments=12,
        club=ClubType.UNKNOWN,
        sync_clock=False,
        labels=BENCH_LABELS,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )


def test_software_mode_issues_s_bang_at_the_interval_and_respects_n():
    clock = FakeClock()
    radar = SoftwareTriggerRadar(clock, dump_s=1.0)
    writer = MemoryWriter()

    counts = _software_loop(radar, clock, writer, max_captures=3, interval=3.0)

    assert radar.requests == [100.0, 103.0, 106.0]
    assert radar.request_timeouts == [10.0, 10.0, 10.0]
    assert radar.hardware_waits == 0
    assert radar.rearmed == [12, 12, 12]
    assert sum(counts.values()) == 3
    assert clock.sleeps == [2.0, 2.0]


def test_software_mode_waits_only_for_refill_when_a_dump_outlasts_the_interval():
    clock = FakeClock()
    radar = SoftwareTriggerRadar(clock, dump_s=4.5)
    writer = MemoryWriter()

    _software_loop(radar, clock, writer, max_captures=3, interval=3.0)

    refill = capture_ops_spin.SOFTWARE_REFILL_S
    assert refill == pytest.approx(0.35)
    assert radar.requests == pytest.approx([100.0, 104.5 + refill, 109.0 + 2 * refill])
    assert clock.sleeps == pytest.approx([refill, refill])


def test_software_records_carry_the_trigger_marker_labels_and_full_buffer():
    clock = FakeClock()
    radar = SoftwareTriggerRadar(clock)
    writer = MemoryWriter()

    _software_loop(radar, clock, writer, max_captures=1)

    by_type = {record["type"]: record for record in writer.records}
    raw = by_type["ops_spin_raw_capture"]
    structured = by_type["rolling_buffer_capture"]
    shot = by_type["shot_detected"]
    for record in (raw, structured):
        assert record["trigger"] == "software"
        assert record["labels"] == BENCH_LABELS
    assert shot["data"]["trigger"] == "software"
    assert raw["first_byte_timestamp"] is None
    assert raw["markers"]["trigger_velocity_detected"] is False
    assert len(structured["i_samples"]) == 4096
    assert len(structured["q_samples"]) == 4096
    assert structured["capture_number"] == 1


def test_software_mode_rejects_a_short_dump_but_keeps_the_raw_bytes():
    clock = FakeClock()
    short = _dump(samples=4000)
    radar = SoftwareTriggerRadar(clock, responses=[short, _dump(4096, q_samples=4095)])
    writer = MemoryWriter()

    counts = _software_loop(radar, clock, writer, max_captures=2)

    types = [record["type"] for record in writer.records]
    assert "rolling_buffer_capture" not in types
    raws = [record for record in writer.records if record["type"] == "ops_spin_raw_capture"]
    assert raws[0]["raw_response"] == short
    errors = [record for record in writer.records if record["type"] == "ops_spin_capture_error"]
    assert errors[0]["reason"] == "iq_length_invalid"
    assert errors[0]["i_samples"] == 4000
    assert errors[0]["q_samples"] == 4000
    assert errors[0]["expected_samples"] == 4096
    # A mismatched I/Q pair is already rejected by the processor's parser.
    assert errors[1]["reason"] == "parse_failed"
    assert counts == {"iq_length_invalid": 1, "parse_failed": 1}
    assert radar.rearmed == [12, 12]


def test_software_mode_counts_an_empty_response_as_an_attempt():
    clock = FakeClock()
    radar = SoftwareTriggerRadar(clock, responses=["", ""])
    writer = MemoryWriter()

    counts = _software_loop(radar, clock, writer, max_captures=2)

    assert len(radar.requests) == 2
    assert counts == {"timeout": 2}
    timeouts = [record for record in writer.records if record["type"] == "ops_spin_timeout"]
    assert [record["trigger"] for record in timeouts] == ["software", "software"]
    assert radar.rearmed == [12, 12]


def test_software_mode_flags_a_native_trigger_that_raced_the_request():
    clock = FakeClock()
    raced = '{"trigger_velocity_detected": 12.0}\r\n' + _dump()
    radar = SoftwareTriggerRadar(clock, responses=[raced])
    writer = MemoryWriter()

    _software_loop(radar, clock, writer, max_captures=1)

    raw = next(record for record in writer.records if record["type"] == "ops_spin_raw_capture")
    assert raw["markers"]["trigger_velocity_detected"] is True


def test_parser_software_trigger_defaults_and_labels():
    args = capture_ops_spin.build_parser().parse_args(
        ["--software-trigger", "-n", "10", "--rpm-label", "tach 2980"]
    )

    assert args.software_trigger is True
    assert args.interval == 3.0
    assert args.max_captures == 10
    assert args.rpm_label == "tach 2980"
    assert capture_ops_spin.resolve_timeout(args) == 10.0
    hardware = capture_ops_spin.build_parser().parse_args([])
    assert hardware.software_trigger is False
    assert capture_ops_spin.resolve_timeout(hardware) == 60.0


class SessionRadar(SoftwareTriggerRadar):
    """Fake for ``run_session``: the connect/metadata surface plus S! dumps."""

    clock = None
    instances = []

    def __init__(self, port=None, **_kwargs):
        super().__init__(SessionRadar.clock)
        self.port = "/dev/ttyACM0"
        self.baud = 115200
        self.bytes_per_second = 11520.0
        self.prepared = []
        SessionRadar.instances.append(self)

    def connect(self):
        return True

    def disconnect(self):
        return None

    def _send_command(self, command):
        return ""

    def get_info(self):
        return {}

    def get_serial_number(self):
        return "OPS-TEST"

    def get_current_units(self):
        return "mph"

    def get_speed_filter(self):
        return None

    def transfer_budget_s(self, floor):
        return floor

    def prepare_persisted_rolling_buffer(self, pre_trigger_segments, sample_rate_ksps):
        self.prepared.append((pre_trigger_segments, sample_rate_ksps))


def test_run_session_software_header_records_mode_and_labels(tmp_path, monkeypatch):
    clock = FakeClock()
    output = tmp_path / "bench.jsonl"
    args = capture_ops_spin.build_parser().parse_args(
        [
            "--software-trigger",
            "--interval",
            "2.5",
            "-n",
            "2",
            "--no-clock-sync",
            "--ball-label",
            "RCT",
            "--rpm-label",
            "tach 4010",
            "--notes",
            "1.0 m",
            "-o",
            str(output),
        ]
    )
    SessionRadar.clock = clock
    SessionRadar.instances = []
    monkeypatch.setattr(capture_ops_spin, "OPS243Radar", SessionRadar)
    monkeypatch.setattr(capture_ops_spin.time, "sleep", clock.sleep)
    monkeypatch.setattr(capture_ops_spin.time, "monotonic", clock.monotonic)

    assert capture_ops_spin.run_session(args) == 0

    records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    config = records[0]["config"]
    assert config["trigger"] == "software"
    assert config["software_interval_s"] == 2.5
    assert config["rpm_label"] == "tach 4010"
    assert config["ball_label"] == "RCT"
    assert config["notes"] == "1.0 m"
    assert config["timeout_s"] == 10.0
    radar = SessionRadar.instances[0]
    assert radar.requests == [100.0, 102.5]
    assert radar.hardware_waits == 0
    structured = [record for record in records if record["type"] == "rolling_buffer_capture"]
    assert [record["labels"]["rpm_label"] for record in structured] == ["tach 4010"] * 2
    assert records[-1]["type"] == "ops_spin_session_end"
    assert records[-1]["captures_attempted"] == 2

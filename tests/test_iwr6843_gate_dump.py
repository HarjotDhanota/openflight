"""Fork GATE schema and capture/logging tests using explicitly synthetic bytes."""

from __future__ import annotations

import json
import struct
import time

import numpy as np
import pytest

from openflight.iwr6843 import dump
from openflight.iwr6843.monitor import IWR6843CaptureMonitor
from openflight.session_logger import SessionLogger
from tests.iwr6843_firmware_fake import FakeClock, FirmwareModel
from tests.test_iwr6843_driver_timing import _radar
from tests.test_iwr6843_dump_evidence import TEMPERATURE, _evidence
from tests.test_iwr6843_monitor import FakeButton, FakeRadar

GATE = {
    "arm_seq": 3,
    "edge_seq": 0,
    "edge_tick": 0xFFFFFF00,
    "frame_seq": 101,
    "freeze_tick": 100,
    "ignored_edges": 2,
    "flags": 3,
}


def gate_dump(version=13, sample_fmt=4, **overrides):
    cube = np.full((4, 6, 4, 5), 2 + 3j)
    capture, frames, trailer = _evidence(4, 2)
    kwargs = {
        "version": version,
        "n_tx": 3,
        "sample_fmt": sample_fmt,
        "range_bin_starts": [20, 21, 22, 23],
        "range_bin_counts": [5, 4, 3, 2],
        "frame_time_offsets_us": [0, 3000, 6000, 12000],
        "temperature_report": TEMPERATURE if version == 12 else None,
        "capture_evidence": capture,
        "frame_evidence": frames,
        "dump_trailer": trailer,
        "gate_evidence": GATE,
    }
    kwargs.update(overrides)
    return dump.pack_dump(cube, **kwargs)


@pytest.mark.parametrize("version", [12, 13])
@pytest.mark.parametrize("sample_fmt", [4, 5])
def test_gate_dump_preserves_ticks_and_sizes_payload(version, sample_fmt):
    raw = gate_dump(version, sample_fmt)
    meta = dump.parse_header(raw)
    assert meta["header_nbytes"] + dump.payload_nbytes(meta, raw) == len(raw)
    meta, cube = dump.parse_dump(raw)
    assert meta["gate_evidence"] == GATE
    assert meta["capture_evidence"] == _evidence(4, 2)[0]
    assert meta["frame_evidence"] == _evidence(4, 2)[1]
    assert meta["temperature_report"] == (TEMPERATURE if version == 12 else None)
    assert meta["frame_time_source"] == "planned_descriptor"
    assert np.all(cube[0] == 2 + 3j)
    assert np.all(cube[3, ..., 2:] == 0)


def test_parser_accepts_independently_encoded_gate_record():
    old = gate_dump(version=9, gate_evidence=None)
    offset = dump.HEADER.size + 4 * dump.TIMED_FRAME_DESCRIPTOR.size + 40
    expected = b"ILG1" + struct.pack("<7I", 3, 0, 0xFFFFFF00, 101, 100, 2, 3)
    raw = bytearray(old[:offset] + expected + old[offset:])
    struct.pack_into("<H", raw, 4, 13)
    assert dump.parse_dump(raw)[0]["gate_evidence"] == GATE


def test_serial_reader_returns_exactly_the_gate_payload_and_waits_for_rearm(monkeypatch):
    clock = FakeClock()
    raw = gate_dump()
    firmware = FirmwareModel(raw, clock, restart_s=1.0)
    firmware.active = True
    radar = _radar(monkeypatch, firmware, clock)
    try:
        received, timing = radar.read_dump_timed()
        assert received == raw
        assert timing.cli_ready_ns - timing.last_byte_ns >= 1_000_000_000
        assert dump.parse_dump(received)[0]["gate_evidence"] == GATE
    finally:
        radar.close()


@pytest.mark.parametrize("flags", [0, 1, 2, 7])
def test_incomplete_or_unknown_gate_flags_are_refused(flags):
    with pytest.raises(ValueError, match="latched edge and completed freeze"):
        gate_dump(gate_evidence={**GATE, "flags": flags})
    raw = bytearray(gate_dump())
    offset = dump.HEADER.size + 16 + 40
    struct.pack_into("<I", raw, offset + 28, flags)
    with pytest.raises(ValueError, match="latched edge and completed freeze"):
        dump.parse_dump(raw)


def test_bad_magic_missing_and_truncated_evidence_are_refused():
    raw = bytearray(gate_dump())
    offset = dump.HEADER.size + 16 + 40
    raw[offset : offset + 4] = b"BAD!"
    with pytest.raises(ValueError, match="GATE evidence magic"):
        dump.parse_dump(raw)
    with pytest.raises(ValueError, match="GATE dump needs"):
        gate_dump(gate_evidence=None)
    with pytest.raises(ValueError):
        dump.parse_dump(gate_dump()[: offset + 31])
    with pytest.raises(ValueError, match="version 12 or 13"):
        gate_dump(version=9)


@pytest.mark.parametrize("version", [10, 11, 14])
def test_divergent_and_future_versions_still_fail_closed(version):
    raw = bytearray(gate_dump())
    struct.pack_into("<H", raw, 4, version)
    with pytest.raises(ValueError, match=f"unsupported dump version {version}"):
        dump.parse_header(raw)


def test_monitor_keeps_both_clock_observations_and_raw_capture_in_session(tmp_path):
    config = tmp_path / "gate.cfg"
    config.write_text("gateCfg 1\nsensorStart\n", encoding="utf-8")
    raw = gate_dump()
    monitor = IWR6843CaptureMonitor(
        config_path=config,
        output_dir=tmp_path / "dumps",
        radar=FakeRadar(raw),
        button_factory=FakeButton,
        save_dumps=True,
    )
    monitor.start()
    try:
        edge = time.time()
        assert monitor.notify_trigger(edge)
        capture = monitor.capture_for_shot(edge, timeout_s=2)
        assert capture is not None and capture.valid
        assert capture.hardware_gate_evidence == GATE
        assert capture.gate_event.observation.observer_wall_s == edge
        assert capture.path.read_bytes() == raw
        assert capture.gate_event.to_dict()["physical_edge_uncertainty_ns"] is None
        logger = SessionLogger(log_dir=tmp_path / "logs", enabled=True)
        logger.start_session(mode="rolling-buffer", trigger_type="sound")
        logger.log_iwr6843_capture(
            shot_number=1,
            shot_timestamp=edge,
            trigger_timestamp=edge,
            capture_path=str(capture.path),
            capture_bytes=len(raw),
            dump_duration_s=0.1,
            capture_error=None,
            ball_speed_mph=90,
            gate_event=capture.gate_event.to_dict(),
            hardware_gate_evidence=capture.hardware_gate_evidence,
        )
        entry = json.loads(logger.session_path.read_text().splitlines()[-1])
        assert entry["hardware_gate_evidence"] == GATE
        assert entry["gate_event"]["event_id"] == capture.gate_event.event_id
        logger.end_session()
    finally:
        monitor.stop()

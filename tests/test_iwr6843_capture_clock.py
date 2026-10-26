"""Synthetic capture/runtime linkage; no physical clock or RF calibration."""

import base64
import copy
import hashlib
import struct
import zlib

import numpy as np
import pytest

from openflight.iwr6843.capture_clock import audit_capture_clock, record_capture_clock
from openflight.iwr6843.compact_clock import MAGIC, PROMPT, CompactExchangeError
from openflight.iwr6843.dump import _repack_kwargs, pack_dump, parse_dump
from openflight.iwr6843.uart_clock_bounds import UARTAssumptions

BUILD = "a" * 64
BOOT = "0000000000000001"
RATE = (200_000_000, 200_000_000)
MASK = 2**32 - 1


class Radar:
    def __init__(self, base=0):
        self.time = 1_000_000_000
        self.base = base
        self.seq = 100
        self.commands = []
        self.pending = b""
        self.ser = self

    def now(self):
        return self.time

    def tick(self):
        return (self.base + (self.time - 1_000_000_000) // 5) & MASK

    def cmd(self, command, _window):
        self.commands.append(command)
        _, hi, lo, seq = command.split()
        self.time += 300_000
        rx = self.tick()
        self.time += 2_000
        tx = self.tick()
        self.time += 700_000
        return (
            f"OFCLKPROBE1 build={BUILD} request={hi}{lo} boot={BOOT} "
            f"seq={int(seq, 16)} tick_hz={RATE[0]} counter_bits=32\n"
            f"OFCLKSTAMP1 rx_tick={rx} tx_tick={tx} frame_seq={self.seq} physical=unqualified\n"
            f"OFCLKPROBEEND1 request={hi}{lo} seq={int(seq, 16)}\nDone\nl3dump:/>"
        )

    def write(self, command):
        self.commands.append(command.decode().strip())
        _, hi, lo, seq = command.decode().split()
        self.time += 300_000
        rx = self.tick()
        self.time += 2_000
        data = struct.pack(
            "<4s9I",
            MAGIC,
            int(hi, 16),
            int(lo, 16),
            0,
            1,
            int(seq, 16),
            rx,
            self.tick(),
            RATE[0],
            self.seq,
        )
        self.pending = data + struct.pack("<I", zlib.crc32(data)) + b"Done\n" + PROMPT
        return len(command)

    @property
    def in_waiting(self):
        return len(self.pending)

    def read(self, _count):
        self.time += 440_000
        data, self.pending = self.pending, b""
        return data

    def acquire(self, *, version=9, unassociated=False):
        frames = []
        self.time += 1_000_000
        for _ in range(4):
            self.seq = (self.seq + 1) & MASK
            frames.append(
                dict(
                    seq=self.seq,
                    start_tick=self.tick(),
                    done_tick=(self.tick() + 1000) & MASK,
                    associated=not unassociated,
                    iq8_scanned=False,
                    iq8_overflow=None,
                    iq8_max_abs=None,
                )
            )
            self.time += 3_000_000
        capture = dict(
            tick_hz=RATE[0],
            request_tick=(frames[2]["start_tick"] - 1000) & MASK,
            request_seq=frames[1]["seq"],
            freeze_tick=self.tick(),
            stop_tick=(self.tick() + 1000) & MASK,
            header_tick=(self.tick() + 2000) & MASK,
            hwa_missed_total=0,
            pre_frames=2,
            post_frames=2,
            flags=0,
        )
        kwargs = {}
        if version == 9:
            kwargs = dict(
                capture_evidence=capture,
                frame_evidence=frames,
                dump_trailer={"payload_end_tick": (self.tick() + 3000) & MASK},
            )
        raw = pack_dump(
            np.ones((4, 6, 4, 5), dtype=complex),
            n_tx=3,
            version=version,
            sample_fmt=4,
            frame_period_us=3000,
            range_bin_starts=[20] * 4,
            range_bin_counts=[5] * 4,
            frame_time_offsets_us=[0, 3000, 6000, 9000],
            **kwargs,
        )
        self.time += 10_000_000
        return raw


def collect(radar=None, **kwargs):
    radar = radar or Radar()
    rows, payloads = [], []
    nonces = iter(f"{n:016x}" for n in range(2, 6))
    record_capture_clock(
        radar,
        lambda r: r.acquire(**kwargs),
        rows.append,
        payloads.append,
        now=radar.now,
        nonce=lambda: next(nonces),
        boot=lambda: "synthetic-host",
    )
    return rows, payloads[0], radar


def audit(rows, raw, **kwargs):
    return audit_capture_clock(
        rows, raw, rate_hz_bounds=RATE, max_distance_ns=1_000_000_000, **kwargs
    )


@pytest.mark.parametrize("base", [0, MASK - 1_000_000])
@pytest.mark.parametrize("uart", [False, True])
def test_saved_capture_is_bound_to_same_runtime_and_all_frame_counters(base, uart):
    rows, raw, radar = collect(Radar(base))
    contract = UARTAssumptions(BUILD, 1_000_000, 1_000_000, "8N1", "synthetic") if uart else None
    result = audit(rows, raw, uart_assumptions=contract)
    assert result["status"] == "conditional_capture_counter_link"
    assert result["physical_timing_qualified"] is False
    assert result["rf_acquisition_interval_ns"] is None
    assert result["wire_timing_qualified"] is False
    assert len(result["frames"]) == 4
    for frame in result["frames"]:
        for name in ("start", "done"):
            tick = frame[f"{name}_tick"]
            truth = 1_000_000_000 + ((tick - base) & MASK) * 5
            low, high = frame[f"{name}_counter"]["conditional_mono_interval_ns"]
            assert low <= truth <= high
            assert frame[f"{name}_counter"]["probe_count"] == 2
    assert len(radar.commands) == 4


@pytest.mark.parametrize(
    "change",
    [
        "hash",
        "append",
        "truncate",
        "host",
        "connection",
        "runtime",
        "ordering",
        "missing",
        "duplicate",
        "failed",
        "identity",
    ],
)
def test_incomplete_changed_or_mismatched_evidence_is_unavailable(change):
    rows, raw, _ = collect()
    if change == "hash":
        raw = raw[:-10] + bytes([raw[-10] ^ 1]) + raw[-9:]
    elif change == "append":
        raw += b"extra"
    elif change == "truncate":
        raw = raw[:-1]
    elif change in ("host", "connection"):
        rows[-1]["boot_id" if change == "host" else "connection_id"] = "different"
    elif change == "runtime":
        rows[-2]["reply_text"] = rows[-2]["reply_text"].replace(
            "boot=" + BOOT, "boot=0000000000000009"
        )
    elif change == "ordering":
        rows[3]["mono_ns"] = rows[2]["before_ns"]
    elif change == "missing":
        del rows[4]
    elif change == "duplicate":
        rows.insert(4, copy.deepcopy(rows[4]))
    elif change == "failed":
        rows[-1]["error"] = "RuntimeError"
    else:
        rows[0]["capture_id"] = "not-an-id"
    result = audit(rows, raw)
    assert result["status"] == "unavailable"
    assert result["frames"] == []
    assert result["reason"]


@pytest.mark.parametrize("kwargs", [dict(version=6), dict(unassociated=True)])
def test_missing_or_unassociated_frame_evidence_does_not_get_invented(kwargs):
    rows, raw, _ = collect(**kwargs)
    assert audit(rows, raw)["status"] == "unavailable"


def test_independent_host_span_must_bound_proximity():
    rows, raw, _ = collect()
    result = audit_capture_clock(rows, raw, rate_hz_bounds=RATE, max_distance_ns=10_000_000)
    assert result["status"] == "unavailable"
    assert "proximity" in result["reason"]


def rewrite_packet(row, word, value):
    data = bytearray(base64.b64decode(row["received_b64"]))
    struct.pack_into("<I", data, 4 + 4 * word, value)
    struct.pack_into("<I", data, 40, zlib.crc32(data[:40]))
    row["received_b64"] = base64.b64encode(data).decode()


def test_stale_ring_from_before_pre_probe_is_rejected():
    rows, raw, _ = collect()
    rewrite_packet(rows[2], 8, 102)
    result = audit(rows, raw)
    assert result["status"] == "unavailable"
    assert "sequence" in result["reason"]


def test_frame_sequence_wrap_is_distinct_from_a_runtime_restart():
    radar = Radar()
    radar.seq = MASK - 2
    rows, raw, _ = collect(radar)
    result = audit(rows, raw)
    assert result["status"] == "conditional_capture_counter_link"
    assert [frame["seq"] for frame in result["frames"]] == [MASK - 1, MASK, 0, 1]


def test_frame_ticks_outside_observed_probe_span_are_rejected():
    rows, raw, _ = collect()
    rewrite_packet(rows[5], 5, 150_000)
    rewrite_packet(rows[5], 6, 150_400)
    result = audit(rows, raw)
    assert result["status"] == "unavailable"


@pytest.mark.parametrize("change", ["rate", "lifecycle", "request_sequence", "trailer"])
def test_payload_binding_alone_does_not_accept_inconsistent_acquisition(change):
    rows, raw, _ = collect()
    meta, cube = parse_dump(raw)
    if change == "rate":
        meta["capture_evidence"]["tick_hz"] = 100_000_000
    elif change == "lifecycle":
        meta["capture_evidence"]["header_tick"] += 100_000_000
        meta["dump_trailer"]["payload_end_tick"] += 100_000_000
    elif change == "request_sequence":
        meta["capture_evidence"]["request_seq"] = 10
    else:
        meta["dump_trailer"]["payload_end_tick"] += 100_000_000
    changed = pack_dump(cube, n_tx=3, **_repack_kwargs(meta))
    rows[4].update(payload_sha256=hashlib.sha256(changed).hexdigest(), payload_bytes=len(changed))
    result = audit(rows, changed)
    assert result["status"] == "unavailable"
    assert result["frames"] == []


def test_long_hold_cannot_silently_invent_counter_wrap_count():
    rows, raw, _ = collect()
    for row in rows[5:]:
        for key in (
            "before_ns",
            "after_ns",
            "write_return_ns",
            "packet_complete_ns",
            "cli_ready_ns",
            "mono_ns",
        ):
            if key in row:
                row[key] += 22_000_000_000
        for read in row.get("reads", []):
            read["received_ns"] += 22_000_000_000
    result = audit_capture_clock(rows, raw, rate_hz_bounds=RATE, max_distance_ns=30_000_000_000)
    assert result["status"] == "unavailable"
    assert "wrap" in result["reason"]


def test_acquisition_failure_retains_partial_bytes_and_marks_incomplete():
    radar = Radar()
    rows, saved = [], []
    nonces = iter(f"{n:016x}" for n in range(2, 6))

    def failed(_radar):
        error = RuntimeError("synthetic failure")
        error.raw = b"partial"
        raise error

    with pytest.raises(RuntimeError):
        record_capture_clock(
            radar,
            failed,
            rows.append,
            saved.append,
            now=radar.now,
            nonce=lambda: next(nonces),
            boot=lambda: "synthetic-host",
        )
    assert saved == [b"partial"]
    assert rows[-1]["error"] == "RuntimeError"
    assert rows[-2]["type"] == "capture_failed"
    assert len(radar.commands) == 2
    assert audit(rows, saved[0])["status"] == "unavailable"


def test_payload_is_saved_before_a_post_probe_failure():
    radar = Radar()
    original = radar.write

    def write(command):
        if command.endswith(b"00000002\n"):
            raise OSError("synthetic UART failure")
        return original(command)

    radar.write = write
    rows, payloads = [], []
    with pytest.raises(CompactExchangeError):
        record_capture_clock(
            radar,
            lambda r: r.acquire(),
            rows.append,
            payloads.append,
            now=radar.now,
            boot=lambda: "synthetic-host",
        )
    assert len(payloads) == 1
    assert rows[-1]["error"] == "CompactExchangeError"
    assert rows[-2]["error"] == "OSError"
    assert audit(rows, payloads[0])["status"] == "unavailable"

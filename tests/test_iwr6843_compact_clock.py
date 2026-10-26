"""Synthetic serial/clock known answers, not measured USB timing performance."""

import base64
import struct
import zlib
from types import SimpleNamespace

import pytest

from openflight.iwr6843.compact_clock import (
    MAGIC,
    PACKET,
    PROMPT,
    CompactExchangeError,
    compact_exchange,
    compact_recording_observations,
    parse_compact,
    record_compact_probes,
)
from tests.test_iwr6843_clock_evidence import BOOT, BUILD, reply_text
from tests.test_iwr_clock_probe_cli import cli


def packet(request="0000000000000002", seq=1, boot=BOOT, rx=2000, tx=2400, hz=200_000_000):
    data = struct.pack(
        "<4s9I",
        MAGIC,
        int(request[:8], 16),
        int(request[8:], 16),
        int(boot[:8], 16),
        int(boot[8:], 16),
        seq,
        rx,
        tx,
        hz,
        23,
    )
    return data + struct.pack("<I", zlib.crc32(data))


class SyntheticClock:
    def __init__(self):
        self.value = 1_000_000_000

    def now(self):
        return self.value

    def advance(self, value):
        self.value += value


class SyntheticPort:
    def __init__(self, clock, transform=None, fragmented=False):
        self.clock = clock
        self.transform = transform
        self.fragmented = fragmented
        self.chunks = []
        self.commands = []

    def write(self, command):
        self.commands.append(command)
        _, hi, lo, seq = command.decode().split()
        value = packet(
            hi + lo, int(seq, 16), rx=2000 + int(seq, 16) * 1000, tx=2400 + int(seq, 16) * 1000
        )
        chunks = [(400_000, command + value), (5_000_000, b"Done\n" + PROMPT)]
        if self.fragmented:
            raw = command + value + b"Done\n" + PROMPT
            chunks = [(1000, bytes([byte])) for byte in raw]
        self.chunks = self.transform(chunks) if self.transform else chunks
        self.clock.advance(200_000)
        return len(command)

    @property
    def in_waiting(self):
        return len(self.chunks[0][1]) if self.chunks else 0

    def read(self, count):
        if not self.chunks:
            self.clock.advance(1_000_000)
            return b""
        delay, value = self.chunks.pop(0)
        self.clock.advance(delay)
        assert count >= len(value)
        return value


class SyntheticRadar:
    def __init__(self, transform=None, fragmented=False):
        self.clock = SyntheticClock()
        self.ser = SyntheticPort(self.clock, transform, fragmented)
        self.commands = []

    def cmd(self, command, window):
        self.commands.append((command, window))
        _, hi, lo, seq = command.split()
        self.clock.advance(4_000_000)
        return reply_text(hi + lo, int(seq, 16))


def collect(radar=None, count=2):
    radar = radar or SyntheticRadar()
    rows = []
    nonces = iter(f"{index + 1:016x}" for index in range(count + 2))
    record_compact_probes(
        radar,
        rows.append,
        count=count,
        interval_s=0,
        now=radar.clock.now,
        sleep=lambda _: None,
        nonce=lambda: next(nonces),
        boot=lambda: "synthetic-host",
    )
    return rows, radar


def exchange(radar, **kwargs):
    return compact_exchange(
        radar, "0000000000000002", 1, build=BUILD, boot=BOOT, now=radar.clock.now, **kwargs
    )


def test_compact_packet_layout_and_all_single_bit_corruptions():
    value = packet()
    assert PACKET.size == len(value) == 44
    parsed = parse_compact(value, "0000000000000002", 1, build=BUILD, boot=BOOT)
    assert (parsed.rx_tick, parsed.tx_tick, parsed.boot, parsed.tick_hz) == (
        2000,
        2400,
        BOOT,
        200_000_000,
    )
    for bit in range(len(value) * 8):
        corrupt = bytearray(value)
        corrupt[bit // 8] ^= 1 << (bit % 8)
        with pytest.raises(ValueError):
            parse_compact(bytes(corrupt), "0000000000000002", 1, build=BUILD, boot=BOOT)


@pytest.mark.parametrize(
    "kwargs", [{"request": BOOT}, {"seq": 2}, {"boot": "0000000000000003"}, {"hz": 0}]
)
def test_valid_crc_does_not_hide_wrong_identity_or_rate(kwargs):
    with pytest.raises(ValueError):
        parse_compact(packet(**kwargs), "0000000000000002", 1, build=BUILD, boot=BOOT)


def test_counter_wrap_is_retained_without_inventing_an_epoch():
    parsed = parse_compact(
        packet(rx=2**32 - 10, tx=200), "0000000000000002", 1, build=BUILD, boot=BOOT
    )
    assert parsed.rx_tick == 2**32 - 10 and parsed.tx_tick == 200


def test_packet_read_is_timestamped_before_later_cli_text():
    radar = SyntheticRadar()
    result = exchange(radar)
    assert result["before_ns"] == 1_000_000_000
    assert result["write_return_ns"] == 1_000_200_000
    assert result["packet_complete_ns"] == 1_000_600_000
    assert result["cli_ready_ns"] == result["after_ns"] == 1_005_600_000
    assert base64.b64decode(result["received_b64"]).endswith(b"Done\n" + PROMPT)
    assert radar.ser.commands == [b"cp 00000000 00000002 00000001\n"]


@pytest.mark.parametrize("echo", [False, True])
def test_complete_recording_accepts_optional_uart_command_echo(echo):
    def transform(chunks):
        if echo:
            return chunks
        delay, data = chunks[0]
        command, newline, packet_bytes = data.partition(b"\n")
        assert newline and command.startswith(b"cp ")
        return [(delay, packet_bytes), *chunks[1:]]

    rows, _radar = collect(SyntheticRadar(transform=transform), count=4)
    observations = compact_recording_observations(rows)
    assert len(observations) == 4
    assert all(
        row["packet_offset"] == (30 if echo else 0) for row in rows if row["type"] == "compact"
    )


def test_fragmented_magic_packet_and_prompt_are_read_without_losing_boundary():
    radar = SyntheticRadar(fragmented=True)
    result = exchange(radar)
    assert result["packet_complete_ns"] < result["cli_ready_ns"]
    assert len(result["reads"]) == len(base64.b64decode(result["received_b64"]))


def test_coalesced_packet_and_trailer_keep_the_same_late_read_bound():
    radar = SyntheticRadar(
        transform=lambda chunks: [(6_000_000, b"".join(value for _, value in chunks))]
    )
    result = exchange(radar)
    assert result["packet_complete_ns"] == result["cli_ready_ns"]
    assert result["packet_complete_ns"] - result["before_ns"] == 6_200_000


@pytest.mark.parametrize(
    "mode",
    ["crc", "prefix", "wrong_nonce", "duplicate", "missing_prompt", "error", "truncated", "limit"],
)
def test_bad_exchange_retains_raw_bytes_and_read_marks(mode):
    def transform(chunks):
        first = chunks[0][1]
        if mode == "crc":
            first = first[:-1] + bytes([first[-1] ^ 1])
        elif mode == "prefix":
            first = b"unrelated capture\n" + first
        elif mode == "wrong_nonce":
            first = packet(request=BOOT)
        elif mode == "duplicate":
            first += packet()
        elif mode == "truncated":
            first = first[:-1]
            return [(400_000, first)]
        elif mode == "limit":
            return [(400_000, b"x" * 4096)]
        if mode == "missing_prompt":
            return [(400_000, first), (1_000_000, b"Done\n")]
        if mode == "error":
            return [(400_000, first), (1_000_000, b"Error -1\n" + PROMPT)]
        return [(400_000, first), chunks[1]]

    radar = SyntheticRadar(transform=transform)
    with pytest.raises(CompactExchangeError) as raised:
        exchange(radar, timeout_ns=20_000_000)
    row = raised.value.evidence
    assert row["error"] and row["received_b64"] and row["reads"]
    assert row["cli_ready_ns"] is None


def test_partial_write_and_serial_failure_do_not_return_a_timing_result():
    radar = SyntheticRadar()
    radar.ser.write = lambda _: 1
    with pytest.raises(CompactExchangeError) as raised:
        exchange(radar)
    assert raised.value.evidence["packet_complete_ns"] is None
    radar = SyntheticRadar()

    def fail(_):
        raise OSError("synthetic serial failure")

    radar.ser.read = fail
    with pytest.raises(CompactExchangeError) as raised:
        exchange(radar)
    assert raised.value.evidence["error"] == "OSError"


def test_collector_surrounds_compact_probes_with_context_and_requires_cli_ready():
    rows, radar = collect()
    assert [row["type"] for row in rows] == [
        "start",
        "preflight",
        "compact",
        "compact",
        "postflight",
        "end",
    ]
    assert len(radar.commands) == 2 and len(radar.ser.commands) == 2
    obs = compact_recording_observations(rows)
    assert len(obs) == 2
    assert obs[0].after_ns == rows[2]["packet_complete_ns"]
    assert obs[0].cli_after_ns == rows[2]["cli_ready_ns"]
    assert obs[1].before_ns >= obs[0].cli_after_ns


@pytest.mark.parametrize(
    "change",
    [
        "end_missing",
        "end_error",
        "host_changed",
        "postflight_missing",
        "postflight_reset",
        "packet_crc",
        "complete_early",
        "complete_float",
        "read_size",
        "read_time",
        "bytes_missing",
        "bad_base64",
        "cli_ready",
        "missing_reads",
        "nonce_reuse",
        "sequence",
        "row_error",
        "write_early",
        "rate_changed",
        "extra_packet",
    ],
)
def test_offline_audit_refuses_tampering_and_missing_evidence(change):
    rows, _ = collect()
    row = rows[2]
    if change == "end_missing":
        rows.pop()
    elif change == "end_error":
        rows[-1]["error"] = "OSError"
    elif change == "host_changed":
        rows[-1]["boot_id"] = "new-boot"
    elif change == "postflight_missing":
        rows.pop(-2)
    elif change == "postflight_reset":
        rows[-2]["reply_text"] = rows[-2]["reply_text"].replace(
            f"boot={BOOT}", "boot=0000000000000010"
        )
    elif change in ("packet_crc", "rate_changed", "extra_packet"):
        raw = bytearray(base64.b64decode(row["received_b64"]))
        if change == "packet_crc":
            raw[row["packet_offset"] + 4] ^= 1
        elif change == "rate_changed":
            raw[row["packet_offset"] : row["packet_offset"] + 44] = packet(
                hz=199_000_000, rx=3000, tx=3400
            )
        else:
            raw.extend(packet())
        row["received_b64"] = base64.b64encode(raw).decode()
    elif change == "complete_early":
        row["packet_complete_ns"] = row["before_ns"]
    elif change == "complete_float":
        row["packet_complete_ns"] = float(row["packet_complete_ns"])
    elif change == "read_size":
        row["reads"][0]["bytes_total"] -= 1
    elif change == "read_time":
        row["reads"][0]["received_ns"] = row["before_ns"] - 1
    elif change == "bytes_missing":
        row.pop("received_b64")
    elif change == "bad_base64":
        row["received_b64"] = "###"
    elif change == "cli_ready":
        row["cli_ready_ns"] -= 1
    elif change == "missing_reads":
        row["reads"] = []
    elif change == "nonce_reuse":
        rows[-2]["request"] = rows[1]["request"]
    elif change == "sequence":
        row["seq"] = 3
    elif change == "row_error":
        row["error"] = "TimeoutError"
    else:
        row["write_return_ns"] = row["before_ns"] - 1
    result = cli.audit(
        rows, rate_hz_bounds=(200_000_000, 200_000_000), max_distance_ns=1_000_000_000
    )
    assert result["status"] == "unavailable"
    assert result["target_mapping"] is None and result["physical_timing_qualified"] is False


def test_audit_keeps_whole_command_round_trip_separate_from_counter_bracket():
    rows, _ = collect()
    result = cli.audit(
        rows, rate_hz_bounds=(200_000_000, 200_000_000), max_distance_ns=1_000_000_000
    )
    assert result["status"] == "conditional_software_counter_only"
    first = result["probe_envelopes"][0]
    assert first["host_round_trip_ns"] == 5_600_000
    assert first["counter_bracket_width_ns"] == 600_000
    assert first["interval_width_ns"] == 598_000
    assert result["rf_acquisition_interval_ns"] is None


def test_failed_compact_attempt_is_emitted_before_end_and_never_retried():
    radar = SyntheticRadar(transform=lambda chunks: [(400_000, packet(request=BOOT))])
    rows = []
    nonces = iter([BOOT, "0000000000000002", "0000000000000003"])
    with pytest.raises(CompactExchangeError):
        record_compact_probes(
            radar,
            rows.append,
            count=2,
            interval_s=0,
            now=radar.clock.now,
            nonce=lambda: next(nonces),
            boot=lambda: "host-test",
        )
    assert [row["type"] for row in rows] == ["start", "preflight", "compact", "end"]
    assert rows[2]["error"] and rows[-1]["error"]
    assert len(radar.ser.commands) == 1


def test_cli_selects_compact_collection(monkeypatch, tmp_path):
    calls = []
    radar = SimpleNamespace(__enter__=lambda _: None)

    class Owner:
        def __enter__(self):
            return radar

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(cli, "IWR6843Radar", lambda **_: Owner())
    monkeypatch.setattr(
        cli, "record_compact_probes", lambda *args, **kwargs: calls.append((args, kwargs))
    )
    assert (
        cli.main(
            [
                "record",
                "--port",
                "synthetic-port",
                "--compact",
                "--count",
                "2",
                "--output",
                str(tmp_path / "record.jsonl"),
            ]
        )
        == 0
    )
    assert calls[0][0][0] is radar and calls[0][1]["count"] == 2

"""Research-only compact counter exchange; no RF or GPIO configuration changes."""

from __future__ import annotations

import base64
import binascii
import math
import secrets
import struct
import time
import uuid
import zlib

from openflight.iwr6843.clock_evidence import ProbeObservation, ProbeReply, _hex, _uint, parse_probe
from openflight.ops_capture_evidence import boot_identity

PACKET = struct.Struct("<4s10I")
MAGIC = b"OFC2"
PROMPT = b"l3dump:/>"
SCHEMA = "openflight.iwr-clock-compact.v1"
MAX_BYTES = 4096


def compact_command(request, seq):
    """Use the existing CLI tokenizer with a fresh nonzero nonce and uint32 sequence."""
    _hex(request, 16)
    _uint(seq)
    return f"cp {request[:8]} {request[8:]} {seq:08x}\n".encode("ascii")


def parse_compact(packet, request, seq, *, build, boot):
    """Check the entire fixed-size CRC-protected packet against observed context."""
    compact_command(request, seq)
    _hex(build, 64)
    _hex(boot, 16)
    if not isinstance(packet, bytes) or len(packet) != PACKET.size:
        raise ValueError("incomplete compact packet")
    magic, hi, lo, boot_hi, boot_lo, sequence, rx, tx, hz, frame, crc = PACKET.unpack(packet)
    if magic != MAGIC or zlib.crc32(packet[:-4]) != crc:
        raise ValueError("compact packet magic or CRC mismatch")
    if f"{hi:08x}{lo:08x}" != request or sequence != seq or f"{boot_hi:08x}{boot_lo:08x}" != boot:
        raise ValueError("compact request or runtime mismatch")
    if hz == 0:
        raise ValueError("zero configured tick rate")
    return ProbeReply(build, request, boot, seq, rx, tx, hz, frame)


def _prefix_valid(prefix, command):
    value = prefix.replace(b"\r\n", b"\n").strip()
    if value.startswith(PROMPT):
        value = value[len(PROMPT) :].strip()
    return value in (b"", command.strip())


class CompactExchangeError(RuntimeError):
    """A failed attempt retains received bytes, read marks and incomplete context."""

    def __init__(self, evidence, reason):
        super().__init__(reason)
        self.evidence = evidence


def compact_exchange(
    radar, request, seq, *, build, boot, now=time.monotonic_ns, timeout_ns=2_000_000_000
):
    """Timestamp the read completing the packet; consume CLI readiness separately."""
    command = compact_command(request, seq)
    _hex(build, 64)
    _hex(boot, 16)
    _uint(timeout_ns, 63)
    if timeout_ns == 0:
        raise ValueError("positive timeout required")
    data = bytearray()
    evidence = {
        "type": "compact",
        "request": request,
        "seq": seq,
        "before_ns": now(),
        "write_return_ns": None,
        "packet_complete_ns": None,
        "cli_ready_ns": None,
        "after_ns": None,
        "packet_offset": None,
        "received_b64": "",
        "reads": [],
        "error": None,
    }
    try:
        written = radar.ser.write(command)
        evidence["write_return_ns"] = now()
        if type(written) is not int or written != len(command):
            raise ValueError("partial command write")
        deadline = evidence["before_ns"] + timeout_ns
        while now() < deadline:
            waiting = radar.ser.in_waiting
            chunk = radar.ser.read(min(max(1, waiting), MAX_BYTES - len(data)))
            received = now()
            if not chunk:
                continue
            data.extend(chunk)
            evidence["reads"].append({"received_ns": received, "bytes_total": len(data)})
            offset = data.find(MAGIC)
            if offset >= 0 and len(data) >= offset + PACKET.size:
                if evidence["packet_complete_ns"] is None:
                    if not _prefix_valid(bytes(data[:offset]), command):
                        raise ValueError("unexpected data before compact packet")
                    packet = bytes(data[offset : offset + PACKET.size])
                    parse_compact(packet, request, seq, build=build, boot=boot)
                    evidence["packet_complete_ns"] = received
                    evidence["packet_offset"] = offset
                suffix = bytes(data[offset + PACKET.size :]).replace(b"\r\n", b"\n").lstrip(b"\n")
                if suffix == b"Done\n" + PROMPT:
                    evidence["cli_ready_ns"] = received
                    evidence["after_ns"] = received
                    evidence["received_b64"] = base64.b64encode(data).decode("ascii")
                    return evidence
                if b"Error" in suffix or len(suffix) > len(b"Done\n" + PROMPT) + 2:
                    raise ValueError("unexpected compact CLI trailer")
            if len(data) >= MAX_BYTES:
                raise ValueError("compact response limit exceeded")
        raise TimeoutError("compact exchange timed out")
    except (OSError, ValueError, TimeoutError) as error:
        evidence["after_ns"] = now()
        evidence["received_b64"] = base64.b64encode(data).decode("ascii")
        evidence["error"] = type(error).__name__
        raise CompactExchangeError(evidence, str(error)) from error


def record_compact_probes(
    radar,
    emit,
    *,
    count,
    interval_s,
    now=time.monotonic_ns,
    sleep=time.sleep,
    nonce=lambda: secrets.token_hex(8),
    boot=boot_identity,
):
    """Bracket compact probes by verbose identity checks on the same serial owner."""
    if type(count) is not int or not 1 <= count <= 1000:
        raise ValueError("count must be between 1 and 1000")
    if (
        not isinstance(interval_s, (int, float))
        or not math.isfinite(interval_s)
        or not 0 <= interval_s <= 10
    ):
        raise ValueError("invalid probe interval")
    host = boot()
    if not isinstance(host, str) or not host:
        raise ValueError("host boot identity unavailable")
    connection = uuid.uuid4().hex
    emit(
        {
            "type": "start",
            "schema": SCHEMA,
            "event_clock": "host_monotonic_ns",
            "boot_id": host,
            "connection_id": connection,
            "requested_count": count,
            "mono_ns": now(),
        }
    )
    seen = set()
    attempted = 0
    failure = None

    def fresh():
        value = _hex(nonce(), 16)
        if value in seen:
            raise ValueError("reused request nonce")
        seen.add(value)
        return value

    def context(kind, seq):
        request = fresh()
        before = now()
        reply = radar.cmd(f"clockProbe {request[:8]} {request[8:]} {seq:08x}", 2.0)
        after = now()
        emit(
            {
                "type": kind,
                "request": request,
                "seq": seq,
                "before_ns": before,
                "after_ns": after,
                "reply_text": reply,
            }
        )
        return parse_probe(reply, request, seq)

    try:
        initial = context("preflight", 0)
        for seq in range(1, count + 1):
            request = fresh()
            attempted += 1
            try:
                evidence = compact_exchange(
                    radar, request, seq, build=initial.build, boot=initial.boot, now=now
                )
            except CompactExchangeError as error:
                emit(error.evidence)
                raise
            emit(evidence)
            if seq < count:
                sleep(interval_s)
        final = context("postflight", count + 1)
        if (initial.build, initial.boot, initial.tick_hz) != (
            final.build,
            final.boot,
            final.tick_hz,
        ):
            raise ValueError("runtime identity changed across compact probes")
    except BaseException as error:
        failure = type(error).__name__
        raise
    finally:
        emit(
            {
                "type": "end",
                "boot_id": boot(),
                "connection_id": connection,
                "probe_count": attempted,
                "mono_ns": now(),
                "error": failure,
            }
        )


def compact_recording_observations(records):
    """Require exact byte/read evidence, CLI recovery and matching context checks."""
    if (
        not isinstance(records, list)
        or len(records) < 5
        or not all(isinstance(row, dict) for row in records)
    ):
        raise ValueError("incomplete compact recording")
    start, pre, post, end = records[0], records[1], records[-2], records[-1]
    count = start.get("requested_count")
    if (
        start.get("type") != "start"
        or start.get("schema") != SCHEMA
        or start.get("event_clock") != "host_monotonic_ns"
        or type(count) is not int
        or not 1 <= count <= 1000
        or len(records) != count + 4
        or not isinstance(start.get("boot_id"), str)
        or not start["boot_id"]
        or not isinstance(start.get("connection_id"), str)
        or not start["connection_id"]
        or end.get("type") != "end"
        or "error" not in end
        or end["error"] is not None
        or end.get("boot_id") != start["boot_id"]
        or end.get("connection_id") != start["connection_id"]
        or type(end.get("probe_count")) is not int
        or end["probe_count"] != count
        or pre.get("type") != "preflight"
        or post.get("type") != "postflight"
        or type(pre.get("seq")) is not int
        or pre["seq"] != 0
        or type(post.get("seq")) is not int
        or post["seq"] != count + 1
    ):
        raise ValueError("invalid compact recording context")
    initial = parse_probe(pre.get("reply_text"), pre.get("request"), 0)
    final = parse_probe(post.get("reply_text"), post.get("request"), count + 1)
    if (initial.build, initial.boot, initial.tick_hz) != (final.build, final.boot, final.tick_hz):
        raise ValueError("compact context runtime changed")
    previous = _uint(start.get("mono_ns"), 63)
    finish = _uint(end.get("mono_ns"), 63)
    seen = set()
    observations = []
    for index, row in enumerate(records[1:-1]):
        before = _uint(row.get("before_ns"), 63)
        after = _uint(row.get("after_ns"), 63)
        if before < previous or after < before or after > finish or row.get("request") in seen:
            raise ValueError("compact recording order or nonce invalid")
        seen.add(row.get("request"))
        if index not in (0, count + 1):
            if (
                row.get("type") != "compact"
                or type(row.get("seq")) is not int
                or row["seq"] != index
            ):
                raise ValueError("compact sequence gap or reorder")
            if "error" not in row or row["error"] is not None:
                raise ValueError("failed compact exchange")
            raw = _received_bytes(row)
            offset = _uint(row.get("packet_offset"))
            if not _prefix_valid(raw[:offset], compact_command(row.get("request"), index)):
                raise ValueError("unexpected compact prefix")
            parsed = parse_compact(
                raw[offset : offset + PACKET.size],
                row["request"],
                index,
                build=initial.build,
                boot=initial.boot,
            )
            if parsed.tick_hz != initial.tick_hz:
                raise ValueError("compact configured rate changed")
            if (
                raw[offset + PACKET.size :].replace(b"\r\n", b"\n").lstrip(b"\n")
                != b"Done\n" + PROMPT
            ):
                raise ValueError("compact CLI recovery missing")
            complete = _read_marks(row, len(raw), offset + PACKET.size)
            observations.append(
                ProbeObservation(
                    parsed,
                    before,
                    complete,
                    start["boot_id"],
                    start["connection_id"],
                    cli_after_ns=after,
                )
            )
        previous = after
    return observations


def _received_bytes(row):
    try:
        value = row.get("received_b64")
        if not isinstance(value, str):
            raise ValueError("missing received bytes")
        raw = base64.b64decode(value, validate=True)
    except binascii.Error as error:
        raise ValueError("invalid received bytes") from error
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError("invalid compact byte count")
    return raw


def _read_marks(row, total, packet_end):
    _uint(row.get("packet_complete_ns"), 63)
    _uint(row.get("cli_ready_ns"), 63)
    marks = row.get("reads")
    if not isinstance(marks, list) or not marks:
        raise ValueError("compact read evidence missing")
    previous_time = _uint(row.get("write_return_ns"), 63)
    if previous_time < row["before_ns"]:
        raise ValueError("write return precedes request")
    previous_size = 0
    complete = None
    for mark in marks:
        if not isinstance(mark, dict):
            raise ValueError("malformed read evidence")
        stamp = _uint(mark.get("received_ns"), 63)
        size = _uint(mark.get("bytes_total"))
        if stamp < previous_time or not previous_size < size <= total:
            raise ValueError("nonmonotonic compact read evidence")
        if complete is None and size >= packet_end:
            complete = stamp
        previous_size, previous_time = size, stamp
    if (
        previous_size != total
        or complete is None
        or row.get("packet_complete_ns") != complete
        or row.get("cli_ready_ns") != previous_time
        or row["after_ns"] != previous_time
    ):
        raise ValueError("packet completion is not tied to observed bytes")
    return complete

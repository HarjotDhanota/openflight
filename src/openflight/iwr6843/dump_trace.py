"""Research-only dump recording through a fixed window, including pre-header stages."""

from __future__ import annotations

import hashlib
import re
import time
import zlib

from openflight.iwr6843.clock_evidence import _uint
from openflight.iwr6843.dump import MAGIC, declared_dump_nbytes, parse_dump

MAX_BYTES = 2 * 1024 * 1024
STAGE = re.compile(
    rb"(?:^|\n)L3STAGE ([a-z_]+) tick=(\d+) frame=(\d+) ring=(\d+) "
    rb"hwa=(\d+) out=(\d+) rearms=(\d+) missed=(\d+) status=(-?\d+)\r?\n"
)
WIRE = re.compile(
    rb"\nL3WIRE calls=([0-9]{1,10}) requested=([0-9]{1,10}) returned=([0-9]{1,10}) "
    rb"errors=([0-9]{1,10}) mismatches=([0-9]{1,10}) first_call=([0-9]{1,10}) "
    rb"first_result=(-?[0-9]{1,10}) crc32=([0-9a-f]{8})\r?\nDone\r?\nl3dump:/>$"
)


def inspect_dump_trace(raw, *, require_wire_trace=False):
    """Inspect only bytes read by the deadline; checksum evidence never repairs data."""
    result = {
        "payload_offset": None,
        "payload_bytes": None,
        "payload_declared_bytes": None,
        "binary_observed_bytes": None,
        "cli_ready": False,
        "wire_trace": None,
        "wire_trace_status": "missing" if require_wire_trace else "not_requested",
    }
    report = {}
    wire = WIRE.search(raw)
    end = len(raw)
    if wire:
        values = [*map(int, wire.groups()[:7]), int(wire[8], 16)]
        report = dict(
            zip(
                (
                    "calls",
                    "requested",
                    "returned",
                    "errors",
                    "mismatches",
                    "first_call",
                    "first_result",
                    "crc32",
                ),
                values,
                strict=True,
            )
        )
        result["wire_trace"] = report
        if (
            any(not 0 <= value < 2**32 for index, value in enumerate(values) if index != 6)
            or not -(2**31) <= values[6] < 2**31
        ):
            result["wire_trace_status"] = "invalid_report"
            return result
        result["wire_trace_status"] = "invalid_binary"
        end = wire.start()
    offset = raw.find(MAGIC, 0, end)
    if offset < 0:
        return result
    binary = raw[offset:end]
    count = declared_dump_nbytes(binary)
    result["payload_declared_bytes"] = count
    if wire:
        result["binary_observed_bytes"] = len(binary)
        if count is None or count != len(binary) or report["requested"] != count:
            result["wire_trace_status"] = "length_mismatch"
            return result
        if (
            not report["calls"]
            or report["returned"] != count
            or any(report[key] for key in ("errors", "mismatches", "first_call", "first_result"))
        ):
            result["wire_trace_status"] = "write_mismatch"
            return result
        if zlib.crc32(binary) != report["crc32"]:
            result["wire_trace_status"] = "crc_mismatch"
            return result
    if count is not None and count <= len(binary):
        try:
            parse_dump(binary[:count])
            result.update(payload_offset=offset, payload_bytes=count)
            if wire:
                result.update(wire_trace_status="complete", cli_ready=True)
            elif not require_wire_trace:
                result["cli_ready"] = binary[count:].replace(b"\r\n", b"\n") == b"Done\nl3dump:/>"
        except (ValueError, IndexError):
            pass
    return result


def record_dump_trace(
    port, *, timeout_ns=15_000_000_000, now=time.monotonic_ns, require_wire_trace=False
):
    """Send only l3dump on an exclusively owned, already configured connection.

    Keep the complete raw stream; do not send recovery commands on a busy CLI.
    This diagnostic waits the full window even when a valid dump finishes early.
    """
    if not 1 <= _uint(timeout_ns, 63) <= 30_000_000_000:
        raise ValueError("dump trace window must be positive and at most 30 seconds")
    original_read, original_write = port.timeout, port.write_timeout
    data = bytearray()
    result = {
        "schema": "openflight.iwr-dump-trace.v1",
        "before_ns": now(),
        "write_return_ns": None,
        "timeout_ns_policy": timeout_ns,
        "reads": [],
        "bytes_by_deadline": 0,
        "status": "unavailable",
        "error": None,
        "physical_timing_qualified": False,
        "require_wire_trace": require_wire_trace,
    }
    deadline = result["before_ns"] + timeout_ns
    try:
        port.timeout = port.write_timeout = min(0.25, timeout_ns / 10**9)
        written = port.write(b"l3dump\n")
        result["write_return_ns"] = now()
        if not isinstance(written, int) or written != 7:
            raise ValueError("partial dump command write")
        while now() < deadline and len(data) < MAX_BYTES:
            chunk = port.read(min(max(1, port.in_waiting), MAX_BYTES - len(data)))
            received = now()
            data.extend(chunk)
            result["reads"].append(
                {"received_ns": received, "bytes_total": len(data), "bytes_read": len(chunk)}
            )
            if received <= deadline:
                result["bytes_by_deadline"] = len(data)
        if len(data) == MAX_BYTES:
            result["status"] = "reply_limit"
    except (OSError, ValueError) as error:
        result.update(status="io_error", error=type(error).__name__)
    finally:
        result["after_ns"] = now()
        port.timeout, port.write_timeout = original_read, original_write
    raw = bytes(data)
    result.update(raw_bytes=len(raw), raw_sha256=hashlib.sha256(raw).hexdigest())
    timely = raw[: result["bytes_by_deadline"]]
    prefix = raw.split(MAGIC, 1)[0]
    result["stages"] = [
        dict(
            zip(
                ("stage", "tick", "frame", "ring", "hwa", "out", "rearms", "missed", "status"),
                [match[1].decode("ascii"), *map(int, match.groups()[1:])],
            ),
            byte_offset=match.start(),
        )
        for match in STAGE.finditer(prefix)
    ]
    result.update(inspect_dump_trace(timely, require_wire_trace=require_wire_trace))
    if result["cli_ready"] and result["status"] == "unavailable":
        result["status"] = "complete"
    return raw, result

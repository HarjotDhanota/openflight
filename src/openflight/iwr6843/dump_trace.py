"""Research-only dump recording through a fixed window, including pre-header stages."""

from __future__ import annotations

import hashlib
import re
import time

from openflight.iwr6843.clock_evidence import _uint
from openflight.iwr6843.dump import MAGIC, declared_dump_nbytes, parse_dump

MAX_BYTES = 2 * 1024 * 1024
STAGE = re.compile(
    rb"(?:^|\n)L3STAGE ([a-z_]+) tick=(\d+) frame=(\d+) ring=(\d+) "
    rb"hwa=(\d+) out=(\d+) rearms=(\d+) missed=(\d+) status=(-?\d+)\r?\n"
)


def record_dump_trace(port, *, timeout_ns=15_000_000_000, now=time.monotonic_ns):
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
    }
    deadline = result["before_ns"] + timeout_ns
    try:
        port.timeout = port.write_timeout = min(0.25, timeout_ns / 10**9)
        written = port.write(b"l3dump\n")
        result["write_return_ns"] = now()
        if type(written) is not int or written != 7:
            raise ValueError("partial dump command write")
        while now() < deadline and len(data) < MAX_BYTES:
            remaining_s = (deadline - now()) / 10**9
            if remaining_s <= 0:
                break
            port.timeout = min(0.25, remaining_s)
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
    offset = timely.find(MAGIC)
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
    result.update(payload_offset=None, payload_bytes=None, cli_ready=False)
    if offset >= 0:
        binary = timely[offset:]
        count = declared_dump_nbytes(binary)
        if count is not None and count <= len(binary):
            try:
                parse_dump(binary[:count])
                result.update(payload_offset=offset, payload_bytes=count)
                result["cli_ready"] = binary[count:].replace(b"\r\n", b"\n") == b"Done\nl3dump:/>"
                if result["cli_ready"] and result["status"] == "unavailable":
                    result["status"] = "complete"
            except (ValueError, IndexError):
                pass
    return raw, result

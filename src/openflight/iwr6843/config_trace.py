"""Research-only, bounded ADC configuration traces; no RF-start or flash path."""

from __future__ import annotations

import base64
import re
import secrets
import time
import uuid

from openflight.iwr6843.clock_evidence import _hex, _uint, parse_probe
from openflight.iwr6843.compact_clock import PROMPT
from openflight.ops_capture_evidence import boot_identity

COMMANDS = (
    "sensorStop",
    "flushCfg",
    "dfeDataOutputMode 1",
    "channelCfg 15 7 0",
    "adcCfg 2 1",
    "stats",
)
MAX_BYTES = 65536


def trace_command(port, command, *, timeout_ns=10_000_000_000, now=time.monotonic_ns):
    """Retain every read without flushing input; complete means terminal plus prompt."""
    clock = re.fullmatch(r"clockProbe ([0-9a-f]{8}) ([0-9a-f]{8}) [0-9a-f]{8}", command)
    if command not in COMMANDS and (not clock or not int(clock[1] + clock[2], 16)):
        raise ValueError("command is outside the configuration diagnostic allowlist")
    if not 1 <= _uint(timeout_ns, 63) <= 15_000_000_000:
        raise ValueError("diagnostic window must be positive and at most 15 seconds")
    original_read, original_write = port.timeout, port.write_timeout
    data = bytearray()
    result = {
        "type": "command",
        "command": command,
        "before_ns": now(),
        "write_return_ns": None,
        "terminal_ns": None,
        "cli_ready_ns": None,
        "after_ns": None,
        "timeout_ns_policy": timeout_ns,
        "received_b64": "",
        "reads": [],
        "status": "timeout",
        "error": None,
    }
    try:
        port.timeout = port.write_timeout = min(0.25, timeout_ns / 10**9)
        encoded = (command + "\n").encode("ascii")
        written = port.write(encoded)
        result["write_return_ns"] = now()
        if type(written) is not int or written != len(encoded):
            raise ValueError("partial command write")
        deadline = result["before_ns"] + timeout_ns
        while now() < deadline:
            chunk = port.read(min(max(1, port.in_waiting), MAX_BYTES - len(data)))
            received = now()
            data.extend(chunk)
            result["reads"].append(
                {"received_ns": received, "bytes_total": len(data), "bytes_read": len(chunk)}
            )
            normalized = bytes(data).replace(b"\r\n", b"\n")
            terminals = list(
                re.finditer(rb"(?:^|\n)(Done|Error(?: [+-]?[0-9]+)?)(?=\n)", normalized)
            )
            if terminals and result["terminal_ns"] is None:
                result["terminal_ns"] = received
            if received > deadline:
                break
            if len(terminals) > 1:
                result["status"] = "invalid_reply"
                break
            if terminals and normalized.rfind(PROMPT) > terminals[0].end():
                if normalized[terminals[0].end() :] != b"\n" + PROMPT:
                    result["status"] = "invalid_reply"
                else:
                    result["cli_ready_ns"] = received
                    result["status"] = "complete" if terminals[0][1] == b"Done" else "rejected"
                break
            if len(data) >= MAX_BYTES:
                result["status"] = "reply_limit"
                break
    except (OSError, ValueError) as error:
        result.update(status="io_error", error=type(error).__name__)
    finally:
        result["after_ns"] = now()
        result["received_b64"] = base64.b64encode(data).decode("ascii")
        port.timeout, port.write_timeout = original_read, original_write
    return result


def record_config_trace(
    port,
    emit,
    *,
    build,
    timeout_ns=10_000_000_000,
    now=time.monotonic_ns,
    nonce=lambda: secrets.token_hex(8),
    boot=boot_identity,
):
    """One exclusively owned serial connection; stop on any incomplete response."""
    _hex(build, 64)
    host = boot()
    if not isinstance(host, str) or not host:
        raise ValueError("host boot identity unavailable")
    emit(
        {
            "type": "start",
            "schema": "openflight.iwr-config-trace.v1",
            "expected_build": build,
            "boot_id": host,
            "connection_id": uuid.uuid4().hex,
            "event_clock": "host_monotonic_ns",
            "mono_ns": now(),
            "timeout_ns_policy": timeout_ns,
            "rf_start_allowed": False,
        }
    )
    status, reason, requests = "unavailable", None, set()

    def command(text):
        value = trace_command(port, text, timeout_ns=timeout_ns, now=now)
        emit(value)
        if value["status"] != "complete":
            raise ValueError(f"{text.split()[0]} response {value['status']}")
        return base64.b64decode(value["received_b64"]).decode("ascii")

    def identity(seq):
        request = _hex(nonce(), 16)
        if request in requests:
            raise ValueError("reused identity request")
        requests.add(request)
        text = command(f"clockProbe {request[:8]} {request[8:]} {seq:08x}")
        parsed = parse_probe(text, request, seq)
        if parsed.build != build:
            raise ValueError("firmware build mismatch")
        return parsed.build, parsed.boot, parsed.tick_hz

    try:
        initial = identity(0)
        for text in COMMANDS:
            reply = command(text)
            if text == "stats" and not (
                re.search(r"\bactive=0\b", reply) and re.search(r"\brf_faults=0\b", reply)
            ):
                raise ValueError("RF idle/fault status unavailable")
        if identity(1) != initial:
            raise ValueError("runtime identity changed")
        if boot() != host:
            raise ValueError("host boot identity changed")
        status = "complete"
    except (ValueError, OSError) as error:
        reason = str(error)
    emit(
        {
            "type": "end",
            "status": status,
            "reason": reason,
            "mono_ns": now(),
            "physical_timing_qualified": False,
            "rf_start_command_sent": False,
        }
    )
    return status

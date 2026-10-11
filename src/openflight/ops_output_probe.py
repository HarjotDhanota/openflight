"""Classify timed OPS243 rolling-buffer output for the stage 1b output-options probe.

Each chunk's time is when its read RETURNED, so a record's completion time is an
upper bound on its arrival. Silence is an observed gap, never proof of completion.
Unknown records stay unknown; binary bytes are kept and not parsed as JSON. Pure
module except for the Journal/run_candidate helpers, which take an injected ``io``.

The transfer arithmetic below restates the plan's recorded numbers (test 7 and the
Pi's historical ``process_capture`` times). It is planning arithmetic, never a
measurement of a probe run, and never a pass on its own.

This module must not import ``openflight.rolling_buffer`` (its ``__init__`` imports
the monitor).
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any, Callable, Optional, Sequence

from openflight.clock_mapping import BUFFER_SEGMENTS, SEGMENT_SAMPLES, association_window_ns
from openflight.pi_trace import _finite
from openflight.pi_trace_summary import OPS_ALLOCATION_MS

UART_BITS_PER_BYTE = 10  # 8N1 framing: start bit, 8 data bits, stop bit
GAP_REPORT_S = 0.2
IQ_SAMPLES = BUFFER_SEGMENTS * SEGMENT_SAMPLES  # 4,096: fixed for the rolling buffer (AN-027-A)
IQ_MAX = 4095  # 12-bit ADC samples
CANDIDATES: dict[str, tuple[list[str], list[str]]] = {
    "baseline": ([], []),
    "sz_on": (["SZ"], ["Sz"]),
    "ob_on": (["OB"], ["Ob"]),
}
# A! persists settings; GS/GC switch modes and trip the HOST_INT firmware bug.
FORBIDDEN_COMMANDS = ("A!", "GS", "GC")

# Recorded numbers the plan's arithmetic uses (Task 6 brief). Historical, not this run.
TARGET_UART_BAUD = 230_400
RECORDED_DUMP_BYTES = 41_035  # one dump on the wire (test 7)
RECORDED_EDGE_TO_FIRST_BYTE_S = 0.086  # GATE edge -> first byte at S#16 (test 7)
RECORDED_PRE_TRIGGER_SEGMENTS = 16
RECORDED_PROCESS_CAPTURE_S = (0.061, 0.096)  # Pi process_capture range, spin included
SAMPLE_RATE_HZ = 30_000

_PLAIN_NUMBER = re.compile(rb"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")
_IQ_PREFIX = re.compile(rb'\{\s*"(I|Q)"\s*:')
_TEXT_BYTES = frozenset(range(0x20, 0x7F)) | {0x09, 0x0A, 0x0D}


def ideal_transfer_s(n_bytes: int, baud: int, bits_per_byte: int = UART_BITS_PER_BYTE) -> float:
    """Wire time for n bytes at a UART rate, framing included."""
    return n_bytes * bits_per_byte / baud


def post_trigger_s(pre_trigger_segments: int, sample_rate_hz: float = SAMPLE_RATE_HZ) -> float:
    """Post-trigger sampling time of one capture at this S# split."""
    _segment_ns, post_ns = association_window_ns(sample_rate_hz, pre_trigger_segments)
    return post_ns / 1e9


def q_samples_received_after_edge_s(
    received: int,
    *,
    pre_trigger_segments: int,
    n_bytes: int = RECORDED_DUMP_BYTES,
    baud: int = TARGET_UART_BAUD,
) -> float:
    """Planning arithmetic: when the first ``received`` Q samples have arrived, after the edge.

    The plan's model, not a measurement: the recorded dump-start overhead (edge to first
    byte minus the post-trigger wait, at S#16) is assumed to hold at any split; I comes
    first and I and Q each take half the wire time; Q samples arrive evenly.
    """
    if not isinstance(received, int) or not 0 <= received <= IQ_SAMPLES:
        raise ValueError(f"received must be an int in 0..{IQ_SAMPLES}, not {received!r}")
    overhead_s = RECORDED_EDGE_TO_FIRST_BYTE_S - post_trigger_s(RECORDED_PRE_TRIGGER_SEGMENTS)
    half_s = ideal_transfer_s(n_bytes, baud) / 2
    return (
        post_trigger_s(pre_trigger_segments) + overhead_s + half_s + received * half_s / IQ_SAMPLES
    )


def transfer_arithmetic() -> dict:
    """The plan's whole-capture arithmetic on recorded numbers, at the target UART rate."""
    transfer = ideal_transfer_s(RECORDED_DUMP_BYTES, TARGET_UART_BAUD)
    last_byte = RECORDED_EDGE_TO_FIRST_BYTE_S + transfer
    ready = [last_byte + t for t in RECORDED_PROCESS_CAPTURE_S]
    allocation = OPS_ALLOCATION_MS / 1000.0
    return {
        "basis": (
            "historical recorded numbers (test 7 dump size and edge-to-first-byte; Pi "
            "process_capture range), not this run; the impact-to-edge allowance is not included"
        ),
        "dump_bytes": RECORDED_DUMP_BYTES,
        "baud": TARGET_UART_BAUD,
        "transfer_s": transfer,
        "last_byte_after_edge_s": last_byte,
        "host_speeds_ready_after_edge_s": ready,
        "ops_allocation_s": allocation,
        "short_of_allocation_s": [t - allocation for t in ready],
    }


@dataclass(frozen=True)
class Record:  # pylint: disable=too-many-instance-attributes
    """One newline-framed record, or a run of binary bytes."""

    kind: str  # sample_time | trigger_time | I | Q | speed | unknown | binary
    key: Optional[str]
    first_byte_ns: int
    complete_ns: int
    n_bytes: int
    valid: Optional[bool]
    censored: bool


def _speed_value_ok(value: Any) -> bool:
    if isinstance(value, list):
        return bool(value) and all(_finite(v) for v in value)
    return _finite(value)


def _iq_ok(samples: Any) -> bool:
    return (
        isinstance(samples, list)
        and len(samples) == IQ_SAMPLES
        and all(
            isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= IQ_MAX for v in samples
        )
    )


def _time_ok(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _classify_text(  # pylint: disable=too-many-return-statements
    text: bytes,
) -> tuple[str, Optional[str], Optional[bool]]:
    """(kind, key, valid) of one stripped text line. Anything unrecognized is unknown."""
    try:
        value = json.loads(text)
    except ValueError:
        iq = _IQ_PREFIX.match(text)
        if iq:  # a broken I/Q line (dropped bytes, cut-off) is still the I/Q, invalid
            key = iq.group(1).decode("ascii")
            return key, key, False
        if _PLAIN_NUMBER.fullmatch(text) and math.isfinite(float(text)):
            return "speed", None, True
        return "unknown", None, None
    if isinstance(value, dict) and "speed" in value:
        if _speed_value_ok(value["speed"]):
            return "speed", "speed", True
        return "unknown", "speed", None
    if _finite(value):  # a bare JSON number is a plain numeric line
        return "speed", None, True
    if not isinstance(value, dict) or len(value) != 1:
        return "unknown", None, None
    key = next(iter(value))
    if key in ("sample_time", "trigger_time"):
        return key, key, _time_ok(value[key])
    if key in ("I", "Q"):
        return key, key, _iq_ok(value[key])
    return "unknown", key, None


def classify_stream(
    chunks: Sequence[tuple[int, bytes]], *, censored_at_limit: bool
) -> list[Record]:
    """Split the stream into records; a trailing unterminated record is censored at a limit.

    Lines are newline-framed. A line holding any non-text byte is binary (``OB`` output
    is not newline JSON) up to the first ``{`` after its last non-text byte; any text
    after that is classified on its own. Binary runs have no framing, so they are never
    called censored. Blank lines are skipped.
    """
    data, times = bytearray(), []
    for returned_ns, chunk in chunks:
        data.extend(chunk)
        times.extend([returned_ns] * len(chunk))
    records: list[Record] = []
    start = 0
    while start < len(data):
        newline = data.find(b"\n", start)
        terminated = newline >= 0
        end = newline if terminated else len(data) - 1
        line = bytes(data[start : end + 1])
        text_from = 0
        last_binary = max((i for i, byte in enumerate(line) if byte not in _TEXT_BYTES), default=-1)
        if last_binary >= 0:
            brace = line.find(b"{", last_binary + 1)
            text_from = brace if brace >= 0 else len(line)
            records.append(
                Record(
                    "binary",
                    None,
                    times[start],
                    times[start + text_from - 1],
                    text_from,
                    None,
                    False,
                )
            )
        text = line[text_from:]
        if text.strip():
            kind, key, valid = _classify_text(text.strip())
            censored = not terminated and censored_at_limit
            if censored and kind in ("I", "Q"):
                valid = False
            records.append(
                Record(kind, key, times[start + text_from], times[end], len(text), valid, censored)
            )
        start = end + 1
    return records


def _after_s(ns: int, sent_ns: int) -> float:
    return (ns - sent_ns) / 1e9


def _iq_problems(records: Sequence[Record]) -> list[str]:
    problems = []
    for key in ("I", "Q"):
        found = [r for r in records if r.kind == key]
        if not found:
            problems.append(f"{key}_missing")
        elif len(found) > 1:
            problems.append("duplicate_iq")
        elif not found[0].valid:
            problems.append(f"{key}_invalid")
    return sorted(set(problems))


def stream_summary(
    chunks: Sequence[tuple[int, bytes]],
    records: Sequence[Record],
    *,
    baud: int,
    sent_ns: int,
    limit_ns: int,
) -> dict:
    """Order, I/Q validity and completion, speed records, unknowns, gaps, censoring, transfer.

    Times are seconds after ``sent_ns`` (the host time just before ``S!`` was written),
    never impact. A speed record counts only once complete (terminated, not censored).
    """
    total = sum(len(c) for _t, c in chunks)
    times = [t for t, _c in chunks]
    problems = _iq_problems(records)
    first_iq = next((i for i, r in enumerate(records) if r.kind in ("I", "Q")), len(records))
    before_iq = records[:first_iq]
    speeds = [r for r in records if r.kind == "speed" and not r.censored]
    speeds_before = [r for r in before_iq if r.kind == "speed" and not r.censored]
    iq_complete = (
        _after_s(max(r.complete_ns for r in records if r.kind in ("I", "Q")), sent_ns)
        if not problems
        else None
    )
    return {
        "total_bytes": total,
        "first_byte_timeout": not chunks,
        "first_byte_after_sent_s": _after_s(times[0], sent_ns) if times else None,
        "order": [r.kind for r in records],
        "iq_valid": not problems,
        "iq_problems": problems,
        "iq_complete_after_sent_s": iq_complete,
        "iq_complete_reason": None if iq_complete is not None else "iq_invalid",
        "speed_records": len(speeds),
        "speed_records_before_iq": len(speeds_before),
        "speed_complete_after_sent_s": [_after_s(r.complete_ns, sent_ns) for r in speeds],
        "first_speed_complete_after_sent_s": (
            _after_s(speeds_before[0].complete_ns, sent_ns) if speeds_before else None
        ),
        "first_speed_complete_reason": None if speeds_before else "no_speed_record_before_iq",
        "unknown_records": sum(r.kind == "unknown" for r in records),
        "unknown_records_before_iq": sum(r.kind == "unknown" for r in before_iq),
        "binary_bytes": sum(r.n_bytes for r in records if r.kind == "binary"),
        "binary_bytes_before_iq": sum(r.n_bytes for r in before_iq if r.kind == "binary"),
        "gaps_s": [(b - a) / 1e9 for a, b in zip(times, times[1:]) if (b - a) / 1e9 > GAP_REPORT_S],
        "trailing_silence_s": (limit_ns - times[-1]) / 1e9 if times else None,
        "censored": any(r.censored for r in records),
        "observed_transfer_s": (times[-1] - times[0]) / 1e9 if times else None,
        "ideal_transfer_s": ideal_transfer_s(total, baud),
        "observation_limit_s": _after_s(limit_ns, sent_ns),
    }


def o4_allocation(iq_complete_after_sent_s: Optional[float], allowance_ms: Optional[float]) -> dict:
    """I/Q completion against O4's allocation, ``1.20 s − A`` (R-F10).

    ``S!`` is time zero, not impact, and host processing is not included, so True is a
    necessary condition only; False is a definite miss.
    """
    if allowance_ms is not None and (not _finite(allowance_ms) or allowance_ms < 0):
        raise ValueError(f"allowance_ms must be a finite number >= 0, not {allowance_ms!r}")
    if allowance_ms is None:
        return {"within_o4_allocation": None, "o4_limit_s": None, "o4_reason": "allowance_missing"}
    limit_s = (OPS_ALLOCATION_MS - allowance_ms) / 1000.0
    if iq_complete_after_sent_s is None:
        return {"within_o4_allocation": None, "o4_limit_s": limit_s, "o4_reason": "iq_incomplete"}
    return {
        "within_o4_allocation": iq_complete_after_sent_s <= limit_s,
        "o4_limit_s": limit_s,
        "o4_reason": None,
    }


class Journal:
    """Every attempted mutation, body run and cleanup, with its outcome."""

    def __init__(self):
        self.entries: list[dict] = []

    def record(
        self,
        step: str,
        command: str,
        reply: Optional[str],
        error: Optional[str],
        *,
        candidate: Optional[str] = None,
    ) -> None:
        """Append one journal entry."""
        self.entries.append(
            {
                "step": step,
                "command": command,
                "reply": reply,
                "error": error,
                "candidate": candidate,
            }
        )

    def attempt(
        self,
        step: str,
        command: str,
        action: Callable[[], Any],
        *,
        candidate: Optional[str] = None,
    ) -> Any:
        """Run ``action``, journal its reply or its error, and re-raise any error."""
        try:
            reply = action()
        except BaseException as error:  # pylint: disable=broad-exception-caught
            self.record(step, command, None, repr(error), candidate=candidate)
            raise
        self.record(step, command, reply, None, candidate=candidate)
        return reply

    def inverse_errors(self, candidate: str) -> list[dict]:
        """The failed cleanup steps of one candidate."""
        return [
            e
            for e in self.entries
            if e["candidate"] == candidate and e["step"] == "inverse" and e["error"] is not None
        ]


def run_candidate(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    io,
    name: str,
    apply: list[str],
    inverse: list[str],
    body: Callable[[], None],
    journal: Journal,
) -> None:
    """Apply, run the body, then attempt every inverse even after failures (re-raises).

    A failed apply is journaled and stops the candidate (the body never runs on a
    half-applied state); every inverse is still attempted, each independently (R-F11).
    """
    forbidden = [c for c in apply + inverse if c in FORBIDDEN_COMMANDS]
    if forbidden:
        raise ValueError(f"candidate {name!r} uses forbidden commands {forbidden}")
    try:
        for command in apply:
            journal.attempt("apply", command, lambda c=command: io.command(c), candidate=name)
        journal.attempt("body", name, body, candidate=name)
    finally:
        for command in inverse:
            try:
                journal.attempt("inverse", command, lambda c=command: io.command(c), candidate=name)
            except Exception:  # pylint: disable=broad-exception-caught
                pass  # journaled; the next inverse is still attempted


def restoration_verified(before: dict[str, str], after: dict[str, str]) -> Optional[bool]:
    """True/False against a known initial state; None when any initial reply is unknown."""
    if not before or any(not (reply or "").strip() for reply in before.values()):
        return None
    return all(before[key].strip() == (after.get(key) or "").strip() for key in before)

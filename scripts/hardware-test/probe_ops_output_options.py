#!/usr/bin/env python3
"""Stage 1b: what OPS243 firmware 1.3.0 settings do to the rolling-buffer output.

Run with the kiosk STOPPED (it needs the serial port). Volatile settings only, journaled;
every inverse is attempted even after failures; never A!, GS or GC. The report is written
even on failure. Power-cycle the OPS243 afterwards (J3 pin 9) and run
test_rolling_buffer_persist.py --test before the next kiosk start.

Each round re-arms at the given split, sends S!, and keeps every byte with the host time
its read returned until a fixed observation limit. Times are seconds after S! was sent,
never impact.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from openflight import ops_output_probe as probe  # noqa: E402
from openflight.clock_mapping import BUFFER_SEGMENTS  # noqa: E402
from openflight.ops243 import OPS243Radar, is_uart_port, parse_firmware_version  # noqa: E402

QUERIES = ["??", "?V", "?B", "I?", "O?", "R?", "P?", "A?"]
EXPECTED_FIRMWARE = "1.3.0"
ARM_SETTLE_S = 0.3  # > one 4,096-sample buffer at 30 ksps (136.5 ms) before S!
TRIGGER_COMMAND = b"S!\r"  # as OPS243Radar.trigger_capture sends it
POLL_S = 0.001
NOTES = [
    "Times are seconds after the host wrote S! (time zero), not impact.",
    "Each chunk's time is when its read returned, so completion times are upper bounds.",
    "A speed record, or its completion time, is not usable ball or club evidence and not an "
    "O4 result.",
    "within_o4_allocation compares I/Q completion with 1.20 s - A. Host processing is not "
    "included, so true is necessary, not sufficient; false is a definite miss.",
    "No recognized speed record within the limit is not proof that 1.3.0 lacks SZ: unknown "
    "and binary records are kept verbatim in the raw .npz files.",
    "Silence is an observed gap, not proof of completion.",
    "restoration_verified null means an unknown initial state, never 'restored'. Power-cycle "
    "the OPS243 after every run (J3 pin 9), then run test_rolling_buffer_persist.py --test.",
    "planning_arithmetic restates the plan's historical numbers; it is not this run.",
]


class RadarIO:  # pylint: disable=too-few-public-methods
    """Sends one command through the driver and returns its reply; refuses forbidden ones."""

    def __init__(self, radar):
        self.radar = radar

    def command(self, text: str) -> str:
        """The driver's own command path (adds the carriage return S#/=/</> need)."""
        if text.strip() in probe.FORBIDDEN_COMMANDS:
            raise ValueError(f"forbidden command {text!r}")
        return self.radar._send_command(text)  # pylint: disable=protected-access


def _rearm(radar, segments: int) -> str:
    """Re-arm at the split; 'rearmed' only when the driver recorded the split as written."""
    radar.rearm_rolling_buffer(segments)
    split, reason = getattr(radar, "_pre_trigger_split", (None, "split_not_recorded"))
    return "rearmed" if split == segments and reason is None else f"not_armed:{reason}"


def _read_waiting(serial) -> bytes:
    data = b""
    while serial.in_waiting:
        data += serial.read(serial.in_waiting)
    return data


def _collect(serial, limit_ns: int, chunks: list[tuple[int, bytes]]) -> Optional[str]:
    """Append every byte until the limit, each chunk timed when its read returned.

    Returns the read error that ended the capture early, or None at the limit.
    """
    while time.monotonic_ns() < limit_ns:
        try:
            waiting = serial.in_waiting
            data = serial.read(waiting) if waiting else b""
        except Exception as error:  # pylint: disable=broad-exception-caught
            return repr(error)
        if data:
            chunks.append((time.monotonic_ns(), data))
        else:
            time.sleep(POLL_S)
    return None


def _save_raw(  # pylint: disable=too-many-arguments
    path: Path, chunks, *, before: bytes, sent_ns: int, written_ns: int, limit_ns: int
):
    np.savez(
        path,
        returned_ns=np.array([t for t, _ in chunks], dtype=np.int64),
        lengths=np.array([len(c) for _, c in chunks], dtype=np.int64),
        data=np.frombuffer(b"".join(c for _, c in chunks), dtype=np.uint8),
        before_trigger=np.frombuffer(before, dtype=np.uint8),
        sent_ns=np.int64(sent_ns),
        write_returned_ns=np.int64(written_ns),
        limit_ns=np.int64(limit_ns),
    )


def _round(radar, journal: probe.Journal, name: str, index: int, args) -> dict:
    """One re-arm, S! and fixed-limit capture; the raw bytes are saved before any parsing."""
    segments = args.pre_trigger_segments
    rearm = journal.attempt(
        "rearm", f"PA S#{segments} PA", lambda: _rearm(radar, segments), candidate=name
    )
    row: dict = {"round": index, "rearm": rearm}
    if rearm != "rearmed":
        row["capture"] = "skipped_rearm_incomplete"
        return row
    time.sleep(ARM_SETTLE_S)
    serial = radar.serial
    before = _read_waiting(serial)  # kept: an edge may have started a dump during the settle
    sent: dict = {}

    def send():
        sent["sent_ns"] = time.monotonic_ns()
        serial.write(TRIGGER_COMMAND)
        serial.flush()
        sent["written_ns"] = time.monotonic_ns()

    journal.attempt("trigger", "S!", send, candidate=name)
    limit_ns = sent["sent_ns"] + round(args.observation_limit_s * 1e9)
    chunks: list[tuple[int, bytes]] = []
    raw_file = f"{name}-{index}.npz"
    try:
        read_error = _collect(serial, limit_ns, chunks)
    finally:  # an interrupt mid-capture still keeps the bytes read so far
        _save_raw(
            args.out / raw_file,
            chunks,
            before=before,
            sent_ns=sent["sent_ns"],
            written_ns=sent["written_ns"],
            limit_ns=limit_ns,
        )
    # Cut by the limit unless a read error ended the capture first.
    records = probe.classify_stream(chunks, censored_at_limit=read_error is None)
    summary = probe.stream_summary(
        chunks, records, baud=radar.baud, sent_ns=sent["sent_ns"], limit_ns=limit_ns
    )
    row.update(
        raw_file=raw_file,
        read_error=read_error,
        bytes_before_trigger=len(before),
        trigger_write_s=(sent["written_ns"] - sent["sent_ns"]) / 1e9,
        **summary,
        **probe.o4_allocation(summary["iq_complete_after_sent_s"], args.allowance_ms),
    )
    return row


def _run_candidates(radar, io: RadarIO, journal: probe.Journal, report: dict, args) -> None:
    """Every candidate in order; stops when a cleanup failed (the state is then unknown)."""
    blocked: Optional[str] = None
    for name, (apply, inverse) in probe.CANDIDATES.items():
        entry = report["candidates"][name]
        if blocked:
            entry["reason"] = f"cleanup of {blocked} failed; the radar state is unknown"
            continue
        entry.pop("reason")
        rows = entry["rounds"]

        def body(name=name, rows=rows):
            for index in range(args.rounds):
                row = _round(radar, journal, name, index, args)
                rows.append(row)
                if row.get("read_error"):
                    raise OSError(f"serial read failed: {row['read_error']}")

        entry["status"] = "running"
        try:
            probe.run_candidate(io, name, apply, inverse, body, journal)
            entry["status"] = "completed"
        except KeyboardInterrupt:
            entry["status"] = "interrupted"
            raise
        except Exception as error:  # pylint: disable=broad-exception-caught
            entry["status"] = "failed"
            report["errors"].append({"candidate": name, "error": repr(error)})
        finally:
            for key in (
                "iq_complete_after_sent_s",
                "first_speed_complete_after_sent_s",
                "within_o4_allocation",
            ):
                entry[key] = [row.get(key) for row in rows]
        if journal.inverse_errors(name):
            blocked = name
            report["errors"].append({"candidate": name, "error": "cleanup_failed"})


def _query_all(io: RadarIO, report: dict, label: str) -> dict:
    """Every query, each attempted on its own; a failed one is None (unknown)."""
    replies: dict = {}
    for query in QUERIES:
        try:
            replies[query] = io.command(query)
        except Exception as error:  # pylint: disable=broad-exception-caught
            replies[query] = None
            report["errors"].append({"candidate": None, "error": f"{label} {query}: {error!r}"})
    return replies


def _finite_at_least(minimum: float, *, strict: bool) -> Callable[[str], float]:
    def parse(text: str) -> float:
        value = float(text)
        if not math.isfinite(value) or value < minimum or (strict and value == minimum):
            bound = ">" if strict else ">="
            raise argparse.ArgumentTypeError(f"must be finite and {bound} {minimum}")
        return value

    return parse


def _int_in(low: int, high: int) -> Callable[[str], int]:
    def parse(text: str) -> int:
        value = int(text)
        if not low <= value <= high:
            raise argparse.ArgumentTypeError(f"must be in {low}..{high}")
        return value

    return parse


def _parse_args(argv: Optional[list[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="/dev/ttyAMA0")
    parser.add_argument("--rounds", type=_int_in(1, 1000), default=3)
    parser.add_argument(
        "--pre-trigger-segments",
        type=_int_in(0, BUFFER_SEGMENTS),
        required=True,
        help="must equal the kiosk's --sound-pre-trigger (recorded; never assumed)",
    )
    parser.add_argument(
        "--observation-limit-s", type=_finite_at_least(0.0, strict=True), default=8.0
    )
    parser.add_argument(
        "--allowance-ms",
        type=_finite_at_least(0.0, strict=False),
        default=None,
        help="impact-to-edge allowance A for the O4 check; omitted means not computed",
    )
    parser.add_argument("--expected-firmware", default=EXPECTED_FIRMWARE)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if (args.out / "report.json").exists() or any(args.out.glob("*.npz")):
        parser.error(f"{args.out} already holds a probe run; choose a new --out")
    return args


def main(argv: Optional[list[str]] = None, *, radar_factory=OPS243Radar) -> int:
    """Run the probe; the report is written in every case. Returns 0, 1 (errors) or 130."""
    args = _parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    report: dict = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "port": args.port,
        "transport": "uart" if is_uart_port(args.port) else "usb",
        "pre_trigger_segments": args.pre_trigger_segments,
        "rounds": args.rounds,
        "observation_limit_s": args.observation_limit_s,
        "allowance_ms": args.allowance_ms,
        "expected_firmware": args.expected_firmware,
        "notes": NOTES,
        "planning_arithmetic": probe.transfer_arithmetic(),
        "candidates": {
            name: {
                "apply": apply,
                "inverse": inverse,
                "status": "not_run",
                "reason": "not_reached",
                "rounds": [],
            }
            for name, (apply, inverse) in probe.CANDIDATES.items()
        },
        "errors": [],
    }
    journal = probe.Journal()
    radar = radar_factory(port=args.port)
    io = RadarIO(radar)
    connected = interrupted = False
    try:
        radar.connect()
        connected = True
        report["baud"] = radar.baud
        report["baud_is_target"] = radar.baud == probe.TARGET_UART_BAUD
        report["queries_before"] = _query_all(io, report, "query_before")
        version = parse_firmware_version(report["queries_before"].get("?V") or "")
        report["firmware_version"] = version
        report["firmware_matches"] = version == args.expected_firmware
        if report["firmware_matches"]:
            _run_candidates(radar, io, journal, report, args)
        else:
            report["errors"].append(
                {"candidate": None, "error": f"firmware_mismatch: {version!r}; nothing changed"}
            )
            for entry in report["candidates"].values():
                entry["reason"] = "firmware_mismatch"
    except KeyboardInterrupt:
        interrupted = True
        report["errors"].append({"candidate": None, "error": "interrupted"})
    except Exception as error:  # pylint: disable=broad-exception-caught
        report["errors"].append({"candidate": None, "error": repr(error)})
    finally:
        _finish(radar, io, journal, report, args, connected)
    print(
        f"Wrote {args.out / 'report.json'}; restoration_verified="
        f"{report['restoration_verified']}. Now power-cycle the OPS243 (J3 pin 9)."
    )
    if interrupted:
        return 130
    return 0 if not report["errors"] else 1


def _finish(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    radar, io: RadarIO, journal: probe.Journal, report: dict, args, connected: bool
):
    """Final re-arm (only after a mutation), re-query, verify, write the report, disconnect."""
    after: dict = {}
    if not connected:
        report["final_rearm"] = "skipped_not_connected"
    else:
        if journal.entries:
            try:
                report["final_rearm"] = journal.attempt(
                    "final_rearm",
                    f"PA S#{args.pre_trigger_segments} PA",
                    lambda: _rearm(radar, args.pre_trigger_segments),
                )
            except Exception as error:  # pylint: disable=broad-exception-caught
                report["final_rearm"] = "failed"
                report["errors"].append({"candidate": None, "error": f"final_rearm: {error!r}"})
        else:
            report["final_rearm"] = "skipped_no_mutation"
        after = _query_all(io, report, "query_after")
    report["queries_after"] = after
    report["restoration_verified"] = probe.restoration_verified(
        report.get("queries_before", {}), after
    )
    report["journal"] = journal.entries
    (args.out / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    try:
        radar.disconnect()
    except Exception:  # pylint: disable=broad-exception-caught
        pass


if __name__ == "__main__":
    sys.exit(main())

"""Research capture/clock sidecars: record an already running profile, or audit offline."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from openflight.iwr6843.capture_clock import audit_capture_clock, record_capture_clock  # noqa: E402
from openflight.iwr6843.clock_evidence import _hex, parse_probe  # noqa: E402
from openflight.iwr6843.driver import IWR6843Radar  # noqa: E402
from openflight.iwr6843.uart_clock_bounds import UARTAssumptions  # noqa: E402


def record(args):
    """No firmware/config/start commands; successful collection verifies RF stopped."""
    _hex(args.build, 64)
    for value, maximum in ((args.settle_s, 5), (args.dump_timeout_s, 40)):
        if not math.isfinite(value) or not 0 <= value <= maximum:
            raise ValueError("invalid collection duration")
    args.directory.mkdir(parents=True, exist_ok=False)
    settings = {
        "reviewed_build": args.build,
        "settle_s_policy": args.settle_s,
        "dump_timeout_s_policy": args.dump_timeout_s,
        "rf_configuration": "already_running_external_profile",
    }
    (args.directory / "collection.json").write_text(json.dumps(settings) + "\n")
    with (args.directory / "capture-clock.jsonl").open("x", encoding="utf-8") as handle:

        def emit(row):
            handle.write(json.dumps(row) + "\n")
            handle.flush()
            if row["type"] == "preflight":
                parsed = parse_probe(row["reply_text"], row["request"], row["seq"])
                if parsed.build != args.build:
                    raise ValueError("observed firmware does not match reviewed build")

        def save(raw):
            with (args.directory / "capture.l3dump").open("xb") as payload:
                payload.write(raw)

        def acquire(radar):
            time.sleep(args.settle_s)
            return radar.read_dump(timeout_s=args.dump_timeout_s)

        with IWR6843Radar(port=args.port) as radar:
            record_capture_clock(radar, acquire, emit, save)
            cleanup = {"status": "unavailable", "rf_idle_verified": False}
            try:
                radar.stop_sensor()
                cleanup.update(status="stopped", rf_idle_verified=True)
            finally:
                (args.directory / "cleanup.json").write_text(json.dumps(cleanup) + "\n")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    collect = commands.add_parser("record", help="requires reviewed clock firmware and active RF")
    collect.add_argument("--port", required=True)
    collect.add_argument("--build", required=True)
    collect.add_argument("--directory", required=True, type=Path)
    collect.add_argument("--settle-s", type=float, default=0.2)
    collect.add_argument("--dump-timeout-s", type=float, default=10.0)
    check = commands.add_parser("audit")
    check.add_argument("directory", type=Path)
    check.add_argument("--output", required=True, type=Path)
    check.add_argument("--rate-min-hz", required=True, type=int)
    check.add_argument("--rate-max-hz", required=True, type=int)
    check.add_argument("--max-distance-ns", required=True, type=int)
    check.add_argument("--reviewed-build")
    check.add_argument("--request-max-baud", type=int)
    check.add_argument("--reply-max-baud", type=int)
    check.add_argument("--uart-reference")
    args = parser.parse_args(argv)
    try:
        if args.command == "record":
            return record(args)
        contract = None
        parts = [
            args.reviewed_build,
            args.request_max_baud,
            args.reply_max_baud,
            args.uart_reference,
        ]
        if any(part is not None for part in parts):
            if any(part is None for part in parts):
                raise ValueError("UART contract needs build, both baud ceilings and reference")
            contract = UARTAssumptions(*parts[:3], "8N1", parts[3])
        evidence = (args.directory / "capture-clock.jsonl").read_bytes()
        raw = (args.directory / "capture.l3dump").read_bytes()
        rows = [json.loads(line) for line in evidence.decode("utf-8").splitlines()]
        result = audit_capture_clock(
            rows,
            raw,
            rate_hz_bounds=(args.rate_min_hz, args.rate_max_hz),
            max_distance_ns=args.max_distance_ns,
            uart_assumptions=contract,
        )
        result["recording_sha256"] = hashlib.sha256(evidence).hexdigest()
        with args.output.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(result, indent=2) + "\n")
        return 1 if result["status"] == "unavailable" else 0
    except (OSError, ValueError, RuntimeError) as error:
        print(f"IWR capture clock failed: {type(error).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

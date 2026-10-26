"""Research-only USB counter probes; requires the matching scratch firmware."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import secrets
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from openflight.iwr6843.clock_evidence import (  # noqa: E402
    map_tick,
    parse_probe,
    probe_envelope,
    recording_observations,
)
from openflight.iwr6843.compact_clock import (  # noqa: E402
    SCHEMA as COMPACT_SCHEMA,
    compact_recording_observations,
    record_compact_probes,
)
from openflight.iwr6843.driver import IWR6843Radar  # noqa: E402
from openflight.ops_capture_evidence import boot_identity  # noqa: E402


def record_probes(
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
    """Bracket complete commands; retain failed replies and stop on identity loss."""
    if type(count) is not int or not 1 <= count <= 1000:
        raise ValueError("count must be between 1 and 1000")
    if (
        not isinstance(interval_s, (int, float))
        or not math.isfinite(interval_s)
        or not 0 <= interval_s <= 10
    ):
        raise ValueError("interval must be finite and between 0 and 10 seconds")
    identity = boot()
    if not isinstance(identity, str) or not identity:
        raise ValueError("Linux boot identity is unavailable")
    connection = uuid.uuid4().hex
    emit(
        {
            "type": "start",
            "schema": "openflight.iwr-clock-probe.v1",
            "event_clock": "host_monotonic_ns",
            "boot_id": identity,
            "connection_id": connection,
            "requested_count": count,
            "mono_ns": now(),
        }
    )
    completed = 0
    failure = None
    epoch = None
    seen = set()
    try:
        for seq in range(count):
            request = nonce()
            if (
                not isinstance(request, str)
                or len(request) != 16
                or any(char not in "0123456789abcdef" for char in request)
                or int(request, 16) == 0
                or request in seen
            ):
                raise ValueError("request nonce invalid or reused")
            seen.add(request)
            before = now()
            reply = radar.cmd(f"clockProbe {request[:8]} {request[8:]} {seq:08x}", 2.0)
            after = now()
            emit(
                {
                    "type": "probe",
                    "seq": seq,
                    "request": request,
                    "before_ns": before,
                    "after_ns": after,
                    "reply_text": reply,
                }
            )
            completed += 1
            parsed = parse_probe(reply, request, seq)
            current = (parsed.build, parsed.boot, parsed.tick_hz)
            if epoch is not None and current != epoch:
                raise ValueError("device runtime, build or configured rate changed")
            epoch = current
            if seq + 1 < count:
                sleep(interval_s)
    except BaseException as error:
        failure = type(error).__name__
        raise
    finally:
        emit(
            {
                "type": "end",
                "boot_id": boot(),
                "connection_id": connection,
                "probe_count": completed,
                "mono_ns": now(),
                "error": failure,
            }
        )


def audit(records, *, rate_hz_bounds, max_distance_ns, target=None):
    """Report conditional widths; leave RF/sample/physical accuracy unavailable."""
    result = {
        "schema": "openflight.iwr-clock-audit.v1",
        "physical_timing_qualified": False,
        "rf_acquisition_interval_ns": None,
        "rate_hz_bounds_assumed": list(rate_hz_bounds),
        "max_distance_ns_assumed": max_distance_ns,
        "target_mapping": None,
        "probe_envelopes": [],
    }
    try:
        if records and isinstance(records[0], dict) and records[0].get("schema") == COMPACT_SCHEMA:
            observations = compact_recording_observations(records)
        else:
            observations = recording_observations(records)
        for obs in observations:
            epoch = (obs.reply.build, obs.reply.boot)
            low, high = probe_envelope(
                obs,
                obs.reply.tx_tick,
                target_epoch=epoch,
                rate_hz_bounds=rate_hz_bounds,
                max_distance_ns=max_distance_ns,
            )
            result["probe_envelopes"].append(
                {
                    "seq": obs.reply.seq,
                    "tx_tick": obs.reply.tx_tick,
                    "host_round_trip_ns": (obs.cli_after_ns or obs.after_ns) - obs.before_ns,
                    "counter_bracket_width_ns": obs.after_ns - obs.before_ns,
                    "conditional_mono_interval_ns": [low, high],
                    "interval_width_ns": high - low,
                }
            )
        if target is not None:
            result["target_mapping"] = map_tick(
                observations,
                target["tick"],
                target_epoch=(target["build"], target["boot"]),
                rate_hz_bounds=rate_hz_bounds,
                max_distance_ns=max_distance_ns,
            )
        result["status"] = "conditional_software_counter_only"
        result["reason"] = None
    except (ValueError, TypeError, KeyError) as error:
        result.update(status="unavailable", reason=str(error), target_mapping=None)
    return result


def main(argv=None):
    """Keep collection and offline auditing explicit; never flash/configure the radar."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    record = commands.add_parser("record")
    record.add_argument("--port", required=True)
    record.add_argument("--count", type=int, default=32)
    record.add_argument("--interval-s", type=float, default=0.1)
    record.add_argument("--output", required=True, type=Path)
    record.add_argument(
        "--compact", action="store_true", help="requires the compact scratch firmware"
    )
    check = commands.add_parser("audit")
    check.add_argument("input", type=Path)
    check.add_argument("--output", required=True, type=Path)
    check.add_argument("--rate-min-hz", required=True, type=int)
    check.add_argument("--rate-max-hz", required=True, type=int)
    check.add_argument("--max-distance-ns", required=True, type=int)
    check.add_argument("--target-tick", type=int)
    check.add_argument("--target-build")
    check.add_argument("--target-boot")
    args = parser.parse_args(argv)
    try:
        with args.output.open("x", encoding="utf-8") as handle:
            if args.command == "record":

                def emit(row):
                    handle.write(json.dumps(row) + "\n")
                    handle.flush()

                with IWR6843Radar(port=args.port) as radar:
                    recorder = record_compact_probes if args.compact else record_probes
                    recorder(radar, emit, count=args.count, interval_s=args.interval_s)
                return 0
            data = args.input.read_bytes()
            records = [json.loads(line) for line in data.decode("utf-8").splitlines()]
            target_values = [args.target_tick, args.target_build, args.target_boot]
            if any(value is not None for value in target_values) and any(
                value is None for value in target_values
            ):
                raise ValueError("target needs tick, build and runtime token together")
            target = (
                None
                if args.target_tick is None
                else {
                    "tick": args.target_tick,
                    "build": args.target_build,
                    "boot": args.target_boot,
                }
            )
            result = audit(
                records,
                rate_hz_bounds=(args.rate_min_hz, args.rate_max_hz),
                max_distance_ns=args.max_distance_ns,
                target=target,
            )
            result["source_sha256"] = hashlib.sha256(data).hexdigest()
            handle.write(json.dumps(result, indent=2) + "\n")
            return 0 if result["status"] != "unavailable" else 1
    except (OSError, ValueError, RuntimeError) as error:
        print(f"IWR clock probe failed: {type(error).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

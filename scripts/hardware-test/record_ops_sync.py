#!/usr/bin/env python3
"""Record input-only OPS sync edges or audit a retained recording and raw captures."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import platform
import re
import sys
import time
import uuid
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from openflight.ops_sync import analyze_recording  # noqa: E402


def record_gpio(
    chip_path, line, duration_s, emit, *, api=None, now=time.monotonic_ns, boot_id=None
):
    """Request one input line exclusively; save kernel sequence counters and times."""
    if not math.isfinite(duration_s) or duration_s <= 0 or type(line) is not int or line < 0:
        raise ValueError("duration must be positive and finite; line must be nonnegative")
    if api is None:
        import gpiod as api  # pylint: disable=import-outside-toplevel,import-error

    with api.Chip(chip_path) as chip:
        info = chip.get_line_info(line)
        label = chip.get_info().label
        if info.used:
            raise ValueError("GPIO line is already in use")
        name = info.name
    settings = api.LineSettings(
        direction=api.line.Direction.INPUT,
        edge_detection=api.line.Edge.BOTH,
        bias=api.line.Bias.AS_IS,
        event_clock=api.line.Clock.MONOTONIC,
    )
    with api.request_lines(
        chip_path,
        consumer="openflight-ops-sync-research",
        config={line: settings},
        event_buffer_size=256,
    ) as request:
        initial = int(request.get_value(line) == api.line.Value.ACTIVE)
        started = now()
        emit(
            {
                "type": "start",
                "schema": "openflight.ops-sync.v1",
                "boot_id": boot_id
                or Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip(),
                "recording_id": uuid.uuid4().hex,
                "event_clock": "monotonic",
                "line_offset": line,
                "line_name": name,
                "chip_label": label,
                "initial_level": initial,
                "mono_ns": started,
                "gpiod_version": api.__version__,
                "kernel": platform.release(),
                "physical_edge_uncertainty_ns": None,
            }
        )
        count, error = 0, None
        deadline = started + math.ceil(duration_s * 1e9)
        try:
            while (remaining := deadline - now()) > 0:
                if not request.wait_edge_events(timedelta(seconds=min(remaining / 1e9, 0.1))):
                    continue
                for edge in request.read_edge_events(max_events=64):
                    count += 1
                    polarity = (
                        "rising" if edge.event_type == api.EdgeEvent.Type.RISING_EDGE else "falling"
                    )
                    emit(
                        {
                            "type": "edge",
                            "timestamp_ns": edge.timestamp_ns,
                            "edge": polarity,
                            "line_offset": edge.line_offset,
                            "global_seqno": edge.global_seqno,
                            "line_seqno": edge.line_seqno,
                            "received_mono_ns": now(),
                        }
                    )
        except BaseException as exc:
            error = type(exc).__name__
            raise
        finally:
            pending = True
            try:
                pending = request.wait_edge_events(timedelta(0))
            finally:
                emit(
                    {
                        "type": "end",
                        "mono_ns": now(),
                        "edge_count": count,
                        "pending_at_end": pending,
                        "error": error,
                    }
                )


def _captures(path):
    from openflight.rolling_buffer.processor import (
        RollingBufferProcessor,  # pylint: disable=import-outside-toplevel
    )

    encoded = path.read_bytes()
    manifest = json.loads(encoded)
    if manifest.get("schema") != "openflight.ops-sync-captures.v1":
        raise ValueError("unsupported capture manifest schema")
    captures = manifest.get("captures")
    if not isinstance(captures, list):
        raise ValueError("capture manifest needs a captures list")
    root = path.parent.resolve()
    validated = []
    processor = RollingBufferProcessor()
    for row in captures:
        relative = Path(row["raw_capture"])
        source = (root / relative).resolve()
        if relative.is_absolute() or not source.is_relative_to(root):
            raise ValueError("raw capture must stay within the manifest directory")
        raw = source.read_bytes()
        parsed = processor.parse_capture(raw.decode("utf-8"))
        unique = all(
            raw.count(key) == 1 for key in (b'"sample_time"', b'"trigger_time"', b'"I"', b'"Q"')
        )
        validated.append(
            {
                **row,
                "raw_sha256": hashlib.sha256(raw).hexdigest(),
                "dump_complete": parsed is not None and unique,
                "sample_count": len(parsed.i_samples) if parsed is not None else 0,
            }
        )
    return validated, hashlib.sha256(encoded).hexdigest()


def export_captures(session_path, output_dir):
    """Extract original byte evidence and conservative windows from a live session."""
    encoded = session_path.read_bytes()
    rows, originals, ids = [], [], set()
    for line in encoded.decode("utf-8").splitlines():
        if not line.strip():
            continue
        evidence = json.loads(line)
        if not isinstance(evidence, dict):
            raise ValueError("session entries must be JSON objects")
        if evidence.get("type") != "ops_capture_evidence":
            continue
        if evidence.get("schema") != "openflight.ops-capture-evidence.v1":
            raise ValueError("unsupported capture evidence schema")
        identity = evidence.get("capture_id")
        if not isinstance(identity, str) or re.fullmatch(r"[a-f0-9]{32}", identity) is None:
            raise ValueError("capture identity must be a lowercase UUID hex string")
        if identity in ids:
            raise ValueError("duplicate capture identity")
        ids.add(identity)
        raw = base64.b64decode(evidence["raw_capture_base64"], validate=True)
        if hashlib.sha256(raw).hexdigest() != evidence.get("raw_sha256"):
            raise ValueError("original capture hash mismatch")
        issues = list(evidence.get("evidence_issues", []))
        if evidence.get("error") or evidence.get("outcome") != "capture":
            issues.append("capture_did_not_complete_normally")
        if evidence.get("arm_command_error"):
            issues.append("arm_command_incomplete")
        bounds = [
            evidence.get(key)
            for key in (
                "armed_mono_ns",
                "arm_command_completed_mono_ns",
                "wait_started_mono_ns",
                "dump_start_mono_ns",
                "response_completed_mono_ns",
            )
        ]
        if not all(type(value) is int and value >= 0 for value in bounds) or bounds != sorted(
            bounds
        ):
            issues.append("invalid_command_or_read_bounds")
        if (
            evidence.get("clock_domain") != "host_monotonic"
            or evidence.get("arm_observation") != "host_command_bounds_not_device_acknowledgement"
            or not evidence.get("boot_id")
            or type(evidence.get("raw_bytes_received")) is not int
            or evidence["raw_bytes_received"] != len(raw)
        ):
            issues.append("invalid_evidence_context")
        filename = f"{identity}.raw"
        rows.append(
            {
                "capture_id": identity,
                "boot_id": evidence.get("boot_id"),
                "armed_mono_ns": bounds[0],
                "dump_start_mono_ns": bounds[3],
                "raw_capture": filename,
                "evidence_issues": issues,
                "arm_observation": evidence.get("arm_observation"),
            }
        )
        originals.append((filename, raw))
    if not rows:
        raise ValueError("session contains no OPS capture evidence")
    manifest = {
        "schema": "openflight.ops-sync-captures.v1",
        "captures": rows,
        "source_session_sha256": hashlib.sha256(encoded).hexdigest(),
        "physical_timing_qualified": False,
    }
    output_dir.mkdir(parents=True, exist_ok=False)
    for filename, raw in originals:
        with (output_dir / filename).open("xb") as stream:
            stream.write(raw)
    with (output_dir / "captures.json").open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(manifest, indent=2, allow_nan=False) + "\n")
    return manifest


def audit(path, captures_path=None):
    """Hash original inputs; capture completeness comes from the existing I/Q parser."""
    raw = path.read_bytes()
    records = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    captures, manifest_hash = _captures(captures_path) if captures_path is not None else ([], None)
    return {
        **analyze_recording(records, captures),
        "recording_sha256": hashlib.sha256(raw).hexdigest(),
        "capture_manifest_sha256": manifest_hash,
        "association_context": (
            "Supplied arm/dump windows must be observed on the declared boot; "
            "no time is inferred from serial arrival or pulse width."
        ),
    }


def main(argv=None):
    """Run an explicit input-only recording or an offline evidence audit."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    recorder = commands.add_parser("record", help="input-only GPIO observer; no radar commands")
    recorder.add_argument("--chip", required=True)
    recorder.add_argument("--line", required=True, type=int)
    recorder.add_argument("--duration-s", required=True, type=float)
    recorder.add_argument("--output", required=True, type=Path)
    analyzer = commands.add_parser("audit")
    analyzer.add_argument("recording", type=Path)
    analyzer.add_argument("--captures", type=Path)
    analyzer.add_argument("--output", required=True, type=Path)
    exporter = commands.add_parser(
        "export", help="extract retained live evidence for offline audit"
    )
    exporter.add_argument("session", type=Path)
    exporter.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "export":
            result = export_captures(args.session, args.output_dir)
            print(json.dumps(result, indent=2, allow_nan=False))
            return int(any(row["evidence_issues"] for row in result["captures"]))
        if args.command == "record":
            with args.output.open("x", encoding="utf-8") as stream:

                def emit(record):
                    stream.write(json.dumps(record, allow_nan=False) + "\n")
                    stream.flush()

                record_gpio(args.chip, args.line, args.duration_s, emit)
            result = audit(args.output)
        else:
            result = audit(args.recording, args.captures)
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(json.dumps(result, indent=2, allow_nan=False) + "\n")
        print(json.dumps(result, indent=2, allow_nan=False))
        return int(
            bool(result["stream_issues"])
            or any(row["status"] != "associated_unqualified" for row in result["captures"])
        )
    except (OSError, ValueError, KeyError, TypeError, ImportError) as error:
        print(f"OPS sync evidence failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Replay manifest-declared camera-fusion sensitivity variants offline."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

from openflight.camera.fusion_sensitivity import run_sensitivity

try:
    from scripts.analysis.replay_camera_fusion import _write_output, replay_frozen_shot
except ModuleNotFoundError:
    from replay_camera_fusion import _write_output, replay_frozen_shot


def _manifest(path: Path) -> tuple[dict, str]:
    raw = path.read_bytes()
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"sensitivity manifest is not valid JSON: {error}") from error
    if not isinstance(value, dict):
        raise ValueError("sensitivity manifest must be an object")
    return value, hashlib.sha256(raw).hexdigest()


def _events(raw: bytes) -> list[dict]:
    events = []
    for line in raw.splitlines():
        if line.strip():
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError("session JSONL records must be objects")
            events.append(value)
    return events


def _protect_output(output: Path, protected: set[Path], capture_directory: Path) -> None:
    destination = output.expanduser().resolve()
    if destination == capture_directory or capture_directory in destination.parents:
        raise ValueError("output cannot overwrite a recorded capture artifact")
    if destination.exists() and any(os.path.samefile(destination, item) for item in protected):
        raise ValueError("output aliases a replay input")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session_file", type=Path)
    parser.add_argument("shot_number", type=int)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--capture", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        manifest, manifest_sha256 = _manifest(args.manifest)
        session_raw = args.session_file.read_bytes()
        events = _events(session_raw)
        frozen = replay_frozen_shot(
            events, args.session_file, args.shot_number, capture=args.capture
        )
        report = run_sensitivity(frozen, manifest)
        report["inputs"] = {
            "session_sha256": hashlib.sha256(session_raw).hexdigest(),
            "capture_npz_sha256": frozen["capture_npz_sha256"],
            "sensitivity_manifest_sha256": manifest_sha256,
            "session_uuid": frozen["session_uuid"],
            "shot_number": frozen["shot_number"],
        }
        protected = {
            args.session_file.expanduser().resolve(),
            args.manifest.expanduser().resolve(),
            Path(frozen["capture_path"]).resolve(),
        }
        capture_directory = Path(frozen["capture_path"]).resolve().parent
        _protect_output(args.output, protected, capture_directory)
        _write_output(args.output, report, protected)
    except (KeyError, OSError, ValueError) as error:
        print(f"camera fusion sensitivity replay failed: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

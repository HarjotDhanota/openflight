#!/usr/bin/env python3
"""Replay the recorded camera fusion core from one immutable shot context."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy as np

from openflight.camera.fusion_processing import process_camera_fusion
from openflight.raw_radar_replay import locate_recorded_capture


def _read_events(session_file: Path) -> list[dict[str, Any]]:
    raw = session_file.read_bytes()
    events = []
    for line in raw.splitlines():
        if line.strip():
            event = json.loads(line)
            if isinstance(event, dict):
                events.append(event)
    return events


def _one(items: list[Any], description: str):
    if len(items) != 1:
        raise ValueError(f"expected exactly one {description}, found {len(items)}")
    return items[0]


def _capture_file(recorded: str, run_dir: Path, override: Path | None) -> Path:
    if override is not None:
        selected = override.expanduser().resolve(strict=True)
    else:
        try:
            selected, _resolution = locate_recorded_capture(recorded, run_dir)
        except FileNotFoundError as error:
            raise ValueError(f"{error}; use --capture") from error
    if selected.is_dir():
        selected = selected / "frames.npz"
    if not selected.is_file() or selected.name != "frames.npz":
        raise ValueError("camera capture must be a frames.npz file or its containing directory")
    return selected


def _comparison_mismatches(recorded: Any, replayed: Any, path: str = "$") -> list[str]:
    if isinstance(recorded, dict) and isinstance(replayed, dict):
        mismatches = []
        if recorded.keys() != replayed.keys():
            mismatches.append(f"{path}: object keys differ")
        for key in recorded.keys() & replayed.keys():
            mismatches.extend(_comparison_mismatches(recorded[key], replayed[key], f"{path}.{key}"))
        return mismatches
    if isinstance(recorded, list) and isinstance(replayed, list):
        if len(recorded) != len(replayed):
            return [f"{path}: list lengths differ"]
        mismatches = []
        for index, (left, right) in enumerate(zip(recorded, replayed)):
            mismatches.extend(_comparison_mismatches(left, right, f"{path}[{index}]"))
        return mismatches
    if isinstance(recorded, bool) or isinstance(replayed, bool):
        return [] if type(recorded) is type(replayed) and recorded == replayed else [path]
    if isinstance(recorded, int) or isinstance(replayed, int):
        return [] if type(recorded) is type(replayed) and recorded == replayed else [path]
    if isinstance(recorded, float) and isinstance(replayed, float):
        return [] if math.isclose(recorded, replayed, rel_tol=0.0, abs_tol=1e-9) else [path]
    return [] if type(recorded) is type(replayed) and recorded == replayed else [path]


def replay_recorded_shot(
    session_file: Path, shot_number: int, *, capture: Path | None = None
) -> dict[str, Any]:
    """Replay a shot bound to its session identity and exact saved NPZ bytes."""
    events = _read_events(session_file)
    result = replay_frozen_shot(events, session_file, shot_number, capture=capture)
    result.pop("_context")
    result.pop("_archive")
    return result


def replay_frozen_shot(
    events: list[dict[str, Any]],
    session_file: Path,
    shot_number: int,
    *,
    capture: Path | None = None,
) -> dict[str, Any]:
    """Replay from caller-frozen session events while reading capture bytes once."""
    start = _one(
        [event for event in events if event.get("type") == "session_start"], "session_start"
    )
    session_uuid = start.get("session_uuid")
    shot = _one(
        [
            event
            for event in events
            if event.get("type") == "shot_detected" and event.get("shot_number") == shot_number
        ],
        "shot_detected event",
    )
    context = shot.get("camera_fusion_context")
    if not isinstance(context, dict) or context.get("available") is not True:
        raise ValueError("shot has no replayable camera fusion context")
    if context.get("session_uuid") != session_uuid or context.get("shot_number") != shot_number:
        raise ValueError("camera fusion context identity does not match the session and shot")
    capture_event = _one(
        [
            event
            for event in events
            if event.get("type") == "camera_capture"
            and event.get("shot_number") == shot_number
            and not event.get("capture_error")
        ],
        "successful camera_capture event",
    )
    recorded_path = capture_event.get("capture_path")
    if not isinstance(recorded_path, str) or not recorded_path:
        raise ValueError("camera_capture event has no recorded directory")
    run_dir = session_file.resolve(strict=True).parent
    capture_path = _capture_file(recorded_path, run_dir, capture)
    capture_bytes = capture_path.read_bytes()
    capture_hash = hashlib.sha256(capture_bytes).hexdigest()
    if capture_hash != context.get("capture_npz_sha256"):
        raise ValueError("camera capture hash does not match the recorded context")
    with np.load(io.BytesIO(capture_bytes), allow_pickle=False) as source:
        archive = {name: source[name] for name in source.files}
    archive["_capture_npz_sha256"] = capture_hash
    replay = process_camera_fusion(context, archive)
    recorded = shot.get("camera_fusion_processing")
    mismatches = _comparison_mismatches(recorded, replay) if isinstance(recorded, dict) else None
    return {
        "session_uuid": session_uuid,
        "shot_number": shot_number,
        "capture_path": str(capture_path),
        "capture_npz_sha256": capture_hash,
        "replay": replay,
        "recorded": recorded,
        "matches_recorded": not mismatches if mismatches is not None else None,
        "comparison": {
            "status": "compared" if mismatches is not None else "recorded_result_unavailable",
            "float_absolute_tolerance": 1e-9,
            "mismatches": mismatches,
        },
        "_context": context,
        "_archive": archive,
    }


def _write_output(path: Path, result: dict[str, Any], protected: set[Path]) -> None:
    destination = path.expanduser().resolve()
    if destination in protected:
        raise ValueError("output cannot overwrite a replay input")
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(result, indent=2, allow_nan=False) + "\n"
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=destination.parent, delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    """Parse the bounded replay inputs and emit strict JSON."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session_file", type=Path)
    parser.add_argument("shot_number", type=int)
    parser.add_argument("--capture", type=Path, help="relocated capture directory or frames.npz")
    parser.add_argument("--output", type=Path, help="atomically write JSON instead of stdout")
    args = parser.parse_args()
    try:
        result = replay_recorded_shot(args.session_file, args.shot_number, capture=args.capture)
        if args.output:
            protected = {args.session_file.expanduser().resolve()}
            protected.add(Path(result["capture_path"]).resolve())
            if args.capture:
                capture_input = args.capture.expanduser().resolve()
                protected.add(
                    capture_input / "frames.npz" if capture_input.is_dir() else capture_input
                )
            _write_output(args.output, result, protected)
        else:
            print(json.dumps(result, indent=2, allow_nan=False))
    except (KeyError, OSError, ValueError) as error:
        print(f"camera fusion replay failed: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

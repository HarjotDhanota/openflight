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

from openflight.camera.club_delivery import ReferenceBallTracker
from openflight.camera.fusion_processing import build_context, lighting_note, process_camera_fusion
from openflight.camera.geometry_contract import EffectiveCameraGeometryInputs
from openflight.clubs import ClubType
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
    recorded_context = isinstance(context, dict) and context.get("available") is True
    if recorded_context and (
        context.get("session_uuid") != session_uuid or context.get("shot_number") != shot_number
    ):
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
    reconstruction: list[str] = []
    if recorded_context:
        if capture_hash != context.get("capture_npz_sha256"):
            raise ValueError("camera capture hash does not match the recorded context")
    else:
        # D15 (P8-7): only missing frames refuse; the rest is rebuilt, labelled
        context, reconstruction = reconstruct_context(start, shot, capture_event, capture_hash)
    with np.load(io.BytesIO(capture_bytes), allow_pickle=False) as source:
        archive = {name: source[name] for name in source.files}
    archive["_capture_npz_sha256"] = capture_hash
    replay = process_camera_fusion(context, archive)
    recorded = shot.get("camera_fusion_processing") if recorded_context else None
    mismatches = _comparison_mismatches(recorded, replay) if isinstance(recorded, dict) else None
    return {
        "session_uuid": session_uuid,
        "shot_number": shot_number,
        "capture_path": str(capture_path),
        "capture_npz_sha256": capture_hash,
        "context_source": "recorded" if recorded_context else "reconstructed",
        "reconstruction": reconstruction,
        "replay": replay,
        "recorded": recorded,
        "matches_recorded": not mismatches if mismatches is not None else None,
        "comparison": {
            "status": (
                "compared"
                if mismatches is not None
                else "recorded_result_unavailable"
                if recorded_context
                else "reconstructed_context_not_compared"
            ),
            "float_absolute_tolerance": 1e-9,
            "mismatches": mismatches,
        },
        "_context": context,
        "_archive": archive,
    }


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _reconstructed_geometry(config: dict[str, Any]) -> EffectiveCameraGeometryInputs:
    """The geometry the kiosk would have frozen, from what the session recorded."""
    recorded = _mapping(config.get("effective_camera_geometry"))
    if recorded.get("available") is True:
        return EffectiveCameraGeometryInputs.from_recorded_session(
            {"effective_camera_geometry": recorded}
        )
    iwr = _mapping(config.get("iwr6843"))
    if iwr.get("tee_slant_range_m") is None:
        handoff = _mapping(config.get("tee_range_handoff"))
        raise ValueError(
            "cannot reconstruct the camera context: the session recorded no tee range "
            f"(tee_range_handoff status {handoff.get('status')!r}, source "
            f"{handoff.get('source')!r}); the camera's geometry needs one"
        )
    return EffectiveCameraGeometryInputs.from_recorded_session(
        {"camera_capture": _mapping(config.get("camera_capture")), "iwr6843": iwr}
    )


def reconstruct_context(
    start: dict[str, Any], shot: dict[str, Any], capture_event: dict[str, Any], capture_hash: str
) -> tuple[dict[str, Any], list[str]]:
    """Rebuild a shot's camera fusion context from the clip, its metadata and the session.

    A kiosk that withheld camera fusion froze no context (Outdoors-test-5, -7).
    Everything the kiosk would have frozen is recorded elsewhere, except the
    IWR range tracks (the replay's own IWR stage supplies them) and the
    trackers' state from earlier shots. Each gap is named.
    """
    config = _mapping(start.get("config"))
    camera = _mapping(config.get("camera_capture"))
    recorded = _mapping(shot.get("camera_fusion_context"))
    notes = [
        "context reconstructed from the saved clip, its metadata and the session records; "
        f"the kiosk froze none ({recorded.get('reason') or 'no reason recorded'})",
        "the resting-ball trackers start empty: earlier shots' state was not recorded",
        "IWR range evidence is not in the session log; the replay's IWR stage supplies it",
    ]
    geometry = _reconstructed_geometry(config)
    auto_exposure = _mapping(_mapping(capture_event.get("metadata")).get("auto_exposure"))
    eligible = bool(auto_exposure.get("analysis_eligible", True))
    club = shot.get("club")
    if not isinstance(club, str) or not club:
        club = ClubType.DRIVER.value
        notes.append("the shot recorded no club; driver assumed")
    vertical = (
        shot.get("launch_angle_vertical")
        if shot.get("launch_angle_vertical_source") == "radar"
        else None
    )
    context = build_context(
        geometry=geometry,
        lighting_eligible=eligible,
        ball_tracker=ReferenceBallTracker(),
        club_tracker=ReferenceBallTracker(),
        ball_range_evidence=None,
        club_range_evidence=None,
        ops_ball_speed_mph=shot.get("ball_speed_raw_mph") or shot.get("ball_speed_mph"),
        ops_club_speed_mph=shot.get("club_speed_mph"),
        iwr_vertical_deg=vertical,
        iwr_horizontal_deg=shot.get("iwr6843_horizontal_deg"),
        iwr_horizontal_confidence=shot.get("iwr6843_horizontal_confidence"),
        club=ClubType(club),
        capture_npz_sha256=capture_hash,
        session_uuid=start["session_uuid"],
        shot_number=shot["shot_number"],
        notes=[] if eligible else [lighting_note(auto_exposure)],
        setup_ball=camera.get("setup_ball"),
    )
    return context, notes


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

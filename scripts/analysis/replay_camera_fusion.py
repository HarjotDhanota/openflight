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

from openflight.camera.fallback_chain import (
    ball_range_resolution,
    fallback_inputs,
    mode_focal,
    size_range_route,
)
from openflight.camera.fusion_processing import (
    build_context,
    lens_record_focal_px,
    lighting_note,
    process_camera_fusion,
)
from openflight.camera.geometry_contract import EffectiveCameraGeometryInputs
from openflight.camera.head_track import gate_latency_ns
from openflight.clubs import ClubType
from openflight.iwr6843.launch_range import LaunchRangePrior
from openflight.raw_radar_replay import locate_recorded_capture
from openflight.rig_geometry import RigGeometry
from openflight.shot_updates import apply_shot_updates


def _read_events(session_file: Path) -> list[dict[str, Any]]:
    raw = session_file.read_bytes()
    events = []
    for line in raw.splitlines():
        if line.strip():
            event = json.loads(line)
            if isinstance(event, dict):
                events.append(event)
    # a late shot_updated carries the camera fusion context the first result lacked
    return apply_shot_updates(events)


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
    with np.load(io.BytesIO(capture_bytes), allow_pickle=False) as source:
        archive = {name: source[name] for name in source.files}
    archive["_capture_npz_sha256"] = capture_hash
    if recorded_context:
        if capture_hash != context.get("capture_npz_sha256"):
            raise ValueError("camera capture hash does not match the recorded context")
    else:
        # D15 (P8-7): only missing frames refuse; the rest is rebuilt, labelled
        metadata_path = capture_path.parent / "metadata.json"
        metadata = json.loads(metadata_path.read_text()) if metadata_path.is_file() else None
        context, reconstruction = reconstruct_context(
            start,
            shot,
            capture_event,
            capture_hash,
            archive=archive,
            gate_latency=gate_latency_ns(metadata),
        )
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


def _reconstructed_geometry(
    config: dict[str, Any],
    *,
    shot: dict[str, Any] | None = None,
    archive: dict[str, Any] | None = None,
) -> tuple[EffectiveCameraGeometryInputs, dict[str, Any] | None]:
    """The geometry the kiosk would have frozen, and the ball range it rests on.

    Without a recorded tee range the kiosk's own range chain gives one (the shot's IWR
    launch point, the camera prior, the resting ball's own size in this clip), labelled.
    """
    recorded = _mapping(config.get("effective_camera_geometry"))
    if recorded.get("available") is True:
        return (
            EffectiveCameraGeometryInputs.from_recorded_session(
                {"effective_camera_geometry": recorded}
            ),
            None,
        )
    iwr = dict(_mapping(config.get("iwr6843")))
    camera = _mapping(config.get("camera_capture"))
    ball_range = None
    if iwr.get("tee_slant_range_m") is None:
        resolution = _ball_range_chain(config, shot or {}, archive)
        if not resolution.present:
            handoff = _mapping(config.get("tee_range_handoff"))
            raise ValueError(
                "cannot reconstruct the camera context: the session recorded no tee range "
                f"(tee_range_handoff status {handoff.get('status')!r}, source "
                f"{handoff.get('source')!r}) and no route gave one: {resolution.reason}"
            )
        ball_range = resolution.to_dict()
        if resolution.details:
            ball_range["details"] = dict(resolution.details)
        scene = _mapping(config.get("scene"))
        iwr["tee_slant_range_m"] = float(resolution.value)
        iwr.setdefault("radar_height_m", scene.get("radar_height_m"))
        iwr.setdefault("ball_height_m", scene.get("ball_height_m"))
    return (
        EffectiveCameraGeometryInputs.from_recorded_session(
            {"camera_capture": camera, "iwr6843": iwr}
        ),
        ball_range,
    )


def _camera_origin(camera: dict[str, Any]) -> tuple[float, float, float]:
    return (
        float(camera.get("lateral_offset_m") or 0.0),
        float(camera.get("forward_offset_m") or 0.0),
        float(camera.get("mount_height_m") or 0.0),
    )


def _rig(config: dict[str, Any]):
    parameters = _mapping(_mapping(config.get("rig_geometry")).get("snapshot")).get("parameters")
    return RigGeometry(**parameters) if parameters else None


def _ball_range_chain(config: dict[str, Any], shot: dict[str, Any], archive):
    """The kiosk's ball-range chain (``camera.fallback_chain``) on the recorded session."""
    camera = _mapping(config.get("camera_capture"))
    iwr = _mapping(config.get("iwr6843"))
    scene = _mapping(config.get("scene"))
    width = int(np.shape(archive["frames"])[-1]) if archive else 0
    focal = mode_focal(width, lens=camera.get("lens"), rig=_rig(config))[0] if width else None
    radar_height = iwr.get("radar_height_m") or scene.get("radar_height_m")
    return ball_range_resolution(
        shot_range={
            name: shot.get(name)
            for name in (
                "ball_range_m",
                "ball_range_source",
                "ball_range_uncertainty_m",
                "ball_range_reason",
            )
        },
        prior=LaunchRangePrior.from_dict(iwr.get("range_prior")),
        size_route=size_range_route(
            archive,
            seed=camera.get("setup_ball"),
            lens_focal_px=focal,
            calibrated_focal_px=None,
            camera_origin_lfu=_camera_origin(camera),
            radar_origin_lfu=(0.0, 0.0, float(radar_height or _camera_origin(camera)[2])),
            ball_height_m=iwr.get("ball_height_m") or scene.get("ball_height_m"),
            roll_correction_deg=float(camera.get("roll_correction_deg") or 0.0),
            horizontal_pixel_sign=-1.0 if camera.get("mirror_horizontal") else 1.0,
        ),
    )


def reconstruct_context(
    start: dict[str, Any],
    shot: dict[str, Any],
    capture_event: dict[str, Any],
    capture_hash: str,
    *,
    archive: dict[str, Any] | None = None,
    gate_latency: float | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Rebuild a shot's camera fusion context from the clip, its metadata and the session.

    A kiosk that withheld camera fusion froze no context (Outdoors-test-5, -7).
    Everything the kiosk would have frozen is recorded elsewhere, except the
    IWR range tracks (the replay's own IWR stage supplies them). Each gap is named.
    """
    config = _mapping(start.get("config"))
    camera = _mapping(config.get("camera_capture"))
    recorded = _mapping(shot.get("camera_fusion_context"))
    notes = [
        "context reconstructed from the saved clip, its metadata and the session records; "
        f"the kiosk froze none ({recorded.get('reason') or 'no reason recorded'})",
        "IWR range evidence is not in the session log; the replay's IWR stage supplies it",
    ]
    geometry, ball_range = _reconstructed_geometry(config, shot=shot, archive=archive)
    aim_sigma = _mapping(_mapping(_mapping(config.get("setup")).get("record")).get("aim")).get(
        "uncertainty_deg"
    )
    if ball_range is not None:
        notes.append(
            f"the session recorded no tee range: the ball range came from {ball_range['source']} "
            f"({ball_range['label']})"
        )
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
        lens_focal_px=lens_record_focal_px(
            camera.get("lens") or _mapping(config.get("rig_geometry")).get("lens"),
            int(geometry.image_width_px),
        ),
        ball_range=ball_range,
        camera_fallback=fallback_inputs(
            focal=mode_focal(
                int(geometry.image_width_px), lens=camera.get("lens"), rig=_rig(config)
            ),
            ops_offset_mm=getattr(_rig(config), "ops_offset_mm", None),
            camera_origin_lfu=_camera_origin(camera),
            target_line={
                **_mapping(config.get("target_line")),
                "total_uncertainty_deg": aim_sigma,
            },
            lis3dh_camera_pitch_deg=None,
        ),
        handedness=shot.get("handed") or _mapping(config.get("golfer")).get("handed") or "RH",
        gate_latency_ns=gate_latency,
        # the kiosk's target line carries the setup aim's uncertainty (apply_setup_aim)
        target_yaw_sigma_deg=aim_sigma,
    )
    notes.append(
        "the camera fallback's LIS3DH pitch check is not in the session start: the replay "
        "carries none"
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

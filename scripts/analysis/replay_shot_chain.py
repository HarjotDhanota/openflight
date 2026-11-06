#!/usr/bin/env python3
"""Replay recorded shots through the kiosk's own chain: the same values the kiosk gives.

Each shot is rebuilt from its recorded sensor readings and ball-reference evidence,
its camera clip is loaded, and the kiosk's early resting-ball measurement and reference
check run before ``server._fuse_camera_measurements``
(the ball-range chain, the shared camera fusion with the camera fallback, the launch
chain), the shot models' hooks, and ``server._finalize_shot_detected`` (full speeds,
spin, carry). The OPS readings after impact are re-derived from the saved I/Q, as the
kiosk derived them, for the full ball speed.

``--ignore-iwr`` drops the IWR's launch and range, to force the camera fallback on a
session whose IWR worked (the cross-check). Output: one JSON record per shot with the
kiosk's logged metrics (before) and the replayed ones (after).
Legacy context seeds and aim uncertainty are restored only after identity/hash checks.
Missing address look/freshness remains unknown; IWR extraction is not rerun. This is
an offline reconstruction using the current code, not a validation of live readiness.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import sys
from copy import deepcopy
from dataclasses import fields
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np

from openflight import kiosk_extensions, server, shot_models
from openflight.clubs import ClubType
from openflight.iwr6843.launch_range import LaunchRangePrior
from openflight.launch_monitor import Shot
from openflight.ops_speed_track import ops_speed_track
from openflight.raw_radar_replay import load_session_events, locate_recorded_capture
from openflight.rig_geometry import RigGeometry, geometry_fingerprint

# The sensor readings a shot is rebuilt from; everything else the chain derives again.
OPS_FIELDS = (
    "ball_speed_mph",
    "club_speed_mph",
    "shot_number",
    "impact_timestamp",
    "mode",
    "handed",
    "handed_source",
    "profile_id",
    "profile_name",
    "spin_rpm",
    "spin_confidence",
    "spin_method",
    "spin_multipath_fade_hz",
    "spin_snr",
    "spin_modulation_depth",
    "spin_peak_freq_hz",
    "spin_candidate_rpm",
    "spin_seam_cycles",
    "spin_at_lower_rail",
    "spin_at_upper_rail",
    "spin_candidates",
    "spin_rejection_reason",
    "inclinometer",
)
IWR_FIELDS = (
    "launch_angle_vertical",
    "launch_angle_vertical_source",
    "launch_angle_vertical_confidence",
    "launch_angle_confidence",
    "angle_source",
    "launch_angle_horizontal",
    "launch_angle_horizontal_source",
    "launch_angle_horizontal_confidence",
    "launch_angle_horizontal_status",
    "launch_angle_horizontal_frame",
    "launch_angle_horizontal_reason",
    "iwr6843_horizontal_deg",
    "iwr6843_horizontal_confidence",
    "ball_range_m",
    "ball_range_source",
    "ball_range_uncertainty_m",
    "ball_range_label",
    "ball_range_reason",
    "iwr_launch_range",
    "experimental_club_path_deg",
    "experimental_club_path_status",
    "experimental_club_path_frame",
    "experimental_attack_angle_deg",
    "experimental_attack_angle_status",
)
RADAR_SOURCES = ("radar", "radar_low_coherence")


def configure_kiosk(start: dict, *, ignore_iwr: bool, aim_uncertainty_deg: float | None) -> None:
    """Set the kiosk's state from the session's start record, as the kiosk had it."""
    config = start.get("config") or {}
    camera = dict(config.get("camera_capture") or {})
    rig = config.get("rig_geometry") or {}
    parameters = (rig.get("snapshot") or {}).get("parameters")
    server.camera_capture_config = camera
    server.rig_geometry = RigGeometry(**parameters) if parameters else None
    server.rig_geometry_config = {"enabled": bool(parameters), "lens": camera.get("lens")}
    line = dict(config.get("target_line") or {"status": "not_set"})
    if aim_uncertainty_deg is not None:
        line["total_uncertainty_deg"] = float(aim_uncertainty_deg)
    server.setup_handoff_config = {"scene": dict(config.get("scene") or {}), "target_line": line}
    server.horizontal_offset_deg = line.get("horizontal_offset_deg")
    iwr = config.get("iwr6843") or {}
    if iwr.get("enabled") and not ignore_iwr:
        server.iwr6843_runtime = SimpleNamespace(
            # a dataclass, as the IWR's calibration is: each shot's range replaces its own
            calibration=server.SceneCalibration(
                tee_range_m=iwr.get("tee_slant_range_m"),
                tee_range_uncertainty_m=0.0,
                radar_height_m=iwr.get("radar_height_m"),
                tee_ball_height_m=iwr.get("ball_height_m"),
            ),
            range_prior=LaunchRangePrior.from_dict(iwr.get("range_prior")),
            horizontal_phase_reference_rad=iwr.get("horizontal_phase_reference_rad"),
        )
    else:
        server.iwr6843_runtime = None
    server.camera_capture_runtime = SimpleNamespace(camera_analysis_eligible=True)
    server.camera_optical_calibration = None
    server.camera_tilt_calibration_path = None
    server.ball_at_address_monitor = None
    server.ballistics_enabled = True
    server.get_session_logger = lambda: None
    server._forward_shot_to_simulators = lambda shot: None  # pylint: disable=protected-access
    server.socketio.emit = lambda *args, **kwargs: None
    server._publish_phone_shot = lambda *args, **kwargs: None  # pylint: disable=protected-access
    kiosk_extensions._extensions = [shot_models.extension]  # pylint: disable=protected-access


def rebuild_shot(record: dict, *, ignore_iwr: bool) -> Shot:
    """A Shot holding only the recorded sensor readings."""
    names = {item.name for item in fields(Shot)}
    values: dict[str, Any] = {name: record.get(name) for name in OPS_FIELDS if name in names}
    values["timestamp"] = datetime.fromisoformat(record["timestamp"])
    # the processor's spin quality label, logged as the shot's spin_quality
    values["spin_result_quality"] = record.get("spin_quality")
    values["club"] = ClubType(record.get("club") or ClubType.DRIVER.value)
    sources = record.get("input_sources") or {}
    evidence = {
        name: deepcopy(sources[name])
        for name in ("ball_reference", "ball_at_address")
        if isinstance(sources.get(name), dict)
    }
    if evidence:
        values["input_sources"] = evidence
    if not ignore_iwr:
        for name in IWR_FIELDS:
            if name in names and record.get(name) is not None:
                values[name] = record[name]
        if record.get("launch_angle_vertical_source") not in RADAR_SOURCES:
            for name in ("launch_angle_vertical", "launch_angle_vertical_source"):
                values.pop(name, None)
        if record.get("launch_angle_horizontal_source") not in RADAR_SOURCES:
            for name in IWR_FIELDS[5:11]:
                values.pop(name, None)
    else:
        values["ball_range_reason"] = "the IWR is ignored in this replay (forced fallback)"
        values["launch_angle_vertical_reason"] = "the IWR is ignored in this replay"
    values = {name: value for name, value in values.items() if value is not None}
    return Shot(**values)


def ops_track_for(events: list[dict], shot_number: int, club: str) -> dict | None:
    """The OPS readings after impact, from the saved I/Q, as the kiosk derived them."""
    from openflight.rolling_buffer.monitor import get_optimal_spin_for_ball_speed  # noqa: PLC0415
    from openflight.rolling_buffer.processor import RollingBufferProcessor  # noqa: PLC0415
    from openflight.rolling_buffer.types import IQCapture  # noqa: PLC0415

    entry = next(
        (
            event
            for event in events
            if event.get("type") == "rolling_buffer_capture"
            and event.get("shot_number") == shot_number
        ),
        None,
    )
    if entry is None or not entry.get("i_samples"):
        return None
    config = entry.get("processor_config") or {}
    capture = IQCapture(
        sample_time=float(entry["sample_time"]),
        trigger_time=float(entry["trigger_time"]),
        i_samples=list(entry["i_samples"]),
        q_samples=list(entry["q_samples"]),
        timestamp=float(entry.get("first_byte_timestamp") or 0.0),
    )
    processor = RollingBufferProcessor(sample_rate=int(config.get("sample_rate_hz") or 30000))
    club_type = ClubType(club)
    processed = processor.process_capture(
        capture,
        expected_spin_for_ball_speed=lambda speed: get_optimal_spin_for_ball_speed(
            speed, club_type
        ),
        club_type=club_type,
    )
    return ops_speed_track(processed) if processed is not None else None


def load_clip(events: list[dict], shot_number: int, run_dir: Path):
    """The shot's camera clip (capture stand-in and archive), or (None, None)."""
    event = next(
        (
            item
            for item in events
            if item.get("type") == "camera_capture"
            and item.get("shot_number") == shot_number
            and item.get("capture_path")
            and not item.get("capture_error")
        ),
        None,
    )
    if event is None:
        return None, None
    directory, _how = locate_recorded_capture(event["capture_path"], run_dir)
    directory = directory if directory.is_dir() else directory.parent
    frozen = (directory / "frames.npz").read_bytes()
    with np.load(io.BytesIO(frozen), allow_pickle=False) as data:
        archive = {name: data[name] for name in data.files}
    archive["_capture_npz_sha256"] = hashlib.sha256(frozen).hexdigest()
    metadata_path = directory / "metadata.json"
    metadata = (
        json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}
    )
    capture = SimpleNamespace(valid=True, path=str(directory), metadata=metadata)
    return capture, archive


def _plain(value: Any):
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _restore_recorded_inputs(shot, start, record, archive, line, aim_uncertainty_deg):
    """Restore observations, never derived launch/range outputs or invented readiness."""
    context = record.get("camera_fusion_context") or {}
    if context.get("available") is True:
        if (
            not start.get("session_uuid")
            or context.get("session_uuid") != start["session_uuid"]
            or context.get("shot_number") != record["shot_number"]
        ):
            raise ValueError("camera fusion context identity does not match the session and shot")
        payload = {key: value for key, value in context.items() if key != "sha256"}
        if context.get("sha256") != geometry_fingerprint(payload):
            raise ValueError("camera fusion context fingerprint mismatch")
        if archive is None or archive.get("_capture_npz_sha256") != context.get(
            "capture_npz_sha256"
        ):
            raise ValueError("camera fusion context does not match the capture archive")
    else:
        context = {}

    notes = ["IWR readings are retained, not re-extracted with the replayed range prior"]
    sources = shot.input_sources or {}
    if "ball_at_address" in sources:
        seed_source = "recorded_address"
    else:
        seed_source = "recorded_context" if "setup_ball" in context else "session_setup"
        seed = (
            context.get("setup_ball")
            if "setup_ball" in context
            else (server.camera_capture_config.get("setup_ball"))
        )
        sources["ball_at_address"] = {
            "look": None,
            "seed": deepcopy(seed),
            "range_prior_rejection": None,
        }
        notes.append("original address look/freshness absent; no readiness validation")
        if seed_source == "session_setup":
            notes.append("per-shot ball seed absent; session setup seed only")
    shot.input_sources = sources

    aim_source = "session_setup"
    if aim_uncertainty_deg is not None:
        line["total_uncertainty_deg"] = float(aim_uncertainty_deg)
        aim_source = "explicit_override"
    else:
        aim = (context.get("camera_fallback") or {}).get("horizontal_offset_sd_deg")
        if aim is not None:
            line["total_uncertainty_deg"] = float(aim)
            aim_source = "recorded_context"
    if line.get("total_uncertainty_deg") is None:
        aim_source = "unavailable"
        notes.append("aim uncertainty absent; replay uncertainty is incomplete")
    return {
        "ball_seed_source": seed_source,
        "aim_uncertainty_source": aim_source,
        "limitations": notes,
    }


def replay_shot(
    events,
    start,
    record,
    run_dir: Path,
    *,
    ignore_iwr: bool,
    aim_uncertainty_deg: float | None = None,
) -> dict:
    """One shot through the kiosk's chain; its before and after metrics."""
    number = record["shot_number"]
    shot = rebuild_shot(record, ignore_iwr=ignore_iwr)
    shot.camera_fusion_session_uuid = start.get("session_uuid")
    shot.ops_speed_track = ops_track_for(events, number, shot.club.value)
    capture, archive = load_clip(events, number, run_dir)
    handoff = server.setup_handoff_config
    line = deepcopy(handoff.get("target_line") or {})
    replay_inputs = _restore_recorded_inputs(
        shot, start, record, archive, line, aim_uncertainty_deg
    )
    server.setup_handoff_config = {**handoff, "target_line": line}
    try:
        reference = deepcopy((shot.input_sources or {}).get("ball_reference") or {})
        server._measure_resting_ball_early(shot, archive)  # pylint: disable=protected-access
        server._record_ball_reference_check(shot, archive)  # pylint: disable=protected-access
        if reference.get("range_prior_rejection"):
            current = shot.input_sources.setdefault("ball_reference", {})
            current["recorded_range_prior_rejection"] = reference["range_prior_rejection"]
            if not current.get("range_prior_rejection"):
                current["range_prior_rejection"] = reference["range_prior_rejection"]
        server._fuse_camera_measurements(shot, capture, archive)  # pylint: disable=protected-access
        server._isolated_stage(  # pylint: disable=protected-access
            shot, "after_enrichment", kiosk_extensions.call_all, "after_enrichment", shot
        )
        server._finalize_shot_detected(shot, emit_event="shot")  # pylint: disable=protected-access
    finally:
        server.setup_handoff_config = handoff
    after = shot.to_dict()
    return _plain(
        {
            "shot_number": number,
            "club": shot.club.value,
            "clip": getattr(capture, "path", None),
            "before": record.get("metrics") or {},
            "after": after["metrics"],
            "input_sources": after.get("input_sources"),
            "camera_fallback_launch": after.get("camera_fallback_launch"),
            "stage_errors": after.get("stage_errors"),
            "replay_inputs": replay_inputs,
            "recorded": {
                "launch_angle_vertical": record.get("launch_angle_vertical"),
                "launch_angle_vertical_source": record.get("launch_angle_vertical_source"),
                "launch_angle_horizontal": record.get("launch_angle_horizontal"),
                "launch_angle_horizontal_source": record.get("launch_angle_horizontal_source"),
                "ball_range_m": record.get("ball_range_m"),
                "ball_range_source": record.get("ball_range_source"),
            },
        }
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session_file", type=Path)
    parser.add_argument("--shots", help="comma-separated shot numbers (default: all)")
    parser.add_argument("--ignore-iwr", action="store_true", help="force the camera fallback")
    parser.add_argument(
        "--aim-uncertainty-deg",
        type=float,
        help="override recorded aim uncertainty; otherwise use the shot's frozen context "
        "when available, then the session setup",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    _hash, start, events = load_session_events(args.session_file)
    configure_kiosk(start, ignore_iwr=args.ignore_iwr, aim_uncertainty_deg=args.aim_uncertainty_deg)
    wanted = {int(item) for item in args.shots.split(",")} if args.shots else None
    run_dir = args.session_file.resolve().parent
    results = []
    for record in events:
        if record.get("type") != "shot_detected":
            continue
        if wanted is not None and record["shot_number"] not in wanted:
            continue
        try:
            results.append(
                replay_shot(
                    events,
                    start,
                    record,
                    run_dir,
                    ignore_iwr=args.ignore_iwr,
                    aim_uncertainty_deg=args.aim_uncertainty_deg,
                )
            )
        except Exception as error:  # pylint: disable=broad-exception-caught
            results.append(
                {"shot_number": record["shot_number"], "error": f"{type(error).__name__}: {error}"}
            )
        print(f"shot {record['shot_number']} done", file=sys.stderr, flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=1, allow_nan=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

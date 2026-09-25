#!/usr/bin/env python3
"""Replay one shot through saved raw OPS, optional IWR, and recorded camera stages."""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import os
import re
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

from openflight.camera.fusion_processing import process_camera_fusion
from openflight.camera.geometry_contract import geometry_fingerprint
from openflight.clubs import ClubType
from openflight.iwr6843.club import ClubWindowPolicy
from openflight.iwr6843.monitor import tx_order_from_config
from openflight.iwr6843.replay import build_replay_calibration
from openflight.iwr6843.runtime import horizontal_confidence_from
from openflight.raw_radar_replay import (
    benchmark_candidate_from_raw_replays,
    load_session_events,
    replay_iwr_capture_bytes,
    replay_ops_capture,
    session_shot_events,
)
from openflight.rolling_buffer.processor import RollingBufferProcessor
from openflight.speed_correction import evaluate_measured_projection_total_speed

try:
    from scripts.analysis.replay_camera_fusion import replay_frozen_shot
except ModuleNotFoundError:  # Direct execution places scripts/analysis first on sys.path.
    from replay_camera_fusion import replay_frozen_shot


def _one(events, event_type):
    matches = [event for event in events if event.get("type") == event_type]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one {event_type} for the shot, found {len(matches)}")
    return matches[0]


def _source_identity(session_start):
    """Expose only recorded source identities; absent evidence remains null."""
    runtime = session_start.get("runtime_provenance")
    runtime = runtime if isinstance(runtime, dict) else {}
    snapshot = runtime.get("source_snapshot")
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    content_hash = snapshot.get("content_manifest_sha256")
    snapshot_hash = snapshot.get("sha256")
    if (
        snapshot.get("status") != "preserved"
        or not isinstance(content_hash, str)
        or re.fullmatch(r"[0-9a-f]{64}", content_hash) is None
        or not isinstance(snapshot_hash, str)
        or re.fullmatch(r"[0-9a-f]{64}", snapshot_hash) is None
    ):
        content_hash = None
        snapshot_hash = None
    return {
        "arm_id": None,
        "rig_geometry_sha256": None,
        "capture_exposure_us": None,
        "capture_gain": None,
        "captured_software_content_sha256": content_hash,
        "setup_config_hash": None,
        "placement_warned": None,
        "runtime_source_snapshot_status": snapshot.get("status"),
        "runtime_source_snapshot_sha256": snapshot_hash,
    }


def _replay_software_content_sha256():
    """Hash the disk source used by this replay entry point, with an import caveat."""
    source_paths = [
        Path(__file__),
        Path(replay_ops_capture.__code__.co_filename),
        Path(replay_iwr_capture_bytes.__code__.co_filename),
        Path(process_camera_fusion.__code__.co_filename),
    ]
    digest = hashlib.sha256()
    for source in sorted({path.resolve() for path in source_paths}, key=str):
        payload = source.read_bytes()
        name = str(source).encode("utf-8")
        digest.update(len(name).to_bytes(8, "big"))
        digest.update(name)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def replay(args, *, frozen_session=None) -> dict:
    """Run requested stages while preserving every stage failure."""
    session_hash, events = session_shot_events(
        args.session, args.shot, frozen_session=frozen_session
    )
    iwr_measurement = None
    iwr_club_path = None
    session_start = _one(events, "session_start")
    report = {
        "schema_version": 1,
        "session_file": str(args.session.resolve()),
        "session_sha256": session_hash,
        "session_uuid": session_start["session_uuid"],
        "session_started_at": session_start.get("started_at_utc"),
        "source_identity": {
            **_source_identity(session_start),
            "replay_software_content_sha256": _replay_software_content_sha256(),
            "replay_software_identity_limitation": (
                "disk source bytes at replay time; already-imported module bytes may differ"
            ),
        },
        "shot_number": args.shot,
        "equivalence_claim": "none_unless_each_stage_is_eligible",
        "stages": {},
        "input_paths": [],
    }
    for event in events:
        if event.get("type") in {"iwr6843_capture", "camera_capture"} and isinstance(
            event.get("capture_path"), str
        ):
            source = Path(event["capture_path"]).expanduser()
            if not source.is_absolute():
                source = args.session.parent / source
            report["input_paths"].append(str(source.resolve()))
    try:
        report["stages"]["ops"] = replay_ops_capture(
            _one(events, "rolling_buffer_capture"),
            sample_rate_hz=args.ops_sample_rate_hz,
            club_type=ClubType(args.club),
        )
    except Exception as error:  # stage failures belong in the artifact
        report["stages"]["ops"] = {
            "status": "error",
            "error": f"{type(error).__name__}: {error}",
        }
    if args.projection_manifest:
        try:
            manifest_bytes = args.projection_manifest.read_bytes()
            manifest = json.loads(manifest_bytes)
            if not isinstance(manifest, dict):
                raise ValueError("projection manifest must be a JSON object")
            readings = report["stages"]["ops"].get("overlapping_readings")
            index = manifest.pop("reading_index", None)
            if isinstance(index, bool) or not isinstance(index, int):
                raise ValueError("projection reading_index must be an integer")
            if not isinstance(readings, list) or not 0 <= index < len(readings):
                raise ValueError("projection reading_index is outside replayed OPS readings")
            reading = readings[index]
            if (
                manifest.pop("expected_speed_mph", None) != reading["speed_mph"]
                or manifest.pop("expected_timestamp_ms", None) != reading["timestamp_ms"]
            ):
                raise ValueError("projection manifest does not match the selected OPS reading")
            session_start = _one(events, "session_start")
            session_uuid = session_start["session_uuid"]
            if manifest.pop("session_uuid", None) != session_uuid:
                raise ValueError("projection manifest session_uuid does not match the session")
            if manifest.pop("shot_number", None) != args.shot:
                raise ValueError(
                    "projection manifest shot_number does not match the requested shot"
                )
            ops_hash = report["stages"]["ops"]["canonical_capture_payload_sha256"]
            if manifest.pop("ops_capture_payload_sha256", None) != ops_hash:
                raise ValueError("projection manifest does not match the replayed OPS capture")
            origin_ns = manifest.pop("ops_capture_origin_ns", None)
            if isinstance(origin_ns, bool) or not isinstance(origin_ns, int):
                raise ValueError("projection manifest needs an integer OPS capture origin")
            window_ns = round(
                RollingBufferProcessor.WINDOW_SIZE / args.ops_sample_rate_hz * 1_000_000_000
            )
            window_start_ns = origin_ns + round(reading["timestamp_ms"] * 1_000_000)
            window_end_ns = window_start_ns + window_ns
            center_ns = window_start_ns + window_ns // 2
            manifest["ops_radial_speed_mph"] = reading["speed_mph"]
            manifest["shot_id"] = f"{session_uuid}:{args.shot}"
            manifest["direction_shot_id"] = manifest["shot_id"]
            manifest["radial_observed_at_ns"] = center_ns
            manifest["radial_window_start_ns"] = window_start_ns
            manifest["radial_window_end_ns"] = window_end_ns
            manifest["radial_quantity"] = "fft_window_center_radial_speed"
            report["stages"]["measured_total_speed_candidate"] = {
                "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                "reading_index": index,
                "selected_reading": reading,
                "time_mapping": {
                    "status": "operator_declared_common_clock_origin",
                    "ops_capture_origin_ns": origin_ns,
                    "window_size_samples": RollingBufferProcessor.WINDOW_SIZE,
                    "sample_rate_hz": args.ops_sample_rate_hz,
                    "derived_window_start_ns": window_start_ns,
                    "derived_window_end_ns": window_end_ns,
                    "derived_center_ns": center_ns,
                    "limitation": "This mapping is an operator assertion, not a hardware timing validation.",
                },
                "candidate": evaluate_measured_projection_total_speed(**manifest),
            }
        except Exception as error:
            report["stages"]["measured_total_speed_candidate"] = {
                "status": "error",
                "error": f"{type(error).__name__}: {error}",
            }
    else:
        report["stages"]["measured_total_speed_candidate"] = {"status": "not_requested"}
    if args.iwr or args.iwr_calibration:
        try:
            capture_event = _one(events, "iwr6843_capture")
            if args.iwr_runtime_config:
                runtime_bytes = args.iwr_runtime_config.read_bytes()
                runtime_config = json.loads(runtime_bytes)
                runtime_config_source = "explicit_file_override"
            else:
                runtime_config = capture_event.get("runtime_config")
                runtime_bytes = json.dumps(
                    runtime_config, sort_keys=True, separators=(",", ":"), allow_nan=False
                ).encode("utf-8")
                runtime_config_source = "recorded_per_shot_snapshot"
            if not isinstance(runtime_config, dict):
                raise ValueError(
                    "IWR runtime config is absent; supply --iwr-runtime-config for a legacy capture"
                )
            recorded_runtime_hash = capture_event.get("runtime_config_sha256")
            embedded_runtime_hash = runtime_config.get("sha256")
            unhashed_runtime = dict(runtime_config)
            unhashed_runtime.pop("sha256", None)
            canonical_runtime_hash = hashlib.sha256(
                json.dumps(
                    unhashed_runtime, sort_keys=True, separators=(",", ":"), allow_nan=False
                ).encode("utf-8")
            ).hexdigest()
            if (
                recorded_runtime_hash is not None
                and recorded_runtime_hash != canonical_runtime_hash
            ):
                raise ValueError("recorded IWR runtime config hash mismatch")
            if (
                embedded_runtime_hash is not None
                and embedded_runtime_hash != canonical_runtime_hash
            ):
                raise ValueError("embedded IWR runtime config hash mismatch")
            calibration_snapshot = runtime_config.get("calibration")
            radar_snapshot = runtime_config.get("radar_config")
            per_shot_inputs = runtime_config.get("per_shot_inputs")
            if args.iwr_calibration:
                calibration_bytes = args.iwr_calibration.read_bytes()
                tee_range_m = args.iwr_tee_m
                tilt_deg = args.iwr_tilt_deg
                radar_height_m = args.iwr_radar_height_m
                ball_height_m = args.ball_height_m
            elif isinstance(calibration_snapshot, dict):
                calibration_bytes = json.dumps(
                    calibration_snapshot["source_payload"], allow_nan=False
                ).encode("utf-8")
                effective = calibration_snapshot["effective"]
                tee_range_m = effective["tee_slant_range_m"]
                per_shot_tilt = (
                    per_shot_inputs.get("effective_tilt_deg")
                    if isinstance(per_shot_inputs, dict)
                    else None
                )
                tilt_deg = per_shot_tilt if per_shot_tilt is not None else effective["tilt_deg"]
                radar_height_m = effective["radar_height_m"]
                ball_height_m = effective["ball_height_m"]
            else:
                raise ValueError("recorded calibration is absent; supply legacy calibration flags")
            if args.iwr_config:
                radar_config_bytes = args.iwr_config.read_bytes()
            elif isinstance(radar_snapshot, dict) and isinstance(
                radar_snapshot.get("source_text"), str
            ):
                radar_config_bytes = radar_snapshot["source_text"].encode("utf-8")
            elif isinstance(radar_snapshot, dict) and isinstance(
                radar_snapshot.get("source_bytes_base64"), str
            ):
                radar_config_bytes = base64.b64decode(
                    radar_snapshot["source_bytes_base64"], validate=True
                )
            else:
                raise ValueError("recorded radar config is absent; supply --iwr-config")
            if (
                not args.iwr_config
                and radar_snapshot.get("source_sha256")
                != hashlib.sha256(radar_config_bytes).hexdigest()
            ):
                raise ValueError("recorded radar config source hash mismatch")
            with tempfile.TemporaryDirectory(prefix="openflight-iwr-replay-") as frozen_dir:
                frozen_calibration = Path(frozen_dir) / "calibration.json"
                frozen_radar_config = Path(frozen_dir) / "radar.cfg"
                frozen_calibration.write_bytes(calibration_bytes)
                frozen_radar_config.write_bytes(radar_config_bytes)
                calibration = build_replay_calibration(
                    frozen_calibration,
                    tee_range_m=tee_range_m,
                    tilt_deg=tilt_deg,
                    radar_height_m=radar_height_m,
                    ball_height_m=ball_height_m,
                )
                tx_order = tx_order_from_config(frozen_radar_config)
            if capture_event.get("capture_error"):
                raise ValueError("IWR capture event records a capture error")
            capture_path = Path(capture_event["capture_path"]).expanduser()
            if not capture_path.is_absolute():
                capture_path = args.session.parent / capture_path
            capture_path = capture_path.resolve(strict=True)
            raw = capture_path.read_bytes()
            if not raw:
                raise ValueError("IWR raw capture is empty")
            recorded_bytes = capture_event.get("capture_bytes")
            if (
                isinstance(recorded_bytes, int)
                and not isinstance(recorded_bytes, bool)
                and recorded_bytes != len(raw)
            ):
                raise ValueError("IWR raw capture byte count differs from the session event")
            ops_result = report["stages"]["ops"].get("result")
            ball_speed = ops_result.get("ball_speed_mph") if isinstance(ops_result, dict) else None
            if isinstance(ball_speed, bool) or not isinstance(ball_speed, (int, float)):
                raise ValueError("IWR capture event has no OPS ball speed")
            club_speed = ops_result.get("club_speed_mph") if isinstance(ops_result, dict) else None
            if isinstance(per_shot_inputs, dict) and (
                per_shot_inputs.get("ball_speed_mph") != ball_speed
                or per_shot_inputs.get("club") != args.club
                or per_shot_inputs.get("club_speed_mph") != club_speed
            ):
                raise ValueError(
                    "replayed OPS/club inputs differ from recorded IWR per-shot inputs"
                )
            measurement, club_path = replay_iwr_capture_bytes(
                raw,
                calibration,
                ball_speed_mph=float(ball_speed),
                club=args.club,
                club_speed_mph=club_speed,
                net_range_m=runtime_config.get("net_range_m"),
                tx_order=tx_order,
                tdm_sign_policy=runtime_config["tdm_sign_policy"],
                azimuth_offset_deg=float(runtime_config["azimuth_offset_deg"]),
                horizontal_phase_reference_rad=runtime_config.get("horizontal_phase_reference_rad"),
                club_window_policy=ClubWindowPolicy(**runtime_config["club_window_policy"]),
                club_impact_correction_s=float(runtime_config["club_impact_correction_s"]),
                recovery_observations=[
                    tuple(item) for item in runtime_config.get("recovery_observations", [])
                ],
            )
            iwr_measurement, iwr_club_path = measurement, club_path
            report["stages"]["iwr6843"] = {
                **measurement.to_dict(),
                "club_path": club_path.to_dict() if club_path is not None else None,
                "capture_path": str(capture_path),
                "capture_sha256": hashlib.sha256(raw).hexdigest(),
                "calibration_reconstructed_payload_sha256": hashlib.sha256(
                    calibration_bytes
                ).hexdigest(),
                "calibration_recorded_source_sha256": (
                    calibration_snapshot.get("source_sha256")
                    if isinstance(calibration_snapshot, dict)
                    else None
                ),
                "config_sha256": hashlib.sha256(radar_config_bytes).hexdigest(),
                "runtime_config_sha256": canonical_runtime_hash,
                "runtime_config_source": runtime_config_source,
                "equivalence_status": "production_pipeline_replayed_caller_config_source_revision_not_proven",
            }
        except Exception as error:  # stage failures belong in the artifact
            report["stages"]["iwr6843"] = {
                "status": "error",
                "error": f"{type(error).__name__}: {error}",
            }
    else:
        report["stages"]["iwr6843"] = {
            "status": "not_requested",
            "reason": "explicit calibration and runtime options were not supplied",
        }
    if args.camera:
        try:
            frozen = replay_frozen_shot(
                events, args.session, args.shot, capture=args.camera_capture
            )
            context = frozen.pop("_context")
            archive = frozen.pop("_archive")
            camera_stage = {"recorded_context": frozen}
            iwr_stage = report["stages"].get("iwr6843", {})
            if iwr_stage.get("status") == "error" or "capture_path" not in iwr_stage:
                camera_stage["recomputed_radar_context"] = {
                    "status": "withheld",
                    "reason": "complete replayed IWR evidence is unavailable",
                }
            else:
                recomputed = dict(context)
                recomputed.pop("sha256", None)
                recomputed["ops_ball_speed_mph"] = report["stages"]["ops"]["result"][
                    "ball_speed_mph"
                ]
                recomputed["ops_club_speed_mph"] = report["stages"]["ops"]["result"].get(
                    "club_speed_mph"
                )
                accepted = bool(getattr(iwr_measurement, "accepted", False))
                recomputed["iwr_vertical_deg"] = (
                    getattr(iwr_measurement, "angle_deg", None) if accepted else None
                )
                recomputed["iwr_horizontal_deg"] = (
                    getattr(iwr_measurement, "horizontal_deg", None) if accepted else None
                )
                recomputed["iwr_horizontal_confidence"] = horizontal_confidence_from(
                    getattr(iwr_measurement, "horizontal_coherence", None) if accepted else None
                )
                recomputed["ball_range_evidence"] = None
                recomputed["club_range_evidence"] = None
                if iwr_measurement.range_evidence is not None:
                    recomputed["ball_range_evidence"] = {
                        "kind": "ball",
                        "track": asdict(iwr_measurement.range_evidence.track),
                        "geometry": asdict(iwr_measurement.range_evidence.geometry),
                        "impact_t_s": iwr_measurement.range_evidence.impact_t_s,
                    }
                if iwr_club_path is not None and iwr_club_path.range_evidence is not None:
                    recomputed["club_range_evidence"] = {
                        "kind": "club",
                        "track": asdict(iwr_club_path.range_evidence.track),
                        "geometry": asdict(iwr_club_path.range_evidence.geometry),
                        "impact_t_s": iwr_club_path.range_evidence.impact_t_s,
                    }
                recomputed["sha256"] = geometry_fingerprint(recomputed)
                camera_stage["recomputed_radar_context"] = {
                    "status": "replayed",
                    "context_sha256": recomputed["sha256"],
                    "result": process_camera_fusion(recomputed, archive),
                    "range_evidence_source": "recomputed_when_estimator_returned_evidence",
                }
            report["stages"]["camera"] = camera_stage
        except Exception as error:  # stage failures belong in the artifact
            report["stages"]["camera"] = {
                "status": "error",
                "error": f"{type(error).__name__}: {error}",
            }
    else:
        report["stages"]["camera"] = {"status": "not_requested"}
    return report


def replay_session_benchmark_candidate(args, *, frozen_session=None) -> dict:
    """Replay every logged sensor shot and produce one scoreable session candidate."""
    frozen_session = frozen_session or load_session_events(args.session)
    _session_hash, _start, all_events = frozen_session
    shot_numbers = sorted(
        {event["shot_number"] for event in all_events if type(event.get("shot_number")) is int}
    )
    if not shot_numbers:
        raise ValueError("session has no logged sensor shot_number identities")
    reports = []
    for shot_number in shot_numbers:
        replay_args = copy.copy(args)
        replay_args.shot = shot_number
        if shot_number != args.shot:
            replay_args.projection_manifest = None
        reports.append(replay(replay_args, frozen_session=frozen_session))
    return benchmark_candidate_from_raw_replays(reports)


def _session_evidence_paths(session: Path, events) -> set[Path]:
    """Find every declared capture path before writing any replay artifact."""
    paths = {session.resolve()}
    for event in events:
        if not isinstance(event, dict) or not isinstance(event.get("capture_path"), str):
            continue
        source = Path(event["capture_path"]).expanduser()
        if not source.is_absolute():
            source = session.parent / source
        paths.add(source.resolve())
    return paths


def _protect_output(output: Path, protected: set[Path]) -> None:
    """Reject direct, hard-link, and capture-directory collisions with evidence."""
    if output in protected:
        raise ValueError("output cannot overwrite replay input evidence")
    if output.exists() and any(
        source.exists() and os.path.samefile(source, output) for source in protected
    ):
        raise ValueError("output cannot overwrite replay input evidence")
    if any(source.is_dir() and source in output.parents for source in protected):
        raise ValueError("output cannot be placed inside replay capture evidence")


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("session", type=Path)
    result.add_argument("shot", type=int)
    result.add_argument("--ops-sample-rate-hz", required=True, type=int)
    result.add_argument("--club", required=True, choices=[club.value for club in ClubType])
    result.add_argument("--camera", action="store_true")
    result.add_argument("--camera-capture", type=Path)
    result.add_argument("--projection-manifest", type=Path)
    result.add_argument("--iwr", action="store_true")
    result.add_argument("--iwr-calibration", type=Path)
    result.add_argument("--iwr-config", type=Path)
    result.add_argument("--iwr-runtime-config", type=Path)
    result.add_argument("--iwr-tee-m", type=float)
    result.add_argument("--iwr-tilt-deg", type=float)
    result.add_argument("--iwr-radar-height-m", type=float)
    result.add_argument("--ball-height-m", type=float, default=0.040)
    result.add_argument("--output", type=Path)
    result.add_argument(
        "--benchmark-candidate-output",
        type=Path,
        help="Write one normalized candidate for every logged sensor shot in this session",
    )
    return result


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.benchmark_candidate_output and args.camera_capture:
            raise ValueError(
                "--camera-capture is a one-shot override and cannot produce a session candidate"
            )
        frozen_session = load_session_events(args.session)
        report = replay(args, frozen_session=frozen_session)
        candidate = (
            replay_session_benchmark_candidate(args, frozen_session=frozen_session)
            if args.benchmark_candidate_output
            else None
        )
        payload = json.dumps(report, indent=2, allow_nan=False) + "\n"
        protected = _session_evidence_paths(args.session, frozen_session[2])
        for source in (
            args.camera_capture,
            args.iwr_calibration,
            args.iwr_config,
            args.iwr_runtime_config,
            args.projection_manifest,
        ):
            if source is not None:
                protected.add(source.resolve())
        protected.update(Path(source).resolve() for source in report.get("input_paths", []))
        report_output = args.output.resolve() if args.output else None
        candidate_output = (
            args.benchmark_candidate_output.resolve() if args.benchmark_candidate_output else None
        )
        if report_output:
            _protect_output(report_output, protected)
        if candidate_output:
            _protect_output(
                candidate_output, protected | ({report_output} if report_output else set())
            )
        if args.output:
            output = report_output
            output.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=output.parent, delete=False
            ) as handle:
                temporary = Path(handle.name)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.replace(temporary, output)
            finally:
                temporary.unlink(missing_ok=True)
        else:
            print(payload, end="")
        if args.benchmark_candidate_output:
            output = candidate_output
            output.parent.mkdir(parents=True, exist_ok=True)
            candidate_payload = json.dumps(candidate, indent=2, allow_nan=False) + "\n"
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=output.parent, delete=False
            ) as handle:
                temporary = Path(handle.name)
                handle.write(candidate_payload)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.replace(temporary, output)
            finally:
                temporary.unlink(missing_ok=True)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"raw fusion replay failed: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

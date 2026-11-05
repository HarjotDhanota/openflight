"""Scoped offline replay of the production shot/model/impact chain from saved signals."""

# Production entry points are private; replay intentionally reuses their ordering.
# pylint: disable=protected-access,too-many-arguments,too-many-positional-arguments,too-many-branches

from __future__ import annotations

import io
import json
import threading
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from dataclasses import fields
from datetime import datetime
from functools import wraps
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from openflight import kiosk_extensions, shot_metrics
from openflight.clubs import ClubType
from openflight.raw_radar_replay import locate_recorded_capture
from openflight.rig_geometry import RigGeometry, geometry_fingerprint

_LOCK = threading.RLock()


def unavailable(reason: str) -> dict:
    """All catalogue states remain explicit when raw OPS cannot produce a shot."""
    metrics = shot_metrics.from_record({})
    for record in metrics.values():
        record.update(value=None, status="not_measured", reason=reason)
    return {
        "status": "partial",
        "metrics": metrics,
        "impact": {"status": "rejected", "reason": reason},
        "limitations": ["raw signal replay, not acquisition or physical accuracy qualification"],
    }


def _isolated_extensions(function):
    @wraps(function)
    def run(*args, **kwargs):
        with (
            _LOCK,
            patch.object(kiosk_extensions, "_extensions", list(kiosk_extensions.registered())),
        ):
            return function(*args, **kwargs)

    return run


@contextmanager
def _server_context(config, record, context, runtime, camera, seed):
    from openflight import server, shot_models  # noqa: PLC0415

    parameters = ((config.get("rig_geometry") or {}).get("snapshot") or {}).get("parameters")
    snapshot = (config.get("rig_geometry") or {}).get("snapshot") or {}
    if parameters and geometry_fingerprint(parameters) != snapshot.get("sha256"):
        raise ValueError("recorded rig geometry fingerprint mismatch")
    line = deepcopy(record.get("target_line") or config.get("target_line") or {"status": "not_set"})
    if line.get("total_uncertainty_deg") is None:
        line["total_uncertainty_deg"] = (context.get("camera_fallback") or {}).get(
            "horizontal_offset_sd_deg"
        )
    box = (seed.get("look") or {}).get("box_px") or (seed.get("seed") or {}).get("box_px")
    values = {
        "camera_capture_config": deepcopy(config.get("camera_capture") or {}),
        "rig_geometry": RigGeometry(**parameters) if parameters else None,
        "rig_geometry_config": {
            "enabled": bool(parameters),
            "lens": (config.get("camera_capture") or {}).get("lens"),
        },
        "setup_handoff_config": {"scene": deepcopy(config.get("scene") or {}), "target_line": line},
        "surface_height_contributions": [],
        "horizontal_offset_deg": line.get("horizontal_offset_deg"),
        "camera_ball_box_px": tuple(box) if box else None,
        "iwr6843_runtime": runtime,
        "camera_capture_runtime": camera,
        "get_session_logger": lambda: None,
        "log_session_error": lambda *_a, **_k: None,
        "socketio": SimpleNamespace(emit=lambda *_a, **_k: None),
        "_forward_shot_to_simulators": lambda _shot: None,
        "_publish_phone_shot": lambda *_a, **_k: None,
        "golfer_handedness": lambda: {
            "handed": record.get("handed"),
            "source": record.get("handed_source"),
        },
        "ballistics_enabled": True,
        "debug_mode": False,
        "debug_log_file": None,
        "air_sensor_runtime_config": {"reason": "air observation absent from recording"},
        "iwr6843_runtime_config": {},
        "inclinometer_runtime_config": deepcopy(config.get("inclinometer") or {}),
        "camera_lens": None,
    }
    for name in (
        "monitor",
        "profile_store",
        "setup_service",
        "kld7_vertical",
        "kld7_horizontal",
        "inclinometer_service",
        "ball_at_address_monitor",
        "camera_calibration_set",
        "camera_tilt_calibration_path",
        "camera_replay_manager",
        "air_sensor_service",
    ):
        values[name] = None
    with ExitStack() as stack:
        stack.enter_context(patch.object(kiosk_extensions, "_extensions", [shot_models.extension]))
        for name, value in values.items():
            stack.enter_context(patch.object(server, name, value))
        yield server


def _camera(events, record, number, session_dir):
    context = record.get("camera_fusion_context") or {}
    context = context if context.get("available") is True else {}
    matches = [
        event
        for event in events
        if event.get("type") == "camera_capture" and not event.get("capture_error")
    ]
    if len(matches) != 1 or not matches[0].get("capture_path"):
        return None, None, {}, "camera raw capture unavailable"
    event = matches[0]
    path, _ = locate_recorded_capture(event["capture_path"], session_dir)
    path = path if path.is_dir() else path.parent
    frozen = (path / "frames.npz").read_bytes()
    import hashlib  # noqa: PLC0415

    digest = hashlib.sha256(frozen).hexdigest()
    if context and (
        context.get("shot_number") != number
        or context.get("session_uuid")
        != next(event["session_uuid"] for event in events if event.get("type") == "session_start")
        or context.get("capture_npz_sha256") != digest
        or context.get("sha256")
        != geometry_fingerprint({key: value for key, value in context.items() if key != "sha256"})
    ):
        raise ValueError("camera context identity, fingerprint or capture hash mismatch")
    with np.load(io.BytesIO(frozen), allow_pickle=False) as data:
        archive = {key: data[key] for key in data.files}
    archive["_capture_npz_sha256"] = digest
    metadata = json.loads((path / "metadata.json").read_text())
    from openflight.camera.capture_runtime import SavedCameraCapture  # noqa: PLC0415

    capture = SavedCameraCapture(
        metadata.get("sequence"),
        event.get("trigger_timestamp"),
        metadata.get("completed_timestamp"),
        path,
        metadata,
    )
    return capture, archive, context, None


def _fresh_ops(entry, start, number, sample_rate, club):
    from openflight.rolling_buffer.monitor import (
        RollingBufferMonitor,
        get_optimal_spin_for_ball_speed,
    )  # noqa: PLC0415
    from openflight.rolling_buffer.processor import RollingBufferProcessor  # noqa: PLC0415
    from openflight.rolling_buffer.types import IQCapture  # noqa: PLC0415

    values = {
        key: deepcopy(value)
        for key, value in entry.items()
        if key in {field.name for field in fields(IQCapture)}
    }
    values["timestamp"] = entry.get("first_byte_timestamp") or 0.0
    values["trigger_type"] = entry.get("trigger_type") or start.get("trigger_type")
    capture = IQCapture(**values)
    processor = RollingBufferProcessor(sample_rate=sample_rate)
    processed = processor.process_capture(
        capture,
        expected_spin_for_ball_speed=lambda speed: get_optimal_spin_for_ball_speed(
            speed, ClubType(club)
        ),
        club_type=ClubType(club),
    )
    if processed is None:
        raise ValueError("OPS raw processor produced no eligible ball shot")
    monitor = object.__new__(RollingBufferMonitor)
    monitor.processor, monitor._current_club, monitor.trigger_type = (
        processor,
        ClubType(club),
        values["trigger_type"],
    )
    shot = monitor._create_shot(processed)
    if shot is None:
        raise ValueError("OPS raw processor produced no eligible ball shot")
    shot.shot_number = number
    if entry.get("trigger_timestamp") is None and entry.get("first_byte_timestamp") is None:
        shot.impact_timestamp = shot.impact_timestamp_kld7 = None
    return shot


def _runtime(inputs):
    if inputs is None:
        return None
    from openflight.iwr6843.club import ClubWindowPolicy  # noqa: PLC0415
    from openflight.iwr6843.launch_range import LaunchRangePrior  # noqa: PLC0415
    from openflight.iwr6843.monitor import IWR6843Capture  # noqa: PLC0415
    from openflight.iwr6843.runtime import IWR6843Runtime  # noqa: PLC0415

    saved, event = inputs["runtime"], inputs["event"]
    capture = IWR6843Capture(
        None,
        event.get("trigger_timestamp"),
        None,
        event.get("dump_duration_s"),
        inputs["raw"],
        inputs["path"],
        error=event.get("capture_error"),
    )
    return IWR6843Runtime(
        SimpleNamespace(capture_for_shot=lambda *_a, **_k: capture),
        deepcopy(inputs["calibration"]),
        net_range_m=saved.get("net_range_m"),
        tx_order=inputs["tx_order"],
        tdm_sign_policy=saved["tdm_sign_policy"],
        azimuth_offset_deg=saved["azimuth_offset_deg"],
        horizontal_phase_reference_rad=saved.get("horizontal_phase_reference_rad"),
        club_window_policy=ClubWindowPolicy(**saved["club_window_policy"]),
        club_impact_correction_s=float(saved["club_impact_correction_s"]),
        range_prior=LaunchRangePrior.from_dict(saved.get("range_prior")),
    )


def _impact(shot, capture, config, session_uuid):
    from openflight import server  # noqa: PLC0415
    from openflight.camera.impact_live import START_KEYS, LiveAnalyser  # noqa: PLC0415

    if capture is None or shot.handed not in ("RH", "LH"):
        return {
            "status": "rejected",
            "reason": "camera capture or recorded handedness unavailable",
        }, {}
    result = LiveAnalyser().analyse(
        {
            "session_uuid": session_uuid,
            "shot_number": shot.shot_number,
            "clip_path": str(capture.path),
            "shot": server.shot_to_dict(shot),
            "start": {key: deepcopy(config[key]) for key in START_KEYS if key in config},
            "handedness": "right" if shot.handed == "RH" else "left",
        }
    )
    performance = {key: result.pop(key) for key in ("analysis_ms", "stages_ms") if key in result}
    return result, performance


@_isolated_extensions
def replay_full_shot(
    events,
    *,
    number,
    session_dir,
    ops_stage,
    sample_rate,
    club,
    iwr_inputs=None,
    include_camera=True,
):
    """Recompute one own-shot result; saved derived metrics never seed the Shot."""
    start = next(event for event in events if event.get("type") == "session_start")
    record = next((event for event in events if event.get("type") == "shot_detected"), {})
    identity = {
        "session_uuid": start["session_uuid"],
        "shot_number": number,
        "source_published": bool(record),
    }
    if not record or ops_stage.get("status") != "ok" or sample_rate is None or club is None:
        return {**unavailable("saved OPS input or published source shot unavailable"), **identity}
    entry = next(event for event in events if event.get("type") == "rolling_buffer_capture")
    errors = {}
    with _LOCK:
        capture = archive = None
        context = {}
        if include_camera:
            try:
                capture, archive, context, reason = _camera(events, record, number, session_dir)
                if reason:
                    errors["camera"] = reason
            except FileNotFoundError:
                errors["camera"] = "camera input refused: saved frames or metadata file missing"
            except (ValueError, OSError, KeyError) as error:
                detail = str(error).replace(str(session_dir), "<session>")
                errors["camera"] = f"camera input refused: {type(error).__name__}: {detail}"
        else:
            errors["camera"] = "camera replay not requested"
        runtime = _runtime(iwr_inputs)
        if runtime is None:
            errors["iwr6843"] = "authenticated IWR raw/config input unavailable"
        look = deepcopy(
            ((iwr_inputs or {}).get("runtime", {}).get("per_shot_inputs") or {}).get(
                "ball_at_address"
            )
        )
        seed = deepcopy((record.get("input_sources") or {}).get("ball_at_address"))
        seed_source = "recorded own-shot address observation"
        if not isinstance(seed, dict):
            seed = {
                "look": look,
                "seed": deepcopy(context.get("setup_ball")),
                "range_prior_rejection": (look or {}).get("range_prior_rejection"),
            }
            seed_source = (
                "authenticated runtime look or camera setup seed; missing look remains unknown"
            )
        seed = {key: seed.get(key) for key in ("look", "seed", "range_prior_rejection")}
        config = deepcopy(start.get("config") or {})
        lens = (config.get("camera_capture") or {}).get("lens") or {}
        geometry_inputs = (context.get("geometry") or {}).get("parameters") or {}
        if lens.get("intrinsics") == "calibrated" or geometry_inputs.get(
            "calibrated_model_snapshot"
        ):
            errors["camera"] = (
                "recorded calibrated camera model/pair cannot be restored by this replay"
            )
            capture = archive = None
            context = {}
            (config.get("camera_capture") or {}).pop("lens", None)
        parameters = ((config.get("rig_geometry") or {}).get("snapshot") or {}).get("parameters")
        try:
            if parameters:
                snapshot = config["rig_geometry"]["snapshot"]
                if geometry_fingerprint(parameters) != snapshot.get("sha256"):
                    raise ValueError("rig hash mismatch")
                RigGeometry(**parameters)
        except (TypeError, ValueError):
            errors["rig"] = "recorded rig calibration incomplete or fingerprint mismatched"
            errors["camera"] = "camera/impact refused because recorded rig calibration is invalid"
            capture = archive = None
            context = {}
            config.pop("rig_geometry", None)
        with _server_context(config, record, context, runtime, None, seed) as server:
            if look is not None and seed_source.startswith("authenticated"):
                seed["seed"] = server._reference_ball_from_look(look)
            shot = _fresh_ops(entry, start, number, sample_rate, club)
            timestamp = record.get("timestamp") or record.get("ts") or start.get("start_time")
            if timestamp is None:
                return {
                    **unavailable("recorded shot observation timestamp unavailable"),
                    **identity,
                }
            shot.timestamp = datetime.fromisoformat(timestamp)
            shot.camera_fusion_session_uuid = start["session_uuid"]
            shot.inclinometer = deepcopy(record.get("inclinometer"))
            shot.handed, shot.handed_source = record.get("handed"), record.get("handed_source")
            shot.target_line = deepcopy(server.setup_handoff_config["target_line"])
            shot.input_sources = {"ball_at_address": seed}
            for key in ("air_density_kg_m3", "air_density_source", "air_conditions"):
                setattr(shot, key, deepcopy(record.get(key)))
            if capture is not None:
                server._isolated_stage(
                    shot, "shot_ball_search", server._find_shot_ball, shot, archive
                )
                server._isolated_stage(
                    shot, "camera_resting_ball", server._measure_resting_ball_early, shot, archive
                )
            for name, function, arguments in (
                ("ball_reference", server._record_ball_reference_check, (shot, archive)),
                ("iwr6843", server._process_iwr6843_angle, (shot,)),
                ("surface_height", server._solve_surface_height, (shot, archive)),
                ("camera_fusion", server._fuse_camera_measurements, (shot, capture, archive)),
                ("after_enrichment", kiosk_extensions.call_all, ("after_enrichment", shot)),
            ):
                server._isolated_stage(shot, name, function, *arguments)
            server._finalize_shot_detected(shot, emit_event="shot")
            try:
                impact, performance = _impact(shot, capture, config, start["session_uuid"])
            except Exception as error:
                errors["impact"] = f"impact producer failed: {type(error).__name__}"
                impact, performance = {"status": "rejected", "reason": errors["impact"]}, {}
            shot.impact_location = impact
            produced = server.shot_to_dict(shot)
            return {
                **identity,
                "status": "partial" if errors or shot.stage_errors else "replayed",
                "metrics": produced["metrics"],
                "impact": impact,
                "performance": performance,
                "errors": errors,
                "stage_errors": produced.get("stage_errors"),
                "input_sources": produced.get("input_sources"),
                "replay_inputs": {
                    "address_source": seed_source,
                    "target_line": shot.target_line,
                    "handed": shot.handed,
                    "ops_capture_id": entry.get("capture_id"),
                    "ops_trigger_timestamp": entry.get("trigger_timestamp"),
                    "iwr_runtime_config_sha256": (iwr_inputs or {})
                    .get("runtime", {})
                    .get("sha256"),
                },
                "camera_fusion_context": produced.get("camera_fusion_context"),
                "limitations": [
                    "own saved inputs with current producers; native acquisition/physical "
                    "matching not reconstructed",
                    "local camera calibration files are not loaded; unsupported calibrated "
                    "geometry is refused and legacy inferred geometry remains labelled",
                    "impact is experimental; no physical accuracy or impact-to-screen "
                    "timing qualification",
                ],
            }

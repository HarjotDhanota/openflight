"""Offline adapters for moving IWR range and camera/IWR anchor diagnostics."""

from __future__ import annotations

import math
import re
from dataclasses import asdict
from typing import Any, Mapping

import numpy as np

from openflight.camera.geometry_contract import EffectiveCameraGeometryInputs
from openflight.camera.moving_iwr_anchor import (
    CameraIwrClockMapping,
    TimedIwrRangeSeries,
    estimate_moving_ball_impact_anchor,
)
from openflight.camera.reference_ball_range import BallPlaneCamera
from openflight.iwr6843.range_evidence import (
    IndependentImpactTime,
    MovingRangeTrackResult,
    build_moving_track_candidate,
    extract_moving_ball_range_track,
)

SCHEMA = "openflight.moving_range_replay.v1"
_SHA256 = re.compile(r"[0-9a-f]{64}")


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _prerequisite(identifier: str, ready: bool, reason: str, source: str | None = None) -> dict:
    return {
        "id": identifier,
        "status": "ready" if ready else "withheld",
        "reason": reason,
        "source": source,
    }


def _range_calibration(runtime_config: Mapping[str, Any], evidence: Mapping[str, Any]) -> dict:
    recorded = _mapping(_mapping(runtime_config.get("calibration")).get("effective"))
    source_hash = _mapping(runtime_config.get("calibration")).get("source_sha256")
    declared = _mapping(evidence.get("range_calibration"))
    bias = _finite(declared.get("bias_m"))
    uncertainty = _finite(declared.get("uncertainty_m"))
    matches = (
        isinstance(source_hash, str)
        and _SHA256.fullmatch(source_hash) is not None
        and declared.get("source_sha256") == source_hash
        and isinstance(declared.get("source"), str)
        and bool(declared["source"].strip())
        and bias is not None
        and uncertainty is not None
        and uncertainty >= 0.0
        and _finite(recorded.get("range_bias_m")) == bias
    )
    qualified = matches and declared.get("qualified") is True
    reason = (
        "recorded range-bias calibration is independently qualified"
        if qualified
        else "a qualified range-bias record matching the captured calibration is required"
    )
    return {
        "qualified": qualified,
        "reason": reason,
        "bias_m": bias if matches else None,
        "uncertainty_m": uncertainty if matches else None,
        "source_sha256": source_hash if matches else None,
        "source": declared.get("source") if matches else None,
    }


def _impact_time(evidence: Mapping[str, Any]) -> tuple[IndependentImpactTime | None, str]:
    value = _mapping(evidence.get("impact_time"))
    provenance = _mapping(value.get("provenance"))
    dependencies = provenance.get("dependencies")
    basis = provenance.get("independence_basis")
    proven = (
        isinstance(basis, str)
        and bool(basis.strip())
        and isinstance(dependencies, list)
        and all(isinstance(item, str) for item in dependencies)
        and "iwr_range" not in dependencies
    )
    if not value:
        return None, "no independent impact-time record was saved"
    try:
        result = IndependentImpactTime(
            time_s=value.get("time_s"),
            uncertainty_s=value.get("uncertainty_s"),
            source=value.get("source", ""),
            qualified=value.get("qualified") is True,
            independent_of_iwr_range=(value.get("independent_of_iwr_range") is True and proven),
            provenance=provenance,
        )
    except (TypeError, ValueError) as error:
        return None, f"independent impact-time record is invalid: {error}"
    if not result.qualified:
        return None, "saved impact time is not qualified"
    if not result.independent_of_iwr_range:
        return None, "saved impact time does not prove independence from IWR range"
    return result, "qualified impact time explicitly excludes IWR range from its dependencies"


def _track_samples(
    result: MovingRangeTrackResult, bias_m: float
) -> tuple[list[float], list[float]]:
    if result.track is None:
        return [], []
    geometry = result.geometry
    times = sorted(
        {
            geometry.loop_time(frame, loop)
            for frame in range(geometry.n_frames)
            for loop in range(geometry.n_loops)
            if result.track.t_first <= geometry.loop_time(frame, loop) <= result.track.t_last
        }
    )
    ranges = [result.track.range_at(value, geometry.range_res_m) - bias_m for value in times]
    return [float(value) for value in times], [float(value) for value in ranges]


def replay_moving_iwr_range(
    raw: bytes | None,
    *,
    capture_event: Mapping[str, Any],
    runtime_config: Mapping[str, Any],
    shot_event: Mapping[str, Any],
    club: str | None,
) -> tuple[dict[str, Any], TimedIwrRangeSeries | None]:
    """Preserve a tee-independent track and conditionally evaluate it at impact."""
    evidence = _mapping(shot_event.get("moving_range_evidence"))
    if raw is None:
        return (
            {
                "schema": SCHEMA,
                "status": "withheld_missing_iwr_capture",
                "reason": "no readable saved IWR raw dump is available",
                "promotion_allowed": False,
                "tee_range_candidate": None,
                "track": None,
            },
            None,
        )
    try:
        track = extract_moving_ball_range_track(
            raw,
            club=club,
            net_range_m=_finite(runtime_config.get("net_range_m")),
        )
    except Exception as error:  # pylint: disable=broad-exception-caught
        return (
            {
                "schema": SCHEMA,
                "status": "withheld_track_error",
                "reason": f"{type(error).__name__}: {error}",
                "promotion_allowed": False,
                "tee_range_candidate": None,
                "track": None,
            },
            None,
        )
    calibration = _range_calibration(runtime_config, evidence)
    impact, impact_reason = _impact_time(evidence)
    bias = calibration["bias_m"] if calibration["qualified"] else 0.0
    times, ranges = _track_samples(track, bias)
    track_record = {
        "status": track.status,
        "scope": track.scope,
        "capture_sha256": track.capture_sha256,
        "fit": asdict(track.track) if track.track is not None else None,
        "geometry": asdict(track.geometry),
        "series": {
            "kind": "fitted_track_at_every_capture_loop_time_within_support",
            "times_s": times,
            "ranges_m": ranges,
            "range_reference": "bias_corrected" if calibration["qualified"] else "apparent",
        },
        "diagnostics": dict(track.diagnostics),
    }
    prerequisites = [
        _prerequisite(
            "moving_iwr_track",
            track.status == "selected" and track.track is not None,
            "tee-independent moving track selected"
            if track.status == "selected"
            else f"moving track status is {track.status}",
            "saved IWR raw dump",
        ),
        _prerequisite(
            "range_calibration",
            calibration["qualified"],
            calibration["reason"],
            calibration["source"],
        ),
        _prerequisite(
            "independent_impact_time",
            impact is not None,
            impact_reason,
            impact.source if impact is not None else None,
        ),
    ]
    candidate = None
    reason = next((item["reason"] for item in prerequisites if item["status"] != "ready"), None)
    if reason is None:
        try:
            candidate = build_moving_track_candidate(
                track,
                impact,
                range_bias_m=calibration["bias_m"],
                range_bias_uncertainty_m=calibration["uncertainty_m"],
                calibration_sha256=calibration["source_sha256"],
            )
        except ValueError as error:
            reason = str(error)
    series = None
    series_error = None
    if track.status == "selected" and calibration["qualified"] and len(times) >= 2:
        fit_uncertainty = max(track.track.rms_bins, 0.5) * track.geometry.range_res_m
        uncertainty = math.hypot(fit_uncertainty, calibration["uncertainty_m"])
        try:
            series = TimedIwrRangeSeries(
                times_s=tuple(times),
                ranges_m=tuple(ranges),
                range_uncertainty_m=uncertainty,
                source="saved_iwr_moving_track_with_qualified_range_bias",
                qualified=True,
            )
        except ValueError as error:
            series_error = str(error)
    stage = {
        "schema": SCHEMA,
        "status": "candidate" if candidate is not None else "withheld",
        "reason": reason,
        "promotion_allowed": False,
        "independent_support_eligible": False,
        "independence_reason": (
            "moving IWR range is diagnostic evidence and cannot independently support "
            "the same IWR-derived tee-range solution"
        ),
        "prerequisites": prerequisites,
        "range_calibration": calibration,
        "track": track_record,
        "qualified_range_series": {
            "status": "ready" if series is not None else "withheld",
            "reason": series_error
            or (None if series is not None else "qualified series unavailable"),
        },
        "tee_range_candidate": asdict(candidate) if candidate is not None else None,
        "capture_event_identity": {
            "shot_number": capture_event.get("shot_number"),
            "capture_bytes": capture_event.get("capture_bytes"),
        },
    }
    return stage, series


def _clock_mapping(evidence: Mapping[str, Any]) -> tuple[CameraIwrClockMapping | None, str]:
    value = _mapping(evidence.get("camera_iwr_clock_mapping"))
    provenance = _mapping(value.get("provenance"))
    if not value:
        return None, "no qualified camera-to-IWR clock mapping was saved"
    source_hash = value.get("source_sha256")
    if not provenance or not isinstance(source_hash, str) or _SHA256.fullmatch(source_hash) is None:
        return None, "camera-to-IWR clock mapping lacks hash-bound provenance"
    try:
        mapping = CameraIwrClockMapping(
            offset_s=value.get("offset_s"),
            uncertainty_s=value.get("uncertainty_s"),
            qualified=value.get("qualified") is True,
            source=value.get("source", ""),
        )
    except (TypeError, ValueError) as error:
        return None, f"camera-to-IWR clock mapping is invalid: {error}"
    if not mapping.qualified:
        return None, "saved camera-to-IWR clock mapping is not qualified"
    return mapping, "saved hash-bound camera-to-IWR clock mapping is qualified"


def _qualified_camera(context: Mapping[str, Any]) -> tuple[BallPlaneCamera | None, str]:
    try:
        geometry = EffectiveCameraGeometryInputs.from_recorded_session(
            {"effective_camera_geometry": context.get("geometry")}
        )
        model = geometry.calibrated_model()
        if model is None:
            return None, "camera context has no calibrated projection model"
        camera = BallPlaneCamera.calibrated(model)
    except (KeyError, TypeError, ValueError) as error:
        return None, f"camera model is invalid: {error}"
    if not camera.accuracy_qualified:
        return None, "camera calibration is recorded but not accuracy-qualified"
    return camera, "camera context contains an accuracy-qualified calibrated model"


def replay_moving_camera_iwr_anchor(
    *,
    context: Mapping[str, Any] | None,
    archive: Mapping[str, Any] | None,
    shot_event: Mapping[str, Any],
    iwr_ranges: TimedIwrRangeSeries | None,
    ops_ball_speed_mph: Any,
) -> dict[str, Any]:
    """Run the moving camera/IWR diagnostic only after every input is qualified."""
    evidence = _mapping(shot_event.get("moving_range_evidence"))
    camera, camera_reason = (
        _qualified_camera(context or {})
        if context is not None
        else (
            None,
            "no replayable saved camera context is available",
        )
    )
    clock, clock_reason = _clock_mapping(evidence)
    frames = np.asarray((archive or {}).get("frames")) if archive is not None else None
    timestamps = (
        np.asarray((archive or {}).get("host_timestamp_ns")) if archive is not None else None
    )
    trigger = (archive or {}).get("trigger_host_timestamp_ns") if archive is not None else None
    saved_camera = (
        frames is not None
        and timestamps is not None
        and frames.ndim == 3
        and timestamps.ndim == 1
        and len(frames) == len(timestamps)
        and trigger is not None
    )
    speed = _finite(ops_ball_speed_mph)
    prerequisites = [
        _prerequisite(
            "saved_camera_frames",
            saved_camera,
            (
                "saved frames, host timestamps, and trigger timestamp are aligned"
                if saved_camera
                else "saved camera frames/timestamps/trigger are missing or misaligned"
            ),
            "camera frames.npz",
        ),
        _prerequisite("qualified_camera_model", camera is not None, camera_reason),
        _prerequisite("qualified_camera_iwr_clock", clock is not None, clock_reason),
        _prerequisite(
            "ops_ball_speed",
            speed is not None and speed > 0.0,
            "replayed OPS ball speed is available"
            if speed is not None and speed > 0.0
            else "a positive replayed OPS ball speed is required",
            "replayed OPS raw capture",
        ),
        _prerequisite(
            "qualified_iwr_range_series",
            iwr_ranges is not None and iwr_ranges.qualified,
            "qualified bias-corrected moving IWR range series is available"
            if iwr_ranges is not None and iwr_ranges.qualified
            else "qualified moving IWR range is unavailable",
            iwr_ranges.source if iwr_ranges is not None else None,
        ),
    ]
    missing = [item for item in prerequisites if item["status"] != "ready"]
    base = {
        "schema": SCHEMA,
        "promotion_allowed": False,
        "independent_camera_support": False,
        "dependency_reason": (
            "this camera result is conditioned on the same moving IWR range series; "
            "it cannot be recycled as independent camera tee-range support"
        ),
        "prerequisites": prerequisites,
    }
    if missing:
        return {
            **base,
            "status": "withheld_prerequisites",
            "reason": "; ".join(item["reason"] for item in missing),
            "result": None,
        }
    try:
        result = estimate_moving_ball_impact_anchor(
            frames,
            timestamps,
            trigger_ns=int(np.asarray(trigger)),
            camera=camera,
            clock_mapping=clock,
            iwr_ranges=iwr_ranges,
            ops_ball_speed_mph=speed,
        )
    except Exception as error:  # pylint: disable=broad-exception-caught
        return {
            **base,
            "status": "error",
            "reason": f"{type(error).__name__}: {error}",
            "result": None,
        }
    return {
        **base,
        "status": result.status,
        "reason": None if result.status == "selected" else result.status,
        "result": result.to_dict(),
    }

"""Shared, replayable camera fusion processing for one saved shot."""

from __future__ import annotations

import math
from dataclasses import asdict
from typing import Any, Mapping

import numpy as np

from openflight.camera.ball_flight import (
    CameraBallEstimate,
    estimate_camera_ball_flight,
    select_camera_assisted_horizontal,
)
from openflight.camera.club_delivery import (
    ChainedDelivery,
    ReferenceBallTracker,
    estimate_chained_delivery,
)
from openflight.camera.club_motion import ReferenceBall
from openflight.camera.geometry_contract import EffectiveCameraGeometryInputs
from openflight.camera.triggered_buffer import exposure_trigger_index
from openflight.clubs import ClubType
from openflight.iwr6843.club import ClubRangeEvidence
from openflight.iwr6843.lcmf import BallRangeEvidence
from openflight.iwr6843.tracking import BallTrack, Geometry
from openflight.rig_geometry import geometry_fingerprint

SCHEMA = "openflight.camera.fusion_context"
VERSION = 1
LIGHTING_NOT_ELIGIBLE = "capture-time lighting was not analysis eligible"


def lighting_note(auto_exposure: Mapping[str, Any] | None) -> str:
    """The capture's lighting verdict as a short label, e.g. the ball's clipped share."""
    auto_exposure = auto_exposure if isinstance(auto_exposure, Mapping) else {}
    judged = auto_exposure.get("analysis_eligibility")
    judged = judged if isinstance(judged, Mapping) else {}
    if judged.get("reason"):
        return f"lighting: {judged['reason']}"
    observation = auto_exposure.get("observation")
    observation = observation if isinstance(observation, Mapping) else {}
    if observation.get("status"):
        clipped = observation.get("clipped_pct")
        share = f", {clipped:.0f}% of the zone clipped" if isinstance(clipped, (int, float)) else ""
        return f"lighting: zone {observation['status']}{share}"
    return f"lighting: {LIGHTING_NOT_ELIGIBLE}"


def _integer_scalar(value: Any, name: str) -> int:
    array = np.asarray(value)
    if array.ndim != 0 or array.dtype.kind not in "iu":
        raise ValueError(f"camera archive {name} must be an integer scalar")
    return int(array)


def _validate_archive(archive: Mapping[str, Any]) -> tuple[np.ndarray, np.ndarray, int]:
    frames = np.asarray(archive["frames"])
    timestamps = np.asarray(archive["host_timestamp_ns"])
    if frames.dtype != np.uint8 or frames.ndim != 3 or any(size <= 0 for size in frames.shape):
        raise ValueError("camera archive frames must be a nonempty uint8 3D array")
    if timestamps.ndim != 1 or timestamps.dtype.kind not in "iu":
        raise ValueError("camera archive host timestamps must be a 1D integer array")
    if len(timestamps) != len(frames):
        raise ValueError("camera archive frames and host timestamps must be aligned")
    if len(timestamps) > 1 and np.any(np.diff(timestamps.astype(object)) <= 0):
        raise ValueError("camera archive host timestamps must be strictly increasing")
    trigger_ns = _integer_scalar(archive["trigger_host_timestamp_ns"], "trigger timestamp")
    return frames, timestamps, trigger_ns


def _sensor_timestamps(archive: Mapping[str, Any]) -> np.ndarray | None:
    """The archive's sensor timestamps, which time ball flight (F7) and club delivery (P6-8)."""
    sensor = archive.get("sensor_timestamp_ns")
    return None if sensor is None else np.asarray(sensor)


def _range_snapshot(evidence: Any, kind: str) -> dict[str, Any] | None:
    if evidence is None:
        return None
    return _json_value(
        {
            "kind": kind,
            "track": asdict(evidence.track),
            "geometry": asdict(evidence.geometry),
            "impact_t_s": float(evidence.impact_t_s),
            "status": getattr(evidence, "status", "accepted"),
        }
    )


def _json_value(value: Any):
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("camera fusion context values must be finite")
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise ValueError(f"camera fusion context cannot serialize {type(value).__name__}")


def _restore_range(snapshot: Any, kind: str):
    if snapshot is None:
        return None
    if not isinstance(snapshot, Mapping) or snapshot.get("kind") != kind:
        raise ValueError(f"invalid {kind} range evidence snapshot")
    track = BallTrack(**dict(snapshot["track"]))
    geometry_data = dict(snapshot["geometry"])
    for name in ("range_bin_starts", "range_bin_counts", "frame_time_offsets_s"):
        if geometry_data.get(name) is not None:
            geometry_data[name] = tuple(geometry_data[name])
    geometry = Geometry(**geometry_data)
    evidence_type = BallRangeEvidence if kind == "ball" else ClubRangeEvidence
    # Contexts frozen before evidence carried a status replay as they ran then.
    return evidence_type(
        track, geometry, float(snapshot["impact_t_s"]), status=snapshot.get("status", "accepted")
    )


def build_context(
    *,
    geometry: EffectiveCameraGeometryInputs,
    lighting_eligible: bool,
    ball_tracker: ReferenceBallTracker,
    club_tracker: ReferenceBallTracker,
    ball_range_evidence: Any,
    club_range_evidence: Any,
    ops_ball_speed_mph: float,
    ops_club_speed_mph: float | None,
    iwr_vertical_deg: float | None,
    iwr_horizontal_deg: float | None,
    iwr_horizontal_confidence: float | None,
    club: ClubType,
    capture_npz_sha256: str,
    session_uuid: str,
    shot_number: int,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    """Freeze every non-archive input before either stateful tracker changes.

    ``notes`` are the capture's labels that no longer block the estimators
    (D15): the lighting and optical-quality verdicts, as the kiosk judged them.
    """
    payload = {
        "schema": SCHEMA,
        "version": VERSION,
        "available": True,
        "geometry": geometry.snapshot(),
        "lighting_eligible": bool(lighting_eligible),
        "ball_tracker": ball_tracker.snapshot(),
        "club_tracker": club_tracker.snapshot(),
        "ball_range_evidence": _range_snapshot(ball_range_evidence, "ball"),
        "club_range_evidence": _range_snapshot(club_range_evidence, "club"),
        "ops_ball_speed_mph": ops_ball_speed_mph,
        "ops_club_speed_mph": ops_club_speed_mph,
        "iwr_vertical_deg": iwr_vertical_deg,
        "iwr_horizontal_deg": iwr_horizontal_deg,
        "iwr_horizontal_confidence": iwr_horizontal_confidence,
        "club": club.value,
        "capture_npz_sha256": capture_npz_sha256,
        "session_uuid": session_uuid,
        "shot_number": shot_number,
        "notes": [str(note) for note in notes or ()],
    }
    payload = _json_value(payload)
    payload["sha256"] = geometry_fingerprint(payload)
    return payload


def process_camera_fusion(context: Mapping[str, Any], archive: Mapping[str, Any]) -> dict:
    """Run both stages from frozen context and a loader-verified archive hash."""
    context = dict(context)
    fingerprint = context.pop("sha256", None)
    if context.get("schema") != SCHEMA or context.get("version") != VERSION:
        raise ValueError("unsupported camera fusion context")
    if context.get("available") is not True:
        raise ValueError("camera fusion context is unavailable")
    if not isinstance(context.get("session_uuid"), str) or not context["session_uuid"]:
        raise ValueError("camera fusion context has no session identity")
    shot_number = context.get("shot_number")
    if not isinstance(shot_number, int) or isinstance(shot_number, bool) or shot_number <= 0:
        raise ValueError("camera fusion context has no positive shot identity")
    if fingerprint != geometry_fingerprint(context):
        raise ValueError("camera fusion context fingerprint mismatch")
    if archive.get("_capture_npz_sha256") != context.get("capture_npz_sha256"):
        raise ValueError("camera fusion context does not match the capture archive")
    geometry = EffectiveCameraGeometryInputs.from_recorded_session(
        {"effective_camera_geometry": context["geometry"]}
    )
    frames, timestamps, trigger_ns = _validate_archive(archive)
    geometry.validate_archive_dimensions(frames.shape[-1], frames.shape[-2])
    ball_tracker = ReferenceBallTracker.from_snapshot(context["ball_tracker"])
    club_tracker = ReferenceBallTracker.from_snapshot(context["club_tracker"])
    errors = {}
    notes = [str(note) for note in context.get("notes") or ()]
    # Lighting labels the result and never blocks it (D15, P8-7). A context
    # frozen before notes existed still says its lighting was not eligible.
    if context["lighting_eligible"] is not True and not notes:
        notes.append(f"lighting: {LIGHTING_NOT_ELIGIBLE}")
    try:
        ball = estimate_camera_ball_flight(
            frames,
            timestamps,
            trigger_ns=trigger_ns,
            range_evidence=_restore_range(context["ball_range_evidence"], "ball"),
            geometry=geometry.ball_geometry(),
            ops_ball_speed_mph=float(context["ops_ball_speed_mph"]),
            iwr_vertical_deg=context["iwr_vertical_deg"],
            ball_tracker=ball_tracker,
            sensor_timestamps_ns=_sensor_timestamps(archive),
            trigger_frame_index=exposure_trigger_index(archive, len(frames)),
        )
    except Exception as error:  # stage isolation is part of the persisted contract
        ball = CameraBallEstimate(status="error")
        errors["ball"] = f"{type(error).__name__}: {error}"
    try:
        pre_trigger_count = _integer_scalar(archive["pre_trigger_count"], "pre-trigger count")
        if not 1 <= pre_trigger_count <= len(frames):
            raise ValueError("camera archive pre-trigger count is outside the frame range")
        # Impact is compared against the frame exposed at the trigger when the
        # clip records it (P6-5); older clips keep their arrival split.
        by_exposure = exposure_trigger_index(archive, len(frames))
        trigger_index = pre_trigger_count - 1 if by_exposure is None else by_exposure
        reference_ball = None
        diagnostics = ball.reference_ball_diagnostics or {}
        selected_candidate = diagnostics.get("selected_candidate")
        if isinstance(selected_candidate, Mapping):
            reference_ball = ReferenceBall(**dict(selected_candidate))
        club = estimate_chained_delivery(
            frames,
            timestamps,
            trigger_index=trigger_index,
            range_evidence=_restore_range(context["club_range_evidence"], "club"),
            geometry=geometry.delivery_geometry(),
            ops_club_speed_mph=context["ops_club_speed_mph"],
            ball_tracker=club_tracker,
            reference_ball=reference_ball,
            reference_ball_selected=bool(diagnostics),
            sensor_timestamp_ns=_sensor_timestamps(archive),
        )
    except Exception as error:  # stage isolation is part of the persisted contract
        club = ChainedDelivery(status="error")
        errors["club"] = f"{type(error).__name__}: {error}"
    decision = select_camera_assisted_horizontal(
        ball,
        iwr_horizontal_deg=context["iwr_horizontal_deg"],
        iwr_confidence=context["iwr_horizontal_confidence"],
    )
    return _json_value(
        {
            "schema_version": 1,
            "context_sha256": fingerprint,
            "ball_estimate": asdict(ball),
            "horizontal_decision": asdict(decision),
            "club_delivery": asdict(club),
            "next_ball_tracker": ball_tracker.snapshot(),
            "next_club_tracker": club_tracker.snapshot(),
            "errors": errors,
            "notes": notes,
        }
    )

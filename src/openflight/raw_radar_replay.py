"""Hash-bound replay of saved raw OPS rolling-buffer captures."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from .clubs import ClubType
from .rolling_buffer.monitor import get_optimal_spin_for_ball_speed
from .rolling_buffer.processor import RollingBufferProcessor
from .rolling_buffer.types import IQCapture

SCHEMA_VERSION = 1


def _benchmark_metric(
    value: Any,
    *,
    unit: str,
    contract_id: str,
    source: str,
    validation: str,
    reason: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    """Build one explicit normalized benchmark metric without changing its semantics."""
    numeric = None
    if not isinstance(value, bool) and isinstance(value, (int, float)):
        candidate = float(value)
        numeric = candidate if math.isfinite(candidate) else None
    resolved_status = status or ("available" if numeric is not None else "withheld")
    if resolved_status != "available":
        numeric = None
    return {
        "status": resolved_status,
        "value": numeric,
        "unit": unit,
        "contract_id": contract_id,
        "source": source,
        "validation": validation,
        "reason": None if resolved_status == "available" else reason or "replay value unavailable",
        "reference_estimated": False,
        "semantics_authority": "raw_replay_adapter",
    }


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _benchmark_attempt_from_raw_replay(report: Mapping[str, Any]) -> dict[str, Any]:
    """Adapt one immutable raw replay report to a normalized benchmark attempt."""
    session_uuid = report.get("session_uuid")
    shot_number = report.get("shot_number")
    if not isinstance(session_uuid, str) or not session_uuid:
        raise ValueError("raw replay report requires a nonempty session_uuid")
    if isinstance(shot_number, bool) or not isinstance(shot_number, int) or shot_number <= 0:
        raise ValueError("raw replay report requires a positive shot_number")
    stages = report.get("stages")
    if not isinstance(stages, Mapping):
        raise ValueError("raw replay report requires stages")
    ops = stages.get("ops") if isinstance(stages.get("ops"), Mapping) else {}
    ops_result = ops.get("result") if isinstance(ops.get("result"), Mapping) else {}
    total_stage = (
        stages.get("measured_total_speed_candidate")
        if isinstance(stages.get("measured_total_speed_candidate"), Mapping)
        else {}
    )
    total = (
        total_stage.get("candidate") if isinstance(total_stage.get("candidate"), Mapping) else {}
    )
    iwr = stages.get("iwr6843") if isinstance(stages.get("iwr6843"), Mapping) else {}
    camera = stages.get("camera") if isinstance(stages.get("camera"), Mapping) else {}
    recomputed = (
        camera.get("recomputed_radar_context")
        if isinstance(camera.get("recomputed_radar_context"), Mapping)
        else {}
    )
    camera_result = (
        recomputed.get("result") if isinstance(recomputed.get("result"), Mapping) else {}
    )
    horizontal = (
        camera_result.get("horizontal_decision")
        if isinstance(camera_result.get("horizontal_decision"), Mapping)
        else {}
    )
    delivery = (
        camera_result.get("club_delivery")
        if isinstance(camera_result.get("club_delivery"), Mapping)
        else {}
    )
    iwr_accepted = isinstance(iwr.get("status"), str) and iwr["status"].startswith("accepted")
    delivery_accepted = delivery.get("status") in {
        "ok",
        "fused",
        "chained_high",
        "approach_high",
    }
    total_status = (
        total.get("status")
        if total.get("status") in {"available", "withheld", "rejected"}
        else None
    )
    metrics = {
        "ball_speed_radial": _benchmark_metric(
            ops_result.get("ball_speed_mph"),
            unit="mph",
            contract_id="ball.speed.radial.mph.v1",
            source="ops_rolling_buffer_replay",
            validation="production_replay",
            reason=ops.get("error") or "OPS replay produced no detection",
        ),
        "ball_speed_total": _benchmark_metric(
            total.get("value_mph"),
            unit="mph",
            contract_id="ball.speed.total.mph.v1",
            source=str(total.get("source") or "ops_measured_los_projection_candidate"),
            validation=str(total.get("validation") or "unvalidated"),
            reason=total.get("reason") or total_stage.get("error"),
            status=total_status,
        ),
        "launch_angle_vertical": _benchmark_metric(
            iwr.get("launch_angle_deg"),
            unit="deg",
            contract_id="ball.launch.vertical.deg.v1",
            source="iwr6843_raw_replay",
            validation="unvalidated",
            reason=iwr.get("error") or f"IWR replay status {iwr.get('status')!r}",
            status=None if iwr_accepted else "withheld",
        ),
        "launch_angle_horizontal": _benchmark_metric(
            horizontal.get("selected_deg")
            if recomputed.get("status") == "replayed"
            else iwr.get("horizontal_deg"),
            unit="deg",
            contract_id="ball.launch.horizontal.deg.v1",
            source=str(horizontal.get("source") or "iwr6843_raw_replay"),
            validation="unvalidated",
            reason=recomputed.get("reason") or iwr.get("error") or "horizontal launch unavailable",
            status=None
            if (horizontal.get("selected_deg") is not None or iwr_accepted)
            else "withheld",
        ),
        "club_speed": _benchmark_metric(
            ops_result.get("club_speed_mph"),
            unit="mph",
            contract_id="club.speed.ops_radial_vs_trackman.conditional.mph.v1",
            source="ops_rolling_buffer_replay_radial",
            validation="production_replay_conditional",
            reason=ops.get("error") or "OPS replay produced no club-speed detection",
        ),
        "club_path": _benchmark_metric(
            delivery.get("club_path_deg"),
            unit="deg",
            contract_id="club.path.optical_feature_vs_face_center.conditional.deg.v1",
            source="camera_iwr_fused_optical_feature_unvalidated",
            validation="unvalidated",
            reason=f"camera delivery status {delivery.get('status')!r}",
            status=None if delivery_accepted else "withheld",
        ),
        "attack_angle": _benchmark_metric(
            delivery.get("attack_angle_deg"),
            unit="deg",
            contract_id="club.attack.optical_feature_vs_face_center.conditional.deg.v1",
            source="camera_iwr_fused_optical_feature_unvalidated",
            validation="unvalidated",
            reason=f"camera delivery status {delivery.get('status')!r}",
            status=None if delivery_accepted else "withheld",
        ),
    }
    has_read = any(metric["status"] == "available" for metric in metrics.values())
    attempt_id = f"{session_uuid}:{shot_number}"
    session_hash = report.get("session_sha256")
    if not isinstance(session_hash, str) or _SHA256.fullmatch(session_hash) is None:
        raise ValueError("raw replay report requires a lowercase session_sha256")
    source_identity = report.get("source_identity")
    source_identity = source_identity if isinstance(source_identity, Mapping) else {}
    return {
        "attempt_id": attempt_id,
        "session_uuid": session_uuid,
        "session_started_at": report.get("session_started_at"),
        "status": "read" if has_read else "no_read",
        "reason": None if has_read else "raw replay produced no available metric",
        "group": {
            "candidate_kind": "raw_replay_session",
            "arm_id": source_identity.get("arm_id"),
            "rig_geometry_sha256": source_identity.get("rig_geometry_sha256"),
            "capture_exposure_us": source_identity.get("capture_exposure_us"),
            "capture_gain": source_identity.get("capture_gain"),
            "software_content_sha256": source_identity.get("replay_software_content_sha256"),
            "session_uuid": session_uuid,
            "setup_config_hash": source_identity.get("setup_config_hash"),
            "placement_warned": source_identity.get("placement_warned"),
            "source_session_sha256": session_hash,
            "captured_software_content_sha256": source_identity.get(
                "captured_software_content_sha256"
            ),
        },
        "provenance": {
            "raw_replay_report_schema_version": report.get("schema_version"),
            "ops_capture_payload_sha256": ops.get("canonical_capture_payload_sha256"),
            "ops_processor_config_sha256": (
                ops.get("processor_config", {}).get("sha256")
                if isinstance(ops.get("processor_config"), Mapping)
                else None
            ),
            "iwr_capture_sha256": iwr.get("capture_sha256"),
            "camera_context_sha256": camera_result.get("context_sha256"),
        },
        "observations": {},
        "metrics": metrics,
    }


def benchmark_candidate_from_raw_replays(reports: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate every replayed shot from one hash-bound session for scoring."""
    if not reports:
        raise ValueError("raw replay benchmark candidate requires at least one report")
    attempts = [_benchmark_attempt_from_raw_replay(report) for report in reports]
    session_uuids = {attempt["session_uuid"] for attempt in attempts}
    session_hashes = {attempt["group"]["source_session_sha256"] for attempt in attempts}
    if len(session_uuids) != 1 or len(session_hashes) != 1:
        raise ValueError("raw replay reports must bind one exact session identity")
    attempt_ids = [attempt["attempt_id"] for attempt in attempts]
    if len(attempt_ids) != len(set(attempt_ids)):
        raise ValueError("raw replay reports must have unique shot identities")
    session_uuid = next(iter(session_uuids))
    session_hash = next(iter(session_hashes))
    source_identity = reports[0].get("source_identity")
    if not isinstance(source_identity, Mapping):
        source_identity = {}
    if any(report.get("source_identity", {}) != source_identity for report in reports[1:]):
        raise ValueError("raw replay reports disagree on source identity")
    return {
        "schema_version": 1,
        "identity": {
            "kind": "raw_replay_session",
            "session_uuid": session_uuid,
            "source_session_sha256": session_hash,
            "replay_software_content_sha256": source_identity.get("replay_software_content_sha256"),
            "captured_software_content_sha256": source_identity.get(
                "captured_software_content_sha256"
            ),
        },
        "attempts": sorted(attempts, key=lambda item: item["attempt_id"]),
        "ledger_entries": [],
        "provenance": {
            "adapter": "openflight.raw_radar_replay.benchmark_candidate_from_raw_replays.v1",
            "source_session_sha256": session_hash,
            "source_identity": dict(source_identity),
            "raw_evidence_unchanged": True,
            "physical_attempt_coverage": "unavailable; no physical attempts are inferred from sensor logs",
            "attempt_coverage": "every positive shot_number recorded in the source session was replayed",
        },
    }


def benchmark_candidate_from_raw_replay(report: Mapping[str, Any]) -> dict[str, Any]:
    """Compatibility wrapper for one-shot callers; use session aggregation for scoring."""
    return benchmark_candidate_from_raw_replays([report])


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be finite")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def replay_ops_capture(
    entry: Mapping[str, Any], *, sample_rate_hz: int, club_type: ClubType
) -> dict[str, Any]:
    """Replay one logged capture through the production processor without mutation."""
    if entry.get("type") != "rolling_buffer_capture":
        raise ValueError("OPS replay entry must be a rolling_buffer_capture")
    i_samples = entry.get("i_samples")
    q_samples = entry.get("q_samples")
    if not isinstance(i_samples, list) or not isinstance(q_samples, list) or not i_samples:
        raise ValueError("OPS replay requires nonempty saved I/Q arrays")
    if len(i_samples) != len(q_samples) or any(
        isinstance(value, bool) or not isinstance(value, int) for value in (*i_samples, *q_samples)
    ):
        raise ValueError("OPS saved I/Q arrays must be aligned integer samples")
    if (
        isinstance(sample_rate_hz, bool)
        or not isinstance(sample_rate_hz, int)
        or sample_rate_hz <= 0
    ):
        raise ValueError("sample_rate_hz must be a positive integer")
    capture = IQCapture(
        sample_time=_finite(entry.get("sample_time"), "sample_time"),
        trigger_time=_finite(entry.get("trigger_time"), "trigger_time"),
        i_samples=i_samples,
        q_samples=q_samples,
        timestamp=(
            _finite(entry["first_byte_timestamp"], "first_byte_timestamp")
            if entry.get("first_byte_timestamp") is not None
            else 0.0
        ),
        first_byte_timestamp=(
            _finite(entry["first_byte_timestamp"], "first_byte_timestamp")
            if entry.get("first_byte_timestamp") is not None
            else None
        ),
        trigger_timestamp=(
            _finite(entry["trigger_timestamp"], "trigger_timestamp")
            if entry.get("trigger_timestamp") is not None
            else None
        ),
        trigger_timestamp_source=entry.get("trigger_timestamp_source"),
        clock_sync_offset_s=(
            _finite(entry["clock_sync_offset_s"], "clock_sync_offset_s")
            if entry.get("clock_sync_offset_s") is not None
            else None
        ),
    )
    processor = RollingBufferProcessor(sample_rate=sample_rate_hz)
    timeline = processor.process_overlapping(capture)
    processed = processor.process_capture(
        capture,
        expected_spin_for_ball_speed=lambda speed: get_optimal_spin_for_ball_speed(
            speed, club_type
        ),
        club_type=club_type,
    )
    config = (
        processed.processor_config
        if processed is not None
        else processor.replay_config(
            club_type=club_type,
            spin_prior_policy="get_optimal_spin_for_ball_speed_v1",
            resolved_spin_prior_rpm=None,
        )
    )
    raw_payload = json.dumps(
        {
            "sample_time": capture.sample_time,
            "trigger_time": capture.trigger_time,
            "i_samples": i_samples,
            "q_samples": q_samples,
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    recorded_config = entry.get("processor_config_sha256")
    recorded_payload = entry.get("processor_config")
    equivalence = (
        "config_match_source_revision_not_proven"
        if isinstance(recorded_payload, Mapping)
        and recorded_payload.get("sha256") == recorded_config == config["sha256"]
        and dict(recorded_payload) == config
        else "unverified_missing_or_different_recorded_config"
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "stage": "ops_rolling_buffer",
        "status": "ok" if processed is not None else "no_detection",
        "canonical_capture_payload_sha256": hashlib.sha256(raw_payload).hexdigest(),
        "processor_config": config,
        "recorded_processor_config_sha256": recorded_config,
        "equivalence_status": equivalence,
        "result": asdict(processed) if processed is not None else None,
        "overlapping_readings": [asdict(reading) for reading in timeline.readings],
        "note": (
            "Canonical ball speed is an aggregate processor result. Per-window readings retain "
            "their own timestamps and are the only valid input to co-temporal LOS projection."
        ),
    }


def load_session_events(session_path: Path) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    """Read and validate exact session bytes once for replay collection."""
    raw = session_path.read_bytes()
    if raw and not raw.endswith(b"\n"):
        raise ValueError("session JSONL is incomplete because it has no final newline")
    events: list[dict[str, Any]] = []
    starts = []
    for line_number, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid session JSON on line {line_number}: {exc.msg}") from exc
        if isinstance(event, dict):
            if "shot_number" in event and (
                isinstance(event["shot_number"], bool)
                or not isinstance(event["shot_number"], int)
                or event["shot_number"] <= 0
            ):
                raise ValueError(f"invalid shot_number identity on line {line_number}")
            if event.get("type") == "session_start":
                starts.append(event)
            events.append(event)
    if (
        len(starts) != 1
        or not isinstance(starts[0].get("session_uuid"), str)
        or not starts[0]["session_uuid"]
    ):
        raise ValueError("session must contain exactly one start with a nonempty session_uuid")
    for event in events:
        if event.get("session_uuid") not in (None, starts[0]["session_uuid"]):
            raise ValueError("event session_uuid does not match session start")
        context = event.get("camera_fusion_context")
        if not isinstance(context, Mapping):
            continue
        context_uuid = context.get("session_uuid")
        if context_uuid is not None and context_uuid != starts[0]["session_uuid"]:
            raise ValueError("camera fusion context does not match session_uuid")
        if context.get("available") is True and context_uuid is None:
            raise ValueError("available camera fusion context requires session_uuid")
    return hashlib.sha256(raw).hexdigest(), starts[0], events


def session_shot_events(
    session_path: Path,
    shot_number: int,
    *,
    frozen_session: tuple[str, dict[str, Any], list[dict[str, Any]]] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Return one shot from a validated session, optionally using frozen bytes."""
    if isinstance(shot_number, bool) or not isinstance(shot_number, int) or shot_number <= 0:
        raise ValueError("shot_number must be a positive integer")
    session_hash, start, all_events = frozen_session or load_session_events(session_path)
    events = [
        event
        for event in all_events
        if type(event.get("shot_number")) is int and event["shot_number"] == shot_number
    ]
    return session_hash, [start, *events]


def session_attempt_numbers(session_path: Path) -> tuple[str, dict[str, Any], list[int]]:
    """Return every logged positive shot identity without inferring physical attempts."""
    session_hash, start, events = load_session_events(session_path)
    return (
        session_hash,
        start,
        sorted({event["shot_number"] for event in events if type(event.get("shot_number")) is int}),
    )


def replay_iwr_capture_bytes(
    raw: bytes,
    calibration,
    *,
    ball_speed_mph: float,
    club: str | None,
    net_range_m: float | None,
    tx_order: str,
    tdm_sign_policy: str,
    club_speed_mph: float | None,
    azimuth_offset_deg: float,
    club_window_policy,
    club_impact_correction_s: float,
    recovery_observations: list[tuple[float, float, float]],
    horizontal_phase_reference_rad: float | None = None,
):
    """Run the complete production ball/club estimator on frozen dump bytes."""
    from .iwr6843.runtime import process_raw_capture

    return process_raw_capture(
        raw,
        calibration,
        ball_speed_mph=ball_speed_mph,
        club=club,
        club_speed_mph=club_speed_mph,
        net_range_m=net_range_m,
        tx_order=tx_order,
        tdm_sign_policy=tdm_sign_policy,
        azimuth_offset_deg=azimuth_offset_deg,
        horizontal_phase_reference_rad=horizontal_phase_reference_rad,
        club_window_policy=club_window_policy,
        club_impact_correction_s=club_impact_correction_s,
        recovery_observations=recovery_observations,
    )

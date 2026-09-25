"""Bounded snapshots and read-only replay for live fusion diagnostics."""

from __future__ import annotations

import hashlib
import json
import math
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from flask import Flask, Response, request, send_file

SCHEMA_VERSION = 1
MAX_READ_BYTES_PER_POLL = 1024 * 1024
MAX_SHOTS_PER_SESSION = 100
MAX_PARTIAL_LINE_BYTES = 256 * 1024
MAX_CACHED_FILES = 64
_ACCEPTED_CAMERA_STATUSES = (
    "camera_assisted_high",
    "camera_assisted_experimental_agreement",
    "camera_experimental_disagreement",
    "camera_experimental_no_iwr",
    "camera_only_experimental",
)


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _metric(
    shot,
    key: str,
    label: str,
    attribute: str,
    unit: str,
    source: str,
    definition: str,
    *,
    status_attribute: str | None = None,
) -> dict[str, Any]:
    value = _finite(getattr(shot, attribute, None))
    recorded_status = getattr(shot, status_attribute, None) if status_attribute else None
    rejected = isinstance(recorded_status, str) and (
        recorded_status.startswith("rejected") or recorded_status == "error"
    )
    status = "rejected" if rejected else "available" if value is not None else "withheld"
    if status_attribute == "experimental_camera_horizontal_status" and value is not None:
        status = "available" if recorded_status in _ACCEPTED_CAMERA_STATUSES else "withheld"
    is_mock = getattr(shot, "mode", None) == "mock"
    validation = "mock" if is_mock else "estimated" if source == "estimated" else "unvalidated"
    if is_mock:
        source = "mock"
    reason = recorded_status if status != "available" and recorded_status else None
    if status != "available" and reason is None:
        reason = "not recorded by the live pipeline"
    return {
        "key": key,
        "label": label,
        "value": value,
        "unit": unit,
        "source": source,
        "status": status,
        "reason": reason,
        "recorded_status": recorded_status,
        "validation": validation,
        "definition": definition,
    }


def _reference_detector_comparison(shot) -> dict[str, Any]:
    """Expose only the bounded scene/impact agreement signal from persisted processing."""
    processing = getattr(shot, "camera_fusion_processing", None)
    ball_estimate = processing.get("ball_estimate") if isinstance(processing, Mapping) else None
    diagnostics = (
        ball_estimate.get("reference_ball_diagnostics")
        if isinstance(ball_estimate, Mapping)
        else None
    )
    if not isinstance(diagnostics, Mapping):
        reason = "reference-ball detector comparison was not recorded"
        available = False
        distance = None
        agreement = None
    else:
        scene = diagnostics.get("scene")
        impact = diagnostics.get("impact")
        scene_candidate = scene.get("candidate") if isinstance(scene, Mapping) else None
        impact_candidate = impact.get("candidate") if isinstance(impact, Mapping) else None
        agreement = diagnostics.get("agreement")
        coordinates = []
        for candidate in (scene_candidate, impact_candidate):
            if not isinstance(candidate, Mapping):
                coordinates = []
                break
            x_coord = _finite(candidate.get("x"))
            y_coord = _finite(candidate.get("y"))
            if x_coord is None or y_coord is None:
                coordinates = []
                break
            coordinates.append((x_coord, y_coord))
        available = len(coordinates) == 2 and isinstance(agreement, bool)
        distance = (
            math.hypot(
                coordinates[0][0] - coordinates[1][0],
                coordinates[0][1] - coordinates[1][1],
            )
            if available
            else None
        )
        reason = (
            "scene and impact detectors agreed"
            if available and agreement
            else "scene and impact detectors disagreed"
            if available
            else "reference-ball detector comparison is unavailable or malformed"
        )
    return {
        "key": "reference_ball_detector_distance_px",
        "label": "Scene versus impact reference-ball detector",
        "value": distance,
        "unit": "px",
        "status": "available" if available else "withheld",
        "reason": reason,
        "shared_inputs": ["saved_camera_frames", "trigger_timestamp"],
    }


def build_snapshot(
    shot,
    *,
    session_uuid: str,
    revision: int,
    phase: str,
    outcome: str,
    reason: str | None = None,
) -> dict[str, Any]:
    """Build a finite, allowlisted diagnostic from fields already on a shot."""
    if revision not in (1, 2) or phase not in ("pending", "terminal"):
        raise ValueError("invalid diagnostic revision or phase")
    if outcome not in ("processing", "complete", "partial", "rejected"):
        raise ValueError("invalid diagnostic outcome")
    shot_number = getattr(shot, "shot_number", None)
    if not isinstance(shot_number, int) or shot_number <= 0:
        raise ValueError("diagnostic shot_number must be positive")

    ball_contract = getattr(shot, "ball_speed_contract", None) or "radial"
    ball_source = (
        "ops_total_cosine_corrected" if ball_contract == "total_cosine_corrected" else "ops_radial"
    )
    horizontal_source = getattr(shot, "launch_angle_horizontal_source", None) or "unavailable"
    vertical_source = getattr(shot, "launch_angle_vertical_source", None) or "unavailable"
    camera_status = getattr(shot, "experimental_camera_horizontal_status", None)
    speed_candidate = getattr(shot, "experimental_ball_speed_total", None)
    candidate_status = (
        speed_candidate.get("status") if isinstance(speed_candidate, Mapping) else "withheld"
    )
    candidate_value = (
        _finite(speed_candidate.get("value_mph")) if isinstance(speed_candidate, Mapping) else None
    )
    candidate_reason = (
        speed_candidate.get("reason")
        if isinstance(speed_candidate, Mapping) and isinstance(speed_candidate.get("reason"), str)
        else None
    )
    metrics = [
        _metric(
            shot,
            "ball_speed_mph",
            "Ball speed",
            "ball_speed_mph",
            "mph",
            ball_source,
            "OPS radial speed, or its explicitly recorded cosine-corrected total.",
        ),
        {
            "key": "experimental_ball_speed_total_mph",
            "label": "Experimental total ball-speed candidate",
            "value": candidate_value if candidate_status == "available" else None,
            "unit": "mph",
            "source": "ops_radial_cosine_candidate",
            "status": "available"
            if candidate_status == "available" and candidate_value is not None
            else "withheld",
            "reason": None
            if candidate_status == "available" and candidate_value is not None
            else candidate_reason or "experimental total-speed candidate was not recorded",
            "recorded_status": candidate_status,
            "validation": "unvalidated",
            "definition": (
                "Historical peak-radial model candidate; it does not replace the OPS radial "
                "measurement or establish co-temporal 3D velocity/LOS alignment."
            ),
        },
        _metric(
            shot,
            "club_speed_mph",
            "Club speed",
            "club_speed_mph",
            "mph",
            "ops_radial",
            "OPS club-speed estimate from the detected swing return.",
        ),
        _metric(
            shot,
            "launch_vertical_deg",
            "Vertical launch",
            "launch_angle_vertical",
            "deg",
            vertical_source,
            "Displayed vertical launch bearing; estimated values are labeled estimated.",
        ),
        _metric(
            shot,
            "launch_horizontal_deg",
            "Horizontal launch",
            "launch_angle_horizontal",
            "deg",
            horizontal_source,
            "Displayed horizontal launch bearing from the recorded live selection.",
        ),
        _metric(
            shot,
            "club_path_deg",
            "Club path",
            "club_path_deg",
            "deg",
            "recorded_canonical",
            "Recorded canonical club-path field; its device provenance is not inferred here.",
        ),
        _metric(
            shot,
            "attack_angle_deg",
            "Attack angle",
            "club_angle_deg",
            "deg",
            "recorded_canonical",
            "Recorded canonical attack-angle field; its device provenance is not inferred here.",
        ),
        _metric(
            shot,
            "iwr_horizontal_deg",
            "IWR horizontal bearing",
            "iwr6843_horizontal_deg",
            "deg",
            "iwr6843_bearing",
            "IWR horizontal ball bearing retained before camera selection.",
        ),
        _metric(
            shot,
            "camera_horizontal_deg",
            "Camera horizontal bearing",
            "experimental_camera_horizontal_deg",
            "deg",
            (
                "camera_only_candidate"
                if camera_status == "camera_only_experimental"
                else "camera_range_assisted_candidate"
            ),
            "Experimental camera bearing reconstructed with recorded range evidence.",
            status_attribute="experimental_camera_horizontal_status",
        ),
        _metric(
            shot,
            "iwr_club_path_candidate_deg",
            "IWR club-path candidate",
            "experimental_club_path_deg",
            "deg",
            "iwr6843_candidate",
            "Experimental IWR club-path candidate, including rejected candidates.",
            status_attribute="experimental_club_path_status",
        ),
        _metric(
            shot,
            "iwr_attack_angle_candidate_deg",
            "IWR attack-angle candidate",
            "experimental_attack_angle_deg",
            "deg",
            "iwr6843_candidate",
            "Experimental IWR attack-angle candidate, including rejected candidates.",
            status_attribute="experimental_attack_angle_status",
        ),
        _metric(
            shot,
            "camera_iwr_club_path_deg",
            "Camera/IWR club-path candidate",
            "experimental_fused_club_path_deg",
            "deg",
            "camera_iwr_candidate",
            "Experimental camera/IWR chained club-path candidate.",
            status_attribute="experimental_fused_status",
        ),
        _metric(
            shot,
            "camera_iwr_attack_angle_deg",
            "Camera/IWR attack-angle candidate",
            "experimental_fused_attack_angle_deg",
            "deg",
            "camera_iwr_candidate",
            "Experimental camera/IWR chained attack-angle candidate.",
            status_attribute="experimental_fused_status",
        ),
    ]
    delta = _finite(getattr(shot, "experimental_camera_iwr_delta_deg", None))
    camera_value = _finite(getattr(shot, "experimental_camera_horizontal_deg", None))
    iwr_value = _finite(getattr(shot, "iwr6843_horizontal_deg", None))
    comparable = (
        phase == "terminal"
        and delta is not None
        and camera_value is not None
        and iwr_value is not None
        and camera_status in _ACCEPTED_CAMERA_STATUSES
    )
    comparison = {
        "key": "camera_iwr_horizontal_delta_deg",
        "label": "Camera versus IWR horizontal bearing",
        "value": delta if comparable else None,
        "unit": "deg",
        "status": "available" if comparable else "withheld",
        "reason": None
        if comparable
        else camera_status or "compatible camera and IWR horizontal evidence was not recorded",
        "shared_inputs": [
            "camera_horizontal_deg",
            "iwr_horizontal_deg",
            "ops_ball_speed_constraint",
            "iwr_range_evidence",
        ],
    }
    fusion_context = getattr(shot, "camera_fusion_context", None) or {}
    geometry_parameters = (fusion_context.get("geometry") or {}).get("parameters") or {}
    calibrated_model = geometry_parameters.get("calibrated_model_snapshot")
    return {
        "schema_version": SCHEMA_VERSION,
        "session_uuid": session_uuid,
        "shot_number": shot_number,
        "revision": revision,
        "phase": phase,
        "outcome": outcome,
        **({"reason": reason} if reason else {}),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "accuracy_qualified": False,
        "camera_model": (
            {
                "status": "experimental_calibrated",
                "validation": "unvalidated",
                "snapshot": calibrated_model,
            }
            if calibrated_model is not None
            else {
                "status": "legacy_inferred_ball_geometry",
                "validation": "unvalidated",
            }
        ),
        "calibrated_camera_status": getattr(shot, "calibrated_camera_status", None),
        "calibrated_camera_reason": getattr(shot, "calibrated_camera_reason", None),
        "metrics": metrics,
        "comparisons": [comparison, _reference_detector_comparison(shot)],
    }


@dataclass
class _FileState:
    identity: tuple[int, int] | None = None
    prefix_sha256: str | None = None
    prefix_length: int = 0
    offset: int = 0
    buffer: bytes = b""
    session_uuid: str | None = None
    ended: bool = False
    saw_diagnostic: bool = False
    shots: dict[int, dict[str, Any]] = field(default_factory=dict)
    ignored_records: int = 0
    truncated: bool = False
    oversized_lines: int = 0
    discarding_oversized_line: bool = False


class DiagnosticReader:
    """Incrementally read only diagnostic records from bounded JSONL chunks."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._states: dict[Path, _FileState] = {}

    @staticmethod
    def _safe_files(run: Path) -> list[Path]:
        files = []
        for path in sorted(run.rglob("session_*.jsonl")):
            relative = path.relative_to(run)
            current = run
            if any((current := current / part).is_symlink() for part in relative.parts):
                continue
            if path.is_file():
                files.append(path)
        return files

    @staticmethod
    def _consume(state: _FileState, line: bytes) -> None:
        if len(line) > MAX_PARTIAL_LINE_BYTES:
            state.oversized_lines += 1
            return
        try:
            entry = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if not isinstance(entry, Mapping):
            return
        entry_type = entry.get("type")
        if entry_type == "session_start":
            value = entry.get("session_uuid")
            new_uuid = value if isinstance(value, str) else None
            if state.session_uuid is not None and new_uuid != state.session_uuid:
                state.shots.clear()
                state.saw_diagnostic = False
                state.ended = False
                state.ignored_records = 0
            state.session_uuid = new_uuid
        elif entry_type == "session_end":
            state.ended = True
        elif entry_type == "fusion_diagnostic":
            if state.session_uuid is None or entry.get("session_uuid") != state.session_uuid:
                state.ignored_records += 1
                return
            shot_number = entry.get("shot_number")
            revision = entry.get("revision")
            phase = entry.get("phase")
            outcome = entry.get("outcome")
            valid_revision = not isinstance(revision, bool) and (revision, phase) in (
                (1, "pending"),
                (2, "terminal"),
            )
            valid_outcome = (revision == 1 and outcome == "processing") or (
                revision == 2 and outcome in ("complete", "partial", "rejected")
            )
            metrics = entry.get("metrics")
            comparisons = entry.get("comparisons")
            valid_metrics = isinstance(metrics, list) and all(
                isinstance(metric, Mapping)
                and isinstance(metric.get("key"), str)
                and metric.get("status") in ("available", "withheld", "rejected")
                and (
                    metric.get("value") is None
                    or (
                        not isinstance(metric.get("value"), bool)
                        and isinstance(metric.get("value"), (int, float))
                        and math.isfinite(float(metric["value"]))
                    )
                )
                for metric in metrics
            )
            valid_comparisons = isinstance(comparisons, list) and all(
                isinstance(comparison, Mapping)
                and isinstance(comparison.get("key"), str)
                and comparison.get("status") in ("available", "withheld")
                and (
                    comparison.get("value") is None
                    or (
                        not isinstance(comparison.get("value"), bool)
                        and isinstance(comparison.get("value"), (int, float))
                        and math.isfinite(float(comparison["value"]))
                    )
                )
                for comparison in comparisons
            )
            if (
                entry.get("schema_version") != SCHEMA_VERSION
                or isinstance(shot_number, bool)
                or not isinstance(shot_number, int)
                or shot_number <= 0
                or not valid_revision
                or not valid_outcome
                or not valid_metrics
                or not valid_comparisons
            ):
                state.ignored_records += 1
                return
            try:
                json.dumps(entry, allow_nan=False)
            except (TypeError, ValueError):
                state.ignored_records += 1
                return
            state.saw_diagnostic = True
            current = state.shots.get(shot_number)
            if current is None or revision > current.get("revision", 0):
                state.shots[shot_number] = dict(entry)
                if len(state.shots) > MAX_SHOTS_PER_SESSION:
                    del state.shots[min(state.shots)]
                    state.truncated = True

    def read(self, run: Path) -> dict[str, Any]:
        with self._lock:
            files = self._safe_files(run)
            total_file_count = len(files)
            if total_file_count > MAX_CACHED_FILES:
                files = files[-MAX_CACHED_FILES:]
            live = set(files)
            self._states = {path: state for path, state in self._states.items() if path in live}
            sessions = []
            complete = True
            any_truncated = False
            remaining_budget = MAX_READ_BYTES_PER_POLL
            for path in files:
                state = self._states.setdefault(path, _FileState())
                stat = path.stat()
                identity = (stat.st_dev, stat.st_ino)
                prefix_length = state.prefix_length or min(stat.st_size, 256)
                prefix_sha256 = None
                if prefix_length:
                    with path.open("rb") as handle:
                        prefix_sha256 = hashlib.sha256(handle.read(prefix_length)).hexdigest()
                if (
                    state.identity != identity
                    or stat.st_size < state.offset
                    or (state.prefix_sha256 is not None and state.prefix_sha256 != prefix_sha256)
                ):
                    state = _FileState(
                        identity=identity,
                        prefix_sha256=prefix_sha256,
                        prefix_length=min(stat.st_size, 256),
                    )
                    self._states[path] = state
                state.identity = identity
                if prefix_sha256 is not None:
                    state.prefix_sha256 = prefix_sha256
                    state.prefix_length = prefix_length
                with path.open("rb") as handle:
                    handle.seek(state.offset)
                    chunk = handle.read(remaining_budget)
                remaining_budget -= len(chunk)
                state.offset += len(chunk)
                data = state.buffer + chunk
                lines = data.split(b"\n")
                state.buffer = lines.pop()
                if state.discarding_oversized_line:
                    if lines:
                        lines.pop(0)
                        state.discarding_oversized_line = False
                    else:
                        state.buffer = b""
                if len(state.buffer) > MAX_PARTIAL_LINE_BYTES:
                    state.buffer = b""
                    state.oversized_lines += 1
                    state.discarding_oversized_line = True
                for line in lines:
                    self._consume(state, line)
                file_complete = state.offset >= stat.st_size and not state.buffer
                complete = complete and file_complete
                snapshots = sorted(state.shots.values(), key=lambda item: item["shot_number"])
                any_truncated = any_truncated or state.truncated
                reasons = []
                if not state.saw_diagnostic and file_complete:
                    reasons.append("session has no fusion diagnostic records")
                if not file_complete:
                    reasons.append("session indexing is still in progress")
                if state.buffer:
                    reasons.append("session log has an incomplete trailing record")
                if state.ignored_records:
                    reasons.append(
                        f"ignored {state.ignored_records} diagnostic records with invalid "
                        "session identity"
                    )
                if state.oversized_lines:
                    reasons.append(f"skipped {state.oversized_lines} oversized JSONL records")
                sessions.append(
                    {
                        "session_uuid": state.session_uuid,
                        "session_file": path.relative_to(run).as_posix(),
                        "ended": state.ended,
                        "diagnostics_available": state.saw_diagnostic,
                        "reasons": reasons,
                        "shots": snapshots,
                    }
                )
            reasons = []
            if not sessions:
                reasons.append("no session logs were found in this run")
            if total_file_count > MAX_CACHED_FILES:
                reasons.append("older session files are outside the bounded reader window")
                any_truncated = True
            if not complete:
                reasons.append("bounded session indexing is still in progress")
            has_diagnostics = any(item["diagnostics_available"] for item in sessions)
            status = (
                "unavailable"
                if not sessions or (complete and not has_diagnostics)
                else "partial"
                if not complete
                else "available"
            )
            return {
                "schema_version": SCHEMA_VERSION,
                "status": status,
                "reasons": reasons,
                "read_complete": complete,
                "retention": {
                    "max_shots_per_session": MAX_SHOTS_PER_SESSION,
                    "truncated": any_truncated,
                },
                "sessions": sessions,
            }


def _json_response(value: Mapping[str, Any], status: int = 200) -> Response:
    return Response(
        json.dumps(value, allow_nan=False, separators=(",", ":")),
        status=status,
        mimetype="application/json",
        headers={"Cache-Control": "no-store"},
    )


def register_fusion_diagnostics(app: Flask, resolve_scope, page_path: Path) -> None:
    """Register the read-only tester diagnostic page and scoped JSON endpoint."""
    reader = DiagnosticReader()

    @app.get("/fusion-diagnostics.html")
    def fusion_diagnostics_page():
        response = send_file(page_path)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/tester/diagnostics")
    def fusion_diagnostics_api():
        try:
            scope, run = resolve_scope(request.args)
            result = reader.read(run)
            result["scope"] = scope
            return _json_response(result)
        except FileNotFoundError as exc:
            return _json_response({"error": str(exc)}, 404)
        except (OSError, ValueError) as exc:
            return _json_response({"error": str(exc)}, 400)

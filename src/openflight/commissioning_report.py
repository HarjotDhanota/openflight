"""Offline commissioning evidence summary for recorded session artifacts."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any, Mapping, Sequence


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) else None


def _summary(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "minimum": None, "median": None, "p90": None, "maximum": None}
    ordered = sorted(values)
    middle = len(ordered) // 2
    median = ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2
    return {
        "n": len(ordered),
        "minimum": ordered[0],
        "median": median,
        "p90": ordered[max(0, math.ceil(0.9 * len(ordered)) - 1)],
        "maximum": ordered[-1],
    }


def build_commissioning_report(sources: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Summarize only recorded events; physical attempts remain outside this evidence."""
    if not sources:
        raise ValueError("at least one session source is required")
    sessions = []
    event_counts: Counter[str] = Counter()
    timing: dict[str, list[float]] = defaultdict(list)
    acquisition_counts: Counter[str] = Counter()
    all_shots = []
    session_uuids = set()
    for source in sources:
        records = source.get("records")
        if not isinstance(records, list):
            raise ValueError("session source records must be a list")
        starts = [
            row
            for row in records
            if isinstance(row, Mapping) and row.get("type") == "session_start"
        ]
        if len(starts) != 1:
            raise ValueError("each session source must contain exactly one session_start")
        session_uuid = starts[-1].get("session_uuid") if starts else None
        if not isinstance(session_uuid, str) or not session_uuid:
            raise ValueError("each session source requires a nonempty session_uuid")
        if session_uuid in session_uuids:
            raise ValueError("session source UUIDs must be unique")
        session_uuids.add(session_uuid)
        if any(
            row.get("session_uuid") not in (None, session_uuid)
            for row in records
            if isinstance(row, Mapping)
        ):
            raise ValueError("session records must not mix session UUIDs")
        by_shot: dict[int, dict[str, list[Mapping[str, Any]]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for row in records:
            if not isinstance(row, Mapping) or not isinstance(row.get("type"), str):
                raise ValueError("session records must be objects with a string type")
            event_type = row["type"]
            event_counts[event_type] += 1
            number = row.get("shot_number")
            if isinstance(number, int) and not isinstance(number, bool) and number > 0:
                by_shot[number][event_type].append(row)
            for field, output_name in (
                ("latency_ms", "trigger_event_latency_ms"),
                ("trigger_latency_ms", "rolling_trigger_latency_ms"),
                ("impact_transition_gap_ms", "impact_transition_gap_ms"),
                ("trigger_delta_ms", f"{event_type}_trigger_delta_ms"),
                ("dump_duration_s", "iwr6843_dump_duration_s"),
            ):
                value = _finite(row.get(field))
                if value is not None:
                    timing[output_name].append(value)
            if event_type == "shot_detected" and isinstance(row.get("pipeline_ms"), Mapping):
                for stage, value in row["pipeline_ms"].items():
                    numeric = _finite(value)
                    if isinstance(stage, str) and numeric is not None:
                        timing[f"pipeline_{stage}_ms"].append(numeric)
        shots = []
        for number, events in sorted(by_shot.items()):
            shot = {"session_uuid": session_uuid, "shot_number": number}
            for event_type, label in (
                ("rolling_buffer_capture", "ops_capture"),
                ("camera_capture", "camera_capture"),
                ("iwr6843_capture", "iwr6843_capture"),
            ):
                rows = events.get(event_type, [])
                if not rows:
                    state, reason = "missing", "no recorded event"
                elif len(rows) > 1:
                    state, reason = (
                        "ambiguous",
                        "multiple recorded events for the same shot and stage",
                    )
                else:
                    latest = rows[-1]
                    error = latest.get("capture_error")
                    artifact = latest.get("_artifact") or {}
                    if error:
                        state, reason = "failed", str(error)
                    elif event_type in ("camera_capture", "iwr6843_capture") and not artifact.get(
                        "available"
                    ):
                        state, reason = (
                            "artifact_missing",
                            artifact.get("reason") or "saved path unavailable",
                        )
                    else:
                        state, reason = "recorded", None
                    if event_type == "camera_capture":
                        metadata = latest.get("metadata") or {}
                        shot["camera_timing"] = {
                            key: metadata.get(key)
                            for key in (
                                "frame_count",
                                "delivered_fps",
                                "gap_count",
                                "max_interval_ms",
                            )
                        }
                        gap_count = metadata.get("gap_count")
                        if (
                            isinstance(gap_count, int)
                            and not isinstance(gap_count, bool)
                            and gap_count >= 0
                        ):
                            acquisition_counts["camera_recorded_frame_gaps"] += gap_count
                    if event_type == "rolling_buffer_capture":
                        shot["ops_sample_count"] = latest.get("sample_count")
                    if event_type == "iwr6843_capture":
                        shot["iwr6843_capture_bytes"] = latest.get("capture_bytes")
                acquisition_counts[f"{label}_{state}"] += 1
                shot[label] = {"status": state, "reason": reason, "event_count": len(rows)}
            shot["shot_detected"] = bool(events.get("shot_detected"))
            shots.append(shot)
            all_shots.append(shot)
        has_diagnostics = any(row.get("type") == "fusion_diagnostic" for row in records)
        terminal_status = (
            "unknown_not_recorded"
            if not has_diagnostics
            else (
                "complete"
                if all(
                    any(
                        row.get("phase") == "terminal"
                        for row in events.get("fusion_diagnostic", [])
                    )
                    for events in by_shot.values()
                )
                else "incomplete"
            )
        )
        sessions.append(
            {
                "path": source.get("path"),
                "sha256": source.get("sha256"),
                "byte_count": source.get("byte_count"),
                "session_uuid": session_uuid,
                "complete_jsonl": source.get("complete") is True,
                "session_ended": any(row.get("type") == "session_end" for row in records),
                "terminal_diagnostics_status": terminal_status,
                "parse_warnings": list(source.get("warnings") or []),
                "recorded_shots": len(by_shot),
                "shots": shots,
            }
        )
    trigger_total = event_counts["trigger_event"]
    accepted = sum(
        row.get("accepted") is True
        for source in sources
        for row in source["records"]
        if row.get("type") == "trigger_event"
    )
    rejected = sum(
        row.get("accepted") is False
        for source in sources
        for row in source["records"]
        if row.get("type") == "trigger_event"
    )
    unknown_trigger_outcomes = trigger_total - accepted - rejected
    return {
        "schema_version": 1,
        "diagnostic": "offline_commissioning_evidence",
        "status": "complete"
        if all(
            item["complete_jsonl"]
            and item["session_ended"]
            and item["terminal_diagnostics_status"] != "incomplete"
            for item in sessions
        )
        else "partial",
        "sessions": sessions,
        "counts": {
            "recorded_sensor_shots": len(all_shots),
            "trigger_events": trigger_total,
            "accepted_trigger_events": accepted,
            "rejected_trigger_events": rejected,
            "unknown_trigger_outcomes": unknown_trigger_outcomes,
            "event_types": dict(sorted(event_counts.items())),
            "acquisition_states": dict(sorted(acquisition_counts.items())),
        },
        "timing": {key: _summary(values) for key, values in sorted(timing.items())},
        "interpretation": {
            "physical_attempt_coverage": "unknown; sensor logs cannot establish unrecorded physical swings",
            "latency": "reported values preserve each recorded field's clock semantics and are not cross-clock validation",
            "acceptance": "diagnostic only; no commissioning limits are inferred",
        },
    }

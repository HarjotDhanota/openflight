"""Audit raw OPS GPIO evidence; association alone never qualifies sample timing."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence


def _ns(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _stream(records):  # pylint: disable=too-many-branches,too-many-boolean-expressions
    issues = []
    starts = [row for row in records if row.get("type") == "start"]
    ends = [row for row in records if row.get("type") == "end"]
    edges = [row for row in records if row.get("type") == "edge"]
    start = starts[0] if starts else {}
    end = ends[-1] if ends else {}
    if len(starts) != 1 or not records or records[0].get("type") != "start":
        issues.append("missing_or_duplicate_start")
    if not ends:
        issues.append("missing_end")
    elif len(ends) != 1 or records[-1].get("type") != "end":
        issues.append("misplaced_or_duplicate_end")
    if any(row.get("type") == "reset" for row in records):
        issues.append("reset_observed")
    if any(row.get("type") not in {"start", "edge", "end", "reset"} for row in records):
        issues.append("unknown_record_type")
    if (
        start.get("schema") != "openflight.ops-sync.v1"
        or start.get("event_clock") != "monotonic"
        or not isinstance(start.get("boot_id"), str)
        or not start.get("boot_id")
        or not _ns(start.get("initial_level"))
        or start.get("initial_level") not in (0, 1)
        or not _ns(start.get("line_offset"))
        or not _ns(start.get("mono_ns"))
    ):
        issues.append("invalid_start_context")
    if end and (
        not _ns(end.get("mono_ns"))
        or not _ns(start.get("mono_ns"))
        or end["mono_ns"] < start["mono_ns"]
        or not _ns(end.get("edge_count"))
        or end.get("edge_count") != len(edges)
        or end.get("error") is not None
        or end.get("pending_at_end") is not False
    ):
        issues.append("unclean_end")
    previous = None
    for index, edge in enumerate(edges, 1):
        for field in ("line_seqno", "global_seqno"):
            if not _ns(edge.get(field)) or edge.get(field) != index:
                issues.append(f"{field.replace('seqno', 'sequence')}_gap_or_duplicate")
        stamp = edge.get("timestamp_ns")
        received = edge.get("received_mono_ns")
        if (
            not _ns(stamp)
            or not _ns(received)
            or received < stamp
            or (previous is not None and stamp <= previous)
            or edge.get("edge") not in {"rising", "falling"}
            or not _ns(edge.get("line_offset"))
            or edge.get("line_offset") != start.get("line_offset")
        ):
            issues.append("invalid_edge")
        if _ns(stamp):
            if _ns(start.get("mono_ns")) and stamp < start["mono_ns"]:
                issues.append("edge_before_initial_sample")
            if _ns(end.get("mono_ns")) and (
                stamp > end["mono_ns"] or (_ns(received) and received > end["mono_ns"])
            ):
                issues.append("edge_after_recording")
            previous = stamp
    return start, end, edges, list(dict.fromkeys(issues))


def _associate(capture, start, end, edges):  # pylint: disable=too-many-return-statements,too-many-branches
    if capture.get("boot_id") != start.get("boot_id"):
        return None, "boot_mismatch"
    if (
        capture.get("dump_complete") is not True
        or not _ns(capture.get("sample_count"))
        or capture["sample_count"] == 0
        or not isinstance(capture.get("raw_sha256"), str)
        or re.fullmatch(r"[a-f0-9]{64}", capture["raw_sha256"]) is None
    ):
        return None, "capture_incomplete"
    low, high = capture.get("armed_mono_ns"), capture.get("dump_start_mono_ns")
    if not _ns(low) or not _ns(high) or high <= low:
        return None, "capture_window_invalid"
    if low < start["mono_ns"] or high > end["mono_ns"]:
        return None, "capture_outside_recording"
    level = start["initial_level"]
    for edge in edges:
        if edge["timestamp_ns"] < low:
            level = int(edge["edge"] == "rising")
    if level != 0:
        return None, "initial_level_not_low"
    rise, pulses = None, []
    for edge in edges:
        stamp = edge["timestamp_ns"]
        if not low <= stamp <= high:
            continue
        if edge["edge"] == "rising":
            if rise is not None:
                return None, "repeated_rising_edge"
            rise = stamp
        else:
            if rise is None:
                return None, "orphan_falling_edge"
            pulses.append({"rising_ns": rise, "falling_ns": stamp, "width_ns": stamp - rise})
            rise = None
    if rise is not None:
        return None, "incomplete_pulse"
    if not pulses:
        return None, "pulse_unobserved"
    if len(pulses) != 1:
        return None, "multiple_pulses"
    return pulses[0], "associated_without_physical_calibration"


def analyze_recording(records: Sequence[dict], captures: Sequence[dict] = ()) -> dict:
    """Associate only complete, unique pulses within explicitly observed arm windows."""
    if any(not isinstance(row, dict) for row in records) or any(
        not isinstance(row, dict) for row in captures
    ):
        raise ValueError("records and captures must contain JSON objects")
    ids = [capture.get("capture_id") for capture in captures]
    if any(not isinstance(value, str) or not value for value in ids):
        raise ValueError("each capture needs a capture_id")
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate capture_id")
    start, end, edges, issues = _stream(records)
    outcomes = []
    for capture in captures:
        pulse, reason = (
            (None, "recording_invalid") if issues else _associate(capture, start, end, edges)
        )
        outcomes.append(
            {
                "capture_id": capture["capture_id"],
                "raw_sha256": capture.get("raw_sha256"),
                "status": "associated_unqualified" if pulse else "unassociated",
                "reason": reason,
                "pulse": pulse,
                "sample_mapping": None,
                "physical_edge_uncertainty_ns": None,
            }
        )
    uses = Counter(row["pulse"]["rising_ns"] for row in outcomes if row["pulse"])
    for row in outcomes:
        if row["pulse"] and uses[row["pulse"]["rising_ns"]] > 1:
            row.update(status="unassociated", reason="pulse_reused", pulse=None)
    return {
        "schema": "openflight.ops-sync-audit.v1",
        "event_clock": start.get("event_clock"),
        "boot_id": start.get("boot_id"),
        "edge_count": len(edges),
        "stream_issues": issues,
        "physical_timing_qualified": False,
        "captures": outcomes,
        "limitation": (
            "Kernel timestamps and pulse association do not establish "
            "edge-to-ADC or physical impact timing."
        ),
    }

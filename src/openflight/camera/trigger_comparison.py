"""Read-only trigger acquisition review; human labels grade, never drive capture."""

import json
from pathlib import Path

from openflight.camera import trigger_policy
from openflight.pi_trace_summary import build_attempts, load_trace


def _json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _rows(path):
    if not path.exists():
        return []
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            rows.append(json.loads(line))
        except ValueError:
            rows.append({"read_error": f"{path.name} line {number}: incomplete evidence"})
    return rows


def _relative(path, root):
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None


def _attempt(folder, run, root):
    result = _json(folder / "RESULT.json", {})
    association = _json(folder / "fusion-association.json", {})
    settings = _json(folder / "trigger-policy.json", {})
    held = _json(folder / "iwr-held.json", {})
    routes = _rows(folder / "ops-camera/routing.jsonl")
    gpio = _rows(folder / "ops-camera/gpio.jsonl")
    iwr = _rows(folder / "iwr-observations.jsonl")
    controls = _rows(folder / "ops-camera/ops-control.jsonl")
    publication = _rows(folder / "publication.jsonl")
    ops = (
        bool(result.get("ops_capture_observed"))
        and (folder / "ops-camera/ops-response.raw").is_file()
    )
    iwr_retained = (folder / "iwr-dump.bin").is_file()
    candidates = []
    used_routes = set()
    for index, row in enumerate(gpio):
        if row.get("type") == "edge":
            matches = [
                (route_index, route)
                for route_index, route in enumerate(routes)
                if route_index not in used_routes
                and route.get("request", {}).get("kernel_mono_interval_ns")
                == [row.get("timestamp_ns")] * 2
            ]
            route_index, routed = matches[0] if matches else (None, {})
            if route_index is not None:
                used_routes.add(route_index)
            candidates.append(
                {
                    "detector": "ops",
                    "event_identity": routed.get("request", {}).get(
                        "event_id", f"gpio-row-{index}"
                    ),
                    "edge": row.get("edge"),
                    "host_received_mono_ns": row.get("received_mono_ns"),
                    "kernel_mono_interval_ns": [row.get("timestamp_ns")] * 2,
                    "timestamp_meaning": "native sampling output edge; not contact",
                    "device_timestamp": None,
                    "decision": routed.get("status", "observed_only"),
                    "reason": routed.get("reason")
                    or ("not_armed_or_nonretention_edge" if not routed else None),
                    "notification_latency_ns": row["received_mono_ns"] - row["timestamp_ns"]
                    if isinstance(row.get("received_mono_ns"), int)
                    and isinstance(row.get("timestamp_ns"), int)
                    else None,
                }
            )
    for row in iwr:
        candidates.append(
            {
                **row,
                "decision": "status_observation",
                "reason": "polls may repeat the same detector latch",
            }
        )
    if held:
        candidates.append(
            {
                "detector": "iwr",
                "event_identity": held.get("record"),
                "host_received_mono_ns": result.get("iwr_result_received_mono_ns"),
                "device_timestamp": held.get("record"),
                "clock_mapping": held.get("mapping"),
                "timestamp_meaning": "held detector/frame counters; conditional mapping, not contact",
                "decision": "retained_independent_detection",
            }
        )
    clips = []
    camera_root = Path(result.get("camera_root", folder / "ops-camera"))
    if not camera_root.is_relative_to(run):
        parts = camera_root.parts
        matches = [index for index, part in enumerate(parts) if part == run.name]
        camera_root = run.joinpath(*parts[matches[-1] + 1 :]) if matches else run
    for clip in result.get("camera", {}).get("clips", []):
        path = camera_root / clip["metadata"]
        relative = _relative(path, root)
        if relative is None or not path.resolve().is_relative_to(run):
            continue
        clips.append(
            {
                "metadata": relative,
                "preview_frames": {
                    key: _relative(path.parent / f"{key}.pgm", root)
                    if (path.parent / f"{key}.pgm").is_file()
                    else None
                    for key in ("first", "trigger", "last")
                },
                "facts": _json(path, {}),
            }
        )
    armed = result.get("armed_mono_ns")
    finished = result.get("disarm_requested_mono_ns", result.get("finished_mono_ns"))
    ends = [
        c.get("host_received_mono_ns")
        for c in candidates
        if c.get("edge") == "rising" or c.get("state", {}).get("trigger", {}).get("latched") == "1"
    ]
    ends = [t for t in ends if type(t) is int and type(armed) is int and t >= armed]
    end = min(ends) if ends else finished
    exposure = end - armed if type(armed) is int and type(end) is int and end >= armed else None
    rearm_start = next(
        (r.get("mono_ns") for r in controls if r.get("type") == "arm_requested"), None
    )
    rearm_end = next((r.get("mono_ns") for r in controls if r.get("type") == "arm_completed"), None)
    processing_start = next(
        (
            row.get("host_mono_ns")
            for row in publication
            if row.get("stage") == "processing_started"
        ),
        None,
    )
    published = next(
        (row.get("host_mono_ns") for row in publication if row.get("stage") == "published"), None
    )
    return {
        "attempt": folder.relative_to(run).as_posix(),
        "settings": settings,
        "result": result,
        "association": association,
        "decision": _json(
            folder / "policy-decision.json",
            {"reason": result.get("error", "interrupted before policy decision")},
        ),
        "candidates": sorted(candidates, key=lambda c: c.get("host_received_mono_ns") or 2**63),
        "routing": routes,
        "controls": controls,
        "ops_sample_coverage": _rows(folder / "ops-camera/ops-summary.jsonl"),
        "iwr_clock": held.get("mapping"),
        "camera_clips": clips,
        "ops_retained": ops,
        "iwr_retained": iwr_retained,
        "full_sensor_association": association.get("status") == "experimental_software_candidate",
        "observed_armed_ns": exposure,
        "host_rearm_command_ns": rearm_end - rearm_start
        if type(rearm_start) is int and type(rearm_end) is int and rearm_end >= rearm_start
        else None,
        "publication": publication,
        "processing_to_publication_ns": published - processing_start
        if type(published) is int
        and type(processing_start) is int
        and published >= processing_start
        else None,
        "trace_attempt_id": result.get("trace_attempt_id"),
        "published": any(r.get("stage") == "published" for r in publication),
        "counterfactual_policy_decisions": {
            name: trigger_policy.decision(
                trigger_policy.snapshot(name), ops=ops, iwr=iwr_retained, association=association
            )
            for name in ("independent", "ops_led", "hybrid")
        },
        "evidence_directory": _relative(folder, root),
        "physical_timing_qualified": False,
    }


def build_comparison(run, root, events=()):
    """Include quiet, interrupted and unpublished attempts; unlabelled activity is unknown."""
    run, root = Path(run).resolve(), Path(root).resolve()
    attempts = [
        _attempt(folder, run, root)
        for folder in sorted(run.glob("native-sync/*/attempt-*"))
        if folder.is_dir() and not folder.is_symlink()
    ]
    if not attempts and not (run / "trigger-policy.json").exists():
        return None
    traces = []
    for path in sorted(run.glob("pi_trace_*.jsonl")):
        try:
            trace = load_trace(path)
            traces.append(
                {
                    "path": _relative(path, root),
                    "integrity": trace["integrity"],
                    "attempts": build_attempts(trace) if trace["integrity"]["valid"] else [],
                }
            )
        except (OSError, ValueError, KeyError, TypeError) as error:
            traces.append({"path": _relative(path, root), "error": str(error)})
    labels_path = run / "trigger-labels.json"
    labels = _json(labels_path, None) if labels_path.exists() else []
    errors = (
        []
        if isinstance(labels, list)
        else ["Label file must contain a list; excluded from grading"]
    )
    valid_labels, ids, assigned = [], set(), set()
    names = {a["attempt"] for a in attempts}
    for label in labels if isinstance(labels, list) else []:
        refs = label.get("attempts") if isinstance(label, dict) else None
        identity = label.get("id") if isinstance(label, dict) else None
        if (
            not isinstance(identity, str)
            or not identity
            or identity in ids
            or label.get("kind") not in {"real_shot", "dry_swing", "quiet", "unknown"}
            or not isinstance(refs, list)
            or any(not isinstance(r, str) or r not in names or r in assigned for r in refs)
            or len(set(refs)) != len(refs)
        ):
            errors.append("Invalid/duplicate label or attempt reference; excluded from grading")
            continue
        ids.add(identity)
        assigned.update(refs)
        valid_labels.append(label)
    by_name = {a["attempt"]: a for a in attempts}
    captured = {a["attempt"] for a in attempts if a["ops_retained"] or a["iwr_retained"]}
    false_captures = sum(
        len(set(label["attempts"]) & captured) for label in valid_labels if label["kind"] == "quiet"
    )
    observed = [a["observed_armed_ns"] for a in attempts if a["observed_armed_ns"] is not None]
    armed_minutes = sum(observed) / 60e9 if observed else None
    return {
        "schema": "openflight.trigger-comparison.v1",
        "settings": _json(run / "trigger-policy.json", {}),
        "attempts": attempts,
        "screen_traces": traces,
        "baseline_trigger_events": [
            event for event in events if event.get("type") == "trigger_event"
        ],
        "labels": valid_labels,
        "label_errors": errors,
        "counts": {
            "attempts_including_no_shot": len(attempts),
            "captured_attempts": len(captured),
            "unlabelled_attempts": len(names - assigned),
            "labelled_swings": sum(
                label["kind"] in {"real_shot", "dry_swing"} for label in valid_labels
            ),
            "detected_labelled_swings": sum(
                bool(set(label["attempts"]) & captured)
                for label in valid_labels
                if label["kind"] in {"real_shot", "dry_swing"}
            ),
            "labelled_real_shots": sum(label["kind"] == "real_shot" for label in valid_labels),
            "missed_labelled_real_shots": sum(
                not (set(label["attempts"]) & captured)
                for label in valid_labels
                if label["kind"] == "real_shot"
            ),
            "unpublished_labelled_real_shots": sum(
                not any(by_name[a]["published"] for a in label["attempts"])
                for label in valid_labels
                if label["kind"] == "real_shot"
            ),
            "valid_dry_swings_retained": sum(
                bool(set(label["attempts"]) & captured)
                for label in valid_labels
                if label["kind"] == "dry_swing"
            ),
            "duplicate_labelled_attempts": sum(
                max(0, len(set(label["attempts"]) & captured) - 1)
                for label in valid_labels
                if label["kind"] in {"real_shot", "dry_swing"}
            ),
            "labelled_false_captures": false_captures,
            "full_sensor_associations": sum(a["full_sensor_association"] for a in attempts),
            "observed_armed_minutes": armed_minutes,
            "attempts_missing_exposure": len(attempts) - len(observed),
            "labelled_false_captures_per_observed_armed_minute": false_captures / armed_minutes
            if armed_minutes and valid_labels and not errors and len(observed) == len(attempts)
            else None,
        },
        "limitations": [
            "Labels grade only. Unlabelled and quiet attempts are not confirmed negatives.",
            "Observed armed exposure ends at first host detector observation; "
            "IWR polling can overestimate it. False capture rates use labelled evidence only.",
            "Counterfactual decisions reuse retained evidence; "
            "they cannot recover buffers lost under another live policy.",
            "Trigger latency means GPIO-to-host receipt, never physical impact latency. "
            "Screen traces use the existing post-render proxy and clock mapping; "
            "absent or invalid traces cannot establish screen latency or physical presentation.",
            "Sound baseline uses existing trigger records and operator tally; "
            "native exposure is unmeasured for sound runs.",
        ],
        "physical_timing_qualified": False,
    }

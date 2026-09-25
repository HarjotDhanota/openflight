"""One review record per attempted shot, from the evidence on disk and its replay reports.

Every path in the review is relative to the folder that holds the tester
directory, which is also the root of a session bundle, so the same review reads
on the Pi and from an extracted or imported bundle.
"""

from __future__ import annotations

import csv
import io
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from openflight.camera import attempt_ledger
from openflight.raw_radar_replay import load_session_events
from openflight.review_metrics import STATUSES, finite, mapping, overlay, review_replay

SCHEMA = "openflight.session_review.v1"
ANALYSIS_DIR = "analysis"
REPLAY_DIR = "replay"
MAX_REJECTED_TRIGGERS_PER_RUN = 200
PREVIEW_LABELS = ("first", "trigger", "last")

_REVIEW_COLUMNS = (
    "attempt_id",
    "arm_id",
    "run",
    "shot_number",
    "kind",
    "metric",
    "status",
    "value",
    "unit",
    "source",
    "confidence",
    "reason",
)


def _relative(path: Path, root: Path) -> str | None:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        return None


def replay_report_path(analysis_dir: Path, arm_id: str, run: str, shot_number: int) -> Path:
    """Where the analysis keeps one shot's replay report."""
    return analysis_dir / REPLAY_DIR / arm_id / run / f"shot-{shot_number:03d}.json"


def _previews(capture_dir: Path | None, root: Path) -> dict[str, str | None]:
    if capture_dir is None:
        return {label: None for label in PREVIEW_LABELS}
    return {
        label: _relative(capture_dir / f"{label}.pgm", root)
        if (capture_dir / f"{label}.pgm").is_file()
        else None
        for label in PREVIEW_LABELS
    }


def _ladder_index(tester_dir: Path) -> tuple[dict[str, dict], dict[str, str], dict[str, dict]]:
    """Picture verdicts, impact photos and ineligible captures keyed by capture name."""
    try:
        ladder = json.loads((tester_dir / "ladder.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        ladder = {}
    ladder = mapping(ladder)
    verdicts = {}
    for rung_id, rung in mapping(ladder.get("rungs")).items():
        for swing in mapping(rung).get("swings") or []:
            if isinstance(swing, Mapping) and isinstance(swing.get("capture"), str):
                verdicts[swing["capture"]] = {
                    "rung_id": rung_id,
                    "color": swing.get("color"),
                    "reasons": list(swing.get("reasons") or []),
                    "meaning": "picture quality only; not a fused-metric verdict",
                }
    photos: dict[str, str] = {}
    for capture, recorded in mapping(ladder.get("photos")).items():
        if not isinstance(recorded, str):
            continue
        candidate = Path(recorded)
        if not candidate.is_absolute():
            candidate = tester_dir / candidate
        if not candidate.is_file():
            candidate = tester_dir / "impact" / Path(recorded.replace("\\", "/")).name
        if candidate.is_file():
            photos[capture] = str(candidate)
    impact = tester_dir / "impact"
    if impact.is_dir():
        for photo in sorted(impact.glob("*.pgm")):
            photos.setdefault(photo.stem, str(photo))
    ineligible = {
        item["capture"]: item
        for item in ladder.get("ineligible_captures") or []
        if isinstance(item, Mapping) and isinstance(item.get("capture"), str)
    }
    return verdicts, photos, ineligible


def _live_values(shot: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "ball_speed_mph",
        "club_speed_mph",
        "spin_rpm",
        "spin_confidence",
        "launch_angle_vertical",
        "launch_angle_horizontal",
        "club_path_deg",
        "club_angle_deg",
        "experimental_fused_status",
    )
    return {key: shot.get(key) for key in keys if key in shot}


def _diagnostic_outcomes(events: Iterable[Mapping[str, Any]]) -> dict[int, dict[str, Any]]:
    outcomes: dict[int, dict[str, Any]] = {}
    for event in events:
        if event.get("type") != "fusion_diagnostic" or not isinstance(
            event.get("shot_number"), int
        ):
            continue
        current = outcomes.get(event["shot_number"])
        if current is None or (event.get("revision") or 0) >= current["revision"]:
            outcome = event.get("outcome")
            outcomes[event["shot_number"]] = {
                "revision": event.get("revision") or 0,
                "outcome": outcome,
                "label": "processing finished" if outcome == "complete" else outcome,
                "reason": event.get("reason"),
            }
    return outcomes


def _capture_dirs(run: Path, arm_id: str) -> dict[str, Path]:
    camera = run / arm_id / "camera"
    if not camera.is_dir():
        return {}
    return {
        folder.name: folder
        for folder in sorted(camera.glob("camera_*"))
        if folder.is_dir() and not folder.is_symlink()
    }


def _recorded_name(recorded: Any) -> str | None:
    if not isinstance(recorded, str) or not recorded:
        return None
    return recorded.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


@dataclass
class _Run:
    """One capture run and the tester-wide evidence it is reviewed against."""

    tester_dir: Path
    root: Path
    arm_id: str
    path: Path
    verdicts: dict[str, dict]
    photos: dict[str, str]
    ineligible: dict[str, dict]
    summary: dict[str, Any] = field(default_factory=dict)

    def relative(self, path: Path | None) -> str | None:
        """A bundle-relative path, or None."""
        return _relative(path, self.root) if path is not None else None

    def photo(self, capture: str | None) -> str | None:
        """The impact photo recorded against a capture, if any."""
        return self.relative(Path(self.photos[capture])) if capture in self.photos else None


def _read_session(run: _Run) -> list[dict[str, Any]]:
    """The run's session events, recording why they are missing when they are."""
    sessions = sorted(run.path.glob("session_*.jsonl"))
    if len(sessions) != 1:
        run.summary["errors"].append(f"expected one session log in the run, found {len(sessions)}")
        return []
    run.summary["session_file"] = run.relative(sessions[0])
    try:
        session_hash, start, events = load_session_events(sessions[0])
    except (OSError, ValueError) as error:
        run.summary["errors"].append(f"session log unreadable: {error}")
        return []
    run.summary["session_sha256"] = session_hash
    run.summary["session_uuid"] = start.get("session_uuid")
    return events


def _summarize_triggers_and_ledger(run: _Run, events: list[dict[str, Any]], shots: int) -> None:
    for event in events:
        if event.get("type") == "trigger_event" and event.get("accepted") is False:
            run.summary["rejected_trigger_count"] += 1
            if len(run.summary["rejected_triggers"]) < MAX_REJECTED_TRIGGERS_PER_RUN:
                run.summary["rejected_triggers"].append(
                    {
                        key: event.get(key)
                        for key in ("ts", "reason", "peak_outbound_mph", "peak_inbound_mph")
                    }
                )
    ledger = run.path / "attempt_ledger.jsonl"
    if ledger.is_file():
        scope = {"tester_id": run.tester_dir.name, "arm_id": run.arm_id, "run": run.path.name}
        try:
            run.summary["attempt_ledger"] = attempt_ledger.summarize(ledger, scope, shots)["counts"]
        except (attempt_ledger.LedgerError, OSError, ValueError) as error:
            run.summary["attempt_ledger"] = {"error": str(error)}


def _load_report(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.is_file():
        return None, None
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, ValueError) as error:
        return None, f"replay report unreadable: {error}"


def _shot_attempt(
    run: _Run, number: int, shot_events: list[dict[str, Any]], shot: Mapping[str, Any] | None
) -> dict[str, Any]:
    captures = [e for e in shot_events if e.get("type") == "camera_capture"]
    camera_event = next((e for e in captures if not e.get("capture_error")), {})
    camera_error = next((e["capture_error"] for e in captures if e.get("capture_error")), None)
    iwr_event = next((e for e in shot_events if e.get("type") == "iwr6843_capture"), {})
    capture_name = _recorded_name(camera_event.get("capture_path"))
    capture_dir = run.path / run.arm_id / "camera" / capture_name if capture_name else None
    if capture_dir is not None and not capture_dir.is_dir():
        capture_dir = None
    iwr_name = _recorded_name(iwr_event.get("capture_path"))
    iwr_file = run.path / "iwr6843" / iwr_name if iwr_name else None
    report_path = replay_report_path(
        run.tester_dir / ANALYSIS_DIR, run.arm_id, run.path.name, number
    )
    report, report_error = _load_report(report_path)
    live = _live_values(shot or {})
    reviewed = review_replay(report, live)
    if report_error:
        for metric in reviewed["metrics"]:
            metric.update(status="processing_failed", value=None, reason=report_error)
    return {
        "attempt_id": f"{run.summary['session_uuid']}:{number}",
        "kind": "shot" if shot is not None else "sensor_event_without_shot",
        "arm_id": run.arm_id,
        "run": run.path.name,
        "session_uuid": run.summary["session_uuid"],
        "shot_number": number,
        "recorded_at": (shot or {}).get("ts"),
        "capture_id": capture_name,
        "evidence": {
            "session_file": run.summary["session_file"],
            "camera_capture": run.relative(capture_dir),
            "camera_capture_error": camera_error,
            "preview_frames": _previews(capture_dir, run.root),
            "iwr_dump": run.relative(iwr_file) if iwr_file and iwr_file.is_file() else None,
            "impact_photo": run.photo(capture_name),
            "replay_report": run.relative(report_path) if report is not None else None,
        },
        "picture_verdict": run.verdicts.get(capture_name),
        "live": live,
        "identity": mapping((report or {}).get("source_identity")),
        "identity_evidence": mapping((report or {}).get("source_identity_evidence")),
        **reviewed,
    }


def _stray_capture_attempt(run: _Run, name: str, folder: Path) -> dict[str, Any]:
    ineligible = run.ineligible.get(name, {})
    return {
        "attempt_id": f"{run.path.name}:{name}",
        "kind": "camera_trigger_without_shot",
        "arm_id": run.arm_id,
        "run": run.path.name,
        "session_uuid": run.summary["session_uuid"],
        "shot_number": None,
        "recorded_at": None,
        "capture_id": name,
        "evidence": {
            "session_file": run.summary["session_file"],
            "camera_capture": run.relative(folder),
            "camera_capture_error": None,
            "preview_frames": _previews(folder, run.root),
            "iwr_dump": None,
            "impact_photo": run.photo(name),
            "replay_report": None,
        },
        "picture_verdict": run.verdicts.get(name),
        "live_processing": None,
        "live": {},
        "identity": {},
        "identity_evidence": {},
        "metrics": [],
        "stages": {},
        "overlay": overlay({}),
        "agreements": [],
        "rejection": {
            "reason": ineligible.get("reason")
            or "no sensor shot was logged for this camera trigger",
            "readiness": ineligible.get("readiness"),
        },
    }


def _review_run(run: _Run) -> list[dict[str, Any]]:
    run.summary.update(
        arm_id=run.arm_id,
        run=run.path.name,
        path=run.relative(run.path),
        session_file=None,
        session_sha256=None,
        session_uuid=None,
        errors=[],
        rejected_triggers=[],
        rejected_trigger_count=0,
        attempt_ledger=None,
    )
    events = _read_session(run)
    shots = {
        e["shot_number"]: e
        for e in events
        if e.get("type") == "shot_detected" and isinstance(e.get("shot_number"), int)
    }
    _summarize_triggers_and_ledger(run, events, len(shots))
    outcomes = _diagnostic_outcomes(events)
    by_number: dict[int, list[dict[str, Any]]] = {}
    for event in events:
        if isinstance(event.get("shot_number"), int):
            by_number.setdefault(event["shot_number"], []).append(event)
    attempts = []
    for number in sorted(by_number):
        attempt = _shot_attempt(run, number, by_number[number], shots.get(number))
        attempt["live_processing"] = outcomes.get(number)
        attempts.append(attempt)
    matched = {attempt["capture_id"] for attempt in attempts}
    attempts.extend(
        _stray_capture_attempt(run, name, folder)
        for name, folder in _capture_dirs(run.path, run.arm_id).items()
        if name not in matched
    )
    return attempts


def build_session_review(
    sessions_root: Path, tester_id: str, *, analysis: Mapping[str, Any]
) -> dict[str, Any]:
    """Review every run of one tester from the evidence on disk and its replay reports."""
    root = Path(sessions_root).resolve()
    tester_dir = root / tester_id
    ladder = _ladder_index(tester_dir)
    runs, attempts = [], []
    for arm_dir in sorted(
        p for p in tester_dir.iterdir() if p.is_dir() and (p / "paired").is_dir()
    ):
        for run in sorted(
            p for p in (arm_dir / "paired").glob("run-*") if p.is_dir() and not p.is_symlink()
        ):
            reviewed = _Run(tester_dir, root, arm_dir.name, run, *ladder)
            attempts.extend(_review_run(reviewed))
            runs.append(reviewed.summary)
    _verdicts, photos, _ineligible = ladder
    attached = {
        a["evidence"]["impact_photo"] for a in attempts if a["evidence"].get("impact_photo")
    }
    unattached = sorted(
        path
        for path in (_relative(Path(p), root) for p in photos.values())
        if path and path not in attached
    )
    counts: dict[str, Counter] = {}
    for attempt in attempts:
        for metric in attempt["metrics"]:
            counts.setdefault(metric["key"], Counter())[metric["status"]] += 1
    return {
        "schema": SCHEMA,
        "tester_id": tester_id,
        "analysis": dict(analysis),
        "status_vocabulary": list(STATUSES),
        "runs": runs,
        "attempts": attempts,
        "unattached_impact_photos": unattached,
        "metric_status_counts": {key: dict(counter) for key, counter in sorted(counts.items())},
    }


def attempts_csv(review: Mapping[str, Any]) -> str:
    """One row per attempt and metric; attempts without metrics keep one row."""
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=_REVIEW_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for attempt in review.get("attempts") or []:
        base = {
            key: attempt.get(key) for key in ("attempt_id", "arm_id", "run", "shot_number", "kind")
        }
        metrics = attempt.get("metrics") or []
        if not metrics:
            writer.writerow({**base, "reason": (attempt.get("rejection") or {}).get("reason")})
        for metric in metrics:
            writer.writerow(
                {
                    **base,
                    "metric": metric["key"],
                    "status": metric["status"],
                    "value": metric["value"],
                    "unit": metric["unit"],
                    "source": metric["source"],
                    "confidence": metric["confidence"],
                    "reason": metric["reason"],
                }
            )
    return output.getvalue()


def _fmt(value: Any) -> str:
    number = finite(value)
    if number is None:
        return "—"
    return f"{number:.1f}" if abs(number) >= 10 else f"{number:.2f}"


def report_markdown(review: Mapping[str, Any]) -> str:
    """A plain summary that reads without any viewer."""
    lines = [
        f"# OpenFlight session review: {review.get('tester_id')}",
        "",
        "No value here is validated against a reference. Status vocabulary: "
        + ", ".join(review.get("status_vocabulary") or STATUSES)
        + ".",
        "",
    ]
    analysis = mapping(review.get("analysis"))
    if analysis:
        lines += [
            f"Analysed {analysis.get('finished_at') or analysis.get('started_at') or ''} "
            f"with software {analysis.get('software_content_sha256') or 'unrecorded'}.",
            "",
        ]
    for run in review.get("runs") or []:
        lines.append(
            f"## {run['arm_id']} / {run['run']} — session {run.get('session_uuid') or 'unreadable'}"
        )
        for error in run.get("errors") or []:
            lines.append(f"- run problem: {error}")
        if run.get("rejected_trigger_count"):
            lines.append(
                f"- {run['rejected_trigger_count']} triggers rejected before a shot was logged"
            )
        lines.append("")
        for attempt in review.get("attempts") or []:
            if attempt["arm_id"] != run["arm_id"] or attempt["run"] != run["run"]:
                continue
            title = (
                f"Shot {attempt['shot_number']}"
                if attempt.get("shot_number") is not None
                else f"Camera trigger {attempt.get('capture_id')}"
            )
            lines.append(f"### {title}")
            verdict = attempt.get("picture_verdict")
            if verdict:
                reasons = "; ".join(verdict["reasons"])
                lines.append(
                    f"Picture: {verdict['color']} ({verdict['rung_id']}) {reasons}".rstrip()
                )
            if attempt.get("rejection"):
                lines.append(f"Not a shot: {attempt['rejection']['reason']}")
            if attempt["evidence"].get("impact_photo"):
                lines.append(f"Impact photo: {attempt['evidence']['impact_photo']}")
            if attempt.get("metrics"):
                lines += [
                    "",
                    "| Metric | Status | Value | Confidence | Reason |",
                    "|---|---|---|---|---|",
                ]
                for metric in attempt["metrics"]:
                    reason = (metric.get("reason") or "").replace("|", "/")
                    value = f"{_fmt(metric['value'])} {metric['unit']}"
                    lines.append(
                        f"| {metric['label']} | {metric['status']} | {value} "
                        f"| {_fmt(metric['confidence'])} | {reason} |"
                    )
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"

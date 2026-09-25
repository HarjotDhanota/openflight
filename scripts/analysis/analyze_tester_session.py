#!/usr/bin/env python3
"""Analyse, review and package one tester's sessions; or verify a bundle reproduces.

    uv run --extra camera python scripts/analysis/analyze_tester_session.py \\
        --sessions-root ~/openflight_sessions/tester_pilot --tester-id <id> --package
    uv run --extra camera python scripts/analysis/analyze_tester_session.py --verify <bundle.zip>

Analysis replays every logged shot of every run (OPS, IWR and camera stages from
the recorded inputs), writes one report per shot under ``<tester>/analysis/replay``,
builds the session review, and optionally writes an immutable session bundle.
Progress is written atomically to ``<tester>/analysis/job.json`` with a heartbeat,
so the tester page survives a refresh or a server restart. A rerun skips shots
whose session bytes and replay software are unchanged.
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import sys
import tempfile
import threading
import traceback
from argparse import Namespace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openflight import session_bundle
from openflight.raw_radar_replay import load_session_events
from openflight.session_review import (
    ANALYSIS_DIR,
    attempts_csv,
    build_session_review,
    replay_report_path,
    report_markdown,
)

try:
    from scripts.analysis.replay_camera_fusion import _comparison_mismatches
    from scripts.analysis.replay_raw_fusion import _replay_software_content_sha256, replay
except ModuleNotFoundError:  # Direct execution places scripts/analysis first on sys.path.
    from replay_camera_fusion import _comparison_mismatches
    from replay_raw_fusion import _replay_software_content_sha256, replay

REPO_ROOT = Path(__file__).resolve().parents[2]
VIEWER = REPO_ROOT / "ui" / "public" / "session-review.html"
JOB_SCHEMA = "openflight.analysis_job.v1"
HEARTBEAT_S = 5.0
ANALYSER = "analyze_tester_session.v1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json_atomic(path: Path, value: Any) -> None:
    """Replace ``path`` with ``value`` so readers never see a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=".", suffix=".tmp", delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(json.dumps(value, indent=2, allow_nan=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_text_atomic(path: Path, text: str) -> None:
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="", dir=path.parent, prefix=".", suffix=".tmp", delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(text)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class Job:
    """The durable progress record the tester page reads."""

    def __init__(self, path: Path, tester_id: str, software: str | None):
        self.path = path
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.state: dict[str, Any] = {
            "schema": JOB_SCHEMA,
            "tester_id": tester_id,
            "state": "running",
            "phase": "starting",
            "pid": os.getpid(),
            "started_at": _now(),
            "updated_at": _now(),
            "finished_at": None,
            "progress": {"done": 0, "total": 0, "current": None},
            "replayed": 0,
            "reused": 0,
            "errors": [],
            "software_content_sha256": software,
            "bundle": None,
        }
        self._write()
        self._beat = threading.Thread(
            target=self._heartbeat, daemon=True, name="analysis-heartbeat"
        )
        self._beat.start()

    def _write(self) -> None:
        self.state["updated_at"] = _now()
        write_json_atomic(self.path, self.state)

    def _heartbeat(self) -> None:
        while not self._stop.wait(HEARTBEAT_S):
            with self._lock:
                self._write()

    def update(self, **fields: Any) -> None:
        with self._lock:
            self.state.update(fields)
            self._write()

    def error(self, message: str) -> None:
        with self._lock:
            self.state["errors"].append(message)
            self._write()

    def finish(self, state: str) -> None:
        self._stop.set()
        self._beat.join(timeout=HEARTBEAT_S)
        with self._lock:
            self.state.update(state=state, phase="finished", finished_at=_now())
            self._write()


def _runs(tester_dir: Path) -> list[tuple[str, Path]]:
    return [
        (arm.name, run)
        for arm in sorted(p for p in tester_dir.iterdir() if p.is_dir() and (p / "paired").is_dir())
        for run in sorted(
            p for p in (arm / "paired").glob("run-*") if p.is_dir() and not p.is_symlink()
        )
    ]


def _replay_args(session: Path, shot: int, path_root: Path) -> Namespace:
    return Namespace(
        session=session,
        shot=shot,
        ops_sample_rate_hz=None,
        club=None,
        projection_manifest=None,
        iwr=True,
        iwr_calibration=None,
        iwr_config=None,
        iwr_runtime_config=None,
        iwr_tee_m=None,
        iwr_tilt_deg=None,
        iwr_radar_height_m=None,
        ball_height_m=0.040,
        camera=True,
        camera_capture=None,
        output=None,
        path_root=path_root,
    )


def _failed_report(session_uuid: Any, shot: int, message: str) -> dict[str, Any]:
    stage = {"status": "error", "error": message}
    return {
        "schema_version": 1,
        "session_uuid": session_uuid,
        "shot_number": shot,
        "stages": {name: dict(stage) for name in ("ops", "iwr6843", "camera")},
    }


def _replay_shot(
    session: Path, frozen: tuple, shot: int, sessions_root: Path, key: str, target: Path, job: Job
) -> None:
    current = job.state["progress"]["current"]
    try:
        report = replay(_replay_args(session, shot, sessions_root), frozen_session=frozen)
    except Exception as error:  # every shot gets a report, failures included
        message = f"{type(error).__name__}: {error}"
        job.error(f"{current}: replay failed: {message}")
        report = _failed_report(frozen[1].get("session_uuid"), shot, message)
        traceback.print_exc()
    report["analysis"] = {"key": key, "analyser": ANALYSER}
    write_json_atomic(target, report)
    job.update(replayed=job.state["replayed"] + 1)


def _already_replayed(target: Path, key: str) -> bool:
    try:
        existing = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return (existing.get("analysis") or {}).get("key") == key


def replay_all(sessions_root: Path, tester_id: str, job: Job, software: str | None) -> None:
    """Replay every logged shot once per session content and software.

    One run's session is held in memory at a time; its events are re-read when
    the run is replayed rather than kept from the counting pass.
    """
    tester_dir = sessions_root / tester_id
    analysis_dir = tester_dir / ANALYSIS_DIR
    plan = []
    for arm_id, run in _runs(tester_dir):
        sessions = sorted(run.glob("session_*.jsonl"))
        if len(sessions) != 1:
            job.error(f"{arm_id}/{run.name}: expected one session log, found {len(sessions)}")
            continue
        try:
            _hash, _start, events = load_session_events(sessions[0])
        except (OSError, ValueError) as error:
            job.error(f"{arm_id}/{run.name}: session log unreadable: {error}")
            continue
        shots = sorted({e["shot_number"] for e in events if type(e.get("shot_number")) is int})
        plan.append((arm_id, run, sessions[0], shots))
        del events
    total = sum(len(shots) for *_rest, shots in plan)
    done = 0
    job.update(
        phase="replay", shots_total=total, progress={"done": 0, "total": total, "current": None}
    )
    for arm_id, run, session, shots in plan:
        try:
            frozen = load_session_events(session)
        except (OSError, ValueError) as error:
            job.error(f"{arm_id}/{run.name}: session log changed or unreadable: {error}")
            done += len(shots)
            continue
        for shot in shots:
            target = replay_report_path(analysis_dir, arm_id, run.name, shot)
            key = f"{frozen[0]}:{software}:{shot}"
            job.update(
                progress={
                    "done": done,
                    "total": total,
                    "current": f"{arm_id}/{run.name} shot {shot}",
                }
            )
            if software is not None and _already_replayed(target, key):
                job.update(reused=job.state["reused"] + 1)
            else:
                _replay_shot(session, frozen, shot, sessions_root, key, target, job)
                gc.collect()
            done += 1
        del frozen
    job.update(progress={"done": total, "total": total, "current": None})


def write_review(sessions_root: Path, tester_id: str, analysis: dict[str, Any]) -> dict[str, Any]:
    """Build and store the review, its CSV and its plain report."""
    review = build_session_review(sessions_root, tester_id, analysis=analysis)
    analysis_dir = sessions_root / tester_id / ANALYSIS_DIR
    write_json_atomic(analysis_dir / "session_review.json", review)
    _write_text_atomic(analysis_dir / "attempts.csv", attempts_csv(review))
    _write_text_atomic(analysis_dir / "report.md", report_markdown(review))
    return review


def analyze(sessions_root: Path, tester_id: str, *, package: bool, viewer: Path | None) -> int:
    sessions_root = sessions_root.expanduser().resolve()
    tester_dir = sessions_root / tester_id
    if not tester_dir.is_dir():
        print(f"no saved data for tester {tester_id}", file=sys.stderr)
        return 2
    software, limitation = _replay_software_content_sha256()
    job = Job(tester_dir / ANALYSIS_DIR / "job.json", tester_id, software)
    try:
        replay_all(sessions_root, tester_id, job, software)
        job.update(phase="review")
        analysis = {
            "analyser": ANALYSER,
            "software_content_sha256": software,
            "software_identity_limitation": limitation,
            "started_at": job.state["started_at"],
            "replayed": job.state["replayed"],
            "reused": job.state["reused"],
            "errors": list(job.state["errors"]),
        }
        review = write_review(sessions_root, tester_id, analysis)
        if package:
            job.update(phase="package")
            job.update(
                bundle=session_bundle.build_bundle(
                    sessions_root,
                    tester_id,
                    viewer=viewer if viewer is not None and viewer.is_file() else None,
                    provenance={
                        **analysis,
                        "attempts": len(review["attempts"]),
                        "review": f"{tester_id}/{ANALYSIS_DIR}/session_review.json",
                    },
                    progress=lambda done, total, name: job.update(
                        progress={"done": done, "total": total, "current": name}
                    ),
                )
            )
    except Exception as error:  # the page must learn why, not just that it stopped
        job.error(f"{type(error).__name__}: {error}")
        job.finish("failed")
        traceback.print_exc()
        return 1
    job.finish("complete")
    return 0


def _without_software_identity(document: dict[str, Any], name: str) -> dict[str, Any]:
    """Drop the fields that name the software, which is compared separately."""
    document = dict(document)
    if name == "session_review.json":
        document.pop("analysis", None)
        document["attempts"] = [
            {**attempt, "identity": _software_free(attempt.get("identity"))}
            for attempt in document.get("attempts") or []
        ]
        return document
    document.pop("analysis", None)
    document["source_identity"] = _software_free(document.get("source_identity"))
    return document


def _software_free(identity: Any) -> Any:
    if not isinstance(identity, dict):
        return identity
    return {key: value for key, value in identity.items() if not key.startswith("replay_software")}


def verify(bundle: Path, work_dir: Path | None) -> dict[str, Any]:
    """Re-derive every analysis output from the bundle's own raw files and compare."""
    manifest = session_bundle.validate_bundle(bundle)
    tester_id = manifest["tester_id"]
    with tempfile.TemporaryDirectory(prefix="openflight-verify-", dir=work_dir) as temporary:
        root = session_bundle.extract_bundle(bundle, Path(temporary) / "bundle")
        recorded_dir = root / tester_id / ANALYSIS_DIR
        recorded = {
            path.relative_to(recorded_dir).as_posix(): json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(recorded_dir.rglob("*.json"))
        }
        fresh = Path(temporary) / "fresh"
        fresh_root = fresh / "sessions"
        (fresh_root).mkdir(parents=True)
        os.replace(root / tester_id, fresh_root / tester_id)
        for path in sorted((fresh_root / tester_id / ANALYSIS_DIR).rglob("*.json")):
            path.unlink()
        software, _limitation = _replay_software_content_sha256()
        job = Job(fresh / "job.json", tester_id, software)
        replay_all(fresh_root, tester_id, job, software)
        job.finish("complete")
        write_review(fresh_root, tester_id, {"analyser": ANALYSER})
        produced_dir = fresh_root / tester_id / ANALYSIS_DIR
        mismatches: list[str] = []
        compared = 0
        for name, before in recorded.items():
            after_path = produced_dir / name
            if not after_path.is_file():
                mismatches.append(f"{name}: not reproduced")
                continue
            after = json.loads(after_path.read_text(encoding="utf-8"))
            before = _without_software_identity(before, name)
            after = _without_software_identity(after, name)
            compared += 1
            mismatches.extend(f"{name}: {path}" for path in _comparison_mismatches(before, after))
    recorded_software = (manifest.get("provenance") or {}).get("software_content_sha256")
    return {
        "bundle": bundle.name,
        "bundle_hashes": "verified",
        "tester_id": tester_id,
        "recorded_software_content_sha256": recorded_software,
        "verifying_software_content_sha256": software,
        "same_software": recorded_software is not None and recorded_software == software,
        "compared_files": compared,
        "float_absolute_tolerance": 1e-9,
        "mismatches": mismatches,
        "status": "reproduced" if not mismatches and compared else "differs",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--sessions-root", type=Path)
    parser.add_argument("--tester-id")
    parser.add_argument("--package", action="store_true", help="Write a session bundle when done")
    parser.add_argument("--viewer", type=Path, default=VIEWER)
    parser.add_argument("--verify", type=Path, help="Verify a bundle reproduces from its raw files")
    parser.add_argument("--work-dir", type=Path)
    args = parser.parse_args(argv)
    if args.verify:
        try:
            result = verify(args.verify, args.work_dir)
        except (OSError, ValueError) as error:
            print(f"bundle verification failed: {error}", file=sys.stderr)
            return 2
        print(json.dumps(result, indent=2))
        return 0 if result["status"] == "reproduced" else 1
    if not args.sessions_root or not args.tester_id:
        parser.error("--sessions-root and --tester-id are required unless --verify is given")
    return analyze(args.sessions_root, args.tester_id, package=args.package, viewer=args.viewer)


if __name__ == "__main__":
    raise SystemExit(main())

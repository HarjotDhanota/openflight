"""Tester routes for the analysis job, the session review page and bundle downloads."""

from __future__ import annotations

import json
import os
import shutil
import signal
import sys
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from flask import Flask, Response, request, send_file

from openflight import session_bundle
from openflight.session_review import ANALYSIS_DIR

REPO_ROOT = Path(__file__).resolve().parents[3]
ANALYSIS_SCRIPT = REPO_ROOT / "scripts" / "analysis" / "analyze_tester_session.py"
# The runner writes a heartbeat every 5 s; a record this old has no live writer.
STALE_AFTER_S = 30.0
_SERVED_TYPES = {
    ".pgm": "image/x-portable-graymap",
    ".json": "application/json",
    ".csv": "text/csv",
    ".md": "text/markdown",
}


def job_path(sessions_root: Path, tester_id: str) -> Path:
    """The analysis runner's durable progress record for one tester."""
    return Path(sessions_root) / tester_id / ANALYSIS_DIR / "job.json"


def analysis_log(sessions_root: Path, tester_id: str) -> Path:
    """The runner's console log, kept beside the bundles rather than in the evidence."""
    return session_bundle.bundle_directory(sessions_root) / f"{tester_id}-analysis.log"


def analysis_command(sessions_root: Path, tester_id: str) -> list[str]:
    """The allowlisted analysis command, at low CPU priority where the OS offers it."""
    command = [
        sys.executable,
        str(ANALYSIS_SCRIPT),
        "--sessions-root",
        str(Path(sessions_root).resolve()),
        "--tester-id",
        tester_id,
        "--package",
    ]
    nice = shutil.which("nice") if os.name == "posix" else None
    return [nice, "-n", "10", *command] if nice else command


def _age_s(stamp: Any, now: datetime) -> float | None:
    try:
        return (now - datetime.fromisoformat(str(stamp))).total_seconds()
    except (TypeError, ValueError):
        return None


def read_analysis(
    sessions_root: Path, tester_id: str, *, now: datetime | None = None
) -> dict[str, Any]:
    """The analysis state a page shows, including a job whose writer disappeared."""
    now = now or datetime.now(timezone.utc)
    try:
        job = json.loads(job_path(sessions_root, tester_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        job = None
    state = "never_run" if job is None else str(job.get("state"))
    reason = None
    if state == "running":
        age = _age_s(job.get("updated_at"), now)
        if age is None or age > STALE_AFTER_S:
            state = "interrupted"
            reason = (
                "the analysis stopped without finishing (the tester restarted or the process was "
                "stopped); analyse again to resume, finished shots are reused"
            )
    review = Path(sessions_root) / tester_id / ANALYSIS_DIR / "session_review.json"
    bundles = session_bundle.list_bundles(sessions_root, tester_id)
    return {
        "state": state,
        "reason": reason,
        "job": job,
        "review_ready": review.is_file(),
        "bundles": bundles,
        "latest_bundle": bundles[0] if bundles else None,
    }


def analysis_running(sessions_root: Path) -> str | None:
    """The tester whose analysis still has a live writer, from any tester process."""
    root = Path(sessions_root)
    if not root.is_dir():
        return None
    for tester in sorted(path for path in root.iterdir() if path.is_dir()):
        if tester.name.startswith("."):
            continue
        if job_path(root, tester.name).is_file():
            if read_analysis(root, tester.name)["state"] == "running":
                return tester.name
    return None


def stop_detached_analysis(sessions_root: Path) -> bool:
    """Stop a live analysis started by an earlier tester process (Linux only)."""
    tester = analysis_running(sessions_root)
    if tester is None or not hasattr(os, "killpg"):
        return False
    pid = (read_analysis(sessions_root, tester)["job"] or {}).get("pid")
    try:
        command = Path(f"/proc/{int(pid)}/cmdline").read_bytes()
    except (OSError, TypeError, ValueError):
        return False
    if ANALYSIS_SCRIPT.name.encode() not in command:
        return False
    try:
        os.kill(int(pid), signal.SIGTERM)
    except OSError:
        return False
    return True


def _json(value: Mapping[str, Any], status: int = 200) -> Response:
    return Response(
        json.dumps(value, allow_nan=False, separators=(",", ":")),
        status=status,
        mimetype="application/json",
        headers={"Cache-Control": "no-store"},
    )


def _evidence_file(sessions_root: Path, tester_id: str, relative: str) -> Path:
    """A reviewable file named by its bundle-relative path, confined to the tester."""
    path = PurePosixPath(relative)
    canonical = (
        bool(relative)
        and "\\" not in relative
        and not path.is_absolute()
        and path.as_posix() == relative
        and not any(part in {"", ".", ".."} for part in path.parts)
    )
    if not canonical or path.parts[0] != tester_id or path.suffix.lower() not in _SERVED_TYPES:
        raise ValueError("path must name a review file inside this tester's folder")
    current = Path(sessions_root)
    for part in path.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("review files may not traverse a symlink")
    if not current.is_file():
        raise FileNotFoundError("that file is not in this tester's evidence")
    return current


def register_session_review(
    app: Flask,
    *,
    sessions_root: Path,
    valid_tester: Callable[[str], bool],
    page_path: Path,
) -> None:
    """Register the read-only review page, its data and bundle downloads."""

    def tester_id() -> str:
        value = str(request.args.get("tester_id", ""))
        if not valid_tester(value):
            raise ValueError("unknown tester")
        return value

    @app.get("/session-review.html")
    def session_review_page():
        response = send_file(page_path)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/tester/review-viewer")
    def review_viewer_download():
        return send_file(page_path, as_attachment=True, download_name="review.html")

    @app.get("/api/tester/analysis")
    def analysis_status():
        try:
            return _json(read_analysis(sessions_root, tester_id()))
        except ValueError as exc:
            return _json({"error": str(exc)}, 400)

    @app.get("/api/tester/session-review")
    def session_review():
        try:
            path = Path(sessions_root) / tester_id() / ANALYSIS_DIR / "session_review.json"
            if not path.is_file():
                raise FileNotFoundError("analyse the session first")
            response = send_file(path, mimetype="application/json")
            response.headers["Cache-Control"] = "no-store"
            return response
        except FileNotFoundError as exc:
            return _json({"error": str(exc)}, 404)
        except ValueError as exc:
            return _json({"error": str(exc)}, 400)

    @app.get("/api/tester/session-review/file")
    def session_review_file():
        try:
            path = _evidence_file(sessions_root, tester_id(), str(request.args.get("path", "")))
            response = send_file(path, mimetype=_SERVED_TYPES[path.suffix.lower()])
            response.headers["Cache-Control"] = "no-store"
            return response
        except FileNotFoundError as exc:
            return _json({"error": str(exc)}, 404)
        except ValueError as exc:
            return _json({"error": str(exc)}, 400)

    @app.get("/api/tester/bundle")
    def download_bundle():
        try:
            tester = tester_id()
            name = request.args.get("name")
            if not name:
                latest = read_analysis(sessions_root, tester)["latest_bundle"]
                if latest is None:
                    raise FileNotFoundError("no bundle has been made yet")
                name = latest["name"]
            path = session_bundle.bundle_path(sessions_root, tester, str(name))
            return send_file(path, as_attachment=True, download_name=path.name)
        except FileNotFoundError as exc:
            return _json({"error": str(exc)}, 404)
        except ValueError as exc:
            return _json({"error": str(exc)}, 400)

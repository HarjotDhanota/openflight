"""Bounded correlation of saved camera, IWR, and terminal shot evidence."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

MAX_SESSION_FILES = 32
MAX_SESSION_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_SESSION_LINES = 20_000


def _check(check_id: str, status: str, reason: str | None = None) -> dict[str, Any]:
    return {"id": check_id, "status": status, "reason": reason}


def _result(
    status: str,
    checks: list[dict[str, Any]],
    *,
    session_uuid: str | None = None,
    shot_number: int | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "checks": checks,
        "blockers": [
            {"id": item["id"], "reason": item["reason"]}
            for item in checks
            if item["status"] == "block"
        ],
        "session_uuid": session_uuid,
        "shot_number": shot_number,
    }


def _contained(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _session_files(run_dir: Path) -> list[Path]:
    files: list[Path] = []
    for directory, names, filenames in os.walk(run_dir, followlinks=False):
        current = Path(directory)
        names[:] = [name for name in names if not (current / name).is_symlink()]
        for name in filenames:
            path = current / name
            if name.startswith("session_") and name.endswith(".jsonl"):
                if path.is_symlink():
                    raise ValueError("session log may not be a symlink")
                files.append(path)
                if len(files) > MAX_SESSION_FILES:
                    raise ValueError("too many session logs")
    return sorted(files)


def _read_entries(path: Path, remaining_bytes: int) -> tuple[list[dict[str, Any]], int]:
    limit = min(MAX_SESSION_BYTES, remaining_bytes)
    with path.open("rb") as handle:
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("session logs exceed the byte limit")
    lines = raw.splitlines()
    if len(lines) > MAX_SESSION_LINES:
        raise ValueError("session log exceeds the line limit")
    entries = []
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        if index == len(lines) - 1 and raw and not raw.endswith(b"\n"):
            continue
        try:
            entry = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("session log contains a malformed complete record") from exc
        if not isinstance(entry, dict) or not isinstance(entry.get("type"), str):
            raise ValueError("session log record must be an object with a type")
        entries.append(entry)
    return entries, len(raw)


def _positive_shot(entry: dict[str, Any]) -> int | None:
    value = entry.get("shot_number")
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _capture_path(value: Any, run_dir: Path) -> Path | None:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        return None
    supplied = Path(value)
    lexical = supplied if supplied.is_absolute() else run_dir / supplied
    try:
        relative = lexical.relative_to(run_dir)
    except ValueError:
        return None
    current = run_dir
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            return None
    resolved = lexical.resolve()
    return resolved if _contained(resolved, run_dir) else None


def evaluate_paired_capture(run_dir: str | Path, capture_dir: str | Path) -> dict[str, Any]:
    """Return whether one saved camera capture has complete paired shot evidence."""
    supplied_run = Path(run_dir)
    if supplied_run.is_symlink():
        return _result("ineligible", [_check("run", "block", "run directory is unavailable")])
    run = supplied_run.resolve()
    capture = Path(capture_dir)
    if not run.is_dir() or run.is_symlink():
        return _result("ineligible", [_check("run", "block", "run directory is unavailable")])
    if capture.is_symlink():
        return _result("ineligible", [_check("capture", "block", "capture may not be a symlink")])
    capture = capture.resolve()
    if not _contained(capture, run) or capture == run or not capture.name:
        return _result(
            "ineligible", [_check("capture", "block", "capture is not a folder within the run")]
        )

    try:
        files = _session_files(run)
        sessions = []
        bytes_read = 0
        for path in files:
            entries, consumed = _read_entries(path, MAX_TOTAL_BYTES - bytes_read)
            sessions.append((path, entries))
            bytes_read += consumed
    except (OSError, ValueError) as exc:
        return _result("ineligible", [_check("session_logs", "block", str(exc))])

    matches: list[tuple[str, int, list[dict[str, Any]]]] = []
    for _path, entries in sessions:
        starts = [entry for entry in entries if entry["type"] == "session_start"]
        if len(starts) != 1:
            continue
        identity = starts[0].get("session_uuid")
        try:
            session_uuid = str(uuid.UUID(identity)) if isinstance(identity, str) else None
        except ValueError:
            session_uuid = None
        if session_uuid is None:
            continue
        for entry in entries:
            if entry["type"] != "camera_capture":
                continue
            logged = _capture_path(entry.get("capture_path"), run)
            shot = _positive_shot(entry)
            if logged == capture and logged.name == capture.name and shot is not None:
                if entry.get("capture_error") is not None:
                    return _result(
                        "ineligible",
                        [_check("camera_capture", "block", "camera capture reports an error")],
                        session_uuid=session_uuid,
                        shot_number=shot,
                    )
                matches.append((session_uuid, shot, entries))

    if not matches:
        return _result(
            "pending",
            [_check("camera_capture", "pending", "camera capture record is not complete")],
        )
    identities = {(session_uuid, shot) for session_uuid, shot, _entries in matches}
    if len(identities) != 1 or len(matches) != 1:
        return _result(
            "ineligible", [_check("identity", "block", "camera capture identity is ambiguous")]
        )

    session_uuid, shot, entries = matches[0]
    checks = [_check("camera_capture", "pass")]
    iwr_records = [
        entry
        for entry in entries
        if entry["type"] == "iwr6843_capture" and _positive_shot(entry) == shot
    ]
    terminal_records = [
        entry
        for entry in entries
        if entry["type"] == "shot_detected" and _positive_shot(entry) == shot
    ]

    if len(iwr_records) > 1 or len(terminal_records) > 1:
        checks.append(_check("paired_records", "block", "paired shot records are ambiguous"))
        return _result("ineligible", checks, session_uuid=session_uuid, shot_number=shot)
    if not iwr_records:
        checks.append(_check("iwr6843_capture", "pending", "IWR6843 capture record is pending"))
    else:
        iwr = iwr_records[0]
        capture_bytes = iwr.get("capture_bytes")
        iwr_path = _capture_path(iwr.get("capture_path"), run)
        if iwr.get("capture_error") is not None:
            checks.append(_check("iwr6843_capture", "block", "IWR6843 capture reports an error"))
        elif (
            not isinstance(capture_bytes, int)
            or isinstance(capture_bytes, bool)
            or capture_bytes <= 0
            or iwr_path is None
        ):
            checks.append(
                _check("iwr6843_capture", "block", "IWR6843 capture bytes or path are invalid")
            )
        elif not iwr_path.is_file() or iwr_path.stat().st_size != capture_bytes:
            checks.append(
                _check(
                    "iwr6843_capture",
                    "block",
                    "saved IWR6843 capture is missing or its size does not match the record",
                )
            )
        else:
            checks.append(_check("iwr6843_capture", "pass"))
    if not terminal_records:
        checks.append(_check("shot_detected", "pending", "terminal shot record is pending"))
    else:
        checks.append(_check("shot_detected", "pass"))

    if any(item["status"] == "block" for item in checks):
        status = "ineligible"
    elif any(item["status"] == "pending" for item in checks):
        status = "pending"
    else:
        status = "eligible"
    return _result(status, checks, session_uuid=session_uuid, shot_number=shot)

"""Append-only operator observations of physical attempts in a saved capture run."""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

SCHEMA_VERSION = 1
KINDS = frozenset({"swing", "warmup", "false_trigger"})
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_LOCK = threading.RLock()


class LedgerError(ValueError):
    """The ledger cannot be trusted or a requested mutation is invalid."""


def read_audit(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    records = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise LedgerError(f"attempt ledger is not UTF-8: byte {exc.start}") from exc
    for line_number, line in enumerate(lines, 1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise LedgerError(f"malformed attempt ledger line {line_number}: {exc.msg}") from exc
        if not isinstance(record, dict) or record.get("schema_version") != SCHEMA_VERSION:
            raise LedgerError(f"invalid attempt ledger record at line {line_number}")
        action = record.get("action")
        scope = record.get("scope")
        if (
            action not in ("add", "correct", "void")
            or not all(
                isinstance(record.get(key), str) and record[key]
                for key in ("request_id", "entry_id", "recorded_at")
            )
            or not isinstance(scope, dict)
            or set(scope) != {"tester_id", "arm_id", "run"}
            or not all(
                isinstance(value, str) and SAFE_ID.fullmatch(value) for value in scope.values()
            )
        ):
            raise LedgerError(f"invalid attempt ledger record at line {line_number}")
        if action in ("add", "correct"):
            if "operator_missed" not in record:
                raise LedgerError(f"invalid attempt ledger record at line {line_number}")
            try:
                _validated_observation(record)
            except LedgerError as exc:
                raise LedgerError(
                    f"invalid attempt ledger record at line {line_number}: {exc}"
                ) from exc
        if action in ("correct", "void") and not (
            isinstance(record.get("target_entry_id"), str) and record["target_entry_id"]
        ):
            raise LedgerError(f"invalid attempt ledger record at line {line_number}")
        records.append(record)
    request_ids = [record["request_id"] for record in records]
    entry_ids = [record["entry_id"] for record in records]
    if len(request_ids) != len(set(request_ids)) or len(entry_ids) != len(set(entry_ids)):
        raise LedgerError("attempt ledger contains duplicate request_id or entry_id")
    return records


def _effective(records: list[dict]) -> list[dict]:
    observations: dict[str, dict] = {}
    order: list[str] = []
    for record in records:
        action = record["action"]
        if action == "add":
            entry_id = record["entry_id"]
            observations[entry_id] = {
                key: record.get(key)
                for key in ("entry_id", "kind", "operator_missed", "rung_id", "note", "recorded_at")
            }
            observations[entry_id]["status"] = "active"
            order.append(entry_id)
        elif action in ("correct", "void"):
            target = record["target_entry_id"]
            if target not in observations:
                raise LedgerError(f"attempt ledger references unknown entry {target}")
            if action == "void":
                observations[target]["status"] = "void"
            else:
                observations[target].update(
                    {key: record.get(key) for key in ("kind", "operator_missed", "rung_id", "note")}
                )
                observations[target]["status"] = "corrected"
                observations[target]["corrected_at"] = record["recorded_at"]
    return [
        observations[entry_id] for entry_id in order if observations[entry_id]["status"] != "void"
    ]


def summarize(path: Path, scope: Mapping, logged_sensor_shots: int) -> dict:
    with _LOCK:
        records = read_audit(path)
    if not records:
        return {
            "schema_version": SCHEMA_VERSION,
            "scope": dict(scope),
            "entries": [],
            "audit": [],
            "counts": {
                "physical_operator_swings": None,
                "operator_reported_misses": None,
                "warmups": None,
                "false_triggers": None,
                "logged_sensor_shots": logged_sensor_shots,
            },
            "reconciliation": {
                "status": "unknown",
                "difference": None,
                "meaning": "no operator ledger exists for this run",
                "physical_availability": None,
            },
        }
    if any(record["scope"] != dict(scope) for record in records):
        raise LedgerError("attempt ledger scope does not match its capture run")
    entries = _effective(records)
    swings = sum(entry["kind"] == "swing" for entry in entries)
    counts = {
        "physical_operator_swings": swings,
        "operator_reported_misses": sum(
            entry["kind"] == "swing" and entry["operator_missed"] for entry in entries
        ),
        "warmups": sum(entry["kind"] == "warmup" for entry in entries),
        "false_triggers": sum(entry["kind"] == "false_trigger" for entry in entries),
        "logged_sensor_shots": logged_sensor_shots,
    }
    difference = logged_sensor_shots - swings
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": dict(scope),
        "entries": entries,
        "audit": records,
        "counts": counts,
        "reconciliation": {
            "status": "count_match" if difference == 0 else "count_mismatch",
            "difference": difference,
            "meaning": "count agreement only; shot identities and coverage are not established",
            "physical_availability": None,
        },
    }


def _validated_observation(payload: Mapping) -> dict:
    kind = str(payload.get("kind", ""))
    if kind not in KINDS:
        raise LedgerError("kind must be swing, warmup, or false_trigger")
    missed = payload.get("operator_missed", False)
    if not isinstance(missed, bool):
        raise LedgerError("operator_missed must be a boolean")
    if missed and kind != "swing":
        raise LedgerError("only a swing can be operator-reported missed")
    note = payload.get("note")
    if note is not None and (not isinstance(note, str) or len(note) > 500):
        raise LedgerError("note must be at most 500 characters")
    rung_id = payload.get("rung_id")
    if rung_id is not None and (not isinstance(rung_id, str) or not SAFE_ID.fullmatch(rung_id)):
        raise LedgerError("rung_id must be a portable identifier")
    return {"kind": kind, "operator_missed": missed, "rung_id": rung_id, "note": note}


def append(
    path: Path, scope: Mapping, payload: Mapping, logged_sensor_shots: int
) -> tuple[dict, bool]:
    """Append one request, returning state and whether it was newly recorded."""
    request_id = str(payload.get("request_id", ""))
    entry_id = str(payload.get("entry_id", ""))
    action = str(payload.get("action", "add"))
    if not SAFE_ID.fullmatch(request_id) or not SAFE_ID.fullmatch(entry_id):
        raise LedgerError("request_id and entry_id must be portable identifiers")
    if set(scope) != {"tester_id", "arm_id", "run"} or not all(
        isinstance(value, str) and SAFE_ID.fullmatch(value) for value in scope.values()
    ):
        raise LedgerError("scope must contain portable tester_id, arm_id, and run identifiers")
    if action not in ("add", "correct", "void"):
        raise LedgerError("action must be add, correct, or void")
    record = {
        "schema_version": SCHEMA_VERSION,
        "request_id": request_id,
        "entry_id": entry_id,
        "action": action,
        "scope": dict(scope),
    }
    if action in ("add", "correct"):
        record.update(_validated_observation(payload))
    if action in ("correct", "void"):
        target = str(payload.get("target_entry_id", ""))
        if not SAFE_ID.fullmatch(target):
            raise LedgerError("target_entry_id must be a portable identifier")
        record["target_entry_id"] = target
    with _LOCK:
        records = read_audit(path)
        if records and any(row["scope"] != dict(scope) for row in records):
            raise LedgerError("attempt ledger scope does not match its capture run")
        _effective(records)
        previous = next((row for row in records if row["request_id"] == request_id), None)
        if previous is not None:
            comparable = {key: value for key, value in previous.items() if key != "recorded_at"}
            if comparable != record:
                raise LedgerError("request_id was already used with different content")
            return summarize(path, scope, logged_sensor_shots), False
        if any(row["entry_id"] == entry_id for row in records):
            raise LedgerError("entry_id already exists")
        effective_ids = {row["entry_id"] for row in records if row["action"] == "add"}
        if action in ("correct", "void") and record["target_entry_id"] not in effective_ids:
            raise LedgerError("target_entry_id does not identify an observation")
        record["recorded_at"] = datetime.now(timezone.utc).isoformat()
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.read_bytes() if path.is_file() else b""
        temporary = None
        try:
            with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(existing)
                if existing and not existing.endswith(b"\n"):
                    handle.write(b"\n")
                handle.write((json.dumps(record, separators=(",", ":")) + "\n").encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return summarize(path, scope, logged_sensor_shots), True

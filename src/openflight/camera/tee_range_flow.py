"""Durable guided tee-range setup epochs for the tester kiosk."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from openflight import session_bundle
from openflight.tee_range import TeeRangeSolution
from openflight.tee_range_setup import (
    TeeRangeEpochReference,
    TeeRangeEvidenceEpoch,
    load_current_reference,
    load_epoch,
    write_epoch,
)

SCHEMA = "openflight.tester_tee_range_flow.v1"
TERMINAL_PHASES = frozenset({"resolved", "raw_only"})
CAPTURE_PHASES = frozenset({"empty_capturing", "ball_capturing"})


def _canonical(payload: Mapping) -> bytes:
    return (
        json.dumps(dict(payload), sort_keys=True, allow_nan=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _immutable(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != content:
            raise FileExistsError(f"immutable tee-range flow snapshot changed: {path.name}")
        return
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != content:
                raise
    finally:
        temporary.unlink(missing_ok=True)


@dataclass(frozen=True)
class FlowState:
    """One immutable state snapshot in an automatic-range setup epoch."""

    epoch_id: str
    sequence: int
    phase: str
    reason: str
    created_at_utc: str
    updated_at_utc: str
    request_ids: tuple[str, ...]
    evidence: Mapping[str, Any]
    setup_admission: Mapping[str, Any]
    solution: Mapping[str, Any] | None = None
    retry_phase: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "epoch_id": self.epoch_id,
            "sequence": self.sequence,
            "phase": self.phase,
            "reason": self.reason,
            "created_at_utc": self.created_at_utc,
            "updated_at_utc": self.updated_at_utc,
            "request_ids": list(self.request_ids),
            "evidence": dict(self.evidence),
            "setup_admission": dict(self.setup_admission),
            "solution": dict(self.solution) if self.solution is not None else None,
            "retry_phase": self.retry_phase,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "FlowState":
        if payload.get("schema") != SCHEMA:
            raise ValueError("unsupported tester tee-range flow schema")
        return cls(
            epoch_id=str(payload["epoch_id"]),
            sequence=int(payload["sequence"]),
            phase=str(payload["phase"]),
            reason=str(payload["reason"]),
            created_at_utc=str(payload["created_at_utc"]),
            updated_at_utc=str(payload["updated_at_utc"]),
            request_ids=tuple(str(value) for value in payload.get("request_ids", [])),
            evidence=dict(payload.get("evidence", {})),
            setup_admission=dict(payload.get("setup_admission", {})),
            solution=(dict(payload["solution"]) if payload.get("solution") is not None else None),
            retry_phase=(str(payload["retry_phase"]) if payload.get("retry_phase") else None),
        )


class FlowStore:
    """Append immutable snapshots and keep one atomic active pointer per tester."""

    def __init__(self, tester_root: Path):
        self.tester_root = Path(tester_root)
        self.root = self.tester_root / "calibration" / "tee-range" / "guided"

    @property
    def pointer_path(self) -> Path:
        return self.root / "active.json"

    def epoch_dir(self, epoch_id: str) -> Path:
        return self.root / "epochs" / epoch_id

    def _state_path(self, epoch_id: str, sequence: int) -> Path:
        return self.epoch_dir(epoch_id) / f"state-{sequence:06d}.json"

    def load(self) -> FlowState | None:
        if not self.pointer_path.is_file():
            return None
        pointer = json.loads(self.pointer_path.read_text(encoding="utf-8"))
        path = self._state_path(str(pointer["epoch_id"]), int(pointer["sequence"]))
        content = path.read_bytes()
        if _sha256(content) != pointer.get("sha256"):
            raise ValueError("guided tee-range state digest mismatch")
        return FlowState.from_dict(json.loads(content))

    def start(self, request_id: str, *, setup_admission: Mapping[str, Any]) -> FlowState:
        with session_bundle.snapshot_lock(self.tester_root, timeout_s=session_bundle.WRITER_WAIT_S):
            current = self.load()
            if current is not None and request_id in current.request_ids:
                return current
            now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            epoch_id = f"setup-{now[:10].replace('-', '')}-{uuid.uuid4().hex[:12]}"
            return self._save_unlocked(
                FlowState(
                    epoch_id=epoch_id,
                    sequence=1,
                    phase="needs_empty",
                    reason="remove_ball_and_keep_setup_still",
                    created_at_utc=now,
                    updated_at_utc=now,
                    request_ids=(request_id,),
                    evidence={},
                    setup_admission=dict(setup_admission),
                )
            )

    def transition(
        self,
        state: FlowState,
        *,
        phase: str,
        reason: str,
        request_id: str | None = None,
        evidence: Mapping[str, Any] | None = None,
        solution: TeeRangeSolution | None = None,
        retry_phase: str | None = None,
    ) -> FlowState:
        with session_bundle.snapshot_lock(self.tester_root, timeout_s=session_bundle.WRITER_WAIT_S):
            current = self.load()
            if current is None or current.epoch_id != state.epoch_id:
                raise RuntimeError("tee-range setup epoch changed")
            if request_id and request_id in current.request_ids:
                return current
            if current.sequence != state.sequence:
                raise RuntimeError("tee-range setup state changed; reload and retry")
            return self._save_unlocked(
                self._next(
                    current,
                    phase=phase,
                    reason=reason,
                    request_id=request_id,
                    evidence=evidence,
                    solution=solution,
                    retry_phase=retry_phase,
                )
            )

    @staticmethod
    def _next(
        state: FlowState,
        *,
        phase: str,
        reason: str,
        request_id: str | None = None,
        evidence: Mapping[str, Any] | None = None,
        solution: TeeRangeSolution | None = None,
        retry_phase: str | None = None,
    ) -> FlowState:
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        merged = dict(state.evidence)
        if evidence:
            merged.update(dict(evidence))
        requests = (*state.request_ids, request_id) if request_id else state.request_ids
        return FlowState(
            epoch_id=state.epoch_id,
            sequence=state.sequence + 1,
            phase=phase,
            reason=reason,
            created_at_utc=state.created_at_utc,
            updated_at_utc=now,
            request_ids=tuple(value for value in requests if value),
            evidence=merged,
            setup_admission=state.setup_admission,
            solution=solution.to_dict() if solution is not None else state.solution,
            retry_phase=retry_phase,
        )

    def finalize(
        self, state: FlowState, solution: TeeRangeSolution, qualification=None
    ) -> FlowState:
        phase = "resolved" if solution.status == "resolved" else "raw_only"
        with session_bundle.snapshot_lock(self.tester_root, timeout_s=session_bundle.WRITER_WAIT_S):
            current = self.load()
            if current is None or current.epoch_id != state.epoch_id:
                raise RuntimeError("tee-range setup epoch changed before finalization")
            if current.phase in TERMINAL_PHASES:
                reference_payload = current.evidence.get("final_reference")
                if not isinstance(reference_payload, Mapping):
                    raise RuntimeError("terminal tee-range state has no final epoch reference")
                reference = TeeRangeEpochReference.from_dict(reference_payload)
                if load_current_reference(self.tester_root) != reference:
                    raise RuntimeError("terminal tee-range state does not match current epoch")
                load_epoch(self.tester_root, reference)
                return current
            if current.sequence != state.sequence:
                raise RuntimeError("tee-range setup state changed before finalization")
            epoch = TeeRangeEvidenceEpoch(
                epoch_id=state.epoch_id,
                created_at_utc=state.created_at_utc,
                qualification=qualification if solution.status == "resolved" else None,
                solution=solution,
            )
            reference = write_epoch(self.tester_root, epoch, make_current=True)
            return self._save_unlocked(
                self._next(
                    current,
                    phase=phase,
                    reason=solution.reason,
                    evidence={"final_reference": reference.to_dict()},
                    solution=solution,
                )
            )

    def _save_unlocked(self, state: FlowState) -> FlowState:
        content = _canonical(state.to_dict())
        _immutable(self._state_path(state.epoch_id, state.sequence), content)
        atomic_write(
            self.pointer_path,
            _canonical(
                {
                    "epoch_id": state.epoch_id,
                    "sequence": state.sequence,
                    "sha256": _sha256(content),
                }
            ),
        )
        return state

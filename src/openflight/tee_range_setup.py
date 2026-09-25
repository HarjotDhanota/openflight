"""Immutable setup-level tee-range evidence epochs and compact references."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping

from openflight.tee_range import TeeRangeQualification, TeeRangeSolution

EPOCH_SCHEMA = "openflight.tee_range_setup_epoch.v1"
EPOCH_SCHEMA_VERSION = 1
REFERENCE_SCHEMA = "openflight.tee_range_setup_reference.v1"
REFERENCE_SCHEMA_VERSION = 1
_EPOCH_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _canonical_bytes(payload: Mapping) -> bytes:
    return (
        json.dumps(dict(payload), sort_keys=True, allow_nan=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _epoch_id(value: str) -> str:
    result = str(value).strip()
    if _EPOCH_ID.fullmatch(result) is None or result in {".", ".."}:
        raise ValueError("epoch_id must be a safe file identifier")
    return result


def _sha256(value: str) -> str:
    result = str(value).strip().lower()
    if _SHA256.fullmatch(result) is None:
        raise ValueError("epoch_sha256 must be a lowercase SHA-256")
    return result


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_temp(destination: Path, content: bytes) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    with temporary.open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    return temporary


def _atomic_replace(destination: Path, content: bytes) -> None:
    temporary = _write_temp(destination, content)
    try:
        os.replace(temporary, destination)
        _fsync_directory(destination.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _write_immutable(destination: Path, content: bytes) -> None:
    if destination.exists():
        if destination.read_bytes() == content:
            return
        raise FileExistsError(f"tee-range epoch {destination.name} is immutable")
    temporary = _write_temp(destination, content)
    try:
        try:
            os.link(temporary, destination)
        except FileExistsError:
            if destination.read_bytes() != content:
                raise FileExistsError(f"tee-range epoch {destination.name} is immutable") from None
        _fsync_directory(destination.parent)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass(frozen=True)
class TeeRangeEpochReference:
    """The only tee-range state copied into an arm or session record."""

    epoch_id: str
    epoch_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "epoch_id", _epoch_id(self.epoch_id))
        object.__setattr__(self, "epoch_sha256", _sha256(self.epoch_sha256))

    def to_dict(self) -> dict:
        """Return the compact, versioned reference."""
        return {
            "schema": REFERENCE_SCHEMA,
            "schema_version": REFERENCE_SCHEMA_VERSION,
            "epoch_id": self.epoch_id,
            "epoch_sha256": self.epoch_sha256,
        }

    @classmethod
    def from_dict(cls, payload: Mapping) -> TeeRangeEpochReference:
        """Validate a compact reference."""
        if (
            payload.get("schema") != REFERENCE_SCHEMA
            or payload.get("schema_version") != REFERENCE_SCHEMA_VERSION
        ):
            raise ValueError("unsupported tee-range epoch reference schema")
        if set(payload) != {"schema", "schema_version", "epoch_id", "epoch_sha256"}:
            raise ValueError("tee-range epoch reference contains unsupported fields")
        return cls(epoch_id=payload["epoch_id"], epoch_sha256=payload["epoch_sha256"])


@dataclass(frozen=True)
class TeeRangeEvidenceEpoch:
    """One immutable setup evidence state shared by every arm and session."""

    epoch_id: str
    created_at_utc: str
    solution: TeeRangeSolution
    qualification: TeeRangeQualification | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "epoch_id", _epoch_id(self.epoch_id))
        timestamp = str(self.created_at_utc).strip()
        try:
            parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("created_at_utc must be an ISO-8601 timestamp") from error
        if not timestamp.endswith("Z") or parsed.utcoffset() is None:
            raise ValueError("created_at_utc must be expressed in UTC with Z")
        object.__setattr__(self, "created_at_utc", timestamp)
        if self.solution.status == "resolved":
            if self.qualification is None:
                raise ValueError("a resolved epoch requires its qualification artifact")
            if self.solution.evidence_epoch_id != self.epoch_id:
                raise ValueError("resolved solution must reference its containing epoch")
            if self.solution.qualification_sha256 != self.qualification.artifact_sha256:
                raise ValueError("resolved solution qualification digest does not match")
            if self.solution.policy_sha256 != self.qualification.policy_sha256:
                raise ValueError("resolved solution policy digest does not match")

    def to_dict(self) -> dict:
        """Return the complete immutable epoch document."""
        return {
            "schema": EPOCH_SCHEMA,
            "schema_version": EPOCH_SCHEMA_VERSION,
            "epoch_id": self.epoch_id,
            "created_at_utc": self.created_at_utc,
            "qualification": self.qualification.to_dict() if self.qualification else None,
            "solution": self.solution.to_dict(),
        }

    @property
    def sha256(self) -> str:
        """Digest the exact canonical bytes persisted for this epoch."""
        return hashlib.sha256(_canonical_bytes(self.to_dict())).hexdigest()

    @property
    def reference(self) -> TeeRangeEpochReference:
        """Return the compact identity used by current, arm and session records."""
        return TeeRangeEpochReference(self.epoch_id, self.sha256)

    @classmethod
    def from_dict(cls, payload: Mapping) -> TeeRangeEvidenceEpoch:
        """Validate a complete epoch document."""
        if (
            payload.get("schema") != EPOCH_SCHEMA
            or payload.get("schema_version") != EPOCH_SCHEMA_VERSION
        ):
            raise ValueError("unsupported tee-range setup epoch schema")
        if set(payload) != {
            "schema",
            "schema_version",
            "epoch_id",
            "created_at_utc",
            "qualification",
            "solution",
        }:
            raise ValueError("tee-range setup epoch contains unsupported fields")
        qualification_payload = payload.get("qualification")
        qualification = (
            TeeRangeQualification.from_dict(qualification_payload)
            if isinstance(qualification_payload, Mapping)
            else None
        )
        solution_payload = payload["solution"]
        if not isinstance(solution_payload, Mapping):
            raise ValueError("tee-range setup solution must be a JSON object")
        if solution_payload.get("status") == "resolved" and qualification is None:
            raise ValueError("a resolved epoch requires its qualification artifact")
        return cls(
            epoch_id=payload["epoch_id"],
            created_at_utc=payload["created_at_utc"],
            qualification=qualification,
            solution=TeeRangeSolution.from_dict(
                solution_payload,
                qualification=qualification,
                epoch_id=payload["epoch_id"],
            ),
        )


def _root(tester_root: str | Path) -> Path:
    return Path(tester_root) / "calibration" / "tee-range"


def write_epoch(
    tester_root: str | Path,
    epoch: TeeRangeEvidenceEpoch,
    *,
    make_current: bool = False,
) -> TeeRangeEpochReference:
    """Durably create an immutable epoch and optionally advance current."""
    root = _root(tester_root)
    content = _canonical_bytes(epoch.to_dict())
    destination = root / "epochs" / f"{epoch.epoch_id}.json"
    _write_immutable(destination, content)
    reference = TeeRangeEpochReference(epoch.epoch_id, hashlib.sha256(content).hexdigest())
    if make_current:
        _atomic_replace(root / "current.json", _canonical_bytes(reference.to_dict()))
    return reference


def load_epoch(tester_root: str | Path, reference: TeeRangeEpochReference) -> TeeRangeEvidenceEpoch:
    """Load an epoch only when its immutable bytes match the reference digest."""
    path = _root(tester_root) / "epochs" / f"{reference.epoch_id}.json"
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != reference.epoch_sha256:
        raise ValueError("tee-range epoch digest does not match its reference")
    payload = json.loads(content)
    epoch = TeeRangeEvidenceEpoch.from_dict(payload)
    if epoch.epoch_id != reference.epoch_id or epoch.sha256 != reference.epoch_sha256:
        raise ValueError("tee-range epoch identity does not match its reference")
    return epoch


def load_current_reference(tester_root: str | Path) -> TeeRangeEpochReference | None:
    """Load the setup's current epoch identity, if one has been established."""
    path = _root(tester_root) / "current.json"
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("tee-range current pointer must be a JSON object")
    return TeeRangeEpochReference.from_dict(payload)


def load_current_epoch(tester_root: str | Path) -> TeeRangeEvidenceEpoch | None:
    """Resolve the current pointer and verify the immutable epoch digest."""
    reference = load_current_reference(tester_root)
    return load_epoch(tester_root, reference) if reference is not None else None


def write_reference(path: str | Path, reference: TeeRangeEpochReference) -> None:
    """Atomically write an arm/session reference without copying setup evidence."""
    _atomic_replace(Path(path), _canonical_bytes(reference.to_dict()))


def load_reference(path: str | Path) -> TeeRangeEpochReference:
    """Load an arm/session epoch reference."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("tee-range epoch reference must be a JSON object")
    return TeeRangeEpochReference.from_dict(payload)


__all__ = [
    "TeeRangeEpochReference",
    "TeeRangeEvidenceEpoch",
    "load_current_epoch",
    "load_current_reference",
    "load_epoch",
    "load_reference",
    "write_epoch",
    "write_reference",
]

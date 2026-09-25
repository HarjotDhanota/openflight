"""Versioned evidence contract for the radar-to-tee slant range."""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

SCHEMA = "openflight.tee_range.v1"
SCHEMA_VERSION = 1
UNRESOLVED_LEGACY_REASON = "legacy_session_has_no_tee_range_contract"
SOURCE_GROUPS = frozenset({"camera", "iwr", "manual_truth"})


def _positive_finite(value: float, name: str) -> float:
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{name} must be positive and finite")
    return result


def _required_text(value: str, name: str) -> str:
    result = str(value).strip()
    if not result:
        raise ValueError(f"{name} must not be empty")
    return result


@dataclass(frozen=True)
class TeeRangeCandidate:
    """One range observation, including the sensor family that produced it."""

    candidate_id: str
    source: str
    source_group: str
    radar_slant_range_m: float
    uncertainty_m: float
    evidence: Mapping
    selectable: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidate_id", _required_text(self.candidate_id, "candidate_id"))
        object.__setattr__(self, "source", _required_text(self.source, "source"))
        group = _required_text(self.source_group, "source_group")
        if group not in SOURCE_GROUPS:
            raise ValueError(f"unsupported source_group: {group}")
        object.__setattr__(self, "source_group", group)
        object.__setattr__(
            self,
            "radar_slant_range_m",
            _positive_finite(self.radar_slant_range_m, "radar_slant_range_m"),
        )
        object.__setattr__(
            self, "uncertainty_m", _positive_finite(self.uncertainty_m, "uncertainty_m")
        )
        evidence = dict(self.evidence)
        json.dumps(evidence, allow_nan=False)
        object.__setattr__(self, "evidence", evidence)
        if group == "manual_truth" and self.selectable:
            raise ValueError("manual truth must not be selectable")

    def to_dict(self) -> dict:
        """Return the stable JSON representation."""
        return {
            "candidate_id": self.candidate_id,
            "source": self.source,
            "source_group": self.source_group,
            "radar_slant_range_m": self.radar_slant_range_m,
            "uncertainty_m": self.uncertainty_m,
            "selectable": self.selectable,
            "evidence": dict(self.evidence),
        }

    @classmethod
    def from_dict(cls, payload: Mapping) -> TeeRangeCandidate:
        """Validate and reconstruct one candidate from JSON data."""
        return cls(
            candidate_id=payload["candidate_id"],
            source=payload["source"],
            source_group=payload["source_group"],
            radar_slant_range_m=payload["radar_slant_range_m"],
            uncertainty_m=payload["uncertainty_m"],
            selectable=bool(payload.get("selectable", True)),
            evidence=payload.get("evidence", {}),
        )


def manual_truth_candidate(range_m: float, *, evidence: Mapping | None = None) -> TeeRangeCandidate:
    """Preserve an optional tape measurement as validation truth only."""
    return TeeRangeCandidate(
        candidate_id="manual-tape-truth",
        source="operator_tape",
        source_group="manual_truth",
        radar_slant_range_m=range_m,
        uncertainty_m=0.005,
        selectable=False,
        evidence=evidence or {},
    )


@dataclass(frozen=True)
class TeeRangeSolution:
    """Resolved or explicitly unresolved radar-origin range evidence."""

    status: str
    reason: str
    candidates: tuple[TeeRangeCandidate, ...]
    selected_candidate_id: str | None = None
    selected_range_m: float | None = None
    selected_uncertainty_m: float | None = None
    supporting_source_groups: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in {"resolved", "unresolved"}:
            raise ValueError("status must be resolved or unresolved")
        _required_text(self.reason, "reason")
        ordered = tuple(sorted(self.candidates, key=lambda item: item.candidate_id))
        if len({item.candidate_id for item in ordered}) != len(ordered):
            raise ValueError("candidate identifiers must be unique")
        object.__setattr__(self, "candidates", ordered)
        object.__setattr__(
            self, "supporting_source_groups", tuple(sorted(set(self.supporting_source_groups)))
        )
        if self.status == "unresolved":
            if any(
                value is not None
                for value in (
                    self.selected_candidate_id,
                    self.selected_range_m,
                    self.selected_uncertainty_m,
                )
            ):
                raise ValueError("unresolved range cannot contain a selected value")
            if self.supporting_source_groups:
                raise ValueError("unresolved range cannot contain supporting groups")
            return
        selected = next(
            (item for item in ordered if item.candidate_id == self.selected_candidate_id), None
        )
        if selected is None or not selected.selectable:
            raise ValueError("resolved range must select a selectable candidate")
        if self.selected_range_m != selected.radar_slant_range_m:
            raise ValueError("selected range must match its candidate")
        if self.selected_uncertainty_m != selected.uncertainty_m:
            raise ValueError("selected uncertainty must match its candidate")
        candidate_groups = tuple(sorted({item.source_group for item in ordered if item.selectable}))
        if len(self.supporting_source_groups) < 2:
            raise ValueError("resolved range requires independent source groups")
        if self.supporting_source_groups != candidate_groups:
            raise ValueError("supporting groups must match selectable candidates")

    @classmethod
    def unresolved(
        cls, candidates: Iterable[TeeRangeCandidate] = (), *, reason: str
    ) -> TeeRangeSolution:
        """Build an explicit pending solution without a selected value."""
        return cls(status="unresolved", reason=reason, candidates=tuple(candidates))

    @classmethod
    def resolved(
        cls,
        selected: TeeRangeCandidate,
        *,
        supporting: Iterable[TeeRangeCandidate],
        reason: str = "independently_supported",
    ) -> TeeRangeSolution:
        """Select a candidate only when another sensor family supports it."""
        candidates = (selected, *tuple(supporting))
        groups = {item.source_group for item in candidates if item.selectable}
        if len(groups) < 2:
            raise ValueError("resolved range requires independent source groups")
        return cls(
            status="resolved",
            reason=reason,
            candidates=candidates,
            selected_candidate_id=selected.candidate_id,
            selected_range_m=selected.radar_slant_range_m,
            selected_uncertainty_m=selected.uncertainty_m,
            supporting_source_groups=tuple(groups),
        )

    def to_dict(self) -> dict:
        """Return the stable JSON representation."""
        return {
            "schema": SCHEMA,
            "schema_version": SCHEMA_VERSION,
            "status": self.status,
            "reason": self.reason,
            "selected_candidate_id": self.selected_candidate_id,
            "selected_range_m": self.selected_range_m,
            "selected_uncertainty_m": self.selected_uncertainty_m,
            "supporting_source_groups": list(self.supporting_source_groups),
            "candidates": [item.to_dict() for item in self.candidates],
        }

    @classmethod
    def from_dict(cls, payload: Mapping) -> TeeRangeSolution:
        """Validate and reconstruct a solution from JSON data."""
        if payload.get("schema") != SCHEMA or payload.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("unsupported tee-range schema")
        return cls(
            status=payload["status"],
            reason=payload["reason"],
            candidates=tuple(
                TeeRangeCandidate.from_dict(item) for item in payload.get("candidates", [])
            ),
            selected_candidate_id=payload.get("selected_candidate_id"),
            selected_range_m=payload.get("selected_range_m"),
            selected_uncertainty_m=payload.get("selected_uncertainty_m"),
            supporting_source_groups=tuple(payload.get("supporting_source_groups", [])),
        )


def write_solution(path: str | Path, solution: TeeRangeSolution) -> None:
    """Atomically persist a range contract beside the session evidence."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp")
    temporary.write_text(
        json.dumps(solution.to_dict(), sort_keys=True, allow_nan=False, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, destination)


def load_solution(path: str | Path) -> TeeRangeSolution:
    """Load a range contract, mapping old sessions to an explicit unresolved state."""
    source = Path(path)
    if not source.exists():
        return TeeRangeSolution.unresolved(reason=UNRESOLVED_LEGACY_REASON)
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("tee-range contract must be a JSON object")
    return TeeRangeSolution.from_dict(payload)


def camera_observation_to_radar_slant_m(
    *,
    camera_range_m: float,
    camera_ray_lfu: Sequence[float],
    camera_origin_lfu: Sequence[float],
    radar_origin_lfu: Sequence[float],
) -> float:
    """Convert a camera-origin ray range into radar-origin slant range."""
    distance = _positive_finite(camera_range_m, "camera_range_m")
    values = tuple(
        tuple(float(value) for value in vector)
        for vector in (
            camera_ray_lfu,
            camera_origin_lfu,
            radar_origin_lfu,
        )
    )
    if any(
        len(vector) != 3 or not all(math.isfinite(value) for value in vector) for vector in values
    ):
        raise ValueError("camera ray and origins must be finite three-vectors")
    ray, camera_origin, radar_origin = values
    norm = math.sqrt(sum(value * value for value in ray))
    if not math.isclose(norm, 1.0, rel_tol=1e-6, abs_tol=1e-6):
        raise ValueError("camera_ray_lfu must be a unit vector")
    ball = tuple(origin + distance * direction for origin, direction in zip(camera_origin, ray))
    return math.sqrt(sum((value - origin) ** 2 for value, origin in zip(ball, radar_origin)))


__all__ = [
    "TeeRangeCandidate",
    "TeeRangeSolution",
    "camera_observation_to_radar_slant_m",
    "load_solution",
    "manual_truth_candidate",
    "write_solution",
]

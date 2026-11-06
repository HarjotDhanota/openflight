"""Explicit angle-input lineage for experimental strike analysis.

This validates producer declarations and capture binding, not physical accuracy.
Shared observation IDs remain correlated; consumers must not count them twice.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Mapping

SCHEMA = "openflight.impact_angle_evidence.v1"
ANGLE_SIGMA_BY_STATUS = {
    "measured": 2.0,
    "calculated": 3.0,
    "model_geometry": 5.0,
    "estimated": 6.0,
}
REFERENCE_POINTS = {
    "face_angle": "face_normal",
    "dynamic_loft": "face_normal",
    "attack_angle": "club_head_velocity",
}


def _integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


@dataclass(frozen=True)
class AngleEvidence:  # pylint: disable=too-many-instance-attributes
    """Original producer identity and uncertainty, before hand/frame conversion."""

    metric: str
    value_deg: float
    source: str
    status: str
    frame: str
    note: str | None
    shot_number: int
    capture_npz_sha256: str
    observation_ids: tuple[str, ...]
    sensor_interval_ns: tuple[int, int]
    reference_point: str
    sigma_deg: float

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe provenance with explicit empty metric conditioning."""
        return {
            "schema": SCHEMA,
            **asdict(self),
            "depends_on": [],
            "conditioned_on": [],
            "event": "impact",
            "clock_domain": "camera_sensor",
        }

    @classmethod
    def from_metric(  # pylint: disable=too-many-branches
        cls,
        name: str,
        entry: Mapping[str, Any],
        *,
        shot_number: int | None,
        capture_npz_sha256: str | None,
        capture_sensor_interval_ns: tuple[int, int] | None,
    ) -> "AngleEvidence":
        """Refuse unbound or conditioned inputs; raw IDs are producer declarations."""
        if name not in REFERENCE_POINTS:
            raise ValueError("unsupported_angle_metric")
        if not isinstance(entry.get("status"), str) or entry["status"] not in ANGLE_SIGMA_BY_STATUS:
            raise ValueError("angle_status_not_eligible")
        evidence = entry.get("evidence")
        if not isinstance(evidence, Mapping) or evidence.get("schema") != SCHEMA:
            raise ValueError("missing_or_unsupported_angle_evidence")
        if evidence.get("depends_on") != [] or evidence.get("conditioned_on") != []:
            raise ValueError("angle_has_metric_dependencies_or_conditioning")
        if (
            not _integer(shot_number)
            or shot_number <= 0
            or evidence.get("shot_number") != shot_number
        ):
            raise ValueError("angle_shot_identity_mismatch")
        if not _integer(evidence.get("shot_number")):
            raise ValueError("invalid_angle_shot_identity")
        if (
            not isinstance(capture_npz_sha256, str)
            or len(capture_npz_sha256) != 64
            or any(c not in "0123456789abcdef" for c in capture_npz_sha256)
            or evidence.get("capture_npz_sha256") != capture_npz_sha256
        ):
            raise ValueError("angle_capture_identity_mismatch")
        ids = evidence.get("observation_ids")
        if (
            not isinstance(ids, list)
            or not ids
            or any(not isinstance(item, str) or not item.strip() for item in ids)
            or len(set(ids)) != len(ids)
        ):
            raise ValueError("invalid_angle_observation_ids")
        interval = evidence.get("sensor_interval_ns")
        if (
            evidence.get("event") != "impact"
            or evidence.get("clock_domain") != "camera_sensor"
            or not isinstance(interval, list)
            or len(interval) != 2
            or not all(_integer(t) for t in interval)
            or interval[0] <= 0
            or interval[0] > interval[1]
            or capture_sensor_interval_ns is None
            or not capture_sensor_interval_ns[0]
            <= interval[0]
            <= interval[1]
            <= capture_sensor_interval_ns[1]
        ):
            raise ValueError("angle_time_not_bound_to_capture")
        if evidence.get("reference_point") != REFERENCE_POINTS.get(name):
            raise ValueError("angle_reference_point_mismatch")
        if entry.get("unit") != "deg" or entry.get("frame") not in (
            "unit_boresight",
            "target_line",
        ):
            raise ValueError("unknown_angle_units_or_frame")
        value = entry.get("value")
        sigma = evidence.get("sigma_deg")
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or isinstance(sigma, bool)
            or not isinstance(sigma, (int, float))
            or not math.isfinite(sigma)
            or sigma <= 0
        ):
            raise ValueError("invalid_angle_value_or_uncertainty")
        source = entry.get("source")
        if not isinstance(source, str) or not source.strip():
            raise ValueError("missing_angle_producer")
        note = entry.get("note")
        if note is not None and not isinstance(note, str):
            raise ValueError("invalid_angle_note")
        return cls(
            name,
            float(value),
            source,
            entry["status"],
            entry["frame"],
            note,
            shot_number,
            capture_npz_sha256,
            tuple(ids),
            tuple(interval),
            evidence["reference_point"],
            float(sigma),
        )

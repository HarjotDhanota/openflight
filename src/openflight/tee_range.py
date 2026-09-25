"""Versioned evidence and promotion policy for radar-to-tee slant range."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence

SCHEMA = "openflight.tee_range.v2"
SCHEMA_VERSION = 2
LEGACY_SCHEMA = "openflight.tee_range.v1"
QUALIFICATION_SCHEMA = "openflight.tee_range_qualification.v2"
QUALIFICATION_SCHEMA_VERSION = 2
PROMOTION_POLICY_VERSION = "tee-range-promotion-v1"
UNRESOLVED_LEGACY_REASON = "legacy_session_has_no_tee_range_contract"
SOURCE_GROUPS = frozenset({"camera", "iwr", "manual_truth"})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _canonical_json(payload: Mapping) -> bytes:
    return json.dumps(dict(payload), sort_keys=True, allow_nan=False, separators=(",", ":")).encode(
        "utf-8"
    )


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw_json(item) for item in value]
    return value


def _frozen_json(value: Any, name: str) -> Any:
    try:
        detached = json.loads(json.dumps(_thaw_json(value), allow_nan=False))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be finite JSON data") from error

    def freeze(item: Any) -> Any:
        if isinstance(item, dict):
            return MappingProxyType({key: freeze(child) for key, child in item.items()})
        if isinstance(item, list):
            return tuple(freeze(child) for child in item)
        return item

    return freeze(detached)


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


def _sha256(value: str, name: str) -> str:
    result = str(value).strip().lower()
    if _SHA256.fullmatch(result) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256")
    return result


@dataclass(frozen=True)
class TeeRangeCandidate:
    """One range observation, including the sensor family that produced it."""

    candidate_id: str
    source: str
    source_group: str
    radar_slant_range_m: float | None
    uncertainty_m: float | None
    evidence: Mapping
    selectable: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.selectable, bool):
            raise ValueError("selectable must be a boolean")
        object.__setattr__(self, "candidate_id", _required_text(self.candidate_id, "candidate_id"))
        object.__setattr__(self, "source", _required_text(self.source, "source"))
        group = _required_text(self.source_group, "source_group")
        if group not in SOURCE_GROUPS:
            raise ValueError(f"unsupported source_group: {group}")
        object.__setattr__(self, "source_group", group)
        if self.radar_slant_range_m is None or self.uncertainty_m is None:
            if (
                self.selectable
                or self.radar_slant_range_m is not None
                or self.uncertainty_m is not None
            ):
                raise ValueError(
                    "evidence-only candidates require null range/uncertainty and selectable=false"
                )
        else:
            object.__setattr__(
                self,
                "radar_slant_range_m",
                _positive_finite(self.radar_slant_range_m, "radar_slant_range_m"),
            )
            object.__setattr__(
                self, "uncertainty_m", _positive_finite(self.uncertainty_m, "uncertainty_m")
            )
        evidence = _frozen_json(self.evidence, "evidence")
        if not isinstance(evidence, Mapping):
            raise ValueError("evidence must be a JSON object")
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
            "evidence": _thaw_json(self.evidence),
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
            selectable=payload.get("selectable", True),
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
class TeeRangeQualification:  # pylint: disable=too-many-instance-attributes
    """Frozen identities and limits required before automatic range promotion."""

    rig_geometry_sha256: str
    camera_calibration_sha256: str
    camera_placement_sha256: str
    camera_mode_profile_sha256: str
    camera_arm_id: str
    iwr_firmware_sha256: str
    iwr_capture_config_sha256: str
    iwr_profile_sha256: str
    iwr_range_calibration_sha256: str
    policy_version: str
    scope: str
    accuracy_qualified: bool
    plausible_range_m: tuple[float, float]
    max_camera_uncertainty_m: float
    max_iwr_uncertainty_m: float
    max_absolute_residual_m: float
    max_normalized_residual_sigma: float

    def __post_init__(self) -> None:
        for name in (
            "rig_geometry_sha256",
            "camera_calibration_sha256",
            "camera_placement_sha256",
            "camera_mode_profile_sha256",
            "iwr_firmware_sha256",
            "iwr_capture_config_sha256",
            "iwr_profile_sha256",
            "iwr_range_calibration_sha256",
        ):
            object.__setattr__(self, name, _sha256(getattr(self, name), name))
        object.__setattr__(
            self, "camera_arm_id", _required_text(self.camera_arm_id, "camera_arm_id")
        )
        object.__setattr__(
            self, "policy_version", _required_text(self.policy_version, "policy_version")
        )
        object.__setattr__(self, "scope", _required_text(self.scope, "scope"))
        if not isinstance(self.accuracy_qualified, bool):
            raise ValueError("accuracy_qualified must be a boolean")
        interval = tuple(float(value) for value in self.plausible_range_m)
        if (
            len(interval) != 2
            or not all(math.isfinite(value) for value in interval)
            or not 0.0 < interval[0] < interval[1]
        ):
            raise ValueError("plausible_range_m must be a positive increasing interval")
        object.__setattr__(self, "plausible_range_m", interval)
        for name in (
            "max_camera_uncertainty_m",
            "max_iwr_uncertainty_m",
            "max_absolute_residual_m",
            "max_normalized_residual_sigma",
        ):
            object.__setattr__(self, name, _positive_finite(getattr(self, name), name))

    def to_dict(self) -> dict:
        """Return the complete qualification artifact."""
        return {
            "schema": QUALIFICATION_SCHEMA,
            "schema_version": QUALIFICATION_SCHEMA_VERSION,
            "accuracy_qualified": self.accuracy_qualified,
            "scope": self.scope,
            "identities": {
                "rig_geometry_sha256": self.rig_geometry_sha256,
                "camera_calibration_sha256": self.camera_calibration_sha256,
                "camera_placement_sha256": self.camera_placement_sha256,
                "camera_mode_profile_sha256": self.camera_mode_profile_sha256,
                "camera_arm_id": self.camera_arm_id,
                "iwr_firmware_sha256": self.iwr_firmware_sha256,
                "iwr_capture_config_sha256": self.iwr_capture_config_sha256,
                "iwr_profile_sha256": self.iwr_profile_sha256,
                "iwr_range_calibration_sha256": self.iwr_range_calibration_sha256,
            },
            "policy": self.policy_dict(),
        }

    def policy_dict(self) -> dict:
        """Return only the frozen numerical promotion policy."""
        return {
            "version": self.policy_version,
            "scope": self.scope,
            "required_camera_arm_id": "arm5",
            "selected_source": "iwr_static_profile_difference",
            "plausible_range_m": list(self.plausible_range_m),
            "max_camera_uncertainty_m": self.max_camera_uncertainty_m,
            "max_iwr_uncertainty_m": self.max_iwr_uncertainty_m,
            "max_absolute_residual_m": self.max_absolute_residual_m,
            "max_normalized_residual_sigma": self.max_normalized_residual_sigma,
        }

    @property
    def artifact_sha256(self) -> str:
        """Digest all identities, qualification state and policy limits."""
        return hashlib.sha256(_canonical_json(self.to_dict())).hexdigest()

    @property
    def policy_sha256(self) -> str:
        """Digest the policy independently from the qualified hardware identities."""
        return hashlib.sha256(_canonical_json(self.policy_dict())).hexdigest()

    @classmethod
    def from_dict(cls, payload: Mapping) -> TeeRangeQualification:
        """Validate and reconstruct a versioned qualification artifact."""
        if (
            payload.get("schema") != QUALIFICATION_SCHEMA
            or payload.get("schema_version") != QUALIFICATION_SCHEMA_VERSION
        ):
            raise ValueError("unsupported tee-range qualification schema")
        if set(payload) != {
            "schema",
            "schema_version",
            "accuracy_qualified",
            "scope",
            "identities",
            "policy",
        }:
            raise ValueError("tee-range qualification contains unsupported fields")
        identities = payload["identities"]
        policy = payload["policy"]
        if set(identities) != {
            "rig_geometry_sha256",
            "camera_calibration_sha256",
            "camera_placement_sha256",
            "camera_mode_profile_sha256",
            "camera_arm_id",
            "iwr_firmware_sha256",
            "iwr_capture_config_sha256",
            "iwr_profile_sha256",
            "iwr_range_calibration_sha256",
        }:
            raise ValueError("tee-range qualification identities do not match the schema")
        expected_policy_keys = {
            "version",
            "scope",
            "required_camera_arm_id",
            "selected_source",
            "plausible_range_m",
            "max_camera_uncertainty_m",
            "max_iwr_uncertainty_m",
            "max_absolute_residual_m",
            "max_normalized_residual_sigma",
        }
        if set(policy) != expected_policy_keys:
            raise ValueError("tee-range qualification policy fields do not match the schema")
        if policy["scope"] != payload["scope"]:
            raise ValueError("tee-range qualification scope does not match its policy")
        if policy["required_camera_arm_id"] != "arm5":
            raise ValueError("tee-range qualification policy must require camera arm5")
        if policy["selected_source"] != "iwr_static_profile_difference":
            raise ValueError("tee-range qualification policy must select static IWR")
        return cls(
            rig_geometry_sha256=identities["rig_geometry_sha256"],
            camera_calibration_sha256=identities["camera_calibration_sha256"],
            camera_placement_sha256=identities["camera_placement_sha256"],
            camera_mode_profile_sha256=identities["camera_mode_profile_sha256"],
            camera_arm_id=identities["camera_arm_id"],
            iwr_firmware_sha256=identities["iwr_firmware_sha256"],
            iwr_capture_config_sha256=identities["iwr_capture_config_sha256"],
            iwr_profile_sha256=identities["iwr_profile_sha256"],
            iwr_range_calibration_sha256=identities["iwr_range_calibration_sha256"],
            policy_version=policy["version"],
            scope=payload["scope"],
            accuracy_qualified=payload["accuracy_qualified"],
            plausible_range_m=tuple(policy["plausible_range_m"]),
            max_camera_uncertainty_m=policy["max_camera_uncertainty_m"],
            max_iwr_uncertainty_m=policy["max_iwr_uncertainty_m"],
            max_absolute_residual_m=policy["max_absolute_residual_m"],
            max_normalized_residual_sigma=policy["max_normalized_residual_sigma"],
        )


@dataclass(frozen=True)
class TeeRangeSolution:  # pylint: disable=too-many-instance-attributes
    """Resolved or explicitly unresolved radar-origin range evidence."""

    status: str
    reason: str
    candidates: tuple[TeeRangeCandidate, ...]
    selected_candidate_id: str | None = None
    selected_range_m: float | None = None
    selected_uncertainty_m: float | None = None
    supporting_source_groups: tuple[str, ...] = ()
    evidence_epoch_id: str | None = None
    qualification_sha256: str | None = None
    policy_sha256: str | None = None
    agreement_residual_m: float | None = None
    agreement_normalized_sigma: float | None = None
    _policy_validated: bool = field(default=False, repr=False, compare=False)

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
        policy_fields = (
            self.evidence_epoch_id,
            self.qualification_sha256,
            self.policy_sha256,
            self.agreement_residual_m,
            self.agreement_normalized_sigma,
        )
        if self.status == "unresolved":
            if any(
                value is not None
                for value in (
                    self.selected_candidate_id,
                    self.selected_range_m,
                    self.selected_uncertainty_m,
                    *policy_fields,
                )
            ):
                raise ValueError("unresolved range cannot contain a selected value or promotion")
            if self.supporting_source_groups:
                raise ValueError("unresolved range cannot contain supporting groups")
            if any(item.selectable for item in ordered):
                raise ValueError("unresolved range cannot contain selectable candidates")
            return
        if any(value is None for value in policy_fields):
            raise ValueError(
                "resolved range requires complete qualification and agreement evidence"
            )
        if self._policy_validated is not True:
            raise ValueError("resolved range must be created by resolve_qualified_tee_range")
        selected = next(
            (item for item in ordered if item.candidate_id == self.selected_candidate_id), None
        )
        if selected is None or not selected.selectable:
            raise ValueError("resolved range must select a promoted candidate")
        if selected.source != "iwr_static_profile_difference":
            raise ValueError("resolved range must select qualified static IWR")
        if self.selected_range_m != selected.radar_slant_range_m:
            raise ValueError("selected range must match its candidate")
        if self.selected_uncertainty_m != selected.uncertainty_m:
            raise ValueError("selected uncertainty must match its candidate")
        if self.supporting_source_groups != ("camera", "iwr"):
            raise ValueError("resolved range requires camera and IWR support")
        selectable = [item for item in ordered if item.selectable]
        if len(selectable) != 2 or {item.source_group for item in selectable} != {"camera", "iwr"}:
            raise ValueError("resolved range requires exactly two promoted sensor candidates")
        for item in selectable:
            promotion = item.evidence.get("promotion")
            if not isinstance(promotion, Mapping):
                raise ValueError("promoted candidates require promotion evidence")
            if (
                promotion.get("epoch_id") != self.evidence_epoch_id
                or promotion.get("qualification_sha256") != self.qualification_sha256
                or promotion.get("policy_sha256") != self.policy_sha256
            ):
                raise ValueError("candidate promotion does not match the solution policy")

    @classmethod
    def unresolved(
        cls, candidates: Iterable[TeeRangeCandidate] = (), *, reason: str
    ) -> TeeRangeSolution:
        """Build an explicit pending solution with all estimator outputs disabled."""
        disabled = tuple(replace(item, selectable=False) for item in candidates)
        return cls(status="unresolved", reason=reason, candidates=disabled)

    @classmethod
    def resolved(
        cls,
        selected: TeeRangeCandidate,
        *,
        supporting: Iterable[TeeRangeCandidate],
        reason: str = "independently_supported",
    ) -> TeeRangeSolution:
        """Reject the former unqualified construction path."""
        del selected, supporting, reason
        raise ValueError("use resolve_qualified_tee_range with a qualification artifact")

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
            "evidence_epoch_id": self.evidence_epoch_id,
            "qualification_sha256": self.qualification_sha256,
            "policy_sha256": self.policy_sha256,
            "agreement_residual_m": self.agreement_residual_m,
            "agreement_normalized_sigma": self.agreement_normalized_sigma,
            "candidates": [item.to_dict() for item in self.candidates],
        }

    @classmethod
    def from_dict(
        cls,
        payload: Mapping,
        *,
        qualification: TeeRangeQualification | None = None,
        epoch_id: str | None = None,
    ) -> TeeRangeSolution:
        """Validate a solution and require qualification context for resolved records."""
        schema = payload.get("schema")
        version = payload.get("schema_version")
        if schema == LEGACY_SCHEMA and version == 1:
            candidates = tuple(
                TeeRangeCandidate.from_dict(item) for item in payload.get("candidates", [])
            )
            if payload.get("status") == "resolved":
                return cls.unresolved(
                    candidates, reason="legacy_resolved_range_requires_requalification"
                )
            return cls.unresolved(candidates, reason=payload["reason"])
        if schema != SCHEMA or version != SCHEMA_VERSION:
            raise ValueError("unsupported tee-range schema")
        expected_fields = {
            "schema",
            "schema_version",
            "status",
            "reason",
            "selected_candidate_id",
            "selected_range_m",
            "selected_uncertainty_m",
            "supporting_source_groups",
            "evidence_epoch_id",
            "qualification_sha256",
            "policy_sha256",
            "agreement_residual_m",
            "agreement_normalized_sigma",
            "candidates",
        }
        if set(payload) != expected_fields:
            raise ValueError("tee-range solution fields do not match the schema")
        candidates = tuple(
            TeeRangeCandidate.from_dict(item) for item in payload.get("candidates", [])
        )
        if payload["status"] == "resolved":
            if qualification is not None and epoch_id is not None:
                return _validated_loaded_solution(payload, candidates, qualification, epoch_id)
            return cls.unresolved(
                (_without_promotion(item) for item in candidates),
                reason="resolved_range_requires_qualification_context",
            )
        return cls(
            status=payload["status"],
            reason=payload["reason"],
            candidates=candidates,
            selected_candidate_id=payload.get("selected_candidate_id"),
            selected_range_m=payload.get("selected_range_m"),
            selected_uncertainty_m=payload.get("selected_uncertainty_m"),
            supporting_source_groups=tuple(payload.get("supporting_source_groups", [])),
            evidence_epoch_id=payload.get("evidence_epoch_id"),
            qualification_sha256=payload.get("qualification_sha256"),
            policy_sha256=payload.get("policy_sha256"),
            agreement_residual_m=payload.get("agreement_residual_m"),
            agreement_normalized_sigma=payload.get("agreement_normalized_sigma"),
        )


def _qualification_facts(candidate: TeeRangeCandidate) -> Mapping[str, Any]:
    facts = candidate.evidence.get("qualification")
    return facts if isinstance(facts, Mapping) else {}


def _unresolved(candidates: Sequence[TeeRangeCandidate], reason: str) -> TeeRangeSolution:
    return TeeRangeSolution.unresolved(candidates, reason=reason)


def _exact_fact(facts: Mapping, name: str, expected: Any, prefix: str) -> str | None:
    if facts.get(name) != expected:
        label = name.removesuffix("_sha256")
        if label.startswith(f"{prefix}_"):
            label = label[len(prefix) + 1 :]
        return f"{prefix}_{label}_mismatch"
    return None


def _promote_candidate(
    candidate: TeeRangeCandidate,
    *,
    epoch_id: str,
    qualification: TeeRangeQualification,
) -> TeeRangeCandidate:
    evidence = dict(candidate.evidence)
    evidence["promotion"] = {
        "epoch_id": epoch_id,
        "qualification_sha256": qualification.artifact_sha256,
        "policy_sha256": qualification.policy_sha256,
        "policy_version": qualification.policy_version,
    }
    return replace(candidate, selectable=True, evidence=evidence)


def _without_promotion(candidate: TeeRangeCandidate) -> TeeRangeCandidate:
    evidence = _thaw_json(candidate.evidence)
    evidence.pop("promotion", None)
    return replace(candidate, selectable=False, evidence=evidence)


def resolve_qualified_tee_range(
    epoch_id: str,
    candidates: Iterable[TeeRangeCandidate],
    qualification: TeeRangeQualification,
) -> TeeRangeSolution:  # pylint: disable=too-many-return-statements,too-many-branches
    """Promote one qualified static-IWR range after independent camera agreement."""
    epoch = _required_text(epoch_id, "epoch_id")
    observed = tuple(replace(item, selectable=False) for item in candidates)
    if not qualification.accuracy_qualified:
        return _unresolved(observed, "qualification_not_accuracy_qualified")
    if qualification.policy_version != PROMOTION_POLICY_VERSION:
        return _unresolved(observed, "qualification_policy_version_mismatch")
    if qualification.camera_arm_id != "arm5":
        return _unresolved(observed, "qualification_requires_camera_arm5")

    camera_pool = [item for item in observed if item.source_group == "camera"]
    cameras = [
        item
        for item in camera_pool
        if _qualification_facts(item).get("status") == "accepted"
        and item.radar_slant_range_m is not None
    ]
    if not cameras:
        return _unresolved(observed, "qualified_camera_candidate_missing")
    if len(cameras) != 1:
        return _unresolved(observed, "qualified_camera_candidate_ambiguous")
    camera = cameras[0]
    camera_facts = _qualification_facts(camera)

    static_pool = [
        item
        for item in observed
        if item.source_group == "iwr"
        and item.source == "iwr_static_profile_difference"
        and item.evidence.get("method") == "pre_mti_empty_vs_ball_present"
    ]
    iwrs = [
        item
        for item in static_pool
        if _qualification_facts(item).get("status") == "accepted"
        and item.radar_slant_range_m is not None
    ]
    if not iwrs:
        return _unresolved(observed, "qualified_static_iwr_candidate_missing")
    if len(iwrs) != 1:
        return _unresolved(observed, "qualified_static_iwr_candidate_ambiguous")
    iwr = iwrs[0]
    iwr_facts = _qualification_facts(iwr)

    if camera_facts.get("epoch_id") != epoch:
        return _unresolved(observed, "camera_evidence_epoch_mismatch")
    if iwr_facts.get("epoch_id") != epoch:
        return _unresolved(observed, "iwr_evidence_epoch_mismatch")
    if camera_facts.get("accuracy_qualified") is not True:
        return _unresolved(observed, "camera_evidence_not_accuracy_qualified")
    if iwr_facts.get("accuracy_qualified") is not True:
        return _unresolved(observed, "iwr_evidence_not_accuracy_qualified")
    if camera_facts.get("camera_arm_id") != "arm5":
        return _unresolved(observed, "camera_evidence_requires_arm5")
    if camera_facts.get("scope") != qualification.scope:
        return _unresolved(observed, "camera_scope_mismatch")
    if iwr_facts.get("scope") != qualification.scope:
        return _unresolved(observed, "iwr_scope_mismatch")

    for name, expected in (
        ("rig_geometry_sha256", qualification.rig_geometry_sha256),
        ("camera_calibration_sha256", qualification.camera_calibration_sha256),
        ("camera_placement_sha256", qualification.camera_placement_sha256),
        ("camera_mode_profile_sha256", qualification.camera_mode_profile_sha256),
    ):
        if reason := _exact_fact(camera_facts, name, expected, "camera"):
            return _unresolved(observed, reason)
    for name, expected in (
        ("rig_geometry_sha256", qualification.rig_geometry_sha256),
        ("iwr_firmware_sha256", qualification.iwr_firmware_sha256),
        ("iwr_capture_config_sha256", qualification.iwr_capture_config_sha256),
        ("iwr_profile_sha256", qualification.iwr_profile_sha256),
        ("iwr_range_calibration_sha256", qualification.iwr_range_calibration_sha256),
    ):
        if reason := _exact_fact(iwr_facts, name, expected, "iwr"):
            return _unresolved(observed, reason)

    camera_dependencies = ("manual_range_used", "iwr_range_used", "moving_iwr_used")
    if any(camera_facts.get(name) is not False for name in camera_dependencies):
        return _unresolved(observed, "camera_evidence_not_independent")
    iwr_dependencies = ("manual_range_used", "camera_range_used", "moving_iwr_used")
    if any(iwr_facts.get(name) is not False for name in iwr_dependencies):
        return _unresolved(observed, "iwr_evidence_not_independent")

    if (
        camera.uncertainty_m is None
        or camera.uncertainty_m > qualification.max_camera_uncertainty_m
    ):
        return _unresolved(observed, "camera_uncertainty_exceeds_policy")
    if iwr.uncertainty_m is None or iwr.uncertainty_m > qualification.max_iwr_uncertainty_m:
        return _unresolved(observed, "iwr_uncertainty_exceeds_policy")
    low, high = qualification.plausible_range_m
    if camera.radar_slant_range_m is None or not low <= camera.radar_slant_range_m <= high:
        return _unresolved(observed, "camera_range_outside_policy_interval")
    if iwr.radar_slant_range_m is None or not low <= iwr.radar_slant_range_m <= high:
        return _unresolved(observed, "iwr_range_outside_policy_interval")

    residual = abs(camera.radar_slant_range_m - iwr.radar_slant_range_m)
    combined_uncertainty = math.hypot(camera.uncertainty_m, iwr.uncertainty_m)
    normalized = residual / combined_uncertainty
    if residual > qualification.max_absolute_residual_m:
        return _unresolved(observed, "absolute_residual_exceeds_policy")
    if normalized > qualification.max_normalized_residual_sigma:
        return _unresolved(observed, "normalized_residual_exceeds_policy")

    promoted_camera = _promote_candidate(camera, epoch_id=epoch, qualification=qualification)
    promoted_iwr = _promote_candidate(iwr, epoch_id=epoch, qualification=qualification)
    selected_ids = {camera.candidate_id, iwr.candidate_id}
    retained = tuple(item for item in observed if item.candidate_id not in selected_ids)
    return TeeRangeSolution(
        status="resolved",
        reason="qualified_static_iwr_supported_by_camera_arm5",
        candidates=(*retained, promoted_camera, promoted_iwr),
        selected_candidate_id=promoted_iwr.candidate_id,
        selected_range_m=promoted_iwr.radar_slant_range_m,
        selected_uncertainty_m=promoted_iwr.uncertainty_m,
        supporting_source_groups=("camera", "iwr"),
        evidence_epoch_id=epoch,
        qualification_sha256=qualification.artifact_sha256,
        policy_sha256=qualification.policy_sha256,
        agreement_residual_m=residual,
        agreement_normalized_sigma=normalized,
        _policy_validated=True,
    )


def _validated_loaded_solution(
    payload: Mapping,
    candidates: Sequence[TeeRangeCandidate],
    qualification: TeeRangeQualification,
    epoch_id: str,
) -> TeeRangeSolution:
    rebuilt = resolve_qualified_tee_range(
        epoch_id,
        (_without_promotion(item) for item in candidates),
        qualification,
    )
    if rebuilt.status != "resolved" or _canonical_json(rebuilt.to_dict()) != _canonical_json(
        payload
    ):
        raise ValueError("resolved tee-range solution failed policy validation")
    return rebuilt


def write_solution(path: str | Path, solution: TeeRangeSolution) -> None:
    """Atomically persist a range contract beside the session evidence."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=destination.parent, delete=False) as handle:
            temporary = handle.name
            handle.write(_canonical_json(solution.to_dict()) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        temporary = None
        _fsync_directory(destination.parent)
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


def load_solution(path: str | Path) -> TeeRangeSolution:
    """Load a standalone contract without trusting an unbound resolved claim."""
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
    "PROMOTION_POLICY_VERSION",
    "TeeRangeCandidate",
    "TeeRangeQualification",
    "TeeRangeSolution",
    "camera_observation_to_radar_slant_m",
    "load_solution",
    "manual_truth_candidate",
    "resolve_qualified_tee_range",
    "write_solution",
]

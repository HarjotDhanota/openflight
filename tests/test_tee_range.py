"""Versioned tee-range evidence stays explicit, independent and replayable."""

import json

import pytest

from openflight import session_bundle
from openflight.tee_range import (
    PROMOTION_POLICY_VERSION,
    TeeRangeCandidate,
    TeeRangeQualification,
    TeeRangeSolution,
    camera_observation_to_radar_slant_m,
    load_solution,
    manual_truth_candidate,
    resolve_qualified_tee_range,
    write_solution,
)


def candidate(candidate_id, group, value, uncertainty=0.04):
    return TeeRangeCandidate(
        candidate_id=candidate_id,
        source=f"{group}_method",
        source_group=group,
        radar_slant_range_m=value,
        uncertainty_m=uncertainty,
        evidence={"capture_sha256": candidate_id * 64},
    )


def test_manual_truth_is_retained_but_never_silently_selected():
    manual = manual_truth_candidate(1.524, evidence={"method": "tape"})
    solution = TeeRangeSolution.unresolved([manual], reason="automatic_range_unavailable")

    document = solution.to_dict()

    assert document["schema"] == "openflight.tee_range.v2"
    assert document["status"] == "unresolved"
    assert document["selected_range_m"] is None
    assert document["candidates"][0]["source_group"] == "manual_truth"
    assert document["candidates"][0]["selectable"] is False


def test_evidence_only_rejection_round_trips_without_a_fabricated_range():
    rejected = TeeRangeCandidate(
        candidate_id="camera-rejected-1",
        source="nominal_uncalibrated",
        source_group="camera",
        radar_slant_range_m=None,
        uncertainty_m=None,
        selectable=False,
        evidence={"rejection_reason": "ray reaches plane behind camera"},
    )

    assert TeeRangeCandidate.from_dict(rejected.to_dict()) == rejected


def test_same_sensor_group_cannot_resolve_the_range():
    size = candidate("a", "camera", 1.50)
    floor = candidate("b", "camera", 1.52)

    with pytest.raises(ValueError, match="resolve_qualified_tee_range"):
        TeeRangeSolution.resolved(size, supporting=[floor])


def test_manual_truth_does_not_count_as_independent_support():
    size = candidate("a", "camera", 1.50)
    tape = manual_truth_candidate(1.51)

    with pytest.raises(ValueError, match="resolve_qualified_tee_range"):
        TeeRangeSolution.resolved(size, supporting=[tape])


HASHES = {
    name: character * 64
    for name, character in (
        ("rig_geometry_sha256", "1"),
        ("camera_calibration_sha256", "2"),
        ("iwr_firmware_sha256", "3"),
        ("iwr_capture_config_sha256", "4"),
        ("iwr_profile_sha256", "5"),
        ("iwr_range_calibration_sha256", "6"),
    )
}


def qualification(**updates):
    values = {
        **HASHES,
        "camera_arm_id": "arm5",
        "policy_version": PROMOTION_POLICY_VERSION,
        "scope": "tester_setup",
        "accuracy_qualified": True,
        "plausible_range_m": (0.75, 3.0),
        "max_camera_uncertainty_m": 0.08,
        "max_iwr_uncertainty_m": 0.06,
        "max_absolute_residual_m": 0.10,
        "max_normalized_residual_sigma": 2.0,
    }
    values.update(updates)
    return TeeRangeQualification(**values)


def qualified_camera(epoch_id="epoch-a", value=1.50, uncertainty=0.05, **updates):
    facts = {
        "epoch_id": epoch_id,
        "status": "accepted",
        "accuracy_qualified": True,
        "rig_geometry_sha256": HASHES["rig_geometry_sha256"],
        "camera_calibration_sha256": HASHES["camera_calibration_sha256"],
        "camera_arm_id": "arm5",
        "scope": "tester_setup",
        "manual_range_used": False,
        "iwr_range_used": False,
        "moving_iwr_used": False,
    }
    facts.update(updates)
    return TeeRangeCandidate(
        candidate_id="camera",
        source="camera_reference_ball_floor_plane",
        source_group="camera",
        radar_slant_range_m=value,
        uncertainty_m=uncertainty,
        selectable=False,
        evidence={"qualification": facts},
    )


def qualified_iwr(epoch_id="epoch-a", value=1.53, uncertainty=0.03, **updates):
    facts = {
        "epoch_id": epoch_id,
        "status": "accepted",
        "accuracy_qualified": True,
        "rig_geometry_sha256": HASHES["rig_geometry_sha256"],
        "iwr_firmware_sha256": HASHES["iwr_firmware_sha256"],
        "iwr_capture_config_sha256": HASHES["iwr_capture_config_sha256"],
        "iwr_profile_sha256": HASHES["iwr_profile_sha256"],
        "iwr_range_calibration_sha256": HASHES["iwr_range_calibration_sha256"],
        "scope": "tester_setup",
        "manual_range_used": False,
        "camera_range_used": False,
        "moving_iwr_used": False,
    }
    facts.update(updates)
    return TeeRangeCandidate(
        candidate_id="iwr",
        source="iwr_static_profile_difference",
        source_group="iwr",
        radar_slant_range_m=value,
        uncertainty_m=uncertainty,
        selectable=False,
        evidence={
            "method": "pre_mti_empty_vs_ball_present",
            "qualification": facts,
        },
    )


def test_cross_sensor_solution_is_deterministic_and_standalone_load_is_safe(tmp_path):
    camera = qualified_camera()
    radar = qualified_iwr()
    first = resolve_qualified_tee_range("epoch-a", [camera, radar], qualification())
    second = resolve_qualified_tee_range("epoch-a", [camera, radar], qualification())
    path = tmp_path / "tee_range.json"

    write_solution(path, first)

    loaded = load_solution(path)

    assert first.to_dict() == second.to_dict()
    assert loaded.status == "unresolved"
    assert loaded.reason == "resolved_range_requires_qualification_context"
    assert first.status == "resolved"
    assert first.selected_range_m == 1.53
    assert first.selected_candidate_id == "iwr"
    assert first.supporting_source_groups == ("camera", "iwr")
    assert first.agreement_residual_m == pytest.approx(0.03)
    assert first.agreement_normalized_sigma == pytest.approx(0.03 / (0.05**2 + 0.03**2) ** 0.5)
    assert first.policy_sha256 == qualification().policy_sha256


def test_candidate_evidence_is_deeply_frozen_and_serialization_is_detached():
    facts = qualified_camera().evidence["qualification"]
    external = {"qualification": dict(facts), "nested": {"values": [1, 2]}}
    camera = TeeRangeCandidate(
        candidate_id="camera-detached",
        source="camera_reference_ball_floor_plane",
        source_group="camera",
        radar_slant_range_m=1.5,
        uncertainty_m=0.05,
        selectable=False,
        evidence=external,
    )

    external["qualification"]["iwr_range_used"] = True
    external["nested"]["values"].append(3)
    serialized = camera.to_dict()
    serialized["evidence"]["qualification"]["iwr_range_used"] = True
    serialized["evidence"]["nested"]["values"].append(4)

    assert camera.evidence["qualification"]["iwr_range_used"] is False
    assert camera.evidence["nested"]["values"] == (1, 2)
    with pytest.raises(TypeError):
        camera.evidence["new"] = "mutation"
    with pytest.raises(TypeError):
        camera.evidence["qualification"]["new"] = "mutation"


def test_resolved_solution_cannot_change_after_external_evidence_mutation():
    camera = qualified_camera()
    radar = qualified_iwr()
    solution = resolve_qualified_tee_range("epoch-a", [camera, radar], qualification())
    before = solution.to_dict()

    exported = solution.to_dict()
    promoted = next(item for item in exported["candidates"] if item["candidate_id"] == "camera")
    promoted["evidence"]["qualification"]["iwr_range_used"] = True
    promoted["evidence"]["promotion"]["policy_sha256"] = "f" * 64

    assert solution.to_dict() == before


def test_legacy_resolved_factory_cannot_bypass_qualification():
    with pytest.raises(ValueError, match="resolve_qualified_tee_range"):
        TeeRangeSolution.resolved(
            candidate("camera", "camera", 1.5),
            supporting=[candidate("iwr", "iwr", 1.5)],
        )


def test_direct_resolved_construction_cannot_bypass_policy():
    with pytest.raises(ValueError, match="resolve_qualified_tee_range"):
        TeeRangeSolution(
            status="resolved",
            reason="forged",
            candidates=(),
            evidence_epoch_id="epoch-a",
            qualification_sha256="a" * 64,
            policy_sha256="b" * 64,
            agreement_residual_m=0.0,
            agreement_normalized_sigma=0.0,
        )


@pytest.mark.parametrize(
    ("candidates", "artifact", "reason"),
    [
        ([qualified_iwr()], qualification(), "qualified_camera_candidate_missing"),
        ([qualified_camera()], qualification(), "qualified_static_iwr_candidate_missing"),
        (
            [qualified_camera(epoch_id="other"), qualified_iwr()],
            qualification(),
            "camera_evidence_epoch_mismatch",
        ),
        (
            [qualified_camera(), qualified_iwr(epoch_id="other")],
            qualification(),
            "iwr_evidence_epoch_mismatch",
        ),
        (
            [qualified_camera(), qualified_iwr()],
            qualification(accuracy_qualified=False),
            "qualification_not_accuracy_qualified",
        ),
        (
            [qualified_camera(), qualified_iwr()],
            qualification(camera_arm_id="arm6"),
            "qualification_requires_camera_arm5",
        ),
        (
            [qualified_camera(camera_calibration_sha256="a" * 64), qualified_iwr()],
            qualification(),
            "camera_calibration_mismatch",
        ),
        (
            [qualified_camera(iwr_range_used=True), qualified_iwr()],
            qualification(),
            "camera_evidence_not_independent",
        ),
        (
            [qualified_camera(), qualified_iwr(moving_iwr_used=True)],
            qualification(),
            "iwr_evidence_not_independent",
        ),
        (
            [qualified_camera(uncertainty=0.09), qualified_iwr()],
            qualification(),
            "camera_uncertainty_exceeds_policy",
        ),
        (
            [qualified_camera(), qualified_iwr(uncertainty=0.07)],
            qualification(),
            "iwr_uncertainty_exceeds_policy",
        ),
        (
            [qualified_camera(value=0.5), qualified_iwr(value=0.52)],
            qualification(),
            "camera_range_outside_policy_interval",
        ),
        (
            [qualified_camera(value=1.40), qualified_iwr(value=1.53)],
            qualification(),
            "absolute_residual_exceeds_policy",
        ),
        (
            [
                qualified_camera(value=1.49, uncertainty=0.001),
                qualified_iwr(value=1.50, uncertainty=0.001),
            ],
            qualification(),
            "normalized_residual_exceeds_policy",
        ),
    ],
)
def test_resolver_returns_explicit_unresolved_for_failed_gates(candidates, artifact, reason):
    solution = resolve_qualified_tee_range("epoch-a", candidates, artifact)

    assert solution.status == "unresolved"
    assert solution.reason == reason
    assert all(not item.selectable for item in solution.candidates)


def test_resolver_refuses_manual_or_moving_iwr_as_support():
    moving = qualified_iwr()
    moving = TeeRangeCandidate(
        candidate_id=moving.candidate_id,
        source="iwr_moving_track_independent_impact",
        source_group="iwr",
        radar_slant_range_m=moving.radar_slant_range_m,
        uncertainty_m=moving.uncertainty_m,
        selectable=False,
        evidence={**moving.evidence, "method": "moving_range_track_at_independent_impact"},
    )
    solution = resolve_qualified_tee_range(
        "epoch-a", [qualified_camera(), moving, manual_truth_candidate(1.52)], qualification()
    )

    assert solution.status == "unresolved"
    assert solution.reason == "qualified_static_iwr_candidate_missing"


def test_resolver_requires_exact_hardware_hashes_and_acceptance_facts():
    camera = qualified_camera(rig_geometry_sha256="a" * 64)
    solution = resolve_qualified_tee_range("epoch-a", [camera, qualified_iwr()], qualification())

    assert solution.reason == "camera_rig_geometry_mismatch"


def test_qualification_artifact_round_trips_with_stable_digests():
    artifact = qualification()

    rebuilt = TeeRangeQualification.from_dict(artifact.to_dict())

    assert rebuilt == artifact
    assert rebuilt.artifact_sha256 == artifact.artifact_sha256
    assert rebuilt.policy_sha256 == artifact.policy_sha256


@pytest.mark.parametrize(
    ("group", "field"),
    [
        ("camera", "rig_geometry_sha256"),
        ("camera", "camera_calibration_sha256"),
        ("iwr", "rig_geometry_sha256"),
        ("iwr", "iwr_firmware_sha256"),
        ("iwr", "iwr_capture_config_sha256"),
        ("iwr", "iwr_profile_sha256"),
        ("iwr", "iwr_range_calibration_sha256"),
    ],
)
def test_resolver_checks_every_bound_identity(group, field):
    camera = qualified_camera(**({field: "a" * 64} if group == "camera" else {}))
    iwr = qualified_iwr(**({field: "a" * 64} if group == "iwr" else {}))

    solution = resolve_qualified_tee_range("epoch-a", [camera, iwr], qualification())

    assert solution.status == "unresolved"
    assert solution.reason.endswith("_mismatch")


def test_resolver_keeps_manual_truth_but_never_promotes_it():
    tape = manual_truth_candidate(1.53)

    solution = resolve_qualified_tee_range(
        "epoch-a", [qualified_camera(), qualified_iwr(), tape], qualification()
    )

    manual = next(item for item in solution.candidates if item.source_group == "manual_truth")
    assert solution.status == "resolved"
    assert manual.selectable is False
    assert solution.selected_candidate_id == "iwr"


def test_resolver_rejects_wrong_policy_scope_acceptance_and_ambiguity():
    wrong_scope = resolve_qualified_tee_range(
        "epoch-a", [qualified_camera(scope="other"), qualified_iwr()], qualification()
    )
    rejected = resolve_qualified_tee_range(
        "epoch-a",
        [qualified_camera(status="rejected"), qualified_iwr()],
        qualification(),
    )
    second = qualified_camera()
    second = TeeRangeCandidate(
        candidate_id="camera-2",
        source=second.source,
        source_group=second.source_group,
        radar_slant_range_m=second.radar_slant_range_m,
        uncertainty_m=second.uncertainty_m,
        evidence=second.evidence,
        selectable=False,
    )
    ambiguous = resolve_qualified_tee_range(
        "epoch-a", [qualified_camera(), second, qualified_iwr()], qualification()
    )

    assert wrong_scope.reason == "camera_scope_mismatch"
    assert rejected.reason == "qualified_camera_candidate_missing"
    assert ambiguous.reason == "qualified_camera_candidate_ambiguous"


def test_qualification_loader_rejects_policy_tampering():
    payload = qualification().to_dict()
    payload["policy"]["selected_source"] = "average"

    with pytest.raises(ValueError, match="static IWR"):
        TeeRangeQualification.from_dict(payload)


def test_qualification_loader_rejects_unbound_identity_fields():
    payload = qualification().to_dict()
    payload["identities"]["unreviewed_override"] = "accepted"

    with pytest.raises(ValueError, match="identities"):
        TeeRangeQualification.from_dict(payload)


def test_legacy_resolved_document_is_downgraded_to_unresolved():
    camera = candidate("camera", "camera", 1.50)
    iwr = candidate("iwr", "iwr", 1.52)
    payload = {
        "schema": "openflight.tee_range.v1",
        "schema_version": 1,
        "status": "resolved",
        "reason": "independently_supported",
        "selected_candidate_id": "iwr",
        "selected_range_m": 1.52,
        "selected_uncertainty_m": 0.04,
        "supporting_source_groups": ["camera", "iwr"],
        "candidates": [camera.to_dict(), iwr.to_dict()],
    }

    loaded = TeeRangeSolution.from_dict(payload)

    assert loaded.status == "unresolved"
    assert loaded.reason == "legacy_resolved_range_requires_requalification"
    assert all(not item.selectable for item in loaded.candidates)


def test_camera_range_is_converted_to_the_radar_origin():
    result = camera_observation_to_radar_slant_m(
        camera_range_m=2.0,
        camera_ray_lfu=(0.0, 1.0, 0.0),
        camera_origin_lfu=(0.0, 0.03, 0.095),
        radar_origin_lfu=(0.0, 0.0, 0.051),
    )

    assert result == pytest.approx((2.03**2 + 0.044**2) ** 0.5)


def test_missing_legacy_record_loads_as_explicit_unresolved(tmp_path):
    solution = load_solution(tmp_path / "missing.json")

    assert solution.status == "unresolved"
    assert solution.reason == "legacy_session_has_no_tee_range_contract"


def test_bundle_preserves_the_exact_range_contract(tmp_path):
    tester = tmp_path / "tester"
    run = tester / "arm1" / "paired" / "run-01"
    run.mkdir(parents=True)
    solution = TeeRangeSolution.unresolved(
        [manual_truth_candidate(1.524)], reason="automatic_range_unavailable"
    )
    path = run / "tee_range.json"
    write_solution(path, solution)

    built = session_bundle.build_bundle(tmp_path, "tester", viewer=None, provenance={})
    manifest = session_bundle.validate_bundle(built["path"])

    assert any(
        entry["path"] == "tester/arm1/paired/run-01/tee_range.json" for entry in manifest["entries"]
    )
    assert json.loads(path.read_text(encoding="utf-8")) == solution.to_dict()

"""Versioned tee-range evidence stays explicit, independent and replayable."""

import json

import pytest

from openflight import session_bundle
from openflight.tee_range import (
    TeeRangeCandidate,
    TeeRangeSolution,
    camera_observation_to_radar_slant_m,
    load_solution,
    manual_truth_candidate,
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

    assert document["schema"] == "openflight.tee_range.v1"
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

    with pytest.raises(ValueError, match="independent source groups"):
        TeeRangeSolution.resolved(size, supporting=[floor])


def test_manual_truth_does_not_count_as_independent_support():
    size = candidate("a", "camera", 1.50)
    tape = manual_truth_candidate(1.51)

    with pytest.raises(ValueError, match="independent source groups"):
        TeeRangeSolution.resolved(size, supporting=[tape])


def test_cross_sensor_solution_is_deterministic_and_round_trips(tmp_path):
    camera = candidate("camera", "camera", 1.50, 0.05)
    radar = candidate("radar", "iwr", 1.53, 0.03)
    first = TeeRangeSolution.resolved(camera, supporting=[radar])
    second = TeeRangeSolution.resolved(camera, supporting=[radar])
    path = tmp_path / "tee_range.json"

    write_solution(path, first)

    assert first.to_dict() == second.to_dict() == load_solution(path).to_dict()
    assert first.selected_range_m == 1.50
    assert first.supporting_source_groups == ("camera", "iwr")


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

import json

from silhouette_poc.eval.corrected_iron import (
    append_arm_a_v2_result,
    build_corrected_iron_bundle,
    render_corrected_iron_markdown,
    revision_2_5_arm_a_verdict,
)
from silhouette_poc.eval.f1_remediation import build_remediation_cells
from silhouette_poc.eval.mesh_fidelity import build_fidelity_cells
from silhouette_poc.eval.run_arm_a_v2_iron import OUTPUT_FILENAMES as ARM_A_V2_OUTPUT_FILENAMES
from silhouette_poc.eval.run_corrected_iron import OUTPUT_FILENAMES


def test_corrected_iron_grid_is_the_frozen_f1_subset():
    baseline = build_fidelity_cells(clubs=("poc_7iron",))
    remediation = build_remediation_cells(clubs=("poc_7iron",))

    assert len(baseline) == 4
    assert len(remediation) == 4
    assert all(cell.club == "poc_7iron" and cell.n == 200 for cell in baseline + remediation)
    assert all(cell.seeds == tuple(range(20260824, 20261024)) for cell in baseline + remediation)
    assert {cell.truth_arm for cell in baseline} == {"analytic_truth", "mesh_truth"}
    assert {cell.arm for cell in remediation} == {
        "arm_b_calibrated_analytic",
        "arm_a_mesh_projection",
    }
    assert OUTPUT_FILENAMES == {
        "json": "results_f1_corrected_iron.json",
        "markdown": "RESULTS_F1_CORRECTED_IRON.md",
    }
    assert ARM_A_V2_OUTPUT_FILENAMES == {
        "json": "results_f1_corrected_iron.json",
        "markdown": "RESULTS_F1_CORRECTED_IRON.md",
        "lut": "poc_7iron_corrected_arm_a_v2_lut.npz",
    }


def _cell(label: str, solve_rate: float, median: float, p90: float) -> dict:
    return {
        "club": "poc_7iron",
        "candidate": label,
        "solve_rate": solve_rate,
        "impact_error_mm_median": median,
        "impact_error_mm_p90": p90,
        "failure_categories": {},
    }


def test_corrected_bundle_holds_driver_and_selects_only_an_iron_provisional_arm():
    old = {
        "baseline": [_cell("strobed_10us", 0.495, 4.0, 5.1)],
        "arm_b": [_cell("strobed_10us", 0.495, 4.5, 6.4)],
        "arm_b_calibration": {"calibrations": [{"fitted_radii_mm": [40.5, 22.6]}]},
        "arm_a_validation": {"passed": False},
    }
    baseline = [
        {**_cell(candidate, 1.0, 1.0, 2.0), "truth_arm": truth}
        for truth in ("analytic_truth", "mesh_truth")
        for candidate in ("strobed_10us", "ambient_500us")
    ]
    arm_b = [
        {**_cell(candidate, 0.9, 2.0, 4.0), "arm": "arm_b_calibrated_analytic"}
        for candidate in ("strobed_10us", "ambient_500us")
    ]
    arm_a = []
    validation = {"clubs": [{"club": "poc_7iron", "passed": True}]}
    manifest = {"sources": [{"source_uid": "iron", "normalization": "face-v2"}]}

    bundle = build_corrected_iron_bundle(
        baseline,
        arm_b,
        validation,
        arm_a,
        old=old,
        mesh_manifest=manifest,
        arm_b_calibration={"calibrations": [{"fitted_radii_mm": [59.9, 37.5]}]},
    )
    report = render_corrected_iron_markdown(bundle)

    assert bundle["verdict"] == {
        "driver": "HOLD_CAD_MESH",
        "iron": "IRON_B_CLEARS",
        "overall": "STOP_FOR_MAINTAINER_REVIEW",
    }
    assert "DRIVER: HOLD_CAD_MESH" in report
    assert "IRON: IRON_B_CLEARS" in report
    assert "40.500 x 22.600" in report
    assert "59.900 x 37.500" in report
    assert "Arm A: no shot taxonomy" in report
    json.dumps(bundle, allow_nan=False)


def test_revision_2_5_gate_uses_only_ambient_and_keeps_strobe_comparison_only():
    rows = [
        _cell("strobed_10us", 0.0, 999.0, 999.0),
        _cell("ambient_500us", 0.80, 12.0, 24.0),
    ]

    verdict = revision_2_5_arm_a_verdict(validation_passed=True, rows=rows)

    assert verdict == "IRON_A_V2_CLEARS_AMBIENT"


def test_revision_2_5_gate_fails_closed_before_shots_when_lut_is_invalid():
    verdict = revision_2_5_arm_a_verdict(validation_passed=False, rows=[])

    assert verdict == "IRON_A_V2_INVALID_LUT"


def test_arm_a_v2_append_preserves_accepted_verdict_and_adds_paired_report():
    old = {
        "baseline": [],
        "arm_b": [],
        "arm_b_calibration": None,
        "arm_a_validation": {"passed": False},
    }
    bundle = build_corrected_iron_bundle(
        [],
        [],
        {"clubs": [{"club": "poc_7iron", "passed": False}]},
        [],
        old=old,
        mesh_manifest={"sources": []},
    )
    accepted_hash = bundle["evaluation_hash"]
    rows = [
        {**_cell("strobed_10us", 0.0, 999.0, 999.0), "arm": "arm_a_mesh_projection"},
        {**_cell("ambient_500us", 0.81, 11.0, 23.0), "arm": "arm_a_mesh_projection"},
    ]
    validation = {
        "clubs": [
            {
                "club": "poc_7iron",
                "passed": True,
                "metrics": {
                    "centroid_error_px_p99": 0.8,
                    "covariance_error_px_p99": 0.9,
                    "contour_iou_p1": 0.96,
                },
            }
        ]
    }

    updated = append_arm_a_v2_result(bundle, validation=validation, rows=rows)
    report = render_corrected_iron_markdown(updated)

    assert updated["verdict"] == bundle["verdict"]
    assert updated["revision_2_5"]["accepted_evaluation_hash"] == accepted_hash
    assert updated["revision_2_5"]["iron_verdict"] == "IRON_A_V2_CLEARS_AMBIENT"
    assert updated["evaluation_hash"] != accepted_hash
    assert "Arm A-v2 prospective result" in report
    assert "comparison-only" in report
    assert "0.800" in report
    assert "0.900" in report
    assert "0.960" in report

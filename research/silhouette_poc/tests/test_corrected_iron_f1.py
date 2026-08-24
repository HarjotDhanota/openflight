import json

from silhouette_poc.eval.corrected_iron import (
    build_corrected_iron_bundle,
    render_corrected_iron_markdown,
)
from silhouette_poc.eval.f1_remediation import build_remediation_cells
from silhouette_poc.eval.mesh_fidelity import build_fidelity_cells
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

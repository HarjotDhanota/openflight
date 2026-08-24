"""Phase 4 end-to-end evaluation and reconciliation contracts."""

from __future__ import annotations

import json

import cv2
import pytest

from silhouette_poc.eval.e2e import (
    CANDIDATES,
    RECONCILIATION_LIMITS,
    SWEEP_AXES,
    _initialize_worker,
    build_core_cells,
    build_reconciliation_cells,
    build_sweep_cells,
    decide_ambient_verdict,
    diagnose_reconciliation,
    reconcile_cell,
    render_markdown,
    render_readme,
    render_sweep_svg,
    summarize_rows,
)


def _accepted(error=(3.0, -2.0), iou=0.9, residual=1.5):
    return {
        "ok": True,
        "status": "accepted",
        "impact_error_mm": float((error[0] ** 2 + error[1] ** 2) ** 0.5),
        "offset_error_mm": error[0],
        "height_error_mm": error[1],
        "silhouette_iou": iou,
        "fit_residual_px": residual,
    }


def test_core_matrix_has_200_paired_shots_per_club_and_candidate():
    cells = build_core_cells(shots_per_cell=200, root_seed=20260824)

    assert len(cells) == 4
    assert {(cell.club, cell.candidate) for cell in cells} == {
        (club, candidate) for club in ("poc_driver", "poc_7iron") for candidate in CANDIDATES
    }
    assert all(cell.n == 200 for cell in cells)
    assert {cell.club: cell.template_variation_fraction for cell in cells} == {
        "poc_driver": 0.08,
        "poc_7iron": 0.10,
    }
    assert all(cell.photometric_noise_sigma_dn == 1.2 for cell in cells)
    assert all(cell.radar_noise_sigma_mm == 3.0 for cell in cells)
    assert all((cell.frame_count, cell.pre_trigger_count) == (3, 2) for cell in cells)
    assert all(len(cell.config_hash) == 64 for cell in cells)
    assert {tuple(cell.seeds) for cell in cells if cell.club == "poc_driver"} == {
        tuple(range(20260824, 20261024))
    }

    controls = build_reconciliation_cells(shots_per_cell=200, root_seed=20260824)
    assert all(cell.template_variation_fraction == 0.0 for cell in controls)
    assert {(cell.club, cell.candidate) for cell in controls} == {
        (cell.club, cell.candidate) for cell in cells
    }


def test_worker_initializer_caps_opencv_threads():
    previous = cv2.getNumThreads()
    try:
        _initialize_worker()
        assert cv2.getNumThreads() == 1
    finally:
        cv2.setNumThreads(previous)


def test_summary_keeps_failures_in_denominator_and_reports_required_metrics():
    rows = [
        _accepted((1.0, -2.0), 0.95, 1.0),
        _accepted((3.0, 4.0), 0.85, 3.0),
        {"ok": False, "status": "component_missing"},
        {"ok": False, "status": "silhouette_ambiguous"},
    ]

    summary = summarize_rows(rows)

    assert summary["n_attempted"] == 4
    assert summary["n_ok"] == 2
    assert summary["solve_rate"] == 0.5
    assert summary["impact_error_mm_median"] == pytest.approx((5**0.5 + 5.0) / 2.0)
    assert summary["offset_error_mm_median"] == 2.0
    assert summary["height_error_mm_median"] == 1.0
    assert summary["silhouette_iou_median"] == pytest.approx(0.9)
    assert summary["fit_residual_px_median"] == 2.0
    assert summary["ambiguity_quality_rejection_rate"] == 0.25
    assert summary["failure_categories"] == {
        "component_missing": 1,
        "silhouette_ambiguous": 1,
    }


def test_reconciliation_limits_are_frozen_and_material_disagreement_fails_closed():
    assert RECONCILIATION_LIMITS == {
        "solve_rate_absolute": 0.10,
        "median_error_mm_absolute": 2.0,
        "p90_error_mm_absolute": 4.0,
    }
    phase1b = {
        "ok_rate": 0.99,
        "impact_error_mm_median": 2.0,
        "impact_error_mm_p90": 4.0,
    }

    agreed = reconcile_cell(
        {"solve_rate": 0.91, "impact_error_mm_median": 3.5, "impact_error_mm_p90": 7.5},
        phase1b,
    )
    disagreed = reconcile_cell(
        {"solve_rate": 0.75, "impact_error_mm_median": 5.0, "impact_error_mm_p90": 10.0},
        phase1b,
    )

    assert agreed["status"] == "AGREES"
    assert disagreed["status"] == "MATERIAL_DISAGREEMENT"
    assert set(disagreed["material_metrics"]) == {
        "solve_rate",
        "impact_error_mm_median",
        "impact_error_mm_p90",
    }

    diagnosis = diagnose_reconciliation(disagreed, agreed)
    assert diagnosis["status"] == "DIAGNOSED_MODEL_GAP"
    assert diagnosis["cause"] == "template_dimension_mismatch_absent_from_phase1b"


def test_degradation_plan_has_all_curves_candidates_clubs_and_baselines():
    cells = build_sweep_cells(shots_per_point=8, root_seed=77)

    assert set(SWEEP_AXES) == {
        "template_variation_fraction",
        "photometric_noise_sigma_dn",
        "radar_residual_mm",
        "sync_offset_us",
    }
    assert all(any(cell.axis == axis for cell in cells) for axis in SWEEP_AXES)
    for axis, values in SWEEP_AXES.items():
        assert any(value == 0.0 for value in values)
        for club in ("poc_driver", "poc_7iron"):
            for candidate in CANDIDATES:
                selected = [
                    cell
                    for cell in cells
                    if cell.axis == axis and cell.club == club and cell.candidate == candidate
                ]
                assert [cell.value for cell in selected] == list(values)
                assert all(cell.n == 8 for cell in selected)


def test_report_publishes_criteria_ambient_reconciliation_and_curve_data():
    cells = []
    for club in ("poc_driver", "poc_7iron"):
        for candidate in CANDIDATES:
            cells.append(
                {
                    "club": club,
                    "candidate": candidate,
                    "exposure_us": 10 if candidate == "strobed_10us" else 500,
                    "n_attempted": 200,
                    "solve_rate": 0.95,
                    "impact_error_mm_median": 3.0,
                    "impact_error_mm_p90": 7.0,
                    "offset_error_mm_median": 0.1,
                    "offset_error_mm_p90": 3.0,
                    "height_error_mm_median": -0.2,
                    "height_error_mm_p90": 4.0,
                    "silhouette_iou_median": 0.9,
                    "fit_residual_px_median": 1.0,
                    "ambiguity_quality_rejection_rate": 0.01,
                    "failure_categories": {},
                    "passes": True,
                    "reconciliation": {"status": "AGREES", "deltas": {}},
                    "config_hash": "a" * 64,
                }
            )
    bundle = {
        "evaluation_hash": "b" * 64,
        "root_seed": 20260824,
        "shots_per_core_cell": 200,
        "shots_per_sweep_point": 8,
        "core_cells": cells,
        "sweeps": [],
        "ambient_verdict": {"verdict": "YES", "reason": "both clubs pass"},
        "reconciliation": {"verdict": "RECONCILED", "material_disagreements": []},
    }

    report = render_markdown(bundle)
    svg = render_sweep_svg(bundle)
    readme = render_readme(bundle)

    assert "## Spec section 1 criteria — actual end-to-end results" in report
    assert "## Ambient 500 us verdict" in report
    assert "**YES**" in report
    assert "## Phase 1b reconciliation" in report
    assert "## Degradation curves" in report
    assert "median AND p90" in report
    assert "<svg" in svg and "</svg>" in svg
    assert "## Results" in readme
    assert "RESULTS_E2E.md" in readme
    assert bundle["evaluation_hash"] in readme
    json.dumps(bundle, allow_nan=False)


def test_ambient_verdict_names_the_exact_failed_gate():
    rows = [
        {
            "club": "poc_driver",
            "candidate": "ambient_500us",
            "passes": False,
            "solve_rate": 0.665,
            "impact_error_mm_median": 1.2,
            "impact_error_mm_p90": 2.5,
        },
        {
            "club": "poc_7iron",
            "candidate": "ambient_500us",
            "passes": True,
            "solve_rate": 0.935,
            "impact_error_mm_median": 1.5,
            "impact_error_mm_p90": 3.1,
        },
    ]

    verdict = decide_ambient_verdict(rows, unresolved=[])

    assert verdict["verdict"] == "NO"
    assert "poc_driver solve rate 0.665 < 0.800" in verdict["reason"]

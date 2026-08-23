"""TDD contract for Phase 1b result generation and gate selection."""

from __future__ import annotations

import json

import pytest

from silhouette_poc.eval.phase1b import run_validation_case
from silhouette_poc.eval.run_phase1b import evaluate_gate, render_markdown, run_evaluation


def _cell(**overrides):
    cell = {
        "category": "core",
        "club": "poc_driver",
        "preset": "A0",
        "exposure_us": 10.0,
        "timing": "iq_gaussian_33us",
        "depth_source": "radar",
        "club_range_residual_mm": 0.0,
        "config_hash": "a" * 64,
        "buildable": True,
        "n_attempted": 1_000,
        "n_ok": 900,
        "ok_rate": 0.9,
        "impact_error_mm_median": 5.0,
        "impact_error_mm_p90": 15.0,
        "offset_error_mm_median": 0.1,
        "offset_error_mm_p90": 4.0,
        "height_error_mm_median": -0.2,
        "height_error_mm_p90": 5.0,
        "silhouette_iou_median": 0.9,
        "silhouette_iou_p10": 0.8,
        "fit_residual_px_median": 0.5,
        "fit_residual_px_p90": 1.0,
        "visibility_failures": 10,
        "ambiguity_rejections": 2,
        "failure_categories": {"visibility_club": 10},
    }
    cell.update(overrides)
    return cell


def test_gate_uses_median_p90_solve_rate_and_buildability():
    cells = [
        _cell(preset="B", buildable=False, impact_error_mm_median=0.1, impact_error_mm_p90=0.2),
        _cell(preset="A1", buildable=False, impact_error_mm_median=0.2, impact_error_mm_p90=0.3),
        _cell(depth_source="oracle", buildable=False, impact_error_mm_median=0.1),
        _cell(config_hash="p" * 64),
        _cell(config_hash="m" * 64, impact_error_mm_median=10.1),
        _cell(config_hash="q" * 64, impact_error_mm_p90=20.1),
        _cell(config_hash="r" * 64, ok_rate=0.799),
    ]

    gate = evaluate_gate(cells)

    assert gate["verdict"] == "PASS"
    assert [cell["config_hash"] for cell in gate["passing_buildable_cells"]] == ["p" * 64]
    assert gate["line"].startswith("PASS —")


def test_gate_reports_no_go_when_no_eligible_driver_cell_passes():
    gate = evaluate_gate([_cell(impact_error_mm_p90=20.01)])

    assert gate["verdict"] == "NO-GO"
    assert gate["passing_buildable_cells"] == []
    assert gate["line"].startswith("NO-GO —")


def test_static_bias_negative_control_is_not_a_candidate():
    result = run_validation_case("static_bias_not_removed", n=16, seed=3)

    assert result["buildable"] is False
    assert result["club_range_error_mm_median"] == pytest.approx(66.0069821, abs=1e-6)


def test_evaluation_bundle_contains_every_cell_validation_and_hash():
    bundle = run_evaluation(core_n=4, stress_n=4, seed=5)

    assert len(bundle["core_cells"]) == 192
    assert len(bundle["stress_cells"]) == 44
    assert set(bundle["validation"]) == {
        "zero_noise_recovery",
        "static_bias_not_removed",
    }
    assert bundle["frozen_contract"]["core_trials_per_cell"] == 4
    assert (
        bundle["frozen_contract"]["model_config_hash"]
        == bundle["core_cells"][0]["model_config_hash"]
    )
    assert bundle["frozen_contract"]["model_config"]["solver"]["fit_residual_limit_px"] == 8.0
    assert len(bundle["evaluation_hash"]) == 64
    json.dumps(bundle, allow_nan=False)


def test_markdown_has_explicit_gate_and_complete_sections():
    bundle = run_evaluation(core_n=2, stress_n=2, seed=9)
    report = render_markdown(bundle)

    assert f"**GATE: {bundle['gate']['verdict']}**" in report
    assert "## Core grid — all 192 cells" in report
    assert "## Mandatory stress grid — all 44 cells" in report
    assert "## Zero-noise and calibration controls" in report
    assert "Preset B is not buildable" in report

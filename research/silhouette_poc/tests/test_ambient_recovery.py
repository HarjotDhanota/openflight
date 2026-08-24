import json
from pathlib import Path

from silhouette_poc.eval.ambient_recovery import (
    ATTRIBUTION_STAGES,
    BLUR_AWARE_FIT_RESIDUAL_LIMIT_PX,
    RECOVERY_SWEEP_AXES,
    attribute_solve_rates,
    build_recovery_cells,
    build_recovery_sweep_cells,
    decide_recovery_verdict,
    merge_recovery_sweep_axis,
    render_recovery_markdown,
    render_recovery_readme,
    render_recovery_sweep_svg,
)
from silhouette_poc.eval.run_ambient_recovery import OUTPUT_FILENAMES
from silhouette_poc.fusion.pipeline import AMBIENT_RECOVERY_POLICY, LEGACY_SINGLE_FRAME_POLICY
from silhouette_poc.generator.artifacts import write_shot
from silhouette_poc.generator.synthetic import GeneratorConfig


def test_recovery_grid_is_frozen_before_outcomes():
    cells = build_recovery_cells()

    assert ATTRIBUTION_STAGES == (
        "baseline",
        "temporal_only",
        "blur_aware",
        "calibrated_template",
    )
    assert BLUR_AWARE_FIT_RESIDUAL_LIMIT_PX == 12.0
    assert len(cells) == 16
    assert all(cell.n == 200 for cell in cells)
    assert {cell.stage for cell in cells} == set(ATTRIBUTION_STAGES)
    calibrated = [cell for cell in cells if cell.stage == "calibrated_template"]
    assert all(cell.template_variation_fraction == 0.01 for cell in calibrated)
    assert all(cell.seeds == tuple(range(20260824, 20261024)) for cell in cells)
    assert OUTPUT_FILENAMES == {
        "json": "results_e2e_4b.json",
        "markdown": "RESULTS_E2E_4B.md",
        "svg": "degradation_curves_4b.svg",
    }


def test_driver_club_speed_is_an_official_recovery_sweep_axis():
    assert RECOVERY_SWEEP_AXES["club_speed_mph"] == (90.0, 100.0, 110.0, 120.0, 130.0, 140.0, 150.0)
    cells = [cell for cell in build_recovery_sweep_cells() if cell.axis == "club_speed_mph"]

    assert len(cells) == 2 * 7
    assert {cell.club for cell in cells} == {"poc_driver"}
    assert {cell.candidate for cell in cells} == {"ambient_500us", "strobed_10us"}
    assert {cell.club_speed_mph for cell in cells} == set(RECOVERY_SWEEP_AXES["club_speed_mph"])


def test_merge_recovery_sweep_axis_replaces_axis_and_rehashes_bundle():
    bundle = {
        "evaluation_hash": "old",
        "sweeps": [
            {"axis": "sync_offset_us", "value": 0.0},
            {"axis": "club_speed_mph", "value": 80.0},
        ],
    }
    speed = [
        {"axis": "club_speed_mph", "value": 90.0},
        {"axis": "club_speed_mph", "value": 100.0},
    ]

    merged = merge_recovery_sweep_axis(bundle, "club_speed_mph", speed)

    assert bundle["evaluation_hash"] == "old"
    assert merged["sweeps"] == [bundle["sweeps"][0], *speed]
    assert merged["evaluation_hash"] != "old"


def test_generator_honors_explicit_club_speed_for_sweep(tmp_path: Path):
    shot = write_shot(
        tmp_path,
        GeneratorConfig(
            root_seed=91,
            club="poc_driver",
            exposure_us=500,
            club_speed_mph=150.0,
        ),
    )
    truth = json.loads((shot / "truth.json").read_text(encoding="utf-8"))

    assert truth["club"]["speed_mm_s"] / 1000.0 * 2.2369362920544 == 150.0


def test_recovery_policy_uses_two_or_three_strictly_preimpact_frames(tmp_path: Path):
    shot_dir = write_shot(
        tmp_path,
        GeneratorConfig(
            root_seed=20260824,
            club="poc_driver",
            exposure_us=500,
            preset="A0",
            frame_count=10,
            pre_trigger_count=8,
            template_dimension_variation_fraction=0.01,
            photometric_noise_sigma_dn=1.2,
        ),
    )

    result = AMBIENT_RECOVERY_POLICY.solve(shot_dir)

    assert result.ok, result.status
    temporal = result.diagnostics["temporal"]
    assert temporal["candidate_preimpact_frame_indices"] == list(range(7))
    assert 2 <= len(temporal["used_frame_indices"]) <= 3
    assert max(temporal["used_frame_indices"]) < 7
    assert result.diagnostics["quality"]["fit_residual_limit_px"] == 12.0


def test_cascade_drops_an_inconsistent_roll_fit_instead_of_rejecting_sequence(tmp_path: Path):
    shot_dir = write_shot(
        tmp_path,
        GeneratorConfig(
            root_seed=20260826,
            club="poc_driver",
            exposure_us=500,
            preset="A0",
            frame_count=10,
            pre_trigger_count=8,
            template_dimension_variation_fraction=0.01,
            photometric_noise_sigma_dn=1.2,
        ),
    )

    result = AMBIENT_RECOVERY_POLICY.solve(shot_dir)

    assert result.ok, result.status
    assert len(result.diagnostics["temporal"]["used_frame_indices"]) == 2
    assert result.diagnostics["temporal"]["angular_fit_rms_rad"] <= 0.008


def test_legacy_policy_remains_available_for_paired_attribution():
    assert LEGACY_SINGLE_FRAME_POLICY.maximum_fused_frames == 1
    assert LEGACY_SINGLE_FRAME_POLICY.minimum_fused_frames == 1
    assert AMBIENT_RECOVERY_POLICY.maximum_fused_frames == 3
    assert AMBIENT_RECOVERY_POLICY.minimum_fused_frames == 2


def test_attribution_is_sequential_and_strobe_cannot_win_buildable_gate():
    rows = []
    rates = {
        "baseline": 0.665,
        "temporal_only": 0.735,
        "blur_aware": 0.810,
        "calibrated_template": 0.900,
    }
    for stage, rate in rates.items():
        rows.append(
            {
                "stage": stage,
                "club": "poc_driver",
                "candidate": "ambient_500us",
                "solve_rate": rate,
                "impact_error_mm_median": 2.0,
                "impact_error_mm_p90": 5.0,
            }
        )
    rows += [
        {
            "stage": "calibrated_template",
            "club": "poc_7iron",
            "candidate": "ambient_500us",
            "solve_rate": 0.94,
            "impact_error_mm_median": 2.0,
            "impact_error_mm_p90": 5.0,
        },
        {
            "stage": "calibrated_template",
            "club": "poc_driver",
            "candidate": "strobed_10us",
            "solve_rate": 1.0,
            "impact_error_mm_median": 1.0,
            "impact_error_mm_p90": 2.0,
        },
    ]

    attribution = attribute_solve_rates(rows)
    verdict = decide_recovery_verdict(rows, unresolved=[])
    report = render_recovery_markdown(
        {
            "evaluation_hash": "a" * 64,
            "recovery_cells": rows,
            "attribution": attribution,
            "ambient_verdict": verdict,
            "reconciliation": {"verdict": "RECONCILED"},
            "reconciliation_controls": [],
            "sweeps": [],
        }
    )

    driver = [
        row
        for row in attribution
        if row["club"] == "poc_driver" and row["candidate"] == "ambient_500us"
    ]
    assert [round(row["incremental_recovery"], 3) for row in driver] == [0.0, 0.07, 0.075, 0.09]
    assert verdict == {
        "verdict": "YES",
        "reason": "calibrated ambient driver and 7-iron meet the frozen recovery gates",
        "buildable_winner": "ambient_500us",
    }
    assert "Strobe is comparison-only" in report
    assert "Before/after solve-rate attribution" in report
    assert "All paired mitigation cells" in report
    assert "Population variation" in report
    assert "Maintainer-directed speed extension" in report
    assert "â" not in render_recovery_sweep_svg({"sweeps": []})
    readme = render_recovery_readme(
        {"ambient_verdict": verdict, "evaluation_hash": "a" * 64, "recovery_cells": rows}
    )
    assert "| poc_driver | ambient_500us |" in readme
    assert "Final calibrated results" in readme

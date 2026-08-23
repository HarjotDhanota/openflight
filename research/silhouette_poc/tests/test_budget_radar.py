"""Phase 1: 0C budget extended with radar-depth, sync, plate-scale and blur cells."""

import numpy as np
import pytest
from club_pose.sim.budget import run_budget

from silhouette_poc.eval.budget_radar import (
    BASELINE_PX_PER_MM,
    blur_sigma_px,
    build_grid,
    camera_scale_for,
    run_cell,
)


def _cell_kwargs(**overrides):
    base = dict(
        club="driver",
        mode="mono",
        sigma_c=0.5,
        delta_bias=0.0,
        sigma_cal=0.5,
        ball_depth_sigma=3.0,
        sync_jitter_us=33.0,
        vel_err_frac=0.03,
        baseline_mm=150.0,
        n=32,
        seed=7,
    )
    base.update(overrides)
    return base


def test_run_budget_defaults_are_backward_compatible():
    """Old call signature still works and reports neutral new params."""
    result = run_budget(**_cell_kwargs())
    assert result["params"]["camera_scale"] == 1.0
    assert result["params"]["ball_depth_bias_mm"] == 0.0
    assert result["params"]["blur_sigma_px"] == 0.0
    assert result["ok_rate"] > 0.5


def test_depth_bias_shifts_ball_systematically():
    """With zero depth noise, a pure bias moves every ball estimate by ~bias mm."""
    result = run_budget(**_cell_kwargs(ball_depth_sigma=0.0, ball_depth_bias_mm=25.0))
    errs = [r["ball_error_mm"] for r in result["rows"] if r["ok"]]
    assert errs, "no successful solves"
    assert np.allclose(errs, 25.0, atol=1e-6)


def test_blur_composes_in_quadrature_with_pixel_noise():
    """blur_sigma_px behaves exactly like extra centroid noise (same seed, same rows)."""
    a = run_budget(**_cell_kwargs(sigma_c=0.7, blur_sigma_px=0.0))
    b = run_budget(**_cell_kwargs(sigma_c=0.0, blur_sigma_px=0.7))
    med_a = np.nanmedian([r["impact_err_mm"] for r in a["rows"] if r["ok"]])
    med_b = np.nanmedian([r["impact_err_mm"] for r in b["rows"] if r["ok"]])
    assert med_a == pytest.approx(med_b)


def test_camera_scale_degrades_impact_accuracy():
    """The POC plate scale (0.656 px/mm) must hurt vs the 0C baseline optics."""
    fine = run_budget(**_cell_kwargs())
    coarse = run_budget(**_cell_kwargs(camera_scale=camera_scale_for(0.656)))
    med_fine = np.nanmedian([r["impact_err_mm"] for r in fine["rows"] if r["ok"]])
    med_coarse = np.nanmedian([r["impact_err_mm"] for r in coarse["rows"] if r["ok"]])
    assert med_coarse > med_fine


def test_camera_scale_for_maps_px_per_mm():
    assert camera_scale_for(BASELINE_PX_PER_MM) == pytest.approx(1.0)
    assert camera_scale_for(BASELINE_PX_PER_MM / 2) == pytest.approx(0.5)


def test_blur_sigma_px_formula():
    # driver head 45 m/s, 500 us, 0.656 px/mm: smear 14.76 px -> sigma = smear/sqrt(12)
    sigma = blur_sigma_px(speed_mm_s=45_000.0, exposure_us=500.0, px_per_mm=0.656)
    assert sigma == pytest.approx(45_000.0 * 500e-6 * 0.656 / np.sqrt(12.0), rel=1e-9)
    assert blur_sigma_px(45_000.0, 10.0, 0.656) < 0.1  # strobed exposure is negligible


def test_build_grid_covers_preregistered_cells():
    """Appendix B: presets x sync x depth x blur x clubs, plus stereo reference."""
    grid = build_grid(n=2, seed=0)
    # 3 presets x 2 sync x (1 stereo-ref + 4 radar bias) x 2 blur x 2 clubs
    assert len(grid) == 3 * 2 * 5 * 2 * 2
    px_scales = {c["preset_px_per_mm"] for c in grid}
    assert px_scales == {0.656, 1.31, 1.33}
    assert {c["sync_label"] for c in grid} == {"iq_33us", "frame_2.14ms"}
    assert {c["depth_label"] for c in grid} == {
        "stereo_ref_3mm",
        "radar_bias_0mm",
        "radar_bias_10mm",
        "radar_bias_20mm",
        "radar_bias_40mm",
    }
    assert {c["exposure_us"] for c in grid} == {10.0, 500.0}
    assert {c["club"] for c in grid} == {"driver", "iron"}


def test_run_cell_executes_and_summarizes():
    grid = build_grid(n=2, seed=0)
    cell = run_cell(grid[0])
    assert "impact_err_mm_median" in cell
    assert cell["n_attempted"] == 2

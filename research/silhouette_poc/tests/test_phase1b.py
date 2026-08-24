"""Frozen Appendix-B contract tests for the corrected Phase 1b gate."""

from __future__ import annotations

import inspect
import math
from dataclasses import replace

import pytest

from silhouette_poc.eval import phase1b


def test_presets_have_independent_intrinsics_and_fixed_bounds():
    presets = phase1b.camera_presets()

    assert set(presets) == {"A0", "A1", "B"}
    assert (presets["A0"].width, presets["A0"].height, presets["A0"].fx) == (
        320,
        200,
        pytest.approx(1033.0),
    )
    assert (presets["A1"].width, presets["A1"].height, presets["A1"].fx) == (
        320,
        200,
        pytest.approx(2063.0),
    )
    assert (presets["B"].width, presets["B"].height, presets["B"].fx) == (
        1280,
        200,
        pytest.approx(2095.0),
    )
    assert presets["A0"].horizontal_fov_deg != pytest.approx(presets["A1"].horizontal_fov_deg)
    assert presets["A0"].sensor_crop == (336, 150, 816, 516)
    assert presets["A0"].sampling_increment == (2, 2)
    assert presets["A0"].isp_offset == (4, 4)
    assert presets["A1"].sensor_crop == (480, 150, 320, 200)
    assert presets["A1"].sampling_increment == (1, 1)
    assert presets["B"].sensor_crop == (0, 300, 1280, 200)
    assert presets["B"].sampling_increment == (1, 1)
    assert presets["B"].orientation == "portrait_experimental"
    assert presets["B"].gate_b1_passed is False


def test_core_grid_is_the_exact_frozen_cartesian_product():
    grid = phase1b.build_core_grid(n=1_000, seed=20260823)

    assert len(grid) == 3 * 2 * 2 * (1 + 7) * 2
    assert {cell["club"] for cell in grid} == {"poc_driver", "poc_7iron"}
    assert {cell["preset"] for cell in grid} == {"A0", "A1", "B"}
    assert {cell["exposure_us"] for cell in grid} == {10.0, 500.0}
    assert {cell["timing"] for cell in grid} == {"iq_gaussian_33us", "frame_uniform_2.137ms"}
    assert {cell["depth_source"] for cell in grid} == {"oracle", "radar"}
    assert {cell["club_range_residual_mm"] for cell in grid if cell["depth_source"] == "radar"} == {
        -40.0,
        -20.0,
        -10.0,
        0.0,
        10.0,
        20.0,
        40.0,
    }
    assert all(cell["n"] == 1_000 for cell in grid)
    assert all(len(cell["config_hash"]) == 64 for cell in grid)
    assert all(len(cell["model_config_hash"]) == 64 for cell in grid)
    assert {cell["model_config_hash"] for cell in grid} == {phase1b.model_config_hash()}


def test_revision_2_1_buildable_rule_makes_ambient_primary_and_strobe_comparison_only():
    assert not phase1b.is_buildable(preset="A0", exposure_us=10.0, depth_source="radar")
    assert phase1b.is_buildable(preset="A0", exposure_us=500.0, depth_source="radar")
    assert not phase1b.is_buildable(preset="A1", exposure_us=10.0, depth_source="radar")
    assert not phase1b.is_buildable(preset="B", exposure_us=10.0, depth_source="radar")
    assert not phase1b.is_buildable(preset="A0", exposure_us=10.0, depth_source="oracle")


def test_scenario_cache_key_includes_the_active_template(monkeypatch):
    phase1b._scenarios.cache_clear()
    original_templates = phase1b.club_templates
    first_hash = phase1b.template_config_hash("poc_driver")
    first = phase1b._scenarios("poc_driver", 4, 17, first_hash)
    templates = original_templates()
    changed = replace(templates["poc_driver"], impact_u_limit_mm=5.0)
    monkeypatch.setattr(
        phase1b,
        "club_templates",
        lambda: {**templates, "poc_driver": changed},
    )

    second_hash = phase1b.template_config_hash("poc_driver")
    second = phase1b._scenarios("poc_driver", 4, 17, second_hash)

    assert second_hash != first_hash
    assert second is not first
    assert all(abs(row.impact_u_mm) <= 5.0 for row in second)


def test_solver_source_has_no_marker_correspondence_path():
    source = inspect.getsource(phase1b)
    assert "club_pose.sim.markers" not in source
    assert "fit_pose_pnp" not in source
    assert "fit_pose_kp_stereo" not in source


def test_zero_noise_recovers_impact_from_silhouette_and_club_range():
    result = phase1b.run_validation_case("zero_noise", n=32, seed=7)

    assert result["ok_rate"] == 1.0
    assert result["impact_error_mm_p90"] < 1e-6
    assert result["silhouette_iou_median"] > 0.999999
    assert result["failure_categories"] == {}


def test_club_range_bias_enters_club_state_not_ball_depth():
    result = phase1b.run_validation_case("club_range_bias", n=32, seed=7)

    assert result["club_range_error_mm_median"] == pytest.approx(25.0, abs=1e-6)
    assert result["ball_range_error_mm_median"] == pytest.approx(0.0, abs=1e-6)
    assert result["impact_error_mm_median"] > 0.0


def test_visibility_is_a_failed_attempt_with_named_category():
    result = phase1b.run_validation_case("fov_edge", n=32, seed=7)

    assert result["ok_rate"] < 1.0
    assert result["visibility_failures"] > 0
    assert any(name.startswith("visibility_") for name in result["failure_categories"])


def test_cell_summary_reports_frozen_metrics_and_rejections():
    spec = next(
        cell
        for cell in phase1b.build_core_grid(n=24, seed=11)
        if cell["preset"] == "A0"
        and cell["club"] == "poc_driver"
        and cell["exposure_us"] == 10.0
        and cell["timing"] == "iq_gaussian_33us"
        and cell["depth_source"] == "radar"
        and cell["club_range_residual_mm"] == 0.0
    )
    result = phase1b.run_cell(spec)

    expected = {
        "config_hash",
        "n_attempted",
        "n_ok",
        "ok_rate",
        "impact_error_mm_median",
        "impact_error_mm_p90",
        "offset_error_mm_median",
        "offset_error_mm_p90",
        "height_error_mm_median",
        "height_error_mm_p90",
        "silhouette_iou_median",
        "silhouette_iou_p10",
        "fit_residual_px_median",
        "fit_residual_px_p90",
        "visibility_failures",
        "ambiguity_rejections",
        "failure_categories",
    }
    assert expected <= result.keys()
    assert result["config_hash"] == spec["config_hash"]
    assert result["n_attempted"] == 24
    assert math.isfinite(result["impact_error_mm_p90"])


def test_stress_grid_names_every_frozen_appendix_b_case():
    grid = phase1b.build_stress_grid(n=64, seed=20260823)
    names = {cell["stress_case"] for cell in grid}

    assert names == {
        "zero_noise_recovery",
        "fov_edge_partial_visibility",
        "forward_motion",
        "reverse_motion",
        "ball_overlap",
        "shaft_connected",
        "false_component",
        "dropped_frame",
        "template_dimension_perturbation",
        "leave_one_template_out",
        "translation_acceleration",
        "angular_acceleration",
        "maximum_extrapolation_horizon",
        "radar_low_confidence",
        "radar_reduced_inliers",
        "radar_measured_rms",
        "radar_missing",
        "camera_radar_extrinsic_offset",
        "camera_radar_time_offset",
        "lens_distortion",
        "principal_point_offset",
        "signed_range_residual_symmetry",
    }
    assert all(cell["category"] == "stress" for cell in grid)

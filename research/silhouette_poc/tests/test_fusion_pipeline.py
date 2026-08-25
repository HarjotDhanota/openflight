"""Artifact-only end-to-end tests for the Phase 3 classical fusion path."""

from __future__ import annotations

import json

import numpy as np
import pytest

from silhouette_poc.fusion.pipeline import _segment_frame, solve_shot
from silhouette_poc.generator.artifacts import write_shot
from silhouette_poc.generator.mesh_truth import default_mesh_asset_root
from silhouette_poc.generator.synthetic import GeneratorConfig, generate_shot


def _config(**overrides) -> GeneratorConfig:
    values = {
        "root_seed": 701,
        "club": "poc_driver",
        "exposure_us": 10,
        "preset": "A0",
        "frame_count": 7,
        "pre_trigger_count": 4,
        "template_dimension_variation_fraction": 0.0,
        "photometric_noise_sigma_dn": 0.0,
        "radar_track_noise_sigma_mm": 0.0,
        "zero_noise_control": True,
    }
    values.update(overrides)
    return GeneratorConfig(**values)


def _truth(shot_dir):
    """Scoring-only test helper; fusion code is structurally barred from this file."""
    return json.loads((shot_dir / "truth.json").read_text(encoding="utf-8"))


def _vector_error(result, truth) -> float:
    return float(
        np.linalg.norm(
            np.asarray(result.impact_offset_mm) - np.asarray(truth["impact"]["face_vector_mm"])
        )
    )


def test_zero_noise_recovers_truth_exactly_through_full_artifact_path(tmp_path):
    shot_dir = write_shot(tmp_path, _config())

    result = solve_shot(shot_dir)

    assert result.ok, result.status
    assert _vector_error(result, _truth(shot_dir)) < 0.1
    assert result.diagnostics["quality"]["template_fit_iou"] >= 0.985
    assert result.confidence is None
    assert result.diagnostics["quality"]["confidence_status"] == "uncalibrated"
    assert result.diagnostics["impact"]["ball_occlusion_model"] == "pre_swing_reference"
    assert result.diagnostics["impact"]["ball_reference_frame_indices"]


def test_every_stage_emits_studio_ready_diagnostics(tmp_path):
    shot_dir = write_shot(tmp_path, _config())

    result = solve_shot(shot_dir)

    assert set(result.diagnostics) == {
        "input",
        "segmentation",
        "hypotheses",
        "radar",
        "temporal",
        "impact",
        "quality",
    }
    assert result.diagnostics["segmentation"]["frames"]
    assert result.diagnostics["hypotheses"]["frames"]
    assert "hessian_condition" in result.diagnostics["hypotheses"]["frames"][-1]
    assert "best_second_margin" in result.diagnostics["hypotheses"]["frames"][-1]
    assert "silhouette_iou" in result.diagnostics["quality"]
    assert "fit_residual_px" in result.diagnostics["quality"]


@pytest.mark.parametrize("exposure_us", [10, 500])
def test_tracked_exposure_candidates_solve_with_measurement_noise(tmp_path, exposure_us):
    shot_dir = write_shot(
        tmp_path,
        _config(
            exposure_us=exposure_us,
            photometric_noise_sigma_dn=1.2,
            radar_track_noise_sigma_mm=3.0,
            zero_noise_control=False,
        ),
    )

    result = solve_shot(shot_dir)

    assert result.ok, result.status
    assert _vector_error(result, _truth(shot_dir)) <= 10.0
    assert result.diagnostics["quality"]["fit_residual_px"] > 0.01
    assert result.diagnostics["quality"]["post_refine_model_residual_px"] < 0.01
    assert result.diagnostics["input"]["hardware_candidate"] == (
        "ambient_500us" if exposure_us == 500 else "strobed_10us"
    )


def test_signed_club_range_residual_remains_signed_in_fusion(tmp_path):
    negative_dir = write_shot(
        tmp_path / "negative",
        _config(club_scattering_center_residual_mm=-10.0),
    )
    positive_dir = write_shot(
        tmp_path / "positive",
        _config(club_scattering_center_residual_mm=10.0),
    )

    negative = solve_shot(negative_dir)
    positive = solve_shot(positive_dir)

    assert negative.ok and positive.ok
    negative_range = negative.diagnostics["radar"]["club_calibrated_range_at_impact_mm"]
    positive_range = positive.diagnostics["radar"]["club_calibrated_range_at_impact_mm"]
    assert positive_range - negative_range == pytest.approx(20.0, abs=1e-6)


def test_extrapolation_horizon_fails_closed_with_named_diagnostic(tmp_path):
    shot_dir = write_shot(tmp_path, _config(fps=300.0))

    result = solve_shot(shot_dir)

    assert not result.ok
    assert result.status == "extrapolation_horizon"
    assert result.diagnostics["temporal"]["extrapolation_horizon_s"] == pytest.approx(1 / 300.0)


def test_artifact_time_mapping_controls_extrapolation_horizon(tmp_path):
    shot_dir = write_shot(tmp_path, _config(sync_offset_us=250.0))

    result = solve_shot(shot_dir)

    expected = 1 / 468.0 + 250e-6
    assert result.diagnostics["temporal"]["extrapolation_horizon_s"] == pytest.approx(expected)


def test_template_mismatch_is_measured_without_club_silhouette_completion(tmp_path):
    matched_dir = write_shot(
        tmp_path / "matched",
        _config(zero_noise_control=False),
    )
    mismatch_dir = write_shot(
        tmp_path / "mismatch",
        _config(
            zero_noise_control=False,
            template_dimension_variation_fraction=0.15,
        ),
    )

    matched = solve_shot(matched_dir)
    mismatch = solve_shot(mismatch_dir)

    assert matched.ok
    assert not mismatch.ok
    assert mismatch.status == "temporal_angular_acceleration"
    mismatch_iou = np.mean(
        [frame["template_fit_iou"] for frame in mismatch.diagnostics["hypotheses"]["frames"]]
    )
    assert mismatch_iou < matched.diagnostics["quality"]["template_fit_iou"]
    assert all(
        frame["occlusion_completion_px"] == 0
        and frame["occlusion_model"] == "club_occludes_ball_no_completion"
        for frame in mismatch.diagnostics["segmentation"]["frames"]
    )
    assert any(
        frame["visible_pixel_objective"] for frame in mismatch.diagnostics["hypotheses"]["frames"]
    )


def test_connected_shaft_is_separated_before_head_fit(tmp_path):
    shot_dir = write_shot(
        tmp_path,
        _config(zero_noise_control=False, shaft_connected=True),
    )

    result = solve_shot(shot_dir)

    assert result.ok, result.status
    assert all(
        frame["moment_source"] == "raw_head_intensity"
        and frame["shaft_pixel_count"] > 0
        and frame["head_pixel_count"] > 0
        for frame in result.diagnostics["segmentation"]["frames"]
    )


def test_mesh_head_is_recovered_from_default_shaft_without_convexification():
    generated = generate_shot(
        _config(
            root_seed=20260824,
            club="poc_7iron",
            truth_geometry="mesh",
            mesh_asset_root=str(default_mesh_asset_root()),
            frame_count=10,
            pre_trigger_count=8,
            zero_noise_control=False,
            photometric_noise_sigma_dn=0.0,
        )
    )
    recovered = []
    for frame_index in range(generated.config.pre_trigger_count - 1):
        try:
            item = _segment_frame(generated.frames[frame_index], frame_index)
        except ValueError:
            continue
        expected = generated.clubhead_masks[frame_index]
        intersection = np.count_nonzero(item.club_mask & expected)
        union = np.count_nonzero(item.club_mask | expected)
        recovered.append((intersection / union, item.diagnostics))

    assert recovered
    assert min(iou for iou, _ in recovered) >= 0.99
    assert all(diagnostics["moment_source"] == "raw_head_intensity" for _, diagnostics in recovered)
    assert all(diagnostics["occlusion_completion_px"] == 0 for _, diagnostics in recovered)


@pytest.mark.parametrize(
    ("reverse_motion", "expected_direction"),
    [(False, "forward"), (True, "reverse")],
)
def test_motion_direction_is_inferred_from_artifact_frames(
    tmp_path, reverse_motion, expected_direction
):
    if reverse_motion:
        with pytest.raises(ValueError, match="scene_occlusion_order_ball_in_front"):
            write_shot(
                tmp_path,
                _config(zero_noise_control=False, reverse_motion=True),
            )
        return
    shot_dir = write_shot(tmp_path, _config(zero_noise_control=False))

    result = solve_shot(shot_dir)

    assert result.ok, result.status
    assert result.diagnostics["temporal"]["motion_direction"] == expected_direction
    assert _vector_error(result, _truth(shot_dir)) <= 10.0


@pytest.mark.parametrize(
    ("acceleration", "angular_acceleration", "expected_status"),
    [
        ((0.0, 1_000_000.0, 0.0), 0.0, "temporal_acceleration"),
        ((0.0, 0.0, 0.0), 8_000.0, "temporal_angular_acceleration"),
    ],
)
def test_acceleration_models_fail_closed(
    tmp_path, acceleration, angular_acceleration, expected_status
):
    shot_dir = write_shot(
        tmp_path,
        _config(
            zero_noise_control=False,
            club_acceleration_world_mm_s2=acceleration,
            angular_acceleration_rad_s2=angular_acceleration,
        ),
    )

    result = solve_shot(shot_dir)

    assert not result.ok
    assert result.status == expected_status
    assert result.diagnostics["temporal"]["status"] == expected_status

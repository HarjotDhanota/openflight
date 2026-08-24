import hashlib
import inspect
from pathlib import Path

import numpy as np
import pytest

from silhouette_poc.eval.f1_remediation import (
    REMEDIATION_ARMS,
    build_remediation_cells,
    decide_remediation,
)
from silhouette_poc.eval.mesh_lut import (
    ARM_A_CONTOUR_SAMPLES,
    ARM_A_PITCH_GRID_DEG,
    ARM_A_ROLL_GRID_DEG,
    ARM_A_VALIDATION_COUNT,
    ARM_A_YAW_GRID_DEG,
)
from silhouette_poc.eval.run_f1_remediation import (
    OUTPUT_FILENAMES,
    build_final_bundle,
    render_final_markdown,
)
from silhouette_poc.eval.template_calibration import (
    ARM_B_CANDIDATE_COUNT,
    ARM_B_POSE_COUNT,
    ARM_B_SEEDS,
    calibration_poses,
    fit_analytic_radii,
)
from silhouette_poc.fusion import solver
from silhouette_poc.fusion.mesh_fit import MeshProjectionLUT
from silhouette_poc.fusion.pipeline import AMBIENT_RECOVERY_POLICY, FusionPolicy, solve_shot
from silhouette_poc.fusion.solver import CAMERA_CENTER_WORLD, SilhouetteObservation, camera_presets
from silhouette_poc.generator.artifacts import write_shot
from silhouette_poc.generator.synthetic import GeneratorConfig


def test_solver_module_is_byte_identical_to_the_accepted_f1_solver():
    path = Path(inspect.getfile(solver))

    assert hashlib.sha256(path.read_bytes()).hexdigest() == (
        "24fcdd5a3d8e943b6f9bda1949113c449175e9b5ac9965c9577187ed60d6eb8f"
    )


def test_arm_a_lut_density_and_validation_are_frozen():
    np.testing.assert_array_equal(ARM_A_YAW_GRID_DEG, np.arange(-20.0, 20.1, 5.0))
    np.testing.assert_array_equal(ARM_A_PITCH_GRID_DEG, np.arange(-20.0, 20.1, 5.0))
    np.testing.assert_array_equal(ARM_A_ROLL_GRID_DEG, np.arange(-90.0, 90.0, 2.0))
    assert ARM_A_CONTOUR_SAMPLES == 72
    assert ARM_A_VALIDATION_COUNT == 512


def test_mesh_projection_lut_corrects_mesh_centroid_bias_before_backprojection():
    camera = camera_presets()["A0"]
    true_center = np.zeros(3)
    center_uv, _ = solver._project(true_center[None, :], camera)
    covariance = np.diag([100.0, 25.0])
    lut = MeshProjectionLUT.constant_for_test(
        centroid_offset_px=np.array([6.0, -3.0]),
        covariance_px2=covariance,
    )
    observation = SilhouetteObservation(center_uv[0] + [6.0, -3.0], covariance)
    apparent_range = float(np.linalg.norm(true_center - CAMERA_CENTER_WORLD))

    state, diagnostics = lut.solve_state(
        observation,
        apparent_range,
        calibration_bias_mm=0.0,
        camera=camera,
        velocity_world=np.zeros(3),
        exposure_us=0.0,
        fit_residual_limit_px=8.0,
        range_origin_world=CAMERA_CENTER_WORLD,
    )

    assert state.ok, state.reason
    np.testing.assert_allclose(state.frame_center_world, true_center, atol=0.25)
    assert state.fit_residual_px == pytest.approx(0.0, abs=1e-8)
    assert diagnostics["centroid_correction_px"] == pytest.approx([6.0, -3.0])


def test_arm_b_calibration_pose_registration_is_deterministic_and_separate_from_eval():
    assert ARM_B_POSE_COUNT == 256
    assert ARM_B_SEEDS == {"poc_driver": 2026082401, "poc_7iron": 2026082402}
    first = calibration_poses("poc_driver")
    second = calibration_poses("poc_driver")

    assert ARM_B_CANDIDATE_COUNT == 2048
    assert len(first) == ARM_B_CANDIDATE_COUNT
    np.testing.assert_array_equal(first.centers_world_mm, second.centers_world_mm)
    np.testing.assert_array_equal(first.roll_rad, second.roll_rad)
    assert np.all((-0.015 <= first.time_s) & (first.time_s <= -0.002))
    assert np.max(np.abs(np.degrees(first.roll_rad))) <= 15.0
    assert not set(first.root_seeds) & set(range(20260824, 20261024))


def test_analytic_radius_fit_recovers_known_covariance_target():
    nominal = np.array([55.0, 30.0])
    expected = np.array([49.0, 27.0])
    # The helper operates on body-plane covariance targets, making this a
    # deterministic test of the exact registered objective independent of a mesh.
    targets = np.repeat(np.diag(expected**2 / 4.0)[None, :, :], 24, axis=0)

    result = fit_analytic_radii(nominal, targets, centroid_floor=1.25)

    np.testing.assert_allclose(result.radii_mm, expected, atol=1e-4)
    assert result.final_objective < result.initial_objective
    assert result.centroid_floor == 1.25
    assert result.optimizer == "L-BFGS-B"


def test_remediation_grid_reuses_f1_cells_and_unchanged_temporal_policy():
    cells = build_remediation_cells()

    assert REMEDIATION_ARMS == ("arm_b_calibrated_analytic", "arm_a_mesh_projection")
    assert len(cells) == 8
    assert all(cell.n == 200 for cell in cells)
    assert all(cell.seeds == tuple(range(20260824, 20261024)) for cell in cells)
    assert all(cell.frame_count == 10 and cell.pre_trigger_count == 8 for cell in cells)
    assert all(cell.template_variation_fraction == 0.01 for cell in cells)
    assert AMBIENT_RECOVERY_POLICY == FusionPolicy(
        name="ambient_recovery",
        candidate_preimpact_frames=7,
        maximum_fused_frames=3,
        minimum_fused_frames=2,
        sharp_fit_residual_limit_px=8.0,
        ambient_fit_residual_limit_px=12.0,
        tolerate_frame_rejections=True,
    )
    assert OUTPUT_FILENAMES == {
        "calibration": "f1_arm_b_calibration.json",
        "arm_b": "results_f1_arm_b.json",
        "lut_validation": "f1_arm_a_lut_validation.json",
        "arm_a": "results_f1_arm_a.json",
        "final_json": "results_f1_remediation.json",
        "final_markdown": "RESULTS_F1_REMEDIATION.md",
    }


@pytest.mark.parametrize(
    ("b_rate", "a_rate", "expected"),
    [(0.8, 0.2, "SHIP_B"), (0.7, 0.8, "SHIP_A"), (0.7, 0.7, "STOP_NEITHER")],
)
def test_frozen_remediation_decision_precedence(b_rate, a_rate, expected):
    rows = []
    for arm, rate in (
        ("arm_b_calibrated_analytic", b_rate),
        ("arm_a_mesh_projection", a_rate),
    ):
        for club in ("poc_driver", "poc_7iron"):
            for candidate in ("strobed_10us", "ambient_500us"):
                rows.append(
                    {
                        "arm": arm,
                        "club": club,
                        "candidate": candidate,
                        "solve_rate": rate,
                        "impact_error_mm_median": 2.0,
                        "impact_error_mm_p90": 4.0,
                    }
                )

    assert decide_remediation(rows)["verdict"] == expected


def test_pipeline_accepts_only_template_constants_for_arm_b(tmp_path: Path):
    shot_dir = write_shot(
        tmp_path,
        GeneratorConfig(
            root_seed=701,
            club="poc_driver",
            exposure_us=10,
            frame_count=7,
            pre_trigger_count=4,
            template_dimension_variation_fraction=0.0,
            photometric_noise_sigma_dn=0.0,
            radar_track_noise_sigma_mm=0.0,
            zero_noise_control=True,
        ),
    )
    template = solver.club_templates()["poc_driver"]

    result = solve_shot(shot_dir, template_override=template)

    assert result.ok, result.status
    assert result.diagnostics["input"]["fit_template"] == "analytic_override"


def test_pipeline_exposes_artifact_only_mesh_projection_template_path():
    parameters = inspect.signature(solve_shot).parameters

    assert "projection_template" in parameters
    for path in (Path(__file__).parents[1] / "fusion").glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "silhouette_poc.generator" not in source
        assert '"truth.json"' not in source


def test_invalid_frozen_arm_a_lut_fails_closed_in_final_report():
    baseline = {
        "evaluation_hash": "baseline-hash",
        "cells": [
            {
                "truth_arm": truth_arm,
                "club": club,
                "candidate": candidate,
                "solve_rate": 1.0 if truth_arm == "analytic_truth" else 0.6,
                "impact_error_mm_median": 1.0,
                "impact_error_mm_p90": 2.0,
                "failure_categories": {},
            }
            for truth_arm in ("analytic_truth", "mesh_truth")
            for club in ("poc_driver", "poc_7iron")
            for candidate in ("strobed_10us", "ambient_500us")
        ],
    }
    arm_b = {
        "cells": [
            {
                "arm": "arm_b_calibrated_analytic",
                "club": club,
                "candidate": candidate,
                "solve_rate": 0.7,
                "impact_error_mm_median": 2.0,
                "impact_error_mm_p90": 4.0,
                "failure_categories": {},
            }
            for club in ("poc_driver", "poc_7iron")
            for candidate in ("strobed_10us", "ambient_500us")
        ]
    }
    validation = {
        "clubs": [
            {
                "club": "poc_driver",
                "passed": False,
                "metrics": {
                    "centroid_error_px_p99": 4.6,
                    "covariance_error_px_p99": 2.5,
                    "contour_iou_p1": 0.81,
                },
            }
        ]
    }

    bundle = build_final_bundle(baseline, arm_b, validation, arm_a=None)
    markdown = render_final_markdown(bundle)

    assert bundle["verdict"]["verdict"] == "STOP_NEITHER"
    assert bundle["arm_a_status"] == "INVALID_LUT_NOT_EVALUATED"
    assert "F1 REMEDIATION GATE: STOP_NEITHER" in markdown
    assert "INVALID / NOT RUN" in markdown

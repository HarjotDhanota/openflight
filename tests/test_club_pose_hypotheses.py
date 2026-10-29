"""Rigid projected-line factors do not pin a hosel or measure head depth."""

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from openflight.camera import club_pose_hypotheses as pose

CAMERA = dict(focal_xy_px=[300.0, 310.0], principal_px=[160.0, 120.0], size=[320, 240])


def model():
    vertices = np.array([[-1, -1, 0], [1, -1, 0], [1, 1, 0], [-1, 1, 0], [0, 1.8, 0.2]])
    return {
        "vertices": vertices,
        "triangles": vertices[[[0, 1, 2], [0, 2, 3], [3, 2, 4]]],
        "axis_origin": np.array([0, 1.0, 0]),
        "axis_direction": np.array([0, 1.0, 0.1]),
    }


def test_projection_known_answer_anisotropic_camera():
    p = [0, 0, 0, 0, 0, np.log(10)]
    assert pose.project([[1, 1, 0]], p, CAMERA) == pytest.approx(np.array([[190, 151]]))


def test_axis_sliding_and_long_hosel_do_not_change_projected_line_factor():
    m = model()
    p = [0, 0, 0, 0, 0, np.log(10)]
    observed = pose.project(
        m["axis_origin"] + np.array([-5, 5])[:, None] * m["axis_direction"], p, CAMERA
    )
    slid = {**m, "axis_origin": m["axis_origin"] + m["axis_direction"] * 7}
    assert pose.axis_residual(p, m, observed, CAMERA) == pytest.approx(0, abs=1e-10)
    assert pose.axis_residual(p, slid, observed, CAMERA) == pytest.approx(0, abs=1e-10)


def test_axis_only_cannot_distinguish_rotation_about_the_model_line():
    m = model()
    m = {**m, "axis_origin": np.zeros(3), "axis_direction": np.array([0.0, 1.0, 0.0])}
    p = np.array([0.0, 0, 0, 0, 0, np.log(10)])
    observed = pose.project([[0, -1, 0], [0, 1, 0]], p, CAMERA)
    q = p.copy()
    q[:3] = Rotation.from_euler("y", 80, degrees=True).as_rotvec()
    assert pose.axis_residual(q, m, observed, CAMERA) == pytest.approx(
        pose.axis_residual(p, m, observed, CAMERA), abs=1e-10
    )
    assert not np.allclose(
        pose.project(m["vertices"], p, CAMERA), pose.project(m["vertices"], q, CAMERA)
    )


def test_refinement_keeps_current_axis_factor_and_withholds_physical_outputs():
    m = model()
    p = [0, 0, 0, 0, 0, np.log(10)]
    edges = pose.project(m["vertices"], p, CAMERA)
    axis = pose.project(
        m["axis_origin"] + np.array([-2, 2])[:, None] * m["axis_direction"], p, CAMERA
    )
    result = pose.refine(m, p, CAMERA, edges, axis, np.zeros((240, 320), bool), max_evaluations=5)
    assert result["axis_factor_during_refinement"]
    assert result["physical_pose"] is result["qualified_face_normal"] is result["strike"] is None
    assert result["depth_source"] == "CAD_conditioned_not_measured_range"


def test_triangle_contour_renders_without_convex_hull_completion():
    m = model()
    lines = pose.triangle_outline(m, [0, 0, 0, 0, 0, np.log(10)], CAMERA)
    assert lines and all(len(line) >= 3 for line in lines)


def test_missing_axis_has_no_fabricated_factor_and_missing_edges_refuses():
    m = model()
    p = [0, 0, 0, 0, 0, np.log(10)]
    assert pose.axis_residual(p, m, [], CAMERA).size == 0
    with pytest.raises(ValueError, match="edge pixels"):
        pose.refine(m, p, CAMERA, [], [], np.zeros((240, 320), bool))


def test_support_acquisition_exercises_all_existing_orientation_starts():
    m = model()
    p = [0, 0, 0, 0, 0, np.log(10)]
    outline = pose.project(m["vertices"], p, CAMERA)
    candidates = pose.acquire(m, outline, CAMERA, [], max_evaluations=2)
    assert len(candidates) == 18
    assert all(np.isfinite(c["parameters"]).all() for c in candidates)


def test_far_axis_mismatch_still_has_a_refinement_gradient():
    m = model()
    p = [0, 0, 0, 0, 0, np.log(10)]
    a = pose._axis_factor(p, m, [[175, 80], [175, 160]], CAMERA)
    b = pose._axis_factor(p, m, [[185, 80], [185, 160]], CAMERA)
    assert np.linalg.norm(b) > np.linalg.norm(a)


def test_full_contour_score_checks_visibility_and_never_qualifies_physical_pose():
    circle = np.column_stack(
        (
            100 + 20 * np.cos(np.arange(200) * 2 * np.pi / 200),
            100 + 20 * np.sin(np.arange(200) * 2 * np.pi / 200),
        )
    )
    normals = (circle - [100, 100]) / 20
    visible = np.zeros((240, 320), bool)
    result = pose.score_outline([circle], circle, normals, visible)
    assert result["capped_rms_px"] == pytest.approx(0)
    assert "not_physical_accuracy" in result["scope"]
    assert pose.score_outline([circle], circle, normals, ~visible) is None

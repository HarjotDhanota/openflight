"""Current ownership is conditional; continuous containment is not visibility truth."""

import cv2
import numpy as np
import pytest
from scipy.optimize import least_squares

from openflight.camera._club_object_support import (
    exposure_support_residual,
    support_candidates,
    triangle_union_distance,
)


def rectangle(top=8):
    return np.array([[[10, top], [40, top], [40, 32]], [[10, top], [40, 32], [10, 32]]], float)


def owned(points, owners=None):
    return support_candidates(
        {
            "points_px": points,
            "owners": ["head"] * len(points) if owners is None else owners,
            "association_id": "conditional_body",
            "observation_ids": ["own_current_pixels"],
        },
        (40, 50),
    )


def test_exact_union_matches_independent_polygon_distance_with_degenerate_triangles():
    rng = np.random.default_rng(29)
    triangles = rng.uniform(0, 40, (100, 3, 2)).astype(np.float32)
    triangles[0] = [[1, 1], [2, 2], [3, 3]]
    points = rng.uniform(-10, 50, (60, 2))
    expected = [
        min(max(0, -cv2.pointPolygonTest(t, tuple(p), True)) for t in triangles) for p in points
    ]
    assert triangle_union_distance(points, triangles) == pytest.approx(expected, abs=1e-5)


def test_union_preserves_gap_and_internal_ridge_is_inside_not_a_required_boundary():
    triangles = np.array([[[0, 0], [5, 0], [0, 5]], [[10, 0], [15, 0], [15, 5]]], float)
    distances = triangle_union_distance([[1, 1], [7.5, 1]], triangles)
    assert distances[0] == 0 and distances[1] > 2
    assert triangle_union_distance([[25, 20]], rectangle())[0] == 0


def test_reflected_native_view_preserves_distance_and_triangle_winding_is_irrelevant():
    triangles = rectangle()
    points = np.array([[4, 4], [20, 20], [45, 35]], float)
    reflected_triangles, reflected_points = triangles.copy(), points.copy()
    reflected_triangles[..., 0] = 49 - reflected_triangles[..., 0]
    reflected_points[:, 0] = 49 - reflected_points[:, 0]
    assert triangle_union_distance(reflected_points, reflected_triangles) == pytest.approx(
        triangle_union_distance(points, triangles), abs=1e-10
    )


def test_pruning_keeps_long_skinny_triangle_with_distant_centroid():
    long = np.array([[[0, 0], [1000, 0], [0, 0.01]]], float)
    decoys = np.array([[[980 + i, 5], [981 + i, 5], [980 + i, 6]] for i in range(16)], float)
    triangles = np.concatenate([long, decoys])
    assert triangle_union_distance([[990, 0.00001], [990, -1]], triangles) == pytest.approx([0, 1])


def test_explicit_empty_current_candidate_set_creates_no_background_observation():
    candidates = owned(np.empty((0, 2)), [])
    residual = exposure_support_residual(
        rectangle()[None], np.zeros((1, 40, 50), bool), candidates, depth_order="head_front"
    )
    assert residual.size == 0


@pytest.mark.parametrize("scale", [1, 0.25])
def test_continuous_factor_recovers_upper_arc_twelve_native_pixels_from_wrong_ridge(scale):
    points = np.array([[20, 8], [22, 8], [24, 8]], float) * scale
    candidates = owned(points)
    balls = np.zeros((1, 40, 50), bool)

    def residual(value):
        return exposure_support_residual(
            (rectangle(value[0]) * scale)[None],
            balls,
            candidates,
            depth_order="head_front",
            analysis_scale=scale,
        )

    derivative = (residual([20.01]) - residual([20])) / 0.01
    assert derivative == pytest.approx(np.full(3, 1 / np.sqrt(3)), abs=1e-8)
    fit = least_squares(residual, [20.0], max_nfev=25)
    assert fit.success and fit.x[0] <= 11.000001


def test_nonhead_assignments_contribute_no_fabricated_head_or_background_evidence():
    candidates = owned([[1, 1], [2, 1], [3, 1], [4, 1]], ["ball", "shaft", "clutter", "unassigned"])
    residual = exposure_support_residual(
        rectangle()[None], np.zeros((1, 40, 50), bool), candidates, depth_order="head_front"
    )
    assert residual.size == 0


def test_occlusion_is_checked_before_minimizing_over_exposure_samples():
    triangles = np.array([rectangle(), rectangle() + [25, 0]])
    balls = np.zeros((2, 40, 50), bool)
    balls[0, 20, 25] = True
    candidates = owned([[25, 20]])
    residual = exposure_support_residual(
        triangles, balls, candidates, depth_order="ball_front", tolerance_px=0
    )
    assert residual[0] > 0
    assert (
        exposure_support_residual(
            triangles, balls, candidates, depth_order="head_front", tolerance_px=0
        )[0]
        == 0
    )


def test_outside_ball_distance_is_only_lower_bound_when_nearest_head_point_is_hidden():
    triangles = np.array([[[5, 4], [6, 4], [6, 6]], [[5, 4], [6, 6], [5, 6]]], float)[None]
    balls = np.zeros((1, 40, 50), bool)
    balls[0, 4:7, 5:7] = True
    candidates = owned([[7, 5]])
    assert exposure_support_residual(triangles, balls, candidates, depth_order="ball_front")[0] == 0
    assert (
        exposure_support_residual(
            triangles, balls, candidates, depth_order="ball_front", tolerance_px=0
        )[0]
        == 1
    )


def test_ball_covered_current_point_cannot_be_explained_as_visible_head():
    balls = np.zeros((1, 40, 50), bool)
    balls[0, 20, 25] = True
    assert (
        exposure_support_residual(
            rectangle()[None], balls, owned([[25, 20]]), depth_order="ball_front"
        )[0]
        == 6
    )


@pytest.mark.parametrize("change", ["owner", "coordinates", "lineage", "association"])
def test_support_contract_rejects_missing_assignment_or_provenance(change):
    value = {
        "points_px": [[20, 20]],
        "owners": ["head"],
        "observation_ids": ["own_current_pixels"],
        "association_id": "body_branch",
    }
    if change == "owner":
        value["owners"] = ["verified_head"]
    elif change == "coordinates":
        value["points_px"] = [[200, 20]]
    elif change == "lineage":
        value["observation_ids"] = []
    else:
        value["association_id"] = ""
    with pytest.raises(ValueError):
        support_candidates(value, (40, 50))

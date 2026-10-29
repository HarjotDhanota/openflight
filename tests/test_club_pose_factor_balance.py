"""Changing contour sampling density cannot silently reweight the current shaft."""

from types import SimpleNamespace

import numpy as np
import pytest

from openflight.camera import club_pose_hypotheses as pose


def test_body_likelihood_weight_is_invariant_to_duplicate_direction_samples(monkeypatch):
    captured = []

    def fit(residual, start, **_):
        captured.append(residual(start))
        return SimpleNamespace(x=start, success=True, nfev=1)

    monkeypatch.setattr(pose, "least_squares", fit)
    vertices = np.array([[-1.0, -1, 0], [1, -1, 0], [1, 1, 0], [-1, 1, 0]])
    model = {
        "vertices": vertices,
        "axis_origin": np.array([0.0, 0, 0]),
        "axis_direction": np.array([0.0, 1, 0]),
    }
    camera = {"focal_xy_px": [300.0, 300.0], "principal_px": [160.0, 120.0]}
    outline = np.array([[120.0, 80], [200, 80], [200, 160], [120, 160]])
    axis = [[150.0, 80], [150, 160]]
    original_count = len(pose.DIRECTIONS)
    pose.acquire(model, outline, camera, axis, max_evaluations=1)
    first = captured[0]
    monkeypatch.setattr(pose, "DIRECTIONS", np.vstack((pose.DIRECTIONS, pose.DIRECTIONS)))
    captured.clear()
    pose.acquire(model, outline, camera, axis, max_evaluations=1)
    second = captured[0]
    assert np.sum(first[:original_count] ** 2) == pytest.approx(
        np.sum(second[: 2 * original_count] ** 2)
    )
    assert np.sum(first[original_count:] ** 2) == pytest.approx(
        np.sum(second[2 * original_count :] ** 2)
    )

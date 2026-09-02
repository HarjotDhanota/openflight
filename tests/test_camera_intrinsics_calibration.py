"""The checkerboard intrinsics solver, proven on a camera whose truth we chose.

A synthetic camera with a known focal, centre and barrel distortion projects
the board into a dozen poses; the solver must hand the truth back. The gates
are then tested by name: too few views, no edge coverage, and the board
generator's output must itself be detectable.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import cv2  # pylint: disable=no-member
import numpy as np
import pytest

_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "hardware-test"
    / "calibrate_camera_intrinsics.py"
)
_SPEC = importlib.util.spec_from_file_location("calibrate_camera_intrinsics", _SCRIPT)
calib = importlib.util.module_from_spec(_SPEC)
# Dataclasses resolve their (string) annotations through sys.modules, so a
# spec-loaded module must be registered before exec -- same pattern as
# test_track_metrics_regression.
sys.modules[_SPEC.name] = calib
_SPEC.loader.exec_module(calib)

WIDTH, HEIGHT = 320, 200
TRUE_FX, TRUE_FY = 470.0, 468.0
TRUE_CX, TRUE_CY = 162.0, 98.5
TRUE_DIST = np.array([-0.28, 0.09, 0.0, 0.0, 0.0])
SQUARE_MM = 24.8


def _synthetic_views() -> list[np.ndarray]:
    """Project the board through the known camera, over a spread of poses."""
    camera = np.array([[TRUE_FX, 0, TRUE_CX], [0, TRUE_FY, TRUE_CY], [0, 0, 1]])
    grid = np.zeros((calib.PATTERN[0] * calib.PATTERN[1], 3), np.float32)
    grid[:, :2] = np.mgrid[0 : calib.PATTERN[0], 0 : calib.PATTERN[1]].T.reshape(-1, 2) * SQUARE_MM
    views = []
    poses = [
        (rx, ry, tx, ty, tz)
        for rx in (-0.25, 0.2)
        for ry in (-0.3, 0.25)
        for (tx, ty, tz) in (
            (-95.0, -60.0, 420.0),
            (-140.0, -75.0, 560.0),
            (-40.0, -35.0, 380.0),
        )
    ]
    for rx, ry, tx, ty, tz in poses:
        rvec = np.array([rx, ry, 0.02])
        tvec = np.array([tx, ty, tz])
        # pylint: disable-next=no-member
        projected, _ = cv2.projectPoints(grid, rvec, tvec, camera, TRUE_DIST)
        points = projected.reshape(-1, 2)
        if (
            points[:, 0].min() > 2
            and points[:, 1].min() > 2
            and points[:, 0].max() < WIDTH - 2
            and points[:, 1].max() < HEIGHT - 2
        ):
            views.append(points)
    return views


class TestTheSolver:
    def test_the_known_camera_is_recovered(self):
        views = _synthetic_views()
        assert len(views) >= calib.MIN_VIEWS, "the synthetic pose set must supply enough views"

        result = calib.solve_from_correspondences(views, (WIDTH, HEIGHT), SQUARE_MM)

        assert result.fx == pytest.approx(TRUE_FX, rel=0.01)
        assert result.fy == pytest.approx(TRUE_FY, rel=0.01)
        assert result.cx == pytest.approx(TRUE_CX, abs=2.0)
        assert result.cy == pytest.approx(TRUE_CY, abs=2.0)
        assert result.dist_coeffs[0] == pytest.approx(float(TRUE_DIST[0]), abs=0.05)
        assert result.rms_reprojection_px < 0.1

    def test_the_report_quantifies_the_barrel_at_working_radii(self):
        """Near the centre the truth is only -0.03 px -- below solver noise,
        so only its MAGNITUDE is asserted; at 120 px the barrel is ~-2 px and
        both its sign and growth are real signals."""
        views = _synthetic_views()
        result = calib.solve_from_correspondences(views, (WIDTH, HEIGHT), SQUARE_MM)

        near = result.distortion_displacement_px(30.0)
        far = result.distortion_displacement_px(120.0)
        assert abs(near) < 0.3, "sub-pixel near the centre, as the pipeline assumes"
        assert far < -1.0, "barrel distortion pulls far points clearly inward"
        assert far < near, "and it grows with radius"

    def test_too_few_views_are_refused_by_name(self):
        views = _synthetic_views()[:3]

        with pytest.raises(ValueError, match="usable_views"):
            calib.solve_from_correspondences(views, (WIDTH, HEIGHT), SQUARE_MM)

    def test_centre_only_coverage_is_refused_by_name(self):
        """Corners that never reach the edges leave the fisheye unmeasured."""
        views = _synthetic_views()
        cramped = [
            (points - [WIDTH / 2, HEIGHT / 2]) * 0.3 + [WIDTH / 2, HEIGHT / 2] for points in views
        ]

        with pytest.raises(ValueError, match="distortion_unconstrained"):
            calib.solve_from_correspondences(cramped, (WIDTH, HEIGHT), SQUARE_MM)


class TestTheBoard:
    def test_the_generated_board_is_itself_detectable(self, tmp_path):
        path = tmp_path / "board.png"
        calib.make_board(path)
        image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)  # pylint: disable=no-member

        assert image is not None
        corners = calib.find_corners(image)
        assert corners is not None
        assert corners.shape == (calib.PATTERN[0] * calib.PATTERN[1], 2)

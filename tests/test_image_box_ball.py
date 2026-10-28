"""Detection in image coordinates precedes size-derived 3D position."""

import math
from dataclasses import replace

import numpy as np
import pytest

from openflight.camera import reference_ball_range as ranges
from openflight.camera.club_motion import ReferenceBall
from openflight.camera.ground_patch import PatchSearch
from tests.camera_helpers import lit_spheres, v3_camera


def image_search(width, height):
    return PatchSearch(
        outline_px=((0, 0), (width - 1, 0), (width - 1, height - 1), (0, height - 1)),
        diameter_px=(1, 2),
        centre_lateral_m=0,
        half_size_m=0.3,
        radar_window_m=(0.5, 4),
    )


@pytest.mark.parametrize("width,height", [(1280, 800), (640, 400)])
@pytest.mark.parametrize("mirrored", [False, True])
@pytest.mark.parametrize("z", [0.02135, 0.07135, 0.16])
def test_position_comes_from_size_and_bearing_not_assumed_surface(
    monkeypatch, width, height, mirrored, z
):
    camera = v3_camera(width, height, mirrored=mirrored)
    camera = replace(camera, vertical_offset={"status": "calibrated"})
    point = np.array([0.1, 1.8, z])
    x, y = ranges.project_to_pixel(camera, point)
    distance = np.linalg.norm(point - camera.camera_origin_lfu)
    focal = ranges._local_focal_size(camera, x, y)  # pylint: disable=protected-access
    diameter = 2 * focal * math.asin(ranges.GOLF_BALL_DIAMETER_M / (2 * distance))
    ball = ReferenceBall(x, y, diameter, round(math.pi * (diameter / 2) ** 2))

    def detect(_frames, **kwargs):
        assert kwargs["seed_filter"](np.array([x]), np.array([y]), diameter)[0]
        assert min(kwargs["expected_diameters_px"]) <= diameter
        assert max(kwargs["expected_diameters_px"]) >= diameter
        return (ball,)

    monkeypatch.setattr(ranges, "reference_ball_candidates", detect)
    for assumed_height in (0.02135, 0.3):
        result = ranges.estimate_patch_ball(
            np.zeros((3, height, width), np.uint8),
            camera,
            search=image_search(width, height),
            ball_center_height_m=assumed_height,
            height_independent=True,
        )
        assert result.status == "selected"
        assert result.selected.size_point_lfu_m == pytest.approx(point, abs=1e-8)
        assert result.selected.size_camera_range_m == pytest.approx(distance)
        assert result.selected.size_radar_range_m == pytest.approx(
            np.linalg.norm(point - camera.radar_origin_lfu)
        )
        assert result.selected.camera_height_m is None
        assert result.selected.size_height_uncertainty_m > 0
        assert result.selected.confidence == "experimental"
        assert result.diagnostics["seed_size_by_row"] == "not_used_image_box"


def test_equal_ball_candidates_remain_ambiguous(monkeypatch):
    camera = v3_camera()
    monkeypatch.setattr(
        ranges,
        "reference_ball_candidates",
        lambda *_a, **_k: (
            ReferenceBall(620, 360, 25, 490, fit_quality=5),
            ReferenceBall(680, 360, 25, 490, fit_quality=5),
        ),
    )
    result = ranges.estimate_patch_ball(
        np.zeros((3, 800, 1280), np.uint8),
        camera,
        search=image_search(1280, 800),
        ball_center_height_m=0.02135,
        height_independent=True,
    )
    assert result.status == "ambiguous"
    assert result.selected is None


@pytest.mark.parametrize("width,height", [(1280, 800), (640, 400)])
def test_real_rim_detector_finds_a_ball_above_the_camera(width, height):
    camera = v3_camera(width, height, focal_px=2000 * width / 1280)
    camera = replace(camera, vertical_offset={"status": "calibrated"})
    point = (0.0, 1.8, 0.16)
    x, y = ranges.project_to_pixel(camera, point)
    radius = (
        camera.focal_size_px
        * ranges.GOLF_BALL_DIAMETER_M
        / math.dist(point, camera.camera_origin_lfu)
        / 2
    )
    search = replace(
        image_search(width, height),
        outline_px=((x - 80, y - 60), (x + 80, y - 60), (x + 80, y + 60), (x - 80, y + 60)),
    )
    frames = lit_spheres(height, width, [(x, y, radius, 90)], count=3)
    result = ranges.estimate_patch_ball(
        frames,
        camera,
        search=search,
        ball_center_height_m=0.02135,
        height_independent=True,
    )
    assert result.status == "selected"
    assert result.selected.x_px == pytest.approx(x, abs=1.5)
    assert result.selected.y_px == pytest.approx(y, abs=1.5)
    assert result.selected.size_point_lfu_m[2] == pytest.approx(point[2], abs=0.015)


def test_detection_cannot_select_an_outside_or_empty_scene(monkeypatch):
    camera = v3_camera()
    search = replace(
        image_search(1280, 800), outline_px=((600, 300), (700, 300), (700, 400), (600, 400))
    )
    for candidates, expected in (
        ((), "not_found"),
        ((ReferenceBall(800, 350, 25, 490),), "no_consistent_candidate"),
    ):
        monkeypatch.setattr(ranges, "reference_ball_candidates", lambda *_a, **_k: candidates)
        result = ranges.estimate_patch_ball(
            np.zeros((3, 800, 1280), np.uint8),
            camera,
            search=search,
            ball_center_height_m=0.02135,
            height_independent=True,
        )
        assert result.status == expected
        assert result.selected is None

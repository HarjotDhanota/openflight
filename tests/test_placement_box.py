"""A search region in pixels for the whole-frame estimator, and the projection it rests on."""

import math

import numpy as np
import pytest

from openflight.camera.reference_ball_range import (
    estimate_reference_ball_range,
    project_to_pixel,
    scale_placement_box,
)
from tests.camera_helpers import lit_spheres as _lit_spheres, v3_camera as _v3

RADIUS_M = 0.0427 / 2.0


def _ball_pixel(camera, lateral_m, distance_m, height_m=RADIUS_M):
    origin = np.asarray(camera.camera_origin_lfu)
    forward = math.sqrt(distance_m**2 - lateral_m**2 - (height_m - origin[2]) ** 2)
    return project_to_pixel(camera, (lateral_m, origin[1] + forward, height_m))


@pytest.mark.parametrize("pitch_deg,roll_deg", [(0.0, 0.0), (2.0, 0.0), (-1.5, 3.0)])
def test_projection_inverts_the_camera_rays(pitch_deg, roll_deg):
    camera = _v3(pitch_deg=pitch_deg, roll_deg=roll_deg)
    point = np.asarray([0.11, 1.4, 0.03])

    pixel = project_to_pixel(camera, point)

    ray = camera.ray_model.rays(np.asarray(pixel))
    expected = (point - np.asarray(camera.camera_origin_lfu)) / np.linalg.norm(
        point - np.asarray(camera.camera_origin_lfu)
    )
    np.testing.assert_allclose(ray, expected, atol=1e-7)


def test_a_box_in_one_mode_is_the_other_mode_s_box_scaled():
    halved = scale_placement_box((562, 361, 718, 491), 0.5)

    assert halved == (281, 180, 359, 246)
    assert all(isinstance(value, int) for value in halved)


def _box_around(x, y, width, height):
    return (int(x - width / 2), int(y - height / 2), int(x + width / 2), int(y + height / 2))


def _two_balls(camera):
    balls = []
    for lateral in (0.0, 0.25):
        x, y = _ball_pixel(camera, lateral, 1.34)
        diameter = camera.focal_size_px * 0.0427 / 1.34
        balls.append((x, y, diameter / 2.0, 90.0))
    return balls, _lit_spheres(camera.image_height_px, camera.image_width_px, balls)


def test_the_search_looks_only_inside_the_placement_box():
    camera = _v3(640, 400)
    balls, frames = _two_balls(camera)
    box = _box_around(balls[0][0], balls[0][1], 78, 66)

    unboxed = estimate_reference_ball_range(
        frames, camera, ball_center_height_m=RADIUS_M, plausible_radar_range_m=(0.6, 3.5)
    )
    boxed = estimate_reference_ball_range(
        frames,
        camera,
        ball_center_height_m=RADIUS_M,
        plausible_radar_range_m=(0.6, 3.5),
        placement_box_px=box,
    )
    moved = _box_around(balls[1][0], balls[1][1], 78, 66)
    spare = estimate_reference_ball_range(
        frames,
        camera,
        ball_center_height_m=RADIUS_M,
        plausible_radar_range_m=(0.6, 3.5),
        placement_box_px=moved,
    )

    assert unboxed.diagnostics["plausible_candidate_count"] == 2
    assert boxed.status == "selected"
    assert boxed.selected.x_px == pytest.approx(balls[0][0], abs=2.0)
    assert boxed.diagnostics["plausible_candidate_count"] == 1
    assert boxed.diagnostics["placement_box_px"] == list(box)
    # a box dragged onto the spare ball finds that ball and nothing else
    assert unboxed.selected.x_px == pytest.approx(balls[0][0], abs=2.0)
    assert spare.status == "selected"
    assert spare.selected.x_px == pytest.approx(balls[1][0], abs=2.0)


def test_a_box_dragged_onto_the_sky_finds_nothing():
    camera = _v3(640, 400)
    _balls, frames = _two_balls(camera)
    # a bright ball-sized thing high in the picture, where the box now is
    frames[:, 40:52, 300:312] = 200
    sky = (230, 0, 308, 66)

    result = estimate_reference_ball_range(
        frames,
        camera,
        ball_center_height_m=RADIUS_M,
        plausible_radar_range_m=(0.6, 3.5),
        placement_box_px=sky,
    )

    assert result.selected is None
    assert result.status in {"not_found", "no_consistent_candidate"}
    assert all(item.rejection_reason is not None for item in result.candidates)

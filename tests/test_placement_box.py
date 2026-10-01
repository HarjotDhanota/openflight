"""A search region in pixels for the whole-frame estimator, and the projection it rests on.

The tester's pixel box (P7-4) became the ground patch (P8-1, tests/test_ground_patch.py);
these keep the estimator's own ``placement_box_px`` region honest.
"""

import math

import numpy as np
import pytest

from openflight.camera.reference_ball_range import (
    BallPlaneCamera,
    camera_range_estimator_policy,
    estimate_reference_ball_range,
    project_to_pixel,
    scale_placement_box,
)

RADIUS_M = 0.04267 / 2.0
V3_FOCAL_1280 = 933.3334


def _v3(width=1280, height=800, *, pitch_deg=0.0, roll_deg=0.0):
    """The v3 enclosure: lens 95 mm up, the IWR phase centre below and just behind it."""
    return BallPlaneCamera.nominal(
        focal_px=V3_FOCAL_1280 * width / 1280,
        image_width_px=width,
        image_height_px=height,
        pitch_deg=pitch_deg,
        roll_correction_deg=roll_deg,
        mirror_horizontal=False,
        camera_origin_lfu=(0.0, 0.0, 0.095),
        radar_origin_lfu=(0.0, -0.0014, 0.0588),
        angular_uncertainty_deg=1.0,
        focal_relative_uncertainty=0.08,
    )


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
    return (
        int(x - width / 2.0),
        int(y - height / 2.0),
        int(x + width / 2.0),
        int(y + height / 2.0),
    )


def _lit_spheres(height, width, spheres, *, seed=4):
    rng = np.random.default_rng(seed)
    yy, xx = np.indices((height, width), dtype=float)
    image = 55.0 + rng.normal(0.0, 1.0, (height, width))
    light = np.asarray([-0.35, -0.62, 0.70])
    light /= np.linalg.norm(light)
    for cx, cy, radius, diffuse in spheres:
        dx, dy = xx - cx, yy - cy
        inside = dx * dx + dy * dy <= radius * radius
        nz = np.sqrt(np.clip(radius * radius - dx * dx - dy * dy, 0.0, None)) / radius
        shading = np.clip(
            dx / radius * light[0] + dy / radius * light[1] + nz * light[2], 0.0, None
        )
        image[inside] = 42.0 + diffuse * shading[inside]
    noise = rng.normal(0.0, 0.8, (7, height, width))
    return np.clip(np.round(image[None] + noise), 0, 255).astype(np.uint8)


def _two_balls(camera):
    """The tester's ball straight ahead, and a spare ball 0.25 m to its right."""
    balls = []
    for lateral in (0.0, 0.25):
        x, y = _ball_pixel(camera, lateral, 1.34)
        diameter = camera.focal_size_px * 0.04267 / 1.34
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
    # a box dragged onto the spare ball finds that ball and nothing else, though
    # the whole-frame search would always have preferred the one straight ahead
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
    # whatever was fitted there still has to pass the physical hitting-area checks
    assert all(item.rejection_reason is not None for item in result.candidates)


def test_the_estimator_policy_names_the_patch():
    policy = camera_range_estimator_policy()

    assert policy["version"] == 5
    assert policy["search_region"] == "the_ground_patch_outline_only"
    assert policy["patch"]["size_m"] == 0.61
    assert policy["floor_row"] == "diagnostic_residual_never_a_refusal"
    assert policy["independent_save_confirmation"] is True

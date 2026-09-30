"""The placement box the tester drags over the hitting spot (P7-4, D10 as changed 30 Sept)."""

import math

import numpy as np
import pytest

from openflight.camera.reference_ball_range import (
    PLACEMENT_BOX_COLUMN_PAD_DEG,
    PLACEMENT_BOX_HALF_WIDTH_M,
    PLACEMENT_BOX_NOMINAL_DISTANCE_M,
    PLACEMENT_BOX_PITCH_PAD_DEG,
    BallPlaneCamera,
    camera_range_estimator_policy,
    estimate_reference_ball_range,
    placement_box_at,
    placement_box_geometry,
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


def test_the_default_box_is_the_zone_straight_ahead_at_the_v3_rig():
    camera = _v3()

    geometry = placement_box_geometry(camera, ball_center_height_m=RADIUS_M, roll_deg=0.0)
    x0, y0, x1, y1 = geometry.default_box_px

    focal = camera.focal_size_px
    width = 2.0 * focal * PLACEMENT_BOX_HALF_WIDTH_M / PLACEMENT_BOX_NOMINAL_DISTANCE_M
    width += 2.0 * focal * math.tan(math.radians(PLACEMENT_BOX_COLUMN_PAD_DEG))
    assert (x1 - x0) == pytest.approx(width, abs=2.0)
    assert (x0 + x1) / 2.0 == pytest.approx(640.0, abs=1.0)
    assert geometry.size_px == (x1 - x0, y1 - y0)
    # every ball the zone allows is inside, from on the surface to on a raised mat
    for distance in (1.2, 1.35, 1.5):
        for height in (RADIUS_M - 0.010, RADIUS_M, RADIUS_M + 0.090):
            x, y = _ball_pixel(camera, 0.0, distance, height)
            assert x0 <= x < x1 and y0 <= y < y1
    # and the rows are padded for the LIS3DH pitch beyond the zone itself
    pad = focal * math.tan(math.radians(PLACEMENT_BOX_PITCH_PAD_DEG))
    lowest = _ball_pixel(camera, 0.0, 1.2, RADIUS_M - 0.010)[1]
    highest = _ball_pixel(camera, 0.0, 1.2, RADIUS_M + 0.090)[1]
    assert y1 >= lowest + pad - 1.0
    assert y0 <= highest - pad + 1.0
    # at 95 mm the whole 1.2-1.5 m depth is only about 11 rows: the box cannot
    # enforce distance, and it is not meant to
    near = _ball_pixel(camera, 0.0, 1.2)[1]
    far = _ball_pixel(camera, 0.0, 1.5)[1]
    assert 8.0 < near - far < 14.0


def test_an_unapplied_roll_reading_makes_the_box_taller():
    camera = _v3()

    level = placement_box_geometry(camera, ball_center_height_m=RADIUS_M, roll_deg=0.0)
    rolled = placement_box_geometry(camera, ball_center_height_m=RADIUS_M, roll_deg=-2.9)
    unknown = placement_box_geometry(camera, ball_center_height_m=RADIUS_M, roll_deg=None)

    assert rolled.size_px[1] > level.size_px[1]
    assert unknown.size_px[1] >= rolled.size_px[1]
    assert rolled.size_px[0] == level.size_px[0]


def test_the_640x400_box_is_the_1280x800_box_halved():
    full = placement_box_geometry(_v3(), ball_center_height_m=RADIUS_M, roll_deg=0.0)
    half = placement_box_geometry(_v3(640, 400), ball_center_height_m=RADIUS_M, roll_deg=0.0)

    halved = scale_placement_box(full.default_box_px, 0.5)

    np.testing.assert_allclose(halved, half.default_box_px, atol=1.5)
    assert all(isinstance(value, int) for value in halved)


def test_a_dragged_box_keeps_its_size_and_stays_inside_the_frame():
    size = (155, 130)

    assert placement_box_at((400, 300), size, (1280, 800)) == (400, 300, 555, 430)
    assert placement_box_at((-40, 760), size, (1280, 800)) == (0, 670, 155, 800)
    assert placement_box_at((1200.6, -3.2), size, (1280, 800)) == (1125, 0, 1280, 130)
    with pytest.raises(ValueError):
        placement_box_at((0, 0), (1300, 130), (1280, 800))


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
    geometry = placement_box_geometry(camera, ball_center_height_m=RADIUS_M, roll_deg=0.0)
    balls, frames = _two_balls(camera)

    unboxed = estimate_reference_ball_range(
        frames, camera, ball_center_height_m=RADIUS_M, plausible_radar_range_m=(0.6, 3.5)
    )
    boxed = estimate_reference_ball_range(
        frames,
        camera,
        ball_center_height_m=RADIUS_M,
        plausible_radar_range_m=(0.6, 3.5),
        placement_box_px=geometry.default_box_px,
    )
    size = geometry.size_px
    moved = placement_box_at(
        (balls[1][0] - size[0] / 2.0, balls[1][1] - size[1] / 2.0), size, (640, 400)
    )
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
    assert boxed.diagnostics["placement_box_px"] == list(geometry.default_box_px)
    # a box dragged onto the spare ball finds that ball and nothing else, though
    # the whole-frame search would always have preferred the one straight ahead
    assert unboxed.selected.x_px == pytest.approx(balls[0][0], abs=2.0)
    assert spare.status == "selected"
    assert spare.selected.x_px == pytest.approx(balls[1][0], abs=2.0)


def test_a_box_dragged_onto_the_sky_finds_nothing():
    camera = _v3(640, 400)
    geometry = placement_box_geometry(camera, ball_center_height_m=RADIUS_M, roll_deg=0.0)
    _balls, frames = _two_balls(camera)
    # a bright ball-sized thing high in the picture, where the box now is
    frames[:, 40:52, 300:312] = 200
    sky = placement_box_at((230, 0), geometry.size_px, (640, 400))

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


def test_the_estimator_policy_names_the_placement_box():
    policy = camera_range_estimator_policy()

    assert policy["version"] == 4
    assert policy["search_region"] == "tester_placed_box_then_hitting_area_in_world_coordinates"
    assert policy["placement_box"]["half_width_m"] == PLACEMENT_BOX_HALF_WIDTH_M
    assert policy["placement_box"]["nominal_distance_m"] == PLACEMENT_BOX_NOMINAL_DISTANCE_M
    assert policy["independent_save_confirmation"] is True

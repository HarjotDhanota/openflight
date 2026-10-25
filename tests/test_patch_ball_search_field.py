"""The patch ball search on recorded scenes (fork only: it loads field frames);
scenes are composites (scripts/analysis/patch_fixtures.py)."""

from pathlib import Path

import numpy as np
import pytest

from openflight.camera.ground_patch import (
    BALL_CENTER_HEIGHT_M,
    PatchSearch,
    patch_at,
    patch_from_centre_pixel,
)
from openflight.camera.reference_ball_range import BallPlaneCamera, estimate_patch_ball
from tests.test_patch_ball_search import V3_FOCAL_1280, _frames, search_for, v3

FIXTURES = Path(__file__).parent / "fixtures" / "ground_patch"
BALL_DETECTOR = Path(__file__).parent / "fixtures" / "ball_detector"
# the ball at (744, 518), inside the dragged box
INDOOR_BALL_PX = (744.0, 518.0)
# a ball 1.25 m from the radar is about 1.252 m from the lens: 31.8 px at 933 px
INDOOR_BALL_DIAMETER_PX = V3_FOCAL_1280 * 0.0427 / 1.252


def indoor_background(level=1.0):
    """The indoor light-screen frame (300 us x 8), brightness scaled by ``level``."""
    data = np.load(FIXTURES / "indoor-c77d227da087-scene.npz")
    background = data["background"].astype(np.float32)
    x0, y0 = (int(value) for value in data["background_origin_px"])
    image = np.full((800, 1280), float(np.median(background)), dtype=np.float32)
    image[y0 : y0 + background.shape[0], x0 : x0 + background.shape[1]] = background
    black = 16.0
    return black + (image - black) * level


def render_ball(image, ball_px, diameter_px, peak_dn):
    """A white ball in diffuse room light, lit from above, drawn over ``image``."""
    height, width = image.shape
    yy, xx = np.indices((height, width), dtype=np.float32)
    radius = diameter_px / 2.0
    dx, dy = (xx - ball_px[0]) / radius, (yy - ball_px[1]) / radius
    nz = np.sqrt(np.clip(1.0 - dx * dx - dy * dy, 0.0, None))
    light = np.asarray([-0.2, -0.55, 0.81])
    light /= np.linalg.norm(light)
    lit = np.clip(dx * light[0] + dy * light[1] + nz * light[2], 0.0, None)
    ball = 16.0 + (peak_dn - 16.0) * (0.45 + 0.55 * lit)
    alpha = np.clip(radius + 0.5 - np.hypot(xx - ball_px[0], yy - ball_px[1]), 0.0, 1.0)
    return alpha * ball + (1.0 - alpha) * image


def indoor_scene(ball_px=INDOOR_BALL_PX, diameter_px=INDOOR_BALL_DIAMETER_PX, level=1.0):
    """The indoor light-screen frame with a ball composited where the tester saw it."""
    image = indoor_background(level)
    carpet = float(np.median(image[450:600, 600:900])) - 16.0
    return _frames(render_ball(image, ball_px, diameter_px, 16.0 + 2.5 * carpet))


def outdoor_scene():
    data = np.load(FIXTURES / "outdoors-test-7-5a7821af3641-camera.npz")
    crop = data["frame"].astype(np.float32)
    x0, y0 = (int(value) for value in data["origin_px"])
    image = np.full((800, 1280), float(np.median(crop)), dtype=np.float32)
    image[y0 : y0 + crop.shape[0], x0 : x0 + crop.shape[1]] = crop
    return _frames(image), tuple(float(value) for value in data["ball_px"])


def test_the_indoor_ball_is_found_in_a_patch_placed_over_it_by_eye():
    """The patch finds a ball that a floor-row term would refuse."""
    camera = v3(pitch_deg=1.72)  # the LIS3DH's reading, not the camera's true tilt
    frames = indoor_scene()
    patch = patch_from_centre_pixel(camera, (INDOOR_BALL_PX[0], INDOOR_BALL_PX[1] + 10.0))

    result = estimate_patch_ball(
        frames, camera, search=search_for(camera, patch), ball_center_height_m=BALL_CENTER_HEIGHT_M
    )

    assert result.status == "selected", [c.rejection_reason for c in result.candidates]
    ball = result.selected
    assert ball.x_px == pytest.approx(INDOOR_BALL_PX[0], abs=2.0)
    assert ball.y_px == pytest.approx(INDOOR_BALL_PX[1], abs=2.0)
    assert ball.diameter_px == pytest.approx(INDOOR_BALL_DIAMETER_PX, rel=0.12)
    assert ball.size_radar_range_m == pytest.approx(1.25, abs=0.15)
    assert ball.rejection_reason is None
    assert ball.floor_height_residual_m is not None
    assert abs(ball.floor_height_residual_m) > 0.03


def test_the_indoor_ball_is_found_when_the_patch_was_placed_by_its_distance():
    """With the patch 1.25 m out, unmodelled tilt puts the ball below the drawn
    patch but inside the pad."""
    camera = v3(pitch_deg=1.72)
    frames = indoor_scene()
    search = search_for(camera, patch_at(camera, 1.25, 0.1))

    result = estimate_patch_ball(
        frames, camera, search=search, ball_center_height_m=BALL_CENTER_HEIGHT_M
    )

    assert result.status == "selected"
    assert result.selected.x_px == pytest.approx(INDOOR_BALL_PX[0], abs=2.0)


def test_the_outdoors_test_7_ball_is_found_in_its_patch():
    frames, (x, y, diameter) = outdoor_scene()
    camera = v3(pitch_deg=0.3)
    patch = patch_from_centre_pixel(camera, (x, y + 8.0))

    result = estimate_patch_ball(
        frames, camera, search=search_for(camera, patch), ball_center_height_m=BALL_CENTER_HEIGHT_M
    )

    assert result.status == "selected"
    assert result.selected.x_px == pytest.approx(x, abs=1.5)
    assert result.selected.y_px == pytest.approx(y, abs=1.5)
    assert result.selected.diameter_px == pytest.approx(diameter, rel=0.08)


def test_a_ball_whose_centre_is_just_inside_the_outline_s_edge_is_found_whole():
    """A ball centred 2 rows inside the box's bottom edge: the search region must
    hold the whole ball."""
    camera = v3()
    diameter = 34.0
    ball_px = (531.0, 531.0)
    search = PatchSearch(
        outline_px=((483.2, 533.3), (796.8, 533.3), (796.8, 397.8), (483.2, 397.8)),
        diameter_px=(8.0, 76.0),
        centre_lateral_m=0.0,
        half_size_m=0.3,
        radar_window_m=(0.58, 4.1),
    )
    frames = indoor_scene(ball_px=ball_px, diameter_px=diameter)

    result = estimate_patch_ball(frames, camera, search=search, ball_center_height_m=0.0213)

    assert result.status == "selected"
    assert result.selected.y_px == pytest.approx(ball_px[1], abs=2.0)
    roi = result.diagnostics["roi_px"]
    assert roi[3] >= ball_px[1] + diameter / 2.0


def empty_mat_scene():
    """Harjot-outdoor-test-1's setup frame (dusk, 6 mm lens, 3000 us x 1): the ball
    on the mat, a stray ball beyond it, and the mat's lit back edge, empty elsewhere."""
    data = np.load(BALL_DETECTOR / "outdoor-test-1-87a0393da1ec-empty-mat-edge.npz")
    crop = data["frame"].astype(np.float32)
    x0, y0 = (int(value) for value in data["origin_px"])
    pad = ((y0, 800 - y0 - crop.shape[0]), (x0, 1280 - x0 - crop.shape[1]))
    x_lo, y_lo, x_hi, y_hi = (float(value) for value in data["box_px"])
    search = PatchSearch(
        outline_px=((x_lo, y_hi), (x_hi, y_hi), (x_hi, y_lo), (x_lo, y_lo)),
        diameter_px=(19.52, 193.16),
        centre_lateral_m=0.0,
        half_size_m=0.3,
        radar_window_m=(0.4797, 4.1),
    )
    return _frames(np.pad(crop, pad, mode="edge")), search


def six_mm(pitch_deg=0.22):
    return BallPlaneCamera.nominal(
        focal_px=2000.0,
        image_width_px=1280,
        image_height_px=800,
        pitch_deg=pitch_deg,
        roll_correction_deg=0.0,
        mirror_horizontal=False,
        camera_origin_lfu=(0.0, 0.0, 0.095),
        radar_origin_lfu=(-0.0019, -0.0314, 0.0522),
        angular_uncertainty_deg=3.0,
        focal_relative_uncertainty=0.08,
    )


def test_the_empty_mat_edge_is_not_a_ball_and_the_ball_on_the_mat_wins():
    """Harjot-outdoor-test-1: fits that slid along the mat's back edge stopped one
    seed radius down, all on one row (427.3), and outscored the ball: "more than one
    ball-like thing is in the box"."""
    frames, search = empty_mat_scene()

    result = estimate_patch_ball(
        frames, six_mm(), search=search, ball_center_height_m=BALL_CENTER_HEIGHT_M
    )

    assert result.status == "selected", [
        (round(c.x_px), round(c.y_px, 1), round(c.diameter_px, 1), c.fit_quality)
        for c in result.candidates
    ]
    ball = result.selected
    # the ball's lit top is at row 400 and its sides at columns 703 and 744 (rendered)
    assert ball.x_px == pytest.approx(723.0, abs=4.0)
    assert ball.y_px == pytest.approx(421.0, abs=4.0)
    assert 38.0 <= ball.diameter_px <= 50.0
    plausible = [c for c in result.candidates if c.rejection_reason is None]
    assert not any(abs(c.x_px - 723.0) > 30.0 and abs(c.y_px - 427.3) < 3.0 for c in plausible)
    ratio = result.diagnostics["second_to_best_quality"]
    assert ratio is None or ratio < 0.75

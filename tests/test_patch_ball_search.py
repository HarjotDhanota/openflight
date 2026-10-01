"""The camera looks for the ball only inside the patch (P8-2).

The indoor scene is a COMPOSITE: harjot-indoor-test-1 saved no frame with the
ball in it, so a crop of its light-screen frame (taken before the ball was
placed) gets a ball sprite cut from Outdoors-test-7, pasted where the tester's
screenshot showed the ball, at about the size a ball 1.25 m out has
(scripts/analysis/patch_fixtures.py).
"""

import math
from pathlib import Path

import numpy as np
import pytest

from openflight.camera.ground_patch import (
    BALL_CENTER_HEIGHT_M,
    PatchSearch,
    patch_at,
    patch_from_centre_pixel,
    project_patch,
    tilt_pads_deg,
)
from openflight.camera.reference_ball_range import (
    BallPlaneCamera,
    estimate_patch_ball,
    project_to_pixel,
)

FIXTURES = Path(__file__).parent / "fixtures" / "ground_patch"
V3_FOCAL_1280 = 933.3334
# the tester's screenshot: the ball at (744, 518), inside the box they had dragged
INDOOR_BALL_PX = (744.0, 518.0)
# a ball 1.25 m from the radar is about 1.252 m from the lens: 31.8 px at 933 px
INDOOR_BALL_DIAMETER_PX = V3_FOCAL_1280 * 0.04267 / 1.252


def v3(pitch_deg=0.0, width=1280, height=800):
    return BallPlaneCamera.nominal(
        focal_px=V3_FOCAL_1280 * width / 1280,
        image_width_px=width,
        image_height_px=height,
        pitch_deg=pitch_deg,
        roll_correction_deg=0.0,
        mirror_horizontal=False,
        camera_origin_lfu=(0.0, 0.0, 0.095),
        radar_origin_lfu=(0.0, -0.0014, 0.0588),
        angular_uncertainty_deg=1.0,
        focal_relative_uncertainty=0.08,
    )


def search_for(camera, patch, *, calibrated=False, roll_deg=-2.62):
    pad, window = tilt_pads_deg(calibrated)
    projection = project_patch(
        camera, patch, tilt_pad_deg=pad, window_pad_deg=window, roll_deg=roll_deg
    )
    return PatchSearch.from_projection(projection)


def _frames(image, count=7, seed=3):
    rng = np.random.default_rng(seed)
    noise = rng.normal(0.0, 0.8, (count, *image.shape))
    return np.clip(np.round(image[None] + noise), 0, 255).astype(np.uint8)


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
    """The indoor light-screen frame with a ball composited where the tester saw it.

    The ball's brightest part is 2.5 times the carpet's signal above black, as a
    white ball on grey carpet under the same room light.
    """
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
    """harjot-indoor-test-1: the old floor-row term refused this ball; the patch finds it."""
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
    # its apparent size puts it about where the tape did, far from the radar's 1.575 m
    assert ball.size_radar_range_m == pytest.approx(1.25, abs=0.15)
    # the floor row's implied lens height is off (the tilt is uncalibrated): kept as a
    # diagnostic, never a refusal
    assert ball.rejection_reason is None
    assert ball.floor_height_residual_m is not None
    assert abs(ball.floor_height_residual_m) > 0.03


def test_the_indoor_ball_is_found_when_the_patch_was_placed_by_its_distance():
    """The same scene, the patch put 1.25 m out by its displayed distance: the camera's
    3-4 deg of unmodelled tilt puts the ball well below the drawn patch, inside the pad."""
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
    return _frames(image, seed=seed)


def _ball(camera, lateral, forward):
    x, y = project_to_pixel(camera, (lateral, forward, BALL_CENTER_HEIGHT_M))
    distance = math.dist((lateral, forward, BALL_CENTER_HEIGHT_M), camera.camera_origin_lfu)
    return x, y, V3_FOCAL_1280 * 0.04267 / distance / 2.0


def test_a_patch_2_m_out_and_to_the_side_finds_its_ball_and_nothing_else():
    camera = v3()
    ball = _ball(camera, 0.3, 2.0)
    near_spare = _ball(camera, -0.2, 1.1)
    frames = _lit_spheres(800, 1280, [(*ball, 90.0), (*near_spare, 90.0)])
    patch = patch_at(camera, 2.0, 0.3)

    result = estimate_patch_ball(
        frames, camera, search=search_for(camera, patch), ball_center_height_m=BALL_CENTER_HEIGHT_M
    )

    assert result.status == "selected"
    assert result.selected.x_px == pytest.approx(ball[0], abs=1.5)
    assert result.selected.diameter_px == pytest.approx(2.0 * ball[2], rel=0.15)
    assert result.selected.size_radar_range_m == pytest.approx(2.0, rel=0.15)
    # a patch this far to the side is fine: the score is relative to the patch
    assert result.selected.score < 1.0
    assert all(
        abs(item.x_px - near_spare[0]) > 5.0 for item in result.candidates if item.score is not None
    )


def test_a_blob_out_of_scale_for_the_patch_is_refused():
    camera = v3()
    patch = patch_at(camera, 2.0)
    x, y, radius = _ball(camera, 0.0, 2.0)
    # three times the size a ball 2 m out can have, at the patch's centre
    frames = _lit_spheres(800, 1280, [(x, y, 3.0 * radius, 90.0)])
    search = search_for(camera, patch, calibrated=True)

    result = estimate_patch_ball(
        frames, camera, search=search, ball_center_height_m=BALL_CENTER_HEIGHT_M
    )

    low, high = search.diameter_px
    # nothing outside the sizes the patch's distances allow is taken for the ball
    for item in result.candidates:
        if not low <= item.diameter_px <= high:
            assert "not a ball at the patch's distances" in (item.rejection_reason or "")
    assert result.selected is None or low <= result.selected.diameter_px <= high
    assert not any(
        abs(item.diameter_px - 6.0 * radius) < 2.0 * radius
        for item in result.candidates
        if item.rejection_reason is None
    )


def test_the_search_never_leaves_the_patch():
    camera = v3()
    ball = _ball(camera, 0.0, 1.25)
    frames = _lit_spheres(800, 1280, [(*ball, 90.0)])
    elsewhere = patch_at(camera, 1.25, 0.9)

    result = estimate_patch_ball(
        frames, camera, search=search_for(camera, elsewhere), ball_center_height_m=0.021335
    )

    assert result.selected is None
    outline = search_for(camera, elsewhere).outline_px
    assert len(result.diagnostics["search_outline_px"]) == len(outline)
    x0, _y0, x1, _y1 = result.diagnostics["roi_px"]
    assert x0 >= math.floor(min(x for x, _y in outline)) - 5
    assert x1 <= math.ceil(max(x for x, _y in outline)) + 6
    # there is no whole-frame search: a search without a patch is refused
    with pytest.raises(TypeError):
        estimate_patch_ball(  # pylint: disable=missing-kwoa
            frames, camera, ball_center_height_m=0.021335
        )

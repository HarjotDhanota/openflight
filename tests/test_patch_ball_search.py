"""The camera looks for the ball only inside the patch (scenes: scripts/analysis/patch_fixtures.py)."""

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
from openflight.camera.reference_ball_range import estimate_patch_ball, project_to_pixel
from tests.camera_helpers import V3_FOCAL_1280, lit_spheres as _lit_spheres, v3_camera as v3

FIXTURES = Path(__file__).parent / "fixtures" / "ground_patch"
# the ball at (744, 518), inside the dragged box
INDOOR_BALL_PX = (744.0, 518.0)
# a ball 1.25 m from the radar is about 1.252 m from the lens: 31.8 px at 933 px
INDOOR_BALL_DIAMETER_PX = V3_FOCAL_1280 * 0.0427 / 1.252


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


@pytest.mark.parametrize("placed_by", ["eye", "distance"])
def test_the_indoor_ball_is_found_in_a_patch_placed_by_eye_or_by_distance(placed_by):
    # the LIS3DH's reading, not the camera's true tilt: by distance the ball sits in the pad
    camera = v3(pitch_deg=1.72)
    if placed_by == "eye":
        patch = patch_from_centre_pixel(camera, (INDOOR_BALL_PX[0], INDOOR_BALL_PX[1] + 10.0))
    else:
        patch = patch_at(camera, 1.25, 0.1)

    result = estimate_patch_ball(
        indoor_scene(),
        camera,
        search=search_for(camera, patch),
        ball_center_height_m=BALL_CENTER_HEIGHT_M,
    )

    assert result.status == "selected", [c.rejection_reason for c in result.candidates]
    ball = result.selected
    assert ball.x_px == pytest.approx(INDOOR_BALL_PX[0], abs=2.0)
    if placed_by == "eye":  # a ball a floor-row term would refuse
        assert ball.y_px == pytest.approx(INDOOR_BALL_PX[1], abs=2.0)
        assert ball.diameter_px == pytest.approx(INDOOR_BALL_DIAMETER_PX, rel=0.12)
        assert ball.size_radar_range_m == pytest.approx(1.25, abs=0.15)
        assert ball.rejection_reason is None
        assert abs(ball.floor_height_residual_m) > 0.03


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


def _ball(camera, lateral, forward):
    x, y = project_to_pixel(camera, (lateral, forward, BALL_CENTER_HEIGHT_M))
    distance = math.dist((lateral, forward, BALL_CENTER_HEIGHT_M), camera.camera_origin_lfu)
    return x, y, V3_FOCAL_1280 * 0.0427 / distance / 2.0


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
    assert result.selected.score < 1.0
    assert all(
        abs(item.x_px - near_spare[0]) > 5.0 for item in result.candidates if item.score is not None
    )


def test_a_blob_out_of_scale_for_the_patch_is_refused():
    camera = v3()
    patch = patch_at(camera, 2.0)
    x, y, radius = _ball(camera, 0.0, 2.0)
    frames = _lit_spheres(800, 1280, [(x, y, 3.0 * radius, 90.0)])
    search = search_for(camera, patch, calibrated=True)

    result = estimate_patch_ball(
        frames, camera, search=search, ball_center_height_m=BALL_CENTER_HEIGHT_M
    )

    low, high = search.diameter_px
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
        frames, camera, search=search_for(camera, elsewhere), ball_center_height_m=0.02135
    )

    assert result.selected is None
    search = search_for(camera, elsewhere)
    outline = search.outline_px
    assert len(result.diagnostics["search_outline_px"]) == len(outline)
    x0, _y0, x1, _y1 = result.diagnostics["roi_px"]
    reach = 5 + search.diameter_px[1] / 2.0
    assert x0 >= math.floor(min(x for x, _y in outline) - reach)
    assert x1 <= math.ceil(max(x for x, _y in outline) + reach) + 1
    with pytest.raises(TypeError):
        estimate_patch_ball(  # pylint: disable=missing-kwoa
            frames, camera, ball_center_height_m=0.02135
        )


def test_a_ball_whose_centre_is_just_inside_the_outline_s_edge_is_found_whole():
    # centred 2 rows inside the box's bottom edge: the search region must hold all of it
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

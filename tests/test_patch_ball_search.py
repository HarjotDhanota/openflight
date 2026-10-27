"""The camera looks for the ball only inside the patch; scenes are synthetic lit
spheres. The recorded scenes are ``test_patch_ball_search_field`` (fork only)."""

import math

import numpy as np
import pytest

from openflight.camera.ground_patch import (
    BALL_CENTER_HEIGHT_M,
    PatchSearch,
    patch_at,
    project_patch,
    tilt_pads_deg,
)
from openflight.camera.reference_ball_range import estimate_patch_ball, project_to_pixel
from tests.camera_helpers import V3_FOCAL_1280, lit_spheres as _lit_spheres, v3_camera as v3


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

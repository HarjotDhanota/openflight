"""The lit-sphere ball model: centre and light from the pixels, size from the distance."""

import numpy as np
import pytest

from openflight.camera.ball_model import fit_lit_ball
from openflight.camera.club_motion import detect_reference_ball

LIGHT_UPPER_LEFT = np.array([-0.55, -0.55, 0.63]) / np.linalg.norm([-0.55, -0.55, 0.63])


def _lit_ball(
    height,
    width,
    cx,
    cy,
    radius,
    *,
    ground=48.0,
    texture=3.0,
    buried=0.0,
    door_gap_y=None,
    seed=3,
):
    """A matte sphere lit from the upper left, with its cast shadow to the lower right.

    ``buried`` hides that fraction of the ball's height, from the bottom, behind
    ground texture, as grass or carpet pile does. ``door_gap_y`` puts a bright
    door panel above a dark gap behind the ball.
    """
    rng = np.random.default_rng(seed)
    yy, xx = np.indices((height, width)).astype(np.float64)
    image = ground + rng.normal(0, texture, (height, width))
    if door_gap_y is not None:
        image[yy < door_gap_y] = 95 + rng.normal(0, texture, (height, width))[yy < door_gap_y]
        image[(yy >= door_gap_y) & (yy < door_gap_y + 8)] = 30
    dx, dy = xx - cx, yy - cy
    inside = dx**2 + dy**2 <= radius**2
    nz = np.sqrt(np.clip(radius**2 - dx**2 - dy**2, 0, None)) / radius
    shade = np.clip(
        dx / radius * LIGHT_UPPER_LEFT[0]
        + dy / radius * LIGHT_UPPER_LEFT[1]
        + nz * LIGHT_UPPER_LEFT[2],
        0,
        None,
    )
    shadow = np.hypot(xx - (cx + 0.7 * radius), yy - (cy + 0.9 * radius)) <= 0.9 * radius
    image[shadow & ~inside] -= 7
    image[inside] = 38 + 60 * shade[inside]
    if buried:
        hidden = inside & (yy > cy + radius * (1.0 - 2.0 * buried))
        image[hidden] = ground + rng.normal(0, 3 * texture, hidden.sum())
    noise = np.random.default_rng(seed + 1).normal(0, 2, (5, height, width))
    return np.clip(image[None] + noise, 0, 255).astype(np.uint8)


def test_with_the_size_known_the_centre_comes_from_the_lit_rim_and_shading():
    frames = _lit_ball(400, 640, 300.3, 260.6, 16.0)

    fit = fit_lit_ball(np.median(frames, axis=0), 297.0, 257.0, 14.0, expected_radius=16.0)

    assert fit is not None
    assert fit.x == pytest.approx(300.3, abs=0.6)
    assert fit.y == pytest.approx(260.6, abs=0.6)
    assert fit.radius_px == pytest.approx(16.0, rel=0.02)
    # the light it recovers comes from the upper left
    assert 200.0 <= fit.light_azimuth_deg <= 250.0


def test_a_ball_half_buried_in_grass_keeps_its_centre():
    frames = _lit_ball(400, 640, 300.3, 260.6, 16.0, buried=0.25, texture=5.0)

    fit = fit_lit_ball(np.median(frames, axis=0), 299.0, 256.0, 13.0, expected_radius=16.0)

    assert fit is not None
    assert fit.x == pytest.approx(300.3, abs=1.0)
    assert fit.y == pytest.approx(260.6, abs=1.0)


def test_a_dark_gap_above_the_ball_is_never_read_as_light_from_below():
    frames = _lit_ball(400, 640, 320.0, 262.0, 19.0, door_gap_y=236)

    fit = fit_lit_ball(np.median(frames, axis=0), 316.0, 252.0, 15.0, expected_radius=19.0)

    assert fit is not None
    assert fit.y == pytest.approx(262.0, abs=1.0)
    assert 170.0 <= fit.light_azimuth_deg % 360.0 or fit.light_azimuth_deg <= 10.0


def test_the_detector_takes_the_ball_not_the_door_when_it_knows_the_size():
    frames = _lit_ball(400, 640, 320.0, 262.0, 19.0, door_gap_y=236)

    ball = detect_reference_ball(frames, expected_diameter_px=38.0)

    assert ball.x == pytest.approx(320.0, abs=1.0)
    assert ball.y == pytest.approx(262.0, abs=1.0)
    assert ball.diameter_px == pytest.approx(38.0, rel=0.02)

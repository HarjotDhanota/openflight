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


def _two_balls(first, second, height=400, width=640, seed=5):
    """Two lit spheres on textured ground: (cx, cy, radius, brightness) each."""
    rng = np.random.default_rng(seed)
    yy, xx = np.indices((height, width)).astype(np.float64)
    image = 60 + rng.normal(0, 3, (height, width))
    for cx, cy, radius, gain in (first, second):
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
        image[inside] = 50 + gain * shade[inside]
    noise = rng.normal(0, 2, (5, height, width))
    return np.clip(image[None] + noise, 0, 255).astype(np.uint8)


def test_a_bright_door_stop_off_to_the_side_loses_to_a_dim_ball_ahead():
    # proportions from a real room: the door stop's white tip fitted 1.6 times
    # better than the ball, 31% of the frame's width off to the side
    frames = _two_balls((122.0, 262.0, 10.0, 75.0), (340.0, 250.0, 10.0, 45.0))

    ball = detect_reference_ball(frames, expected_diameter_px=20.0)

    assert ball.x == pytest.approx(340.0, abs=1.0)
    assert ball.y == pytest.approx(250.0, abs=1.0)


def test_the_row_the_floor_predicts_picks_the_ball_that_rests_there():
    # two equal balls ahead; only one sits where the distance and tilt put it
    frames = _two_balls((300.0, 200.0, 10.0, 80.0), (340.0, 300.0, 10.0, 80.0))

    ball = detect_reference_ball(frames, expected_diameter_px=20.0, expected_row_px=(296.0, 40.0))

    assert ball.y == pytest.approx(300.0, abs=1.0)


LIGHT_OVERHEAD = np.array([0.0, -np.sin(np.radians(48.0)), np.cos(np.radians(48.0))])


def _dim_room(  # pylint: disable=too-many-arguments,too-many-locals
    *,
    ball=True,
    door_stop=True,
    clipped_floor=False,
    streak=False,
    stray=False,
    levels=(18.0, 41.0, 35.7, 11.0),
    seed=11,
):
    """The 2 m placement in a dim room, from a real 994 us x2 frame at 1280x800.

    A 19.7 px ball lit from overhead sits where a dark storage bin (18 DN) meets
    the carpet (41 DN): its lit top is 11 DN over a shade of 36, so its underside
    is darker than the carpet it rests on. A door stop's flat, square white tip
    (65 DN, 11 x 9 px) sits far off to the left on a 35 DN baseboard.
    ``levels`` is (bin, carpet, ball shade, ball diffuse) in DN.
    """
    bin_dn, carpet_dn, shade_dn, diffuse_dn = levels
    rng = np.random.default_rng(seed)
    height, width = 800, 1280
    yy, xx = np.indices((height, width)).astype(np.float64)
    floor = 255.0 if clipped_floor else carpet_dn
    image = np.where(yy < 478, bin_dn, floor) + rng.normal(0, 1.5, (height, width))
    if door_stop:
        image[500:518, 180:320] = 35.0
        image[506:515, 261:272] = 65.0
    if ball:
        cx, cy, radius = 760.4, 481.2, 9.85
        dx, dy = xx - cx, yy - cy
        inside = dx**2 + dy**2 <= radius**2
        nz = np.sqrt(np.clip(radius**2 - dx**2 - dy**2, 0, None)) / radius
        lit = np.clip(dy / radius * LIGHT_OVERHEAD[1] + nz * LIGHT_OVERHEAD[2] + dx * 0.0, 0, None)
        shadow = (np.hypot(dx / 1.3, yy - (cy + 0.85 * radius)) <= 0.8 * radius) & ~inside
        image[shadow & (yy >= 478)] -= 9.0
        image[inside] = 255.0 if clipped_floor else shade_dn + diffuse_dn * lit[inside]
    if streak:
        # a bright diagonal edge on the bin, as its plastic catches the light
        along = (xx - 650.0) * 0.8 - (yy - 430.0) * 0.6
        across = (xx - 650.0) * 0.6 + (yy - 430.0) * 0.8
        image[(np.abs(across) <= 2.5) & (np.abs(along) <= 30)] = 70.0
    if stray:
        # a dark bag strap on the wall high up, where no ball on the floor can be
        image[225:305, 830:925] = 60.0
        image[np.hypot(xx - 876.0, yy - 265.0) <= 12.0] = 14.0
    noise = np.random.default_rng(seed + 1).normal(0, 1.2, (5, height, width))
    return np.clip(np.round(image[None] + noise), 0, 255).astype(np.uint8)


def test_a_dim_ball_darker_underneath_than_the_carpet_is_still_a_ball():
    # its lit top stands only 11 DN over its shade: a fixed floor of DN turned
    # it away whenever the room or the gain was low
    frames = _dim_room(door_stop=False)

    fit = fit_lit_ball(
        np.median(frames, axis=0), 762.0, 478.0, 9.85, noise_dn=1.0, expected_radius=9.85
    )

    assert fit is not None
    assert fit.x == pytest.approx(760.4, abs=1.0)
    assert fit.y == pytest.approx(481.2, abs=1.0)


def test_the_dim_ball_beats_a_brighter_square_door_stop():
    frames = _dim_room()

    ball = detect_reference_ball(frames, expected_diameter_px=19.7, expected_row_px=(492.0, 90.0))

    assert ball.x == pytest.approx(760.4, abs=1.0)
    assert ball.y == pytest.approx(481.2, abs=1.0)


def test_a_bright_ball_barely_brighter_underneath_than_the_carpet_is_still_a_ball():
    # the light on: the top stands 60 DN over the dark bin, the underside 3 DN
    # over the carpet; how much darker the ring must be is set by the noise
    frames = _dim_room(door_stop=False, levels=(40.0, 150.0, 143.0, 60.0))

    ball = detect_reference_ball(frames, expected_diameter_px=19.7, expected_row_px=(492.0, 90.0))

    assert ball.x == pytest.approx(760.4, abs=1.0)
    assert ball.y == pytest.approx(481.2, abs=1.0)


def test_a_round_reflection_above_the_rows_the_ball_can_rest_in_is_not_it():
    # a lit, ball-sized reflection on a storage bin, just beyond the rows the
    # floor allows: a fit seeded at the rows' edge slides up onto it
    rng = np.random.default_rng(7)
    yy, xx = np.indices((800, 1280)).astype(np.float64)
    image = 120.0 + rng.normal(0, 1.5, (800, 1280))
    dx, dy = xx - 583.0, yy - 391.0
    inside = dx**2 + dy**2 <= 9.85**2
    nz = np.sqrt(np.clip(9.85**2 - dx**2 - dy**2, 0, None)) / 9.85
    image[inside] = 125.0 + 60.0 * np.clip(-dy / 9.85 * 0.74 + nz * 0.67, 0, None)[inside]
    frames = np.clip(np.round(image[None] + rng.normal(0, 1.2, (5, 800, 1280))), 0, 255)

    with pytest.raises(ValueError):
        detect_reference_ball(
            frames.astype(np.uint8), expected_diameter_px=19.7, expected_row_px=(486.0, 90.0)
        )


def test_a_bright_streak_is_not_a_ball_when_the_floor_is_clipped_white():
    # the ball on clipped carpet cannot be seen; a streak on the bin must not stand in
    frames = _dim_room(clipped_floor=True, door_stop=False, streak=True)

    with pytest.raises(ValueError):
        detect_reference_ball(frames, expected_diameter_px=19.7, expected_row_px=(486.0, 90.0))


def test_with_the_size_and_row_known_nothing_elsewhere_stands_in_for_the_ball():
    frames = _dim_room(ball=False, door_stop=False, stray=True)

    with pytest.raises(ValueError):
        detect_reference_ball(frames, expected_diameter_px=19.7, expected_row_px=(486.0, 90.0))


def test_a_ball_on_ground_clipped_white_is_not_measured():
    # clipped white explains any bright sphere exactly, with nothing left over:
    # a door's dark foot just above made it the best "ball" in the frame
    # (the layout of a real 994 us x15.9 frame; before, this fitted 231 DN of
    # diffuse light with a misfit of exactly 0)
    rng = np.random.default_rng(4)
    image = np.full((200, 200), 255.0)
    image[:, 106:] = 185.0 + rng.normal(0, 3, (200, 94))
    image[128:131, :] = 135.0 + rng.normal(0, 3, (3, 200))
    image[131:, :] = 190.0 + rng.normal(0, 3, (69, 200))

    fit = fit_lit_ball(image, 98.0, 118.0, 9.85, noise_dn=1.5, expected_radius=9.85)

    assert fit is None

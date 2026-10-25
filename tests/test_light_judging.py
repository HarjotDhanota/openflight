"""The one light rule the camera applies to a clip's resting ball and hitting zone."""

import numpy as np
import pytest

from openflight.camera import light_judging as lj

HEIGHT, WIDTH = 200, 320
BALL = {"x": 160.0, "y": 120.0, "diameter_px": 24.0}


def _frames(*, background=60, ball=None, count=10):
    """A still clip: a flat background with an optional ball disk of this brightness."""
    frames = np.full((count, HEIGHT, WIDTH), background, np.uint8)
    if ball is not None:
        yy, xx = np.mgrid[0:HEIGHT, 0:WIDTH]
        disk = (xx - BALL["x"]) ** 2 + (yy - BALL["y"]) ** 2 <= (BALL["diameter_px"] / 2) ** 2
        frames[:, disk] = ball
    return frames


def test_resting_frames_are_the_first_ten_but_never_fewer_than_three():
    clip = np.arange(30).reshape(30, 1, 1)
    assert lj.resting_frames(clip).shape[0] == lj.RESTING_FRAMES == 10
    assert lj.resting_frames(clip[:2]).shape[0] == 2
    assert lj.resting_frames(clip[:5]).shape[0] == 5


def test_without_a_setup_ball_nothing_is_searched():
    assert lj.ball_light(_frames(ball=180), lj.SENSOR_BLACK_LEVEL_DN, None) is None


def test_a_well_lit_ball_on_a_lit_mat_is_ok_and_eligible():
    frames = _frames(background=70, ball=170)
    light = lj.judge_light(frames, lj.SENSOR_BLACK_LEVEL_DN, BALL)
    assert light["cause"] == "ok" and light["message"] is None
    assert light["ball"]["median_dn"] == pytest.approx(170.0)
    judged = lj.capture_analysis_eligibility(frames, lj.SENSOR_BLACK_LEVEL_DN, BALL)
    assert judged["eligible"] is True and judged["reason"] is None
    assert judged["rule"] == "setup_ball" and judged["setup_ball"] == BALL


def test_a_clipped_ball_is_too_bright_and_ineligible():
    frames = _frames(background=70, ball=255)
    light = lj.judge_light(frames, lj.SENSOR_BLACK_LEVEL_DN, BALL)
    assert light["cause"] == "ball_clipped"
    assert light["cause"] in lj.BRIGHT_LIGHT_CAUSES
    judged = lj.capture_analysis_eligibility(frames, lj.SENSOR_BLACK_LEVEL_DN, BALL)
    assert judged["eligible"] is False and judged["reason"].startswith("too bright for the ball")


def test_a_dark_zone_with_no_ball_is_zone_dark():
    frames = _frames(background=int(lj.SENSOR_BLACK_LEVEL_DN) + 2)
    light = lj.judge_light(frames, lj.SENSOR_BLACK_LEVEL_DN, BALL)
    assert light["ball"] is None and light["cause"] == "zone_dark"
    judged = lj.capture_analysis_eligibility(frames, lj.SENSOR_BLACK_LEVEL_DN, BALL)
    assert judged["eligible"] is False
    assert judged["reason"] == "the resting ball was not found where the setup saw it"


# The box is fixed and the tester tees each ball anywhere in it (Harjot-outdoor-test-1:
# up to 131 px, 2.2 setup diameters, from where the setup saw its ball).
FULL = (800, 1280)
ZONE_BOX = (480, 380, 800, 560)
SETUP_BALL = {"x": 560.0, "y": 470.0, "diameter_px": 20.0}


def _lit_scene(balls, *, background=40.0, noise=0.8, count=10, shade=60.0, diffuse=150.0):
    """Still frames of lit spheres (x, y, diameter) on flat ground, with sensor noise."""
    yy, xx = np.indices(FULL, dtype=np.float32)
    image = np.full(FULL, background, dtype=np.float32)
    light = np.asarray([-0.3, -0.6, 0.74])
    light /= np.linalg.norm(light)
    for x, y, diameter in balls:
        radius = diameter / 2.0
        dx, dy = (xx - x) / radius, (yy - y) / radius
        nz = np.sqrt(np.clip(1.0 - dx * dx - dy * dy, 0.0, None))
        lit = np.clip(dx * light[0] + dy * light[1] + nz * light[2], 0.0, None)
        inside = dx * dx + dy * dy <= 1.0
        image[inside] = shade + diffuse * lit[inside]
    rng = np.random.default_rng(7)
    frames = image[None] + rng.normal(0.0, noise, (count, *FULL))
    return np.clip(np.round(frames), 0, 255).astype(np.uint8)


def test_a_ball_anywhere_in_the_box_is_found_and_judged():
    frames = _lit_scene([(720.0, 470.0, 20.0)])  # 8 diameters from the setup's ball

    ball = lj.ball_light(frames, lj.SENSOR_BLACK_LEVEL_DN, SETUP_BALL, zone_box=ZONE_BOX)
    light = lj.judge_light(frames, lj.SENSOR_BLACK_LEVEL_DN, SETUP_BALL, ZONE_BOX)

    assert ball is not None and ball["found_by"] == "detector"
    assert ball["x"] == pytest.approx(720.0, abs=1.5)
    assert ball["y"] == pytest.approx(470.0, abs=1.5)
    assert ball["diameter_px"] == pytest.approx(20.0, abs=2.5)
    assert light["cause"] == "ok" and light["ball"]["x"] == pytest.approx(720.0, abs=1.5)


def test_without_a_box_the_ball_is_still_sought_only_near_the_setup_ball():
    frames = _lit_scene([(720.0, 470.0, 20.0)])

    assert lj.ball_light(frames, lj.SENSOR_BLACK_LEVEL_DN, SETUP_BALL) is None


def test_a_ball_outside_the_box_is_not_the_resting_ball():
    frames = _lit_scene([(900.0, 470.0, 20.0)])

    assert lj.ball_light(frames, lj.SENSOR_BLACK_LEVEL_DN, SETUP_BALL, zone_box=ZONE_BOX) is None


def test_a_ball_of_another_size_in_the_box_is_not_the_resting_ball():
    frames = _lit_scene([(720.0, 470.0, 60.0)])  # three times the setup's ball

    assert lj.ball_light(frames, lj.SENSOR_BLACK_LEVEL_DN, SETUP_BALL, zone_box=ZONE_BOX) is None


def test_a_dim_ball_in_a_dark_box_is_found_and_judged_too_dark():
    # the 30 us rung at dusk: ground 8 DN above black, the ball about 15 (shot 31)
    frames = _lit_scene(
        [(700.0, 480.0, 20.0)], background=24.0, shade=22.0, diffuse=14.0, noise=2.0
    )

    light = lj.judge_light(frames, lj.SENSOR_BLACK_LEVEL_DN, SETUP_BALL, ZONE_BOX)

    assert light["ball"] is not None
    assert light["ball"]["x"] == pytest.approx(700.0, abs=2.0)
    assert light["cause"] == "ball_dark"
    assert light["cause"] in lj.DARK_LIGHT_CAUSES


def test_no_ball_in_the_box_says_so():
    frames = _lit_scene([])

    judged = lj.capture_analysis_eligibility(
        frames, lj.SENSOR_BLACK_LEVEL_DN, SETUP_BALL, zone_box=ZONE_BOX
    )

    assert judged["eligible"] is False
    assert judged["reason"] == "the resting ball was not found in the box"


def test_a_found_dark_ball_still_reports_the_dark_zone_behind_it():
    frames = _lit_scene(
        [(700.0, 480.0, 20.0)], background=24.0, shade=22.0, diffuse=14.0, noise=2.0
    )

    light = lj.judge_light(frames, lj.SENSOR_BLACK_LEVEL_DN, SETUP_BALL, ZONE_BOX)

    assert light["cause"] == "ball_dark"
    assert light["zone_dark"] is True
    assert "the box is 8 DN above black" in light["zone_message"]


def test_a_lit_zone_is_not_dark():
    light = lj.judge_light(_lit_scene([(720.0, 470.0, 20.0)]), 16.0, SETUP_BALL, ZONE_BOX)

    assert light["zone_dark"] is False and light["zone_message"] is None

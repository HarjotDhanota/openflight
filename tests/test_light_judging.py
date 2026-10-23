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

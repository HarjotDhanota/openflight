"""Ball pixel limits follow the camera mode (wiring audit B3, 29 Sept).

The detectors were tuned on the 640x400 mode; at 1280x800 the same ball is twice
as wide, and a ball 1.0-1.3 m out (31-40 px) failed every fixed 9-30 px gate.
"""

import numpy as np
import pytest

from openflight.camera import ball_flight, ball_pixels
from openflight.camera.club_delivery import ReferenceBallTracker
from openflight.camera.club_motion import (
    ReferenceBall,
    detect_impact_reference_ball,
    detect_reference_ball,
)


def test_pixel_scale_follows_the_mode_not_the_crop():
    assert ball_pixels.pixel_scale(1280) == pytest.approx(2.0)
    assert ball_pixels.pixel_scale(640) == pytest.approx(1.0)
    # 320x200 is a crop of the 2x-binned 640x400 mode: same focal length
    assert ball_pixels.pixel_scale(320) == pytest.approx(1.0)
    assert ball_pixels.pixel_scale(1280, focal_px=700.0) == pytest.approx(1.5)


def test_diameter_bounds_keep_the_640_limits_and_double_at_1280():
    assert ball_pixels.ball_diameter_bounds_px(1.0) == pytest.approx((9.0, 30.0))
    assert ball_pixels.ball_diameter_bounds_px(2.0) == pytest.approx((18.0, 60.0))


def _scene(width, height, diameter, *, level=235, count=24, gone_after=None):
    rng = np.random.default_rng(0)
    frames = np.clip(60 + rng.normal(0, 2, (count, height, width)), 0, 255)
    yy, xx = np.indices((height, width))
    x, y = width / 2.0, height * 0.72
    disc = np.hypot(xx - x, yy - y) <= diameter / 2.0
    for index in range(count):
        if gone_after is None or index <= gone_after:
            frames[index][disc] = level
    return frames.astype(np.uint8), (x, y)


def test_the_scene_detector_finds_a_1280_ball_at_address():
    frames, (x, y) = _scene(1280, 800, 36.0)

    ball = detect_reference_ball(frames)

    assert ball.x == pytest.approx(x, abs=2.0)
    assert ball.y == pytest.approx(y, abs=2.0)
    assert 30.0 <= ball.diameter_px <= 42.0


def test_the_scene_detector_still_finds_a_640_ball():
    frames, (x, y) = _scene(640, 400, 18.0)

    ball = detect_reference_ball(frames)

    assert ball.x == pytest.approx(x, abs=2.0)
    assert 14.0 <= ball.diameter_px <= 22.0


def test_the_impact_detector_finds_a_1280_ball_that_departs():
    frames, (x, y) = _scene(1280, 800, 36.0, gone_after=10)

    ball = detect_impact_reference_ball(frames, trigger_frame_index=11)

    assert ball.x == pytest.approx(x, abs=3.0)
    assert 30.0 <= ball.diameter_px <= 42.0


def test_the_club_delivery_tracker_accepts_a_1280_ball():
    tracker = ReferenceBallTracker()
    candidate = ReferenceBall(x=640.0, y=576.0, diameter_px=36.0, area_px=1018)

    _resolved, source = tracker.resolve(candidate, pixel_scale=2.0)

    assert source == "detected"


def test_the_club_delivery_tracker_keeps_the_640_limit_by_default():
    tracker = ReferenceBallTracker()
    candidate = ReferenceBall(x=320.0, y=288.0, diameter_px=36.0, area_px=1018)

    _resolved, source = tracker.resolve(candidate)

    assert source == "unverified"


def test_ball_flight_selects_a_1280_reference_ball():
    frames, (x, y) = _scene(1280, 800, 36.0, gone_after=10)
    geometry = ball_flight.CameraBallGeometry(
        camera_height_m=0.095,
        radar_height_m=0.051,
        tee_range_m=1.13,
        ball_height_m=0.021335,
        image_width_px=1280,
        image_height_px=800,
    )

    anchor, diagnostics = ball_flight._select_reference_ball(  # pylint: disable=protected-access
        frames, 11, geometry, None
    )

    assert anchor is not None, diagnostics
    assert anchor.x == pytest.approx(x, abs=3.0)

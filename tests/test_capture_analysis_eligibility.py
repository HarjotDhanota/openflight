"""Capture-time analysis eligibility is judged on the setup's ball, not the whole zone.

The ball's core must be clipped 5 % or less and sit 20 DN or more above black;
a sunlit background alone does not withhold the camera. The real sunlit clip is
``test_capture_analysis_eligibility_field`` (fork only).
"""

import numpy as np
import pytest

from openflight.camera import light_judging as lj
from openflight.camera.auto_exposure import measure_exposure
from openflight.camera.capture_runtime import parse_setup_ball

BLACK = 16.0


def test_a_clipped_background_behind_a_well_exposed_ball_is_eligible():
    rng = np.random.default_rng(5)
    frames = np.clip(60 + rng.normal(0, 1.0, (10, 800, 1280)), 0, 255)
    frames[:, 360:480] = 255  # a sunlit strip beyond the mat: 33 % of the zone
    yy, xx = np.indices((800, 1280))
    frames[:, np.hypot(xx - 640, yy - 520) <= 10] = 180
    frames = frames.astype(np.uint8)
    assert measure_exposure(frames[0]).status == "too_bright"

    judged = lj.capture_analysis_eligibility(
        frames, BLACK, {"x": 640.0, "y": 520.0, "diameter_px": 20.0}
    )

    assert judged["eligible"] is True
    assert judged["light_cause"] == "background_clipped"


def test_no_ball_where_the_setup_saw_it_is_not_eligible():
    frames = np.full((10, 800, 1280), 90, np.uint8)

    judged = lj.capture_analysis_eligibility(
        frames, BLACK, {"x": 640.0, "y": 520.0, "diameter_px": 20.0}
    )

    assert judged["eligible"] is False
    assert "not found" in judged["reason"]


def test_the_setup_ball_argument_is_three_positive_numbers():
    assert parse_setup_ball("778.4,464.6,30.9") == {"x": 778.4, "y": 464.6, "diameter_px": 30.9}
    assert parse_setup_ball(None) is None
    for bad in ("1,2", "1,2,0", "a,b,c", "1,2,nan", "-1,2,3"):
        with pytest.raises(ValueError):
            parse_setup_ball(bad)

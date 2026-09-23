"""The resting ball is searched for once, then watched until something changes."""

import numpy as np
import pytest

from openflight.camera.club_motion import ReferenceBall
from openflight.camera.resting_ball import RestingBallTracker


def _scene(ball=(760.0, 481.0), *, brighter=0.0, seed=0):
    """Five frames of a textured floor at a high gain's noise, with a lit 20 px ball."""
    rng = np.random.default_rng(99)
    yy, xx = np.indices((800, 1280)).astype(np.float64)
    image = np.where(yy < 478, 18.0, 41.0) + rng.normal(0, 3, (800, 1280))
    if ball is not None:
        image[np.hypot(xx - ball[0], yy - ball[1]) <= 10.0] = 60.0
    noise = np.random.default_rng(seed).normal(0, 3.3, (5, 800, 1280))
    return np.clip(np.round(image[None] + brighter + noise), 0, 255).astype(np.uint8)


class _Search:
    """Stands in for the detector: says where the ball is, and counts being asked."""

    def __init__(self, ball=(760.0, 481.0)):
        self.ball = ball
        self.calls = 0

    def __call__(self, frames, *, expected_diameter_px=None, expected_row_px=None):
        self.calls += 1
        if self.ball is None:
            raise ValueError("no ball of the size the distance gives, where it has to rest")
        return ReferenceBall(x=self.ball[0], y=self.ball[1], diameter_px=19.7, area_px=305)


PRIORS = {"expected_diameter_px": 19.7, "expected_row_px": (486.0, 90.0)}


def test_a_still_ball_is_searched_for_once():
    search = _Search()
    tracker = RestingBallTracker(search)

    first = tracker.look(_scene(seed=1), **PRIORS)
    second = tracker.look(_scene(seed=2), **PRIORS)

    assert search.calls == 1
    assert second == first


def test_a_moved_ball_is_searched_for_again():
    search = _Search()
    tracker = RestingBallTracker(search)
    tracker.look(_scene(seed=1), **PRIORS)

    search.ball = (772.0, 481.0)
    ball = tracker.look(_scene((772.0, 481.0), seed=2), **PRIORS)

    assert search.calls == 2
    assert ball.x == pytest.approx(772.0)


def test_a_light_flickering_is_not_a_change():
    search = _Search()
    tracker = RestingBallTracker(search)
    tracker.look(_scene(seed=1), **PRIORS)

    tracker.look(_scene(brighter=8.0, seed=2), **PRIORS)

    assert search.calls == 1


def test_a_wandering_tilt_reading_keeps_the_answer_while_the_ball_still_fits():
    search = _Search()
    tracker = RestingBallTracker(search)
    tracker.look(_scene(seed=1), **PRIORS)

    tracker.look(_scene(seed=2), expected_diameter_px=19.7, expected_row_px=(490.0, 90.0))
    assert search.calls == 1

    # a row the ball no longer fits in asks a new question
    tracker.look(_scene(seed=3), expected_diameter_px=19.7, expected_row_px=(640.0, 90.0))
    assert search.calls == 2


def test_a_new_distance_asks_again():
    search = _Search()
    tracker = RestingBallTracker(search)
    tracker.look(_scene(seed=1), **PRIORS)

    tracker.look(_scene(seed=2), expected_diameter_px=24.6, expected_row_px=(486.0, 90.0))

    assert search.calls == 2


def test_no_ball_is_remembered_until_one_is_placed():
    search = _Search(ball=None)
    tracker = RestingBallTracker(search)
    for seed in (1, 2):
        with pytest.raises(ValueError, match="no ball"):
            tracker.look(_scene(None, seed=seed), **PRIORS)
    assert search.calls == 1

    search.ball = (760.0, 481.0)
    ball = tracker.look(_scene(seed=3), **PRIORS)

    assert search.calls == 2
    assert ball.x == pytest.approx(760.0)

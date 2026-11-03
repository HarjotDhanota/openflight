"""The ball's departure is the frame after which it never returns to rest (C12).

The club stage's contact detector took the first frames in which the resting ball's core
looked different as its departure. A head passing between the camera and the ball covers
it the same way: on Aug 25 shot 4 (7-iron) the head covered the ball from f68 to f70 and
the stage read contact at f67|f68, two frames before the ball left (its cap and rim were
back at rest in f71-f73; it flew from f74). A covered ball reappears at its resting spot
once the head has passed; a departing one never does. The rest witnesses are the
impact reader's own (the ball's top cap unchanged, its rim at its resting row).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from openflight.camera import club_delivery as cd
from openflight.camera.club_motion import ReferenceBall

AUGUST = Path(__file__).parent / "fixtures" / "club_delivery" / "august-shot4.npz"


def _scene(*, occluded=(), leaves_at=30, count=40):
    """A bright ball resting at (50, 50) on a grey ground, a dark head covering it in the
    ``occluded`` frames, the ball rising away from ``leaves_at`` on."""
    rng = np.random.default_rng(3)
    yy, xx = np.mgrid[0:100, 0:100]
    frames = np.empty((count, 100, 100), np.uint8)
    for index in range(count):
        image = np.full((100, 100), 60.0)
        y = 50.0 - (12.0 * (index - leaves_at + 1) if index >= leaves_at else 0.0)
        x = 50.0 + (4.0 * (index - leaves_at + 1) if index >= leaves_at else 0.0)
        image[np.hypot(xx - x, yy - y) <= 10.0] = 180.0
        if index in occluded:
            image[30:75, 25:80] = 25.0
        frames[index] = np.clip(image + rng.normal(0, 1.0, image.shape), 0, 255).astype(np.uint8)
    ball = ReferenceBall(x=50.0, y=50.0, diameter_px=20.0, area_px=314, fit_quality=None)
    return frames, ball


def test_a_head_passing_over_a_ball_that_stays_is_not_its_departure():
    frames, ball = _scene(occluded=(20, 21, 22))
    assert cd._detect_impact_index(frames, ball, trigger_index=25) == 29


def test_a_departure_with_no_occlusion_is_the_last_rest_frame():
    frames, ball = _scene()
    assert cd._detect_impact_index(frames, ball, trigger_index=31) == 29


def test_august_shot4_leaves_where_its_ball_flies_not_where_the_head_covers_it():
    with np.load(AUGUST) as data:
        frames = data["frames"]
        trigger = int(data["trigger_index"])
        ball = ReferenceBall(**json.loads(str(data["ball_json"])), fit_quality=None)

    assert cd._detect_impact_index(frames, ball, trigger_index=trigger) == 73


def test_oct27_shot1_keeps_f43_f44():
    from tests.test_camera_club_contact_tangent import _shot1_club  # noqa: PLC0415

    case = _shot1_club()
    frames, ball = case["frames"], case["kwargs"]["reference_ball"]
    assert (
        cd._detect_impact_index(frames, ball, trigger_index=case["kwargs"]["trigger_index"]) == 43
    )

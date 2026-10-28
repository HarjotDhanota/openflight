"""A departure witness must distinguish the ball from unchanged bright scenery."""

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

import test_camera_impact_location as synthetic  # noqa: E402

from openflight.camera import impact_location as il  # noqa: E402


def scene():
    background = np.full((160, 200), 20, dtype=np.uint8)
    ball = il.RestingBall(120.0, 110.0, 20.0)
    cv2.circle(background, (120, 110), 10, 100, -1)
    cv2.circle(background, (140, 40), 12, 230, -1)
    return background, ball


def test_unchanged_scene_is_not_a_departing_ball():
    background, ball = scene()
    assert il.flying_ball(background, ball, background) is None


def test_flying_ball_is_not_hidden_by_a_brighter_static_highlight():
    background, ball = scene()
    frame = background.copy()
    cv2.circle(frame, (120, 110), 10, 20, -1)
    cv2.circle(frame, (125, 80), 10, 120, -1)
    found = il.flying_ball(frame, ball, background)
    assert found is not None
    assert found[:2] == pytest.approx((125, 80), abs=1)


def test_rim_occlusion_and_an_unrelated_glint_do_not_precede_ball_departure():
    frames, ball = synthetic.render_swing(truth_mm=-6.0, contact_time=10.5)
    top = round(ball.y - ball.diameter_px / 2)
    half = round(il.EDGE_COLUMNS_FRACTION * ball.diameter_px / 2) + 1
    for index in (8, 9):
        cv2.rectangle(
            frames[index], (round(ball.x) - half, top - 1), (round(ball.x) + half, top + 3), 20, -1
        )
        cv2.circle(frames[index], (round(ball.x) - 30, round(ball.y) - 60), 12, 250, -1)
    contact = il.find_contact(frames, ball)
    assert contact.status == "ok", contact.reason
    assert contact.contact_frame == 11

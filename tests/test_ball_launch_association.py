"""Outgoing-ball association must distinguish departure residue from motion."""

import cv2
import numpy as np
import pytest

from openflight.camera.ball_launch_fallback import track_launch_ball


def departure_scene(moving=True):
    frames = np.full((32, 120, 160), 80, dtype=np.uint8)
    for frame in frames[:20]:
        cv2.circle(frame, (60, 90), 6, 20, -1)
        cv2.circle(frame, (60, 101), 8, 180, -1)
    if moving:
        for index, centre in enumerate([(65, 77), (70, 57), (75, 37), (80, 17)]):
            cv2.circle(frames[20 + index], centre, 6, 200, -1)
    return frames, np.arange(len(frames)) / 200.0


def test_departure_remnant_does_not_outvote_the_shorter_moving_ball():
    frames, times = departure_scene()

    track = track_launch_ball(
        frames, times, first_post_index=20, rest_px=(60, 101), rest_diameter_px=16
    )

    assert [point.frame for point in track] == [20, 21, 22, 23]
    np.testing.assert_allclose(
        [(point.x, point.y) for point in track],
        [(65, 77), (70, 57), (75, 37), (80, 17)],
        atol=0.1,
    )


@pytest.mark.parametrize("noise_sd", [0, 2, 5])
def test_departure_remnant_without_visible_ball_does_not_make_a_track(noise_sd):
    frames, times = departure_scene(moving=False)
    if noise_sd:
        noise = np.random.default_rng(231).normal(0, noise_sd, frames.shape)
        frames = np.clip(frames.astype(float) + noise, 0, 255).astype(np.uint8)

    track = track_launch_ball(
        frames, times, first_post_index=20, rest_px=(60, 101), rest_diameter_px=16
    )

    assert track == []


@pytest.mark.parametrize("step", [(-2, 0), (2, 0), (0, -2)])
def test_slow_continuous_motion_does_not_require_a_large_step_per_frame(step):
    frames = np.full((28, 120, 160), 40, dtype=np.uint8)
    for frame in frames[:20]:
        cv2.circle(frame, (60, 101), 8, 180, -1)
    expected = []
    for index in range(8):
        centre = (85 + step[0] * index, 85 + step[1] * index)
        cv2.circle(frames[20 + index], centre, 6, 200, -1)
        expected.append(centre)

    track = track_launch_ball(
        frames,
        np.arange(len(frames)) / 200.0,
        first_post_index=20,
        rest_px=(60, 101),
        rest_diameter_px=16,
    )

    assert [point.frame for point in track] == list(range(20, 28))
    np.testing.assert_allclose([(p.x, p.y) for p in track], expected, atol=0.1)

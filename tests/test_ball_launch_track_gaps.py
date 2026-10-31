"""Use observations on both sides of one dropout without inventing a frame."""

import cv2
import numpy as np

from openflight.camera.ball_launch_fallback import track_launch_ball


def scene(missing):
    frames = np.full((26, 120, 160), 40, dtype=np.uint8)
    for frame in frames[:20]:
        cv2.circle(frame, (60, 100), 8, 180, -1)
    for index, centre in enumerate([(65, 75), (70, 60), (75, 45), (80, 30), (85, 15)]):
        if index not in missing:
            cv2.circle(frames[20 + index], centre, 6, 200, -1)
    return frames, np.arange(len(frames)) * 0.004


def test_a_single_missed_frame_does_not_discard_the_later_observed_ball():
    frames, times = scene({2})
    track = track_launch_ball(
        frames, times, first_post_index=20, rest_px=(60, 100), rest_diameter_px=16
    )
    assert [point.frame for point in track] == [20, 21, 23, 24]
    assert [point.t_s for point in track] == [times[i] for i in [20, 21, 23, 24]]
    np.testing.assert_allclose(
        [(point.x, point.y) for point in track], [(65, 75), (70, 60), (80, 30), (85, 15)], atol=0.1
    )


def test_two_consecutive_missing_frames_are_not_bridged():
    frames, times = scene({1, 2})
    track = track_launch_ball(
        frames, times, first_post_index=20, rest_px=(60, 100), rest_diameter_px=16
    )
    assert len(track) <= 2
    assert not ({20, 23} <= {point.frame for point in track})


def test_one_missing_first_continuation_has_no_interpolated_point():
    frames, times = scene({1})
    track = track_launch_ball(
        frames, times, first_post_index=20, rest_px=(60, 100), rest_diameter_px=16
    )
    assert [point.frame for point in track] == [20, 22, 23, 24]

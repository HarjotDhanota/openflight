"""The ball-departure detector in dim light, on synthetic scenes.

The ball's lit cap and shaded body can sit 5-10 DN either side of the mat, far under
a fixed 30 DN threshold, and cancel in a plain mean. The six real dusk swings are
``test_camera_ball_departure_field`` (fork only).
"""

import numpy as np
import pytest

from openflight.camera.club_delivery import BALL_PRESENT_DELTA, _detect_impact_index
from openflight.camera.club_motion import ReferenceBall


def _bright_scene(frame_count: int, *, seed: int) -> tuple[np.ndarray, ReferenceBall]:
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[:80, :80]
    ball = ReferenceBall(x=40.0, y=40.0, diameter_px=20.0, area_px=314)
    body = (xx - ball.x) ** 2 + (yy - ball.y) ** 2 <= 10**2
    scene = np.full((80, 80), 45.0)
    scene[body] = 185.0
    frames = np.repeat(scene[None], frame_count, axis=0)
    frames = frames + rng.normal(0.0, 3.0, frames.shape)
    return frames, ball


def _legacy_detect(frames, ball, trigger_index):
    """The fixed-threshold detector, for bright-clip comparisons."""
    radius = max(3, int(round(ball.diameter_px * 0.4)))
    yy, xx = np.mgrid[0 : frames.shape[1], 0 : frames.shape[2]]
    disk = (xx - ball.x) ** 2 + (yy - ball.y) ** 2 <= radius * radius
    reference = float(np.median(frames[:15], axis=0)[disk].mean())
    means = np.array([float(frame[disk].mean()) for frame in frames])
    present = np.abs(means - reference) < BALL_PRESENT_DELTA
    indexes = np.nonzero(present)[0]
    indexes = indexes[(indexes >= trigger_index - 8) & (indexes <= trigger_index + 10)]
    for idx in indexes:
        after = present[idx + 1 : idx + 3]
        if len(after) == 2 and not after.any():
            return int(idx)
    return None


@pytest.mark.parametrize("seed", range(4))
def test_a_bright_ball_keeps_the_fixed_threshold_answers(seed):
    frames, ball = _bright_scene(60, seed=seed)
    yy, xx = np.mgrid[:80, :80]
    # A dark club head half-covers the ball in frame 40, then the ball leaves.
    frames[40, (yy < 40) & (np.abs(xx - 40) < 14)] = 60.0
    frames[41:, (xx - ball.x) ** 2 + (yy - ball.y) ** 2 <= 10**2] = 45.0
    # A small halo that moves the core by less than 30 DN in frame 39.
    frames[39, (xx - ball.x) ** 2 + (yy - ball.y) ** 2 <= 4**2] -= 60.0
    frames = np.clip(frames, 0, 255).astype(np.uint8)

    found = _detect_impact_index(frames, ball, trigger_index=44)

    assert found == _legacy_detect(frames, ball, trigger_index=44)
    assert found == 39


def test_a_dim_ball_whose_halves_cancel_in_the_mean_is_still_seen_leaving():
    rng = np.random.default_rng(7)
    yy, xx = np.mgrid[:80, :80]
    ball = ReferenceBall(x=40.0, y=40.0, diameter_px=20.0, area_px=314)
    body = (xx - ball.x) ** 2 + (yy - ball.y) ** 2 <= 10**2
    scene = np.full((80, 80), 30.0)
    scene[body & (yy < 40)] = 37.0  # lit cap
    scene[body & (yy >= 40)] = 23.0  # shaded body
    frames = np.repeat(scene[None], 60, axis=0) + rng.normal(0.0, 3.0, (60, 80, 80))
    frames[42:, body] = 30.0 + rng.normal(0.0, 3.0, (18, int(body.sum())))
    frames = np.clip(np.round(frames), 0, 255).astype(np.uint8)

    assert _legacy_detect(frames, ball, trigger_index=44) is None
    assert _detect_impact_index(frames, ball, trigger_index=44) == 41


def test_a_patch_with_no_ball_in_it_gives_no_departure():
    rng = np.random.default_rng(3)
    ball = ReferenceBall(x=40.0, y=40.0, diameter_px=20.0, area_px=314)
    frames = np.clip(np.round(30.0 + rng.normal(0.0, 3.0, (60, 80, 80))), 0, 255)

    assert _detect_impact_index(frames.astype(np.uint8), ball, trigger_index=44) is None

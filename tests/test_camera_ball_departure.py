"""The ball-departure detector in dim light (P6-4).

The fixture is cropped from the six swings of Outdoors-test-5 (29 Sept 2026, at
dusk, 1280x800 at 8.685 ms per frame): 24 frames of 64x64 px around the teed
ball, with the clip's timestamps, pre-trigger count and the crop's offset. The
ball centres were marked by hand on renders of the resting frames (the whole
ball, lit cap and shaded body, 21 px across).

At dusk the ball's lit cap is 5-10 DN above the mat behind it and its shaded
body 6-10 DN below, so it changes the patch by far less than the fixed 30 DN
the detector used to demand, and its two halves cancel in a plain mean.
"""

from pathlib import Path

import numpy as np
import pytest

from openflight.camera.club_delivery import (
    BALL_PRESENT_DELTA,
    _detect_impact_index,
    camera_contact_time,
    frame_clock,
)
from openflight.camera.club_motion import ReferenceBall

FIXTURE = Path(__file__).parent / "fixtures" / "ball_departure" / "outdoors-test-5-dusk.npz"

# The ball is last at rest in frame 18 and gone in frame 19 in all six swings.
# In swings 3-6 the club head already covers part of the ball's core in frame
# 18 (its sole over the upper half in 3, 4 and 6, the dark head over the left
# half in 5), so by the detector's own rule - the last frame the ball's core is
# undisturbed, the same rule it applies on bright clips - frame 17 is the answer
# there.
EXPECTED_LAST_UNDISTURBED = {1: 18, 2: 18, 3: 17, 4: 17, 5: 17, 6: 17}


def _swing(archive, swing: int):
    frames = archive[f"s{swing}_frames"]
    x0, y0 = archive[f"s{swing}_crop_offset_xy"]
    bx, by = archive[f"s{swing}_ball_xy"]
    diameter = float(archive["ball_diameter_px"])
    ball = ReferenceBall(
        x=float(bx - x0),
        y=float(by - y0),
        diameter_px=diameter,
        area_px=int(round(np.pi * diameter * diameter / 4)),
    )
    return frames, ball


@pytest.fixture(scope="module")
def archive():
    with np.load(FIXTURE) as data:
        return {key: data[key] for key in data.files}


def test_the_fixture_is_small_and_holds_only_the_ball_crops(archive):
    assert FIXTURE.stat().st_size < 1_000_000
    assert int(archive["swing_count"]) == 6
    for swing in range(1, 7):
        assert archive[f"s{swing}_frames"].shape == (24, 64, 64)
        assert int(archive[f"s{swing}_pre_trigger_count"]) == 18


@pytest.mark.parametrize("swing", range(1, 7))
def test_the_departure_is_found_on_every_dusk_swing(archive, swing):
    frames, ball = _swing(archive, swing)
    trigger_index = int(archive[f"s{swing}_pre_trigger_count"]) - 1

    found = _detect_impact_index(frames, ball, trigger_index=trigger_index)

    assert found == EXPECTED_LAST_UNDISTURBED[swing]


@pytest.mark.parametrize("swing", range(1, 7))
def test_the_departure_needs_no_trigger_hint(archive, swing):
    frames, ball = _swing(archive, swing)

    assert _detect_impact_index(frames, ball) == EXPECTED_LAST_UNDISTURBED[swing]


def test_the_contact_time_comes_back_on_a_dusk_swing(archive):
    """F7's contact time returned nothing on all six swings before the fix."""
    frames, ball = _swing(archive, 1)
    clock, source = frame_clock(archive["s1_host_timestamp_ns"], archive["s1_sensor_timestamp_ns"])

    contact = camera_contact_time(frames, clock, ball, trigger_index=17, timestamp_source=source)

    assert contact is not None
    assert source == "sensor_timestamp_ns"
    assert contact.impact_frame == 18
    sensor = archive["s1_sensor_timestamp_ns"]
    assert contact.contact_ns == pytest.approx((int(sensor[18]) + int(sensor[19])) / 2)


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
    """The detector as it was before P6-4, for bright-clip comparisons."""
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

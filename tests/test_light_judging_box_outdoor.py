"""The resting ball is judged anywhere in the box, on Harjot-outdoor-test-1's own frames.

Free play on 24 Oct 2026 (dusk, 6 mm lens at 1280x800): the swing verdict said "resting
ball not found in the pre-impact frames" on 23 of 33 swings. The ball was in the box on
all but two of them; the verdict only took a ball within one of the setup's diameters
(58.98 px) of where the setup saw it, and the tester tees each ball anywhere in the box.
On the 30 us rung the ball was too dim for the detector at its own brightness, so those
swings were judged on a missed ball, not on the light.

Each fixture is the first ten frames of a clip cropped around the resting ball (or the
gap in the net shot 5's search took for one), built by
scripts/analysis/ball_detector_fixtures.py and set back into a frame of its own size
filled with the crop's median, inside the session's box.
"""

import json
from pathlib import Path

import numpy as np
import pytest

from openflight.camera import free_play_ladder, light_judging as lj

FIXTURES = Path(__file__).parent / "fixtures" / "ball_detector"
FRAME_SHAPE = (800, 1280)
BOX = (301, 296, 975, 676)
# the setup ball the kiosk judged by on 24 Oct (setup.json); measure_rim has since made it 42.65
SETUP_BALL = {"x": 724.5, "y": 429.2, "diameter_px": 58.98}
BLACK = 16.0


def _frames(shot):
    data = np.load(FIXTURES / f"outdoor-test-1-shot-{shot:02d}-resting.npz")
    crop, (x0, y0) = data["frames"], tuple(int(v) for v in data["origin_px"])
    # the fill carries the crop's own frame-to-frame noise, which the ball's fit is judged by
    noise = float(np.median(np.std(crop.astype(np.float32), axis=0)))
    fill = np.median(crop) + np.random.default_rng(shot).normal(
        0.0, noise, (len(crop), *FRAME_SHAPE)
    )
    frames = np.clip(np.round(fill), 0, 255).astype(np.uint8)
    frames[:, y0 : y0 + crop.shape[1], x0 : x0 + crop.shape[2]] = crop
    return frames


@pytest.mark.parametrize(
    "shot, where",
    [
        (6, (855.0, 419.0)),  # 131 px from the setup's ball
        (10, (834.5, 419.5)),  # 110 px from it
    ],
)
def test_a_ball_teed_elsewhere_in_the_box_is_found_and_judged(shot, where):
    frames = _frames(shot)

    near_the_setup = lj.ball_light(frames, BLACK, SETUP_BALL)
    in_the_box = lj.ball_light(frames, BLACK, SETUP_BALL, zone_box=BOX)

    assert near_the_setup is None  # what the 24 Oct verdict saw
    assert in_the_box is not None and in_the_box["found_by"] == "detector"
    assert in_the_box["x"] == pytest.approx(where[0], abs=2.0)
    assert in_the_box["y"] == pytest.approx(where[1], abs=2.5)
    assert 39.0 <= in_the_box["diameter_px"] <= 46.0


@pytest.mark.parametrize("setup_diameter", [58.98, 42.65])
def test_the_gap_in_the_net_is_still_no_ball(setup_diameter):
    setup = {**SETUP_BALL, "diameter_px": setup_diameter}

    assert lj.ball_light(_frames(5), BLACK, setup, zone_box=BOX) is None


def test_the_dim_ball_on_the_30_us_rung_is_judged_on_its_light():
    light = lj.judge_light(_frames(31), BLACK, SETUP_BALL, zone_box=BOX)

    assert light["ball"] is not None
    assert light["ball"]["x"] == pytest.approx(784.0, abs=3.0)
    assert light["ball"]["y"] == pytest.approx(420.0, abs=3.5)
    assert light["cause"] == "ball_dark"
    assert light["ball"]["signal_dn"] < lj.BALL_MIN_SIGNAL_DN


def _clip(tmp_path, shot, exposure_us, gain):
    folder = tmp_path / f"shot-{shot:02d}"
    folder.mkdir()
    frames = _frames(shot)
    np.savez(
        folder / "frames.npz",
        frames=frames,
        exposure_us=np.full(len(frames), exposure_us, np.int32),
        analogue_gain=np.full(len(frames), gain, np.float32),
    )
    (folder / "metadata.json").write_text(json.dumps({"delivered_fps": 115.1, "gap_count": 0}))
    return folder


def _judge(folder, exposure_us, gain):
    return free_play_ladder.judge_swing(
        folder,
        arm_id="arm5",
        rung_id=f"full-{exposure_us}",
        exposure_us=exposure_us,
        gain=gain,
        expected_ball=SETUP_BALL,
        zone_box=BOX,
    )


def test_shot_6_is_green_with_a_usable_ball(tmp_path):
    """Amber 'resting ball not found' on 24 Oct; the ball sat in the box, well lit."""
    verdict = _judge(_clip(tmp_path, 6, 200, 12.0), 200, 12.0)

    assert verdict["color"] == "green", verdict["reasons"]
    assert verdict["ball_usable"] is True
    assert verdict["ball"]["x"] == pytest.approx(855.0, abs=2.0)


def test_shot_31_is_red_for_its_light_not_for_a_missed_ball(tmp_path):
    verdict = _judge(_clip(tmp_path, 31, 30, 12.0), 30, 12.0)

    assert verdict["color"] == "red"
    assert verdict["light_cause"] == "ball_dark"
    assert verdict["ball_usable"] is False
    assert not any("not found" in reason for reason in verdict["reasons"])


def test_shot_31s_club_is_unusable_in_its_dark_box(tmp_path):
    """The ball is now found (too dark); the club's background is still too dark."""
    verdict = _judge(_clip(tmp_path, 31, 30, 12.0), 30, 12.0)

    assert verdict["club_usable"] is False
    assert any("too dark in this light" in reason for reason in verdict["reasons"])

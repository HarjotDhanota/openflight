"""The expected-size choice on a real dark shot (fork only: it loads field frames).

Harjot-outdoor-test-1, shot 14 (dusk, 6 mm lens, 145 us x 12): the scene detector
measured the ball 40.6 px across; the impact detector, thresholding the ball's
departure at a fixed 45 DN, kept only its lit upper half, 23.2 px. Both sat on the
ball 6.6 px apart, and the shot was withheld as a detector disagreement.
"""

from pathlib import Path

import numpy as np
import pytest

from openflight.camera import ball_flight
from openflight.camera.club_motion import detect_impact_reference_ball
from tests.test_reference_ball_expected_size import (
    SHOT_14_LOOK,
    SIX_MM_FOCAL_PX,
    shot_14_geometry,
)

FIXTURE = Path(__file__).parent / "fixtures" / "ball_detector" / "outdoor-test-1-shot-14-dark.npz"
# the last frame exposed by the trigger (pre_trigger_count_by_exposure 19)
SHOT_14_TRIGGER = 18


def shot_14_frames():
    data = np.load(FIXTURE)
    frames = data["frames"]
    x0, y0 = (int(value) for value in data["origin_px"])
    pad = ((0, 0), (y0, 800 - y0 - frames.shape[1]), (x0, 1280 - x0 - frames.shape[2]))
    return np.pad(frames, pad, mode="edge")


def test_in_dark_frames_the_impact_detector_measures_only_the_lit_cap():
    frames = shot_14_frames()

    ball = detect_impact_reference_ball(
        frames, trigger_frame_index=SHOT_14_TRIGGER, pixel_scale=2.0, region=(678, 303, 908, 533)
    )

    # no pixel reaches the 210 DN refinement; the 45 DN departure keeps the lit half
    assert ball.x == pytest.approx(793.2, abs=1.0)
    assert ball.diameter_px == pytest.approx(23.2, abs=1.0)


def test_shot_14_takes_the_scene_ball_whose_size_the_ball_s_look_confirms():
    selected, diagnostics = ball_flight._select_reference_ball(  # pylint: disable=protected-access
        shot_14_frames(), SHOT_14_TRIGGER, shot_14_geometry(), setup_ball=SHOT_14_LOOK
    )

    assert selected is not None
    assert selected.x == pytest.approx(797.6, abs=0.5)
    assert selected.diameter_px == pytest.approx(40.6, abs=0.5)
    assert diagnostics["agreement"] is False
    assert diagnostics["selected_source"] == "scene_by_expected_size"
    assert diagnostics["expected_diameter"] == {
        "diameter_px": pytest.approx(38.43),
        "source": "setup_ball",
    }


def test_shot_14_takes_the_scene_ball_whose_size_its_distance_predicts():
    selected, diagnostics = ball_flight._select_reference_ball(  # pylint: disable=protected-access
        shot_14_frames(),
        SHOT_14_TRIGGER,
        shot_14_geometry(),
        setup_ball=SHOT_14_LOOK,
        lens_focal_px=SIX_MM_FOCAL_PX,
    )

    assert selected.diameter_px == pytest.approx(40.6, abs=0.5)
    assert diagnostics["selected_source"] == "scene_by_expected_size"
    expected = diagnostics["expected_diameter"]
    assert expected["source"] == "lens_focal_and_tee_range"
    # 2000 px x 42.7 mm over the lens's 1.88 m to the ball
    assert expected["diameter_px"] == pytest.approx(45.5, abs=0.3)

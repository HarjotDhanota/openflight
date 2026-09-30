"""The resting-ball gate follows the setup's ball, not fixed image fractions (P8-7)."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from openflight.camera import ball_flight
from openflight.camera.club_motion import detect_impact_reference_ball

WIDTH, HEIGHT = 1280, 800
# Outdoors-test-7's setup ball sits at row 463 of 800; the fixed gate needs 320-760
# and the impact detector only looks below row 496.
HIGH_BALL = {"x": 640.0, "y": 280.0, "diameter_px": 30.0}


def _geometry():
    return SimpleNamespace(image_width_px=WIDTH, image_height_px=HEIGHT, calibrated_model=None)


def _clip(ball: dict, *, depart_after: int = 21, n_frames: int = 30) -> np.ndarray:
    frames = np.full((n_frames, HEIGHT, WIDTH), 60, np.uint8)
    yy, xx = np.mgrid[:HEIGHT, :WIDTH]
    disk = (xx - ball["x"]) ** 2 + (yy - ball["y"]) ** 2 <= (ball["diameter_px"] / 2) ** 2
    frames[: depart_after + 1, disk] = 235
    return frames


def test_the_fixed_gate_refuses_a_ball_above_its_rows():
    selected, diagnostics = ball_flight._select_reference_ball(  # pylint: disable=protected-access
        _clip(HIGH_BALL), 21, _geometry(), None
    )
    assert selected is None
    assert diagnostics["gate"]["source"] == "fixed_fractions"


def test_the_setup_ball_gate_selects_the_ball_where_the_setup_saw_it():
    selected, diagnostics = ball_flight._select_reference_ball(  # pylint: disable=protected-access
        _clip(HIGH_BALL), 21, _geometry(), None, setup_ball=HIGH_BALL
    )
    assert selected is not None
    assert selected.x == pytest.approx(640.0, abs=2.0)
    assert selected.y == pytest.approx(280.0, abs=2.0)
    gate = diagnostics["gate"]
    assert gate["source"] == "setup_ball"
    assert gate["setup_ball"] == HIGH_BALL
    x0, y0, x1, y1 = gate["region_px"]
    assert x0 < 640 < x1 and y0 < 280 < y1


def test_the_setup_ball_gate_refuses_a_ball_of_another_size():
    small = {**HIGH_BALL, "diameter_px": 12.0}
    selected, diagnostics = ball_flight._select_reference_ball(  # pylint: disable=protected-access
        _clip(small), 21, _geometry(), None, setup_ball=HIGH_BALL
    )
    assert selected is None
    assert "setup ball" in diagnostics["scene"]["reason"]


def test_the_impact_detector_searches_the_region_it_is_given():
    frames = _clip(HIGH_BALL)
    with pytest.raises(ValueError, match="no persistent tee-ball departure"):
        detect_impact_reference_ball(frames, trigger_frame_index=21)
    ball = detect_impact_reference_ball(frames, trigger_frame_index=21, region=(580, 220, 700, 340))
    assert ball.x == pytest.approx(640.0, abs=2.0)
    assert ball.y == pytest.approx(280.0, abs=2.0)

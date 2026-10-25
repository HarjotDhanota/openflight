"""The resting-ball gate follows the setup's ball, not fixed image fractions."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from openflight.camera import ball_flight
from openflight.camera.club_motion import ReferenceBall, detect_impact_reference_ball

WIDTH, HEIGHT = 1280, 800
# the fixed gate needs rows 320-760 and the impact detector looks only below row 496
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
        _clip(HIGH_BALL), 21, _geometry()
    )
    assert selected is None
    assert diagnostics["gate"]["source"] == "fixed_fractions"


def test_the_setup_ball_gate_selects_the_ball_where_the_setup_saw_it():
    selected, diagnostics = ball_flight._select_reference_ball(  # pylint: disable=protected-access
        _clip(HIGH_BALL), 21, _geometry(), setup_ball=HIGH_BALL
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
        _clip(small), 21, _geometry(), setup_ball=HIGH_BALL
    )
    assert selected is None
    # the lit fit starts at the setup ball's size and reaches down to half of it: a ball
    # under half the setup's size is no lit ball of about that size, before the size gate
    assert diagnostics["scene"]["reason"] == "no lit ball where the scene detector found one"


def test_the_impact_detector_searches_the_region_it_is_given():
    frames = _clip(HIGH_BALL)
    with pytest.raises(ValueError, match="no persistent tee-ball departure"):
        detect_impact_reference_ball(frames, trigger_frame_index=21)
    ball = detect_impact_reference_ball(frames, trigger_frame_index=21, region=(580, 220, 700, 340))
    assert ball.x == pytest.approx(640.0, abs=2.0)
    assert ball.y == pytest.approx(280.0, abs=2.0)


def _sky_lit_clip(ball: dict, *, n_frames: int = 30, seed: int = 2) -> np.ndarray:
    """A resting ball lit by an open sky, as at dusk outdoors: brightness falls from its top
    right round to a shaded underside over a dark cast shadow, on a textured mat."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[:HEIGHT, :WIDTH].astype(np.float64)
    image = 85.0 + rng.normal(0.0, 2.0, (HEIGHT, WIDTH))
    radius = ball["diameter_px"] / 2.0
    dx, dy = xx - ball["x"], yy - ball["y"]
    shadow = (dx / radius) ** 2 + ((dy - 0.95 * radius) / (0.3 * radius)) ** 2 <= 1.0
    image[shadow] = 45.0
    inside = dx * dx + dy * dy <= radius * radius
    nz = np.sqrt(np.clip(1.0 - (dx * dx + dy * dy) / radius**2, 0.0, None))
    facing = -dy / radius * np.sin(np.radians(80.0)) + nz * np.cos(np.radians(80.0))
    image[inside] = (40.0 + 320.0 * (1.0 + facing) / 2.0)[inside]
    noise = rng.normal(0.0, 2.0, (n_frames, HEIGHT, WIDTH))
    return np.clip(np.round(image[None] + noise), 0, 255).astype(np.uint8)


def _scene_finds(monkeypatch, found):
    monkeypatch.setattr(ball_flight, "detect_reference_ball", lambda *_a, **_k: found)

    def no_departure(*_a, **_k):
        raise ValueError("no persistent tee-ball departure found")

    monkeypatch.setattr(ball_flight, "detect_impact_reference_ball", no_departure)


SKY_BALL = {"x": 640.0, "y": 420.0, "diameter_px": 42.0}


def test_with_a_setup_ball_the_resting_ball_is_measured_on_its_outline(monkeypatch):
    """Harjot-outdoor-test-1 shots 10 and 21: the scene detector kept the lit cap, 34.5 px
    of a ball the outline puts at 42."""
    _scene_finds(monkeypatch, ReferenceBall(640.0, 414.0, 34.5, 935))

    selected, diagnostics = ball_flight._select_reference_ball(  # pylint: disable=protected-access
        _sky_lit_clip(SKY_BALL), 21, _geometry(), setup_ball=SKY_BALL
    )

    assert selected is not None, diagnostics
    assert selected.diameter_px == pytest.approx(42.0, rel=0.04)
    assert selected.x == pytest.approx(640.0, abs=1.0)
    assert selected.y == pytest.approx(420.0, abs=1.0)
    assert diagnostics["scene"]["candidate"]["diameter_px"] == pytest.approx(selected.diameter_px)


def test_with_a_setup_ball_a_ball_sized_dark_gap_in_the_net_is_not_the_resting_ball(
    monkeypatch,
):
    """Harjot-outdoor-test-1 shots 5 and 8: no ball, and the scene detector found a 33 px
    dark gap in the netting, inside the setup ball's size gate once that size is right."""
    rng = np.random.default_rng(5)
    yy, xx = np.mgrid[:HEIGHT, :WIDTH]
    net = 90.0 + 25.0 * ((xx % 6 < 2) | (yy % 6 < 2)) + rng.normal(0.0, 2.0, (HEIGHT, WIDTH))
    net[(xx - 560.0) ** 2 + (yy - 390.0) ** 2 <= 16.5**2] = 45.0
    frames = np.clip(net[None] + rng.normal(0.0, 2.0, (30, HEIGHT, WIDTH)), 0, 255)
    _scene_finds(monkeypatch, ReferenceBall(560.0, 390.0, 33.0, 855))

    selected, diagnostics = ball_flight._select_reference_ball(  # pylint: disable=protected-access
        frames.astype(np.uint8), 21, _geometry(), setup_ball=SKY_BALL
    )

    assert selected is None
    assert diagnostics["scene"]["reason"] == "no lit ball where the scene detector found one"


def test_the_setup_ball_gate_reaches_across_the_box_the_ball_may_rest_anywhere_in():
    """Harjot-outdoor-test-1: resting balls up to 3.1 of the setup ball's diameters (131 px)
    away from where the setup saw it."""
    gate = ball_flight.reference_ball_gate(
        {"x": 724.2, "y": 421.6, "diameter_px": 42.65}, WIDTH, HEIGHT
    )

    assert ball_flight.gate_admits(gate, ReferenceBall(854.6, 419.4, 41.6, 1359))

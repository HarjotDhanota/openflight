"""One still scene and one resting-ball fit per shot.

The scene detector and the resting-ball fit read the same per-pixel median of a clip's
first frames: taken once, it gives the same ball. The range chain's lit-sphere fit of the
resting ball (five windows, outliers out) is the ball flight's too, so the two stages of
one shot use one size: on Harjot-outdoor-test-1 a second, single fit of the same ball
differed by 0.3-1.5 px on most shots and grew into the shade on shot 22 (71.7 px against
44.8 px).
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from openflight.camera import ball_flight, fusion_processing
from openflight.camera.club_motion import (
    ReferenceBall,
    background_median,
    detect_reference_ball,
    measure_resting_ball,
)

WIDTH, HEIGHT = 640, 400
BALL = {"x": 320.0, "y": 210.0, "diameter_px": 30.0}


def _lit_clip(ball: dict, *, n_frames: int = 24, seed: int = 3) -> np.ndarray:
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[:HEIGHT, :WIDTH].astype(np.float64)
    image = 85.0 + rng.normal(0.0, 2.0, (HEIGHT, WIDTH))
    radius = ball["diameter_px"] / 2.0
    dx, dy = xx - ball["x"], yy - ball["y"]
    inside = dx * dx + dy * dy <= radius * radius
    nz = np.sqrt(np.clip(1.0 - (dx * dx + dy * dy) / radius**2, 0.0, None))
    facing = -dy / radius * np.sin(np.radians(80.0)) + nz * np.cos(np.radians(80.0))
    image[inside] = (40.0 + 320.0 * (1.0 + facing) / 2.0)[inside]
    noise = rng.normal(0.0, 2.0, (n_frames, HEIGHT, WIDTH))
    return np.clip(np.round(image[None] + noise), 0, 255).astype(np.uint8)


def _geometry():
    return SimpleNamespace(image_width_px=WIDTH, image_height_px=HEIGHT, calibrated_model=None)


def test_a_shared_still_scene_finds_and_fits_the_same_ball():
    frames = _lit_clip(BALL)
    background = background_median(frames)

    found = detect_reference_ball(frames)
    assert detect_reference_ball(frames, background=background) == found
    assert measure_resting_ball(frames, found, 30.0, background=background) == (
        measure_resting_ball(frames, found, 30.0)
    )


def test_a_stale_seed_is_not_confirmation_when_the_clip_finds_a_different_ball():
    from openflight.camera.fallback_chain import ball_reference_check, keep_resting_ball_measure

    archive = {
        "frames": _lit_clip(BALL),
        "sensor_timestamp_ns": np.arange(24, dtype=np.int64) * 8_333_333,
        "pre_trigger_count": 24,
    }
    seed = {"x": 292.0, "y": 210.0, "diameter_px": 20.0}

    measure = keep_resting_ball_measure(archive, seed)
    record = ball_reference_check(measure)

    assert measure.found == (292.0, 210.0, 20.0)
    assert measure.size is not None
    assert measure.size.x == pytest.approx(BALL["x"], abs=1.0)
    assert measure.size.diameter_px == pytest.approx(BALL["diameter_px"], abs=1.0)
    assert record["status"] == "conflict"
    assert record["range_prior_rejection"]


def _scene_finds(monkeypatch, found):
    monkeypatch.setattr(ball_flight, "detect_reference_ball", lambda *_a, **_k: found)

    def no_departure(*_a, **_k):
        raise ValueError("no persistent tee-ball departure found")

    monkeypatch.setattr(ball_flight, "detect_impact_reference_ball", no_departure)


def test_the_range_chain_s_fit_of_the_ball_is_not_fitted_again(monkeypatch):
    _scene_finds(monkeypatch, ReferenceBall(321.0, 207.0, 24.0, 452))

    def refit(*_a, **_k):
        raise AssertionError("the resting ball was fitted a second time")

    monkeypatch.setattr(ball_flight, "measure_resting_ball", refit)

    selected, diagnostics = ball_flight._select_reference_ball(  # pylint: disable=protected-access
        _lit_clip(BALL), 18, _geometry(), setup_ball=BALL, resting_ball=(320.2, 209.8, 30.4)
    )

    assert selected is not None, diagnostics
    assert (selected.x, selected.y, selected.diameter_px) == (320.2, 209.8, 30.4)


def test_a_fit_of_another_place_is_not_this_ball(monkeypatch):
    _scene_finds(monkeypatch, ReferenceBall(321.0, 207.0, 24.0, 452))
    fits = []
    real = ball_flight.measure_resting_ball

    def counted(*args, **kwargs):
        fits.append(args[1])
        return real(*args, **kwargs)

    monkeypatch.setattr(ball_flight, "measure_resting_ball", counted)

    selected, _ = ball_flight._select_reference_ball(  # pylint: disable=protected-access
        _lit_clip(BALL), 18, _geometry(), setup_ball=BALL, resting_ball=(420.0, 209.8, 30.4)
    )

    assert len(fits) == 1
    assert selected is not None
    assert selected.diameter_px == pytest.approx(30.0, rel=0.05)


@pytest.mark.parametrize(
    ("ball_range", "expected"),
    [
        (
            {"details": {"status": "measured_from_size", "rest_px": [1.5, 2.5], "diameter_px": 40}},
            (1.5, 2.5, 40.0),
        ),
        ({"details": {"status": "no_resting_ball"}}, None),
        ({"details": {"status": "measured_from_size"}}, None),
        (None, None),
    ],
)
def test_only_a_range_measured_from_the_ball_s_size_carries_its_fit(ball_range, expected):
    context = {"ball_range": ball_range}
    assert fusion_processing._measured_resting_ball(context) == expected  # pylint: disable=protected-access

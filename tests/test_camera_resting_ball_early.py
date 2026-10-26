"""The resting ball is measured while the IWR's dump is still arriving.

It needs the clip's frames only, so the kiosk's camera prefetch (which already runs
beside the IWR) measures it and keeps it on the shot's in-memory archive; the range
chain and the camera fallback read it there instead of measuring after the IWR. The
values are the same: one function, one seed, one clip.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from openflight import server
from openflight.camera import fallback_chain, resting_ball_range
from openflight.camera.fallback_chain import (
    RESTING_BALL_KEY,
    RestingBallMeasure,
    keep_resting_ball_measure,
    resting_ball_measure,
)

SEED = {"x": 320.0, "y": 210.0, "diameter_px": 30.0, "source": "setup_ball"}


def _archive():
    return {
        "frames": np.zeros((12, 40, 60), np.uint8),
        "sensor_timestamp_ns": np.arange(12, dtype=np.int64) * 8_333_333,
    }


def _size(diameter=30.4):
    return resting_ball_range.RestingBallSize(
        x=320.1, y=209.9, diameter_px=diameter, diameter_sd_px=1.0, windows=5
    )


@pytest.fixture
def measured(monkeypatch):
    calls = []

    def measure(frames, *, first_post_index, seed, frame_interval_s):
        calls.append(seed)
        return _size()

    monkeypatch.setattr(resting_ball_range, "measure_resting_ball_size", measure)
    return calls


def test_a_kept_measure_is_read_back_for_the_same_seed(measured):
    archive = _archive()
    kept = keep_resting_ball_measure(archive, SEED)

    assert archive[RESTING_BALL_KEY] is kept
    assert resting_ball_measure(archive, dict(SEED, source="ball_at_address")) is kept
    assert measured == [(320.0, 210.0, 30.0)]


def test_another_seed_is_measured_again(measured):
    archive = _archive()
    keep_resting_ball_measure(archive, SEED)

    again = resting_ball_measure(archive, {**SEED, "x": 300.0})

    assert again.seed == (300.0, 210.0, 30.0)
    assert len(measured) == 2


def test_without_a_kept_measure_the_ball_is_measured_where_it_is_needed(measured):
    measure = resting_ball_measure(_archive(), SEED)

    assert measure == RestingBallMeasure(
        seed=(320.0, 210.0, 30.0), found=(320.0, 210.0, 30.0), size=_size()
    )


def test_a_size_already_measured_is_not_measured_again(monkeypatch):
    def refuse(*_a, **_k):
        raise AssertionError("measured twice")

    monkeypatch.setattr(resting_ball_range, "measure_resting_ball_size", refuse)

    record = resting_ball_range.resting_ball_range(
        np.zeros((12, 40, 60), np.uint8),
        first_post_index=10,
        seed=(320.0, 210.0, 30.0),
        lens_focal_px=1000.0,
        calibrated_focal_px=None,
        camera_origin_lfu=np.zeros(3),
        radar_origin_lfu=np.zeros(3),
        ball_height_m=0.021,
        image_width_px=60,
        image_height_px=40,
        roll_correction_deg=0.0,
        size=None,
    )

    assert record["status"] == "no_resting_ball"


def test_the_kiosk_measures_the_resting_ball_before_the_iwr_finishes(monkeypatch, measured):
    monkeypatch.setattr(server, "shot_reference_ball", lambda _at: dict(SEED))
    archive = _archive()

    elapsed = server._measure_resting_ball_early(  # pylint: disable=protected-access
        SimpleNamespace(impact_timestamp=100.0), archive
    )

    assert elapsed is not None and elapsed >= 0.0
    assert archive[RESTING_BALL_KEY].size == _size()


def test_a_failed_early_measure_leaves_the_chain_to_measure_it(monkeypatch):
    def broken(*_a, **_k):
        raise RuntimeError("fit diverged")

    monkeypatch.setattr(server, "shot_reference_ball", lambda _at: dict(SEED))
    monkeypatch.setattr(fallback_chain, "resting_ball_measure", broken)
    archive = _archive()

    elapsed = server._measure_resting_ball_early(  # pylint: disable=protected-access
        SimpleNamespace(impact_timestamp=100.0), archive
    )

    assert elapsed is None
    assert RESTING_BALL_KEY not in archive


def test_no_clip_no_early_measure():
    assert server._measure_resting_ball_early(SimpleNamespace(impact_timestamp=1.0), None) is None  # pylint: disable=protected-access

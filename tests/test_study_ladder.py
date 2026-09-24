"""The exposure ladder's rungs, gains, pre-rung check and per-swing verdict."""

import json

import numpy as np
import pytest

from openflight.camera import study_ladder as sl


def _capture(
    tmp_path, *, level=60.0, exposure=150, gain=8.0, fps=120.0, gaps=0, name="camera_1", ball=True
):
    folder = tmp_path / name
    folder.mkdir()
    rng = np.random.default_rng(0)
    frames = np.clip(level + rng.normal(0, 1.5, (12, 800, 1280)), 0, 255)
    if ball:
        yy, xx = np.indices((800, 1280))
        frames[:, (np.hypot(xx - 640, yy - 520) <= 10)] = 200
    np.savez(
        folder / "frames.npz",
        frames=frames.astype(np.uint8),
        exposure_us=np.full(12, exposure, np.int32),
        analogue_gain=np.full(12, gain, np.float32),
        pre_trigger_count=np.int32(9),
    )
    (folder / "metadata.json").write_text(json.dumps({"delivered_fps": fps, "gap_count": gaps}))
    return folder


def test_the_ladder_is_the_agreed_rungs():
    assert [(r.arm_id, r.exposure_us) for r in sl.LADDER] == [
        ("arm5", 300),
        ("arm5", 200),
        ("arm5", 150),
        ("arm5", 100),
        ("arm5", 75),
        ("arm6", 300),
        ("arm6", 150),
        ("arm6", 75),
    ]
    assert all(r.photos for r in sl.LADDER if r.arm_id == "arm5")
    assert not any(r.photos for r in sl.LADDER if r.arm_id == "arm6")


def test_gain_keeps_the_brightness_until_the_ceiling():
    assert sl.rung_gain(3.0, 150) == pytest.approx(6.0)
    assert sl.rung_gain(5.0, 75) == pytest.approx(12.0)  # 20 capped


def test_photo_exposure_stays_under_the_frame_period():
    assert sl.photo_exposure_us(0.01, 20.0, 120.0) <= 8000
    assert sl.photo_exposure_us(10.0, 20.0, 120.0) == 100
    assert sl.photo_exposure_us(0.05, 20.0, 120.0) == 800  # (100-20)/(0.05*2)


def test_a_dark_rung_is_skipped_before_any_swing():
    frames = np.full((5, 800, 1280), 25.0) + np.random.default_rng(1).normal(0, 1, (5, 800, 1280))
    check = sl.pre_rung_check(frames, black_floor=18.0)
    assert check["ok"] is False and "too dark" in check["reason"]


def test_a_lit_rung_passes_the_pre_rung_check():
    frames = np.full((5, 800, 1280), 60.0) + np.random.default_rng(1).normal(0, 1, (5, 800, 1280))
    check = sl.pre_rung_check(frames, black_floor=18.0)
    assert check["ok"] is True and check["signal_dn"] == pytest.approx(42.0, abs=1.0)


def test_a_good_swing_is_green(tmp_path):
    rung = sl.Rung("full-150", "arm5", 150, True)
    verdict = sl.swing_verdict(_capture(tmp_path), rung, 8.0, 18.0, [])
    assert verdict["color"] == "green", verdict["reasons"]


@pytest.mark.parametrize(
    "kwargs, word",
    [
        ({"fps": 100.0}, "frames"),
        ({"gaps": 2}, "gap"),
        ({"exposure": 300}, "exposure"),
        ({"gain": 4.0}, "gain"),
        ({"level": 22.0}, "dark"),
        ({"level": 254.0}, "clipped"),
    ],
)
def test_each_picture_failure_is_red_and_named(tmp_path, kwargs, word):
    rung = sl.Rung("full-150", "arm5", 150, True)
    verdict = sl.swing_verdict(_capture(tmp_path, **kwargs), rung, 8.0, 18.0, [])
    assert verdict["color"] == "red"
    assert any(word in reason for reason in verdict["reasons"])


def test_no_resting_ball_is_only_amber(tmp_path):
    rung = sl.Rung("full-150", "arm5", 150, True)
    verdict = sl.swing_verdict(_capture(tmp_path, ball=False), rung, 8.0, 18.0, [])
    assert verdict["color"] == "amber"
    assert any("resting ball" in reason for reason in verdict["reasons"])

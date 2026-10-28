"""A preview cache needs current evidence and bounded wider reacquisition."""

import numpy as np
import pytest

from openflight.camera.patch_watch import PatchWatch

BOUNDS = (16, 16, 144, 112)


def scene(*, noise=0.4, offset=0.0, seed=1):
    rng = np.random.default_rng(seed)
    yy, xx = np.indices((128, 160))
    background = 50.0 + 5.0 * np.sin(xx / 3.0) + 4.0 * np.cos(yy / 5.0)
    background[(xx - 60) ** 2 + (yy - 60) ** 2 < 8**2] = 170.0
    return np.clip(background + offset + rng.normal(0.0, noise, (5, 128, 160)), 0, 255).astype(
        np.uint8
    )


def watch(frames):
    result = PatchWatch(max_age_s=2.0)
    result.remember(frames, BOUNDS, "same controls", at=10.0, sequence=1)
    return result


def check(watcher, frames, **kwargs):
    return watcher.check(frames, BOUNDS, "same controls", at=10.5, sequence=2, **kwargs)


def test_unchanged_textured_pixels_with_new_noise_can_be_watched():
    assert check(watch(scene()), scene(seed=2))["matched"]


def test_uniform_brightness_offset_is_not_motion():
    evidence = check(watch(scene()), scene(offset=15.0, seed=2))
    assert evidence["matched"]
    assert evidence["offset_dn"] == pytest.approx(15.0, abs=0.3)


@pytest.mark.parametrize("change", ["remove", "move", "rival", "occlude", "light"])
def test_changes_require_search_even_away_from_the_old_ball(change):
    original = scene()
    changed = original.copy()
    if change == "remove":
        changed[:, 51:69, 51:69] = 50
    elif change == "move":
        changed[:, 51:69, 51:69] = 50
        changed[:, 51:69, 75:93] = 170
    elif change == "rival":
        changed[:, 75:90, 115:130] = 170
    elif change == "occlude":
        changed[:, 40:80, 45:75] = 30
    else:
        changed[:, 55:65, 55:65] = 220
    assert not check(watch(original), changed)["matched"]


def test_high_noise_cannot_raise_the_change_limit_and_hide_a_departure():
    watcher = watch(scene(noise=8.0))
    assert not check(watcher, scene(noise=8.0, seed=2))["matched"]


@pytest.mark.parametrize("at", [9.0, 12.0, 40.0])
def test_repeated_matching_pixels_cannot_extend_the_broad_search_deadline(at):
    frames = scene()
    watcher = watch(frames)
    assert check(watcher, frames)["matched"]
    assert not watcher.check(frames, BOUNDS, "same controls", at=at, sequence=3)["matched"]


def test_same_or_older_sequence_and_changed_context_cannot_be_watched():
    frames = scene()
    watcher = watch(frames)
    assert not watcher.check(frames, BOUNDS, "same controls", at=10.5, sequence=1)["matched"]
    assert not watcher.check(frames, BOUNDS, "new exposure", at=10.5, sequence=2)["matched"]
    assert not watcher.check(frames[:, :, :-1], BOUNDS, "same controls", at=10.5, sequence=2)[
        "matched"
    ]
    assert not watcher.check(
        frames.astype(np.uint16), BOUNDS, "same controls", at=10.5, sequence=2
    )["matched"]
    watcher.forget()
    assert not check(watcher, frames)["matched"]


def test_gradual_change_is_compared_with_the_original_not_a_moving_baseline():
    frames = scene(noise=0.0)
    watcher = watch(frames)
    for value in (1, 2, 3):
        changed = frames.copy()
        changed[:, 20:40, 20:40] += value
        assert check(watcher, changed)["matched"]
    changed[:, 20:40, 20:40] += 1
    assert not check(watcher, changed)["matched"]

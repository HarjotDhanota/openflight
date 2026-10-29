"""Failure and visibility boundaries for the standalone observation consumer."""

import copy

import numpy as np
import pytest

from openflight.camera.club_observations import observe_parts, part_regions
from tests.test_club_observations import record, scene


@pytest.mark.parametrize(
    "kind", ["frame_type", "background_shape", "noise_shape", "noise_negative", "background_nan"]
)
def test_invalid_images_do_not_produce_observations(kind):
    frame, background, variation = scene()
    if kind == "frame_type":
        frame = frame.astype(float)
    elif kind == "background_shape":
        background = background[:-1]
    elif kind == "noise_shape":
        variation = variation[:, :-1]
    elif kind == "noise_negative":
        variation[0, 0] = -1
    else:
        background[0, 0] = np.nan
    with pytest.raises(ValueError):
        observe_parts(frame, background, variation, record())


def test_input_arrays_and_roi_are_not_mutated():
    frame, background, variation = scene()
    before = [a.copy() for a in (frame, background, variation)]
    geometry = record()
    saved = copy.deepcopy(geometry)
    observe_parts(frame, background, variation, geometry)
    for actual, expected in zip((frame, background, variation), before):
        np.testing.assert_array_equal(actual, expected)
    assert geometry == saved


def test_stale_disk_is_a_separate_visibility_ablation():
    geometry = record()
    plain = part_regions((180, 240), geometry)
    stale = part_regions((180, 240), {**geometry, "additional_unknown_disks": [[120, 111, 10]]})
    assert plain["head"][111, 120]
    assert not stale["head"][111, 120]
    assert all(np.all(stale[name] <= plain[name]) for name in plain)


@pytest.mark.parametrize("disk", [[2, 3, -1], [2, 3], [2, np.nan, 4]])
def test_malformed_visibility_cannot_be_silently_ignored(disk):
    with pytest.raises(ValueError):
        part_regions((180, 240), {**record(), "additional_unknown_disks": [disk]})


def test_only_weak_change_cannot_create_its_own_strong_seed():
    _, background, variation = scene()
    frame = (background + 8).astype(np.uint8)
    result = observe_parts(frame, background, variation, record())
    assert result["strong_points"] == []
    assert result["region"]["status"] == "withheld_no_current_strong_support"

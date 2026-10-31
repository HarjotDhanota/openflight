"""A recorded seed never confirms an empty part of a real mat (fork only: field frames)."""

import pytest

from openflight.camera import resting_ball_range as rbr
from tests.test_patch_ball_search_field import empty_mat_scene


@pytest.mark.parametrize("x", [450.0, 500.0, 530.0])
def test_empty_part_of_recorded_mat_is_not_confirmed_by_a_seed(x):
    frames, _search = empty_mat_scene()

    assert (
        rbr.measure_resting_ball_size(frames, first_post_index=len(frames), seed=(x, 427.3, 40.0))
        is None
    )

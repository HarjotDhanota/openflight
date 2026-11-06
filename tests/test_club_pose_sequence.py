"""Missing poses, static equipment and collision-discontinuous soft motion."""

import numpy as np
import pytest

from openflight.camera.club_pose_sequence import sequence_hypotheses


def candidate(family, degrees, cost):
    return {
        "model_id": family,
        "parameters": [0, 0, np.deg2rad(degrees), 0, 0, 3],
        "score": {"cost": cost},
    }


def test_equipment_family_is_static_and_missing_frame_is_not_a_pose():
    pools = [
        [candidate("short", 0, 0), candidate("long", 0, 1)],
        [],
        [candidate("short", 0, 1), candidate("long", 0, 0)],
    ]
    result = sequence_hypotheses(
        pools, [1, 4_000_001, 8_000_001], [10, 11, 12], collision_window_ns=None
    )
    assert len(result["best_by_static_model"]) == 2
    for path in result["paths"]:
        assert path["indices"][1] is None
        assert all(
            pools[f][i]["model_id"] == path["model_id"]
            for f, i in enumerate(path["indices"])
            if i is not None
        )
    assert result["physical_pose"] is result["strike"] is None


def test_collision_break_allows_rotation_jump_without_smoothing_through_it():
    pools = [[candidate("short", 0, 0)], [candidate("short", 170, 0)]]
    smooth = sequence_hypotheses(pools, [1, 4_000_001], [10, 11], collision_window_ns=None)
    collision = sequence_hypotheses(
        pools, [1, 4_000_001], [10, 11], collision_window_ns=[2, 3_000_000]
    )
    assert smooth["paths"][0]["cost"] > 0
    assert collision["paths"][0]["cost"] == 0


def test_gap_breaks_motion_prior_and_retains_near_equal_rotation_rivals():
    pools = [[candidate("short", 0, 0)], [candidate("short", 170, 0), candidate("short", 0, 0.01)]]
    result = sequence_hypotheses(pools, [1, 8_000_001], [10, 12], collision_window_ns=None)
    assert len(result["retained_endings"]) == 2
    assert result["paths"][0]["indices"] == [0, 0]


def test_empty_sequence_pools_have_no_reconstructed_pose():
    result = sequence_hypotheses([[], []], [1, 2], [10, 11], collision_window_ns=None)
    assert result["paths"] == []
    assert result["qualified_face_normal"] is None


@pytest.mark.parametrize("times", [[1, 1], [2, 1]])
def test_reordered_or_duplicate_times_rejected(times):
    with pytest.raises(ValueError):
        sequence_hypotheses([[], []], times, [10, 11], collision_window_ns=None)


@pytest.mark.parametrize("bad", [np.nan, np.inf, -1])
def test_nonfinite_or_negative_objective_is_not_ranked(bad):
    with pytest.raises(ValueError):
        sequence_hypotheses([[candidate("short", 0, bad)]], [1], [10], collision_window_ns=None)

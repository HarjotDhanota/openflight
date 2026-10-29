"""A contact-frame pose mode must survive even when paths reconverge later."""

from copy import deepcopy

import numpy as np
import pytest

from openflight.camera.club_pose_seeds import anchored_sequence_hypotheses


def candidate(family, degrees, cost=0):
    return {
        "model_id": family,
        "parameters": [0, 0, np.deg2rad(degrees), 0, 0, 3],
        "score": {"cost": cost},
    }


def run(pools, indices, anchor, **options):
    return anchored_sequence_hypotheses(
        pools,
        [1 + 4_000_000 * i for i in range(len(pools))],
        indices,
        anchor_frame_index=anchor,
        collision_window_ns=None,
        temporal_weight=0,
        paths_retained=1,
        **options,
    )


def test_contact_modes_survive_reconvergence_at_later_endpoint():
    pools = [
        [candidate("one", 0)],
        [candidate("one", 0), candidate("one", 170)],
        [candidate("one", 0)],
    ]
    result = run(pools, [10, 11, 12], 11)
    assert len(result["unanchored"]["retained_endings"]) == 1
    assert len(result["paths_by_anchor_candidate"]) == 2
    assert {row["paths"][0]["indices"][1] for row in result["paths_by_anchor_candidate"]} == {0, 1}
    assert result["selected_path"] is None
    assert result["qualified_face_normal"] is result["physical_pose"] is result["strike"] is None


def test_family_and_original_pool_indices_are_preserved():
    pools = [
        [candidate("long", 0), candidate("short", 30)],
        [candidate("short", 35), candidate("long", 5)],
        [candidate("long", 10), candidate("short", 40)],
    ]
    result = run(pools, [40, 41, 42], 41)
    for group in result["paths_by_anchor_candidate"]:
        path = group["paths"][0]
        assert path["indices"][1] == group["anchor_candidate_index"]
        assert all(
            pools[f][i]["model_id"] == path["model_id"]
            for f, i in enumerate(path["indices"])
            if i is not None
        )


def test_missing_frames_stay_missing_and_input_pools_are_unchanged():
    pools = [[candidate("one", 0)], [], [candidate("one", 170)]]
    before = deepcopy(pools)
    result = run(pools, [10, 11, 12], 12)
    assert result["paths_by_anchor_candidate"][0]["paths"][0]["indices"][1] is None
    assert pools == before


def test_missing_anchor_pose_is_not_filled_from_other_frames():
    result = run([[candidate("one", 0)], []], [10, 11], 11)
    assert result["status"] == "withheld_no_current_anchor_pose"
    assert result["paths_by_anchor_candidate"] == []


def test_invalid_anchor_identity_refuses():
    with pytest.raises(ValueError, match="anchor frame"):
        run([[candidate("one", 0)]], [10], 12)

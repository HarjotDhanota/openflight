"""Separate current-image evidence, background reliability and layer ownership."""

import numpy as np
import pytest

from openflight.camera.club_visibility import boundary_evidence


def scene():
    head = np.zeros((48, 64), bool)
    head[12:36, 20:44] = True
    ball = np.zeros_like(head)
    ball[18:30, 38:50] = True
    return head, ball


def evaluate(head, ball, **changes):
    fields = dict(
        depth_order="unknown",
        background_reliable=np.ones_like(head),
        difference_foreground=head.copy(),
        current_edges=np.zeros_like(head),
        current_foreground=np.zeros_like(head),
        current_background=np.zeros_like(head),
    )
    fields.update(changes)
    return boundary_evidence(head, [ball], **fields)


def test_unknown_depth_retains_distinct_layer_hypotheses():
    head, ball = scene()
    result = evaluate(head, ball)
    a, b = result["hypotheses"]
    assert a["depth_order"] == "ball_front"
    assert b["depth_order"] == "head_front"
    assert a["occluded_boundary_pixels"] > 0
    assert b["occluded_boundary_pixels"] == 0
    assert a["visible_boundary_pixels"] < b["visible_boundary_pixels"]
    assert b["ball_facing_pairs"] > 0
    assert result["selected_hypothesis"] is None
    assert result["qualified_face_normal"] is None


def test_background_residual_is_not_a_current_occluder_or_confirmed_foreground():
    head, ball = scene()
    reliable = np.ones_like(head)
    reliable[:, 40:] = False
    edges = np.zeros_like(head)
    edges[12:36, 43] = True
    result = evaluate(
        head,
        ball & False,
        depth_order="head_front",
        background_reliable=reliable,
        current_edges=edges,
        difference_foreground=np.ones_like(head),
    )
    hypothesis = result["hypotheses"][0]
    assert hypothesis["occluded_boundary_pixels"] == 0
    assert hypothesis["current_edge_supported_boundary_pixels"] > 0
    assert hypothesis["difference_unreliable_pairs"] > 0
    assert hypothesis["paired_foreground_background_support"] == 0
    assert hypothesis["current_unknown_pairs"] > 0


def test_full_ball_occlusion_is_unknown_not_perfect_boundary_support():
    head, _ = scene()
    result = evaluate(head, np.ones_like(head), depth_order="ball_front")
    hypothesis = result["hypotheses"][0]
    assert hypothesis["visible_boundary_pixels"] == 0
    assert hypothesis["paired_foreground_background_support"] == 0
    assert hypothesis["status"] == "withheld_no_visible_head_boundary"
    assert "cost" not in hypothesis


def test_both_foreground_sides_contradict_external_rim():
    head, ball = scene()
    foreground = np.zeros_like(head)
    foreground[8:40, 16:48] = True
    result = evaluate(head, ball & False, depth_order="head_front", current_foreground=foreground)
    hypothesis = result["hypotheses"][0]
    assert hypothesis["both_sides_foreground_pairs"] > 0
    assert hypothesis["paired_foreground_background_support"] == 0
    assert hypothesis["current_contradiction_pairs"] > 0


def test_explicit_current_background_can_support_boundary():
    head, ball = scene()
    result = evaluate(
        head,
        ball & False,
        depth_order="head_front",
        current_foreground=head,
        current_background=~head,
    )
    hypothesis = result["hypotheses"][0]
    assert hypothesis["paired_foreground_background_support"] > 0
    assert hypothesis["current_contradiction_pairs"] == 0
    assert hypothesis["current_unknown_pairs"] == 0


def test_multiple_ball_candidates_remain_separate():
    head, ball = scene()
    result = boundary_evidence(
        head,
        [ball, np.roll(ball, -20, axis=1)],
        depth_order="unknown",
        background_reliable=np.ones_like(head),
        difference_foreground=head,
        current_edges=np.zeros_like(head),
    )
    assert len(result["hypotheses"]) == 4
    assert {row["ball_candidate_index"] for row in result["hypotheses"]} == {0, 1}


def test_missing_ball_input_keeps_unknown_visibility_arm():
    head, ball = scene()
    result = boundary_evidence(
        head,
        [ball, None],
        depth_order="unknown",
        background_reliable=np.ones_like(head),
        difference_foreground=head,
        current_edges=np.zeros_like(head),
    )
    assert len(result["hypotheses"]) == 3
    missing = result["hypotheses"][-1]
    assert missing["status"] == "withheld_missing_ball_visibility"
    assert missing["unknown_boundary_pixels"] > 0 and missing["visible_boundary_pixels"] == 0


def test_invalid_channel_shapes_or_contradictory_pixel_labels_refused():
    head, ball = scene()
    with pytest.raises(ValueError, match="matching boolean"):
        evaluate(head, ball, current_edges=np.zeros((2, 2), bool))
    with pytest.raises(ValueError, match="contradictory"):
        evaluate(head, ball, current_foreground=head, current_background=head)
    with pytest.raises(ValueError, match="depth order"):
        evaluate(head, ball, depth_order="guess")


def test_no_observed_edge_or_hidden_channel_is_invented():
    head, ball = scene()
    result = evaluate(head, ball & False)
    assert all(row["current_edge_supported_boundary_pixels"] == 0 for row in result["hypotheses"])
    assert all(row["current_unknown_pairs"] > 0 for row in result["hypotheses"])
    assert result["physical_pose"] is None and result["strike"] is None


def test_ball_crossing_changes_only_current_layer_visibility():
    head, ball = scene()
    counts = []
    for displacement in (-35, -15, 0, 20):
        moved = np.roll(ball, displacement, axis=1)
        result = evaluate(head, moved, depth_order="ball_front")
        counts.append(result["hypotheses"][0]["occluded_boundary_pixels"])
    assert counts[0] == counts[-1] == 0
    assert max(counts) > 0
    assert (
        evaluate(head, ball & False, depth_order="ball_front")["hypotheses"][0][
            "occluded_boundary_pixels"
        ]
        == 0
    )


def test_ball_facing_edge_never_becomes_external_background_evidence():
    head, ball = scene()
    result = evaluate(
        head, ball, depth_order="head_front", current_foreground=head, current_background=~head
    )
    with_ball = result["hypotheses"][0]
    no_ball = evaluate(
        head,
        ball & False,
        depth_order="head_front",
        current_foreground=head,
        current_background=~head,
    )["hypotheses"][0]
    assert with_ball["ball_facing_pairs"] > 0
    assert (
        with_ball["paired_foreground_background_support"]
        < no_ball["paired_foreground_background_support"]
    )


def test_inputs_are_unchanged_and_empty_head_remains_withheld():
    head, ball = scene()
    before = head.copy(), ball.copy()
    result = evaluate(head & False, ball)
    assert all(row["status"] == "withheld_no_visible_head_boundary" for row in result["hypotheses"])
    evaluate(head, ball)
    assert np.array_equal(head, before[0]) and np.array_equal(ball, before[1])

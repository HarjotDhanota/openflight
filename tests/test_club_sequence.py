"""Own-frame integrity, ambiguous identity, loss, and context invalidation."""

import dataclasses
import hashlib

import numpy as np
import pytest

from openflight.camera.club_sequence import (
    ClubAssociationGraph,
    ClubFrameIdentity,
    bind_observation,
)


def packet(index=10, *, centres=((20, 20),), context=None):
    frame = np.zeros((80, 100), np.uint8)
    components, points = [], []
    for x, y in centres:
        outline = [[x, y], [x + 5, y], [x + 5, y + 5], [x, y + 5]]
        components.append({"outlines": [outline]})
        points.extend(outline)
    identity = ClubFrameIdentity(
        "a" * 64,
        index,
        hashlib.sha256(frame.tobytes()).hexdigest(),
        1_000_000_000 + index * 4_000_000,
        298.0,
        "recorded_start",
        "raw",
        "mode1",
        "control1",
        None,
        None,
    )
    identity = dataclasses.replace(identity, **(context or {}))
    evidence = {
        "schema": "openflight.club_part_observations.v1",
        "strong_points": points,
        "weak_points": [],
        "region": {"components": components},
        "uses_future_frames": False,
        "measurement_eligible": False,
        "qualified_outline": None,
        "physical_pose": None,
        "strike": None,
    }
    return frame, evidence, identity


def bound(index=10, **kwargs):
    frame, e, identity = packet(index, **kwargs)
    return bind_observation(
        frame, e, identity, expected_identity=identity, background_frame_indices=range(8)
    )


def graph():
    return ClubAssociationGraph(
        motion_scale_px_s=1000, base_margin_px=4, reacquire_after_ns=20_000_000
    )


def test_binding_is_immutable_and_never_physical():
    b = bound()
    first = b.evidence
    first["parts"]["strong_points"].clear()
    assert b.evidence["parts"]["strong_points"]
    assert b.identity.equipment_id is b.identity.optics_id is None
    with pytest.raises(dataclasses.FrozenInstanceError):
        b.identity.frame_index = 20


@pytest.mark.parametrize(
    "field,value",
    [
        ("capture_sha256", "b" * 64),
        ("frame_index", 11),
        ("view_id", "rectified"),
        ("mode_id", "mode2"),
        ("controls_id", "control2"),
        ("optics_id", "new"),
        ("equipment_id", "other"),
    ],
)
def test_wrong_context_cannot_bind(field, value):
    frame, e, identity = packet()
    with pytest.raises(ValueError, match="identity"):
        bind_observation(
            frame,
            e,
            dataclasses.replace(identity, **{field: value}),
            expected_identity=identity,
            background_frame_indices=range(8),
        )


def test_wrong_image_bytes_and_future_background_refused():
    frame, e, identity = packet()
    frame[0, 0] = 1
    with pytest.raises(ValueError, match="image bytes"):
        bind_observation(
            frame, e, identity, expected_identity=identity, background_frame_indices=range(8)
        )
    frame[0, 0] = 0
    with pytest.raises(ValueError, match="past-frame"):
        bind_observation(
            frame, e, identity, expected_identity=identity, background_frame_indices=[11]
        )


@pytest.mark.parametrize(
    "key,value",
    [
        ("schema", "unknown"),
        ("measurement_eligible", True),
        ("uses_future_frames", True),
        ("physical_pose", [1, 2]),
    ],
)
def test_unsupported_schema_prediction_or_qualification_refused(key, value):
    frame, e, identity = packet()
    with pytest.raises(ValueError):
        bind_observation(
            frame,
            {**e, key: value},
            identity,
            expected_identity=identity,
            background_frame_indices=range(8),
        )


def test_all_identity_competitors_and_links_retained():
    tracker = graph()
    tracker.update(bound(centres=((20, 20), (60, 20))))
    result = tracker.update(bound(11, centres=((22, 20), (62, 20))))
    assert len(result["current_candidates"]) == 2 and len(result["candidate_links"]) == 4
    assert result["selected_identity"] is result["physical_pose"] is result["strike"] is None
    assert not result["uses_future_frames"]


def test_loss_expands_search_then_reacquires_without_carried_detection():
    tracker = graph()
    tracker.update(bound())
    first = tracker.update(bound(11, centres=()))
    second = tracker.update(bound(12, centres=()))
    assert first["current_candidates"] == second["current_candidates"] == []
    assert second["prior_search_boxes_px"][0][0] < first["prior_search_boxes_px"][0][0]
    assert first["status"] == "lost_no_current_observation"
    late = tracker.update(bound(16, centres=()))
    assert late["search"] == "broad_reacquisition" and late["prior_search_boxes_px"] == []
    fresh = tracker.update(bound(17, centres=((75, 30),)))
    assert len(fresh["current_candidates"]) == 1 and fresh["candidate_links"] == []


@pytest.mark.parametrize(
    "context",
    [
        {"capture_sha256": "b" * 64},
        {"controls_id": "new"},
        {"mode_id": "same_size_new_crop"},
        {"view_id": "rectified"},
        {"optics_id": "new"},
        {"equipment_id": "short_hosel"},
    ],
)
def test_context_changes_invalidate_prior_identity(context):
    tracker = graph()
    tracker.update(bound())
    result = tracker.update(bound(11, context=context))
    assert result["context_reset"] and not result["candidate_links"]
    assert result["search"] == "broad_reacquisition"


def test_duplicate_and_reordered_frames_rejected():
    tracker = graph()
    tracker.update(bound())
    with pytest.raises(ValueError, match="stale"):
        tracker.update(bound())
    with pytest.raises(ValueError, match="stale"):
        tracker.update(bound(9))


def test_disconnected_pixel_inside_region_box_does_not_shift_identity_centre():
    frame, e, identity = packet()
    e["region"]["components"][0]["outlines"] = [
        [[20, 20], [30, 20], [30, 22], [22, 22], [22, 30], [20, 30]]
    ]
    e["strong_points"] = [[20, 20], [21, 20], [20, 21], [29, 29]]
    b = bind_observation(
        frame, e, identity, expected_identity=identity, background_frame_indices=range(8)
    )
    current = graph().update(b)["current_candidates"][0]
    assert current["centre_px"] == pytest.approx([20 + 1 / 3, 20 + 1 / 3])

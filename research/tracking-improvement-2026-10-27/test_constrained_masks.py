"""Known-answer selection/refusal tests; no field recordings are required."""

import cv2
import numpy as np
import pytest
from constrained_masks import fully_precontact_frames, select_component

from openflight.camera import impact_location as il


def scene(*, distractor=False, thin=False):
    mask = np.zeros((240, 360), np.uint8)
    cv2.rectangle(mask, (110, 130), (190, 145 if thin else 180), 1, -1)
    cv2.line(mask, (113, 150), (70, 35), 1, 5)
    if distractor:
        cv2.rectangle(mask, (230, 70), (340, 190), 1, -1)
    return mask.astype(bool)


@pytest.mark.parametrize("mirror", [False, True], ids=["RH", "LH"])
def test_shaft_anchor_selects_head_over_larger_moving_background(mirror):
    mask = scene(distractor=True)
    anchor = (113.0, 151.0)
    if mirror:
        mask = mask[:, ::-1].copy()
        anchor = (mask.shape[1] - 1 - anchor[0], anchor[1])
    head, reason = select_component(mask, 1.0, anchor)
    assert reason == "candidate_only"
    assert head is not None
    truth = scene()[:, ::-1] if mirror else scene()
    assert not np.any(head & ~truth)
    assert head[160, 170 if not mirror else 189]


def test_missing_anchor_cannot_be_replaced_with_largest_blob():
    head, reason = select_component(scene(), 1.0, None)
    assert head is None and reason == "no_observed_hosel"


def test_static_reflection_is_not_a_connected_anchor():
    head, reason = select_component(scene(distractor=True), 1.0, (215, 25))
    assert head is None and reason == "no_component_at_observed_hosel"


def test_multiple_components_within_anchor_uncertainty_refuse():
    mask = np.zeros((160, 220), bool)
    mask[60:110, 20:95] = True
    mask[60:110, 99:174] = True
    head, reason = select_component(mask, 1.0, (96.5, 85))
    assert head is None and reason == "ambiguous_components_at_observed_hosel"


def test_thin_head_does_not_lower_existing_core_gate():
    head, reason = select_component(scene(thin=True), 1.0, (113, 140))
    assert head is None and reason == "no_body_thick_enough_to_be_a_clubhead"


def test_head_cropped_at_border_refuses():
    mask = scene()[:, 135:]
    head, reason = select_component(mask, 1.0, (1, 160))
    assert head is None and reason == "head_touches_crop"


def test_ball_occlusion_refuses_even_when_core_survives():
    mask = scene()
    occlusion = np.zeros_like(mask)
    occlusion[168:190, 165:200] = True
    head, reason = select_component(mask, 1.0, (113, 151), occlusion=occlusion)
    assert head is None and reason == "head_overlaps_occlusion"


def test_small_and_large_regions_keep_existing_area_gates():
    small = np.zeros((100, 100), np.uint8)
    cv2.circle(small, (50, 50), 15, 1, -1)
    head, reason = select_component(small, 1.0, (50, 50))
    assert head is None and reason == "head_area_below_existing_gate"
    head, reason = select_component(scene(), 2.0, (113, 151))
    assert head is None and reason == "head_area_above_existing_gate"


@pytest.mark.parametrize("anchor", [(float("nan"), 3), (-1, 30), (1000, 30)])
def test_invalid_anchor_refuses(anchor):
    head, reason = select_component(scene(), 1.0, anchor)
    assert head is None and reason == "invalid_hosel"


@pytest.mark.parametrize("mirror", [False, True], ids=["RH", "LH"])
@pytest.mark.parametrize("head_level", [30, 200], ids=["dark_head", "bright_head"])
def test_translated_head_with_static_reflection_and_moving_distractor(mirror, head_level):
    background = np.full((240, 360), 90, np.uint8)
    cv2.circle(background, (290, 30), 18, 255, -1)
    frames = [background.copy() for _ in range(3)]
    truths = []
    for offset in (0, 8, 16):
        truth = np.roll(scene(), offset, axis=1)
        image = background.copy()
        image[truth] = head_level
        cv2.rectangle(image, (230, 70), (340, 190), 240, -1)
        frames.append(image)
        truths.append(truth)
    frames = np.stack(frames)
    if mirror:
        frames = frames[:, :, ::-1].copy()
        truths = [t[:, ::-1] for t in truths]
    moving = il._moving_masks(frames, slice(0, 3), (3, 4, 5))
    centres = []
    for index, offset, truth in zip((3, 4, 5), (0, 8, 16), truths):
        anchor_x = 113 + offset
        anchor = (359 - anchor_x if mirror else anchor_x, 151)
        head, reason = select_component(moving[index], 1, anchor)
        assert reason == "candidate_only"
        # The existing 3x3 closing can fill one boundary pixel at the shaft join.
        outside_distance = cv2.distanceTransform((~truth).astype(np.uint8), cv2.DIST_L2, 5)
        assert outside_distance[head].max() <= 1.0
        reflection_x = 69 if mirror else 290
        assert not head[10:50, reflection_x - 20 : reflection_x + 20].any()
        centres.append(np.nonzero(head)[1].mean())
    assert np.diff(centres) == pytest.approx([-8, -8] if mirror else [8, 8], abs=0.1)


def test_insufficient_contrast_is_not_filled_from_hosel():
    background = np.full((240, 360), 90, np.uint8)
    image = background.copy()
    image[scene()] = 100
    frames = np.stack([background, background, background, image])
    moving = il._moving_masks(frames, slice(0, 3), (3,))
    head, reason = select_component(moving[3], 1, (113, 151))
    assert head is None and reason == "no_component_at_observed_hosel"


def test_crossing_and_postcontact_exposures_are_excluded():
    stamps = np.arange(5) * 1_000_000
    complete = fully_precontact_frames(stamps, [100, 100, 1500, 100, 100], 3_000_000)
    assert complete.tolist() == [0, 1]


@pytest.mark.parametrize("exposures", [None, [100], [100, -1], [100, float("nan")]])
def test_unknown_or_invalid_exposure_cannot_claim_precontact(exposures):
    with pytest.raises(ValueError, match="unaligned_or_unknown"):
        fully_precontact_frames([0, 1_000_000], exposures, 2_000_000)

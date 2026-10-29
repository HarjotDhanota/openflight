"""Uncertain ball proposals may suppress club evidence without erasing alternatives."""

import cv2
import numpy as np
import pytest

from openflight.camera.club_acquisition import current_hypotheses


def scene():
    background = np.full((120, 160), 80, np.float32)
    image = background.astype(np.uint8)
    cv2.line(image, (30, 8), (80, 85), 150, 3)
    cv2.ellipse(image, (94, 87), (18, 12), 0, 0, 360, 110, -1)
    return image, background, np.zeros_like(background)


def acquire(veto, **options):
    return current_hypotheses(
        *scene(),
        ball_diameter_px=15,
        noise_dn=0,
        ball_veto=veto,
        ball_observation_ids=["own-current-proposal"],
        **options,
    )


def test_uncertain_branch_reuses_exact_unexcluded_pixels_without_merging_hypotheses():
    branches = acquire([53, 45, 15])
    assert [b["ball_identity_branch"] for b in branches] == [
        "ball_excluded",
        "ball_identity_uncertain",
    ]
    control = acquire(None)[0]
    for key in ("axis", "parts", "contours", "geometry"):
        assert branches[1][key] == control[key]
    assert branches[0]["ball_veto_used"] == (53, 45, 15)
    assert branches[1]["ball_veto_used"] is None
    assert branches[1]["physical_pose"] is branches[1]["strike"] is None


def test_axis_roi_restores_current_support_to_full_image_coordinates():
    full = acquire(None)[0]
    cropped = acquire(None, axis_roi=(10, 0, 150, 120))[0]
    pixels = np.asarray(cropped["axis"]["support_pixels_px"])
    image = scene()[0]
    assert np.all(image[pixels[:, 1], pixels[:, 0]] > 80)
    assert cropped["axis"]["end_px"] == pytest.approx(full["axis"]["end_px"], abs=1)


def test_missing_ball_proposal_keeps_single_named_branch_without_assuming_absence():
    branches = acquire(None)
    assert len(branches) == 1
    assert branches[0]["ball_identity_branch"] == "no_ball_veto_available"


def test_ball_disk_requires_explicit_own_proposal_lineage():
    with pytest.raises(ValueError, match="lineage"):
        current_hypotheses(*scene(), ball_diameter_px=15, noise_dn=0, ball_veto=[50, 40, 12])


@pytest.mark.parametrize("veto", [[50, 40, -1], [50, np.nan, 12], [50, 40]])
def test_invalid_disk_is_not_silently_applied_or_discarded(veto):
    with pytest.raises(ValueError, match="ball proposal"):
        acquire(veto)


def test_input_arrays_are_unchanged_and_branches_do_not_share_observation_mutations():
    image, background, variation = scene()
    original = image.copy()
    branches = current_hypotheses(
        image,
        background,
        variation,
        ball_diameter_px=15,
        noise_dn=0,
        ball_veto=[140, 110, 3],
        ball_observation_ids=["own-current-proposal"],
    )
    assert np.array_equal(image, original)
    assert branches[0]["parts"] is not branches[1]["parts"]

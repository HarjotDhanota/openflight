"""Club-part label contract, crop mapping and landmark visibility."""

import numpy as np
import pytest

from openflight.camera.club_parts import (
    IGNORE,
    LABEL_V1,
    LABEL_V2,
    V1_CLASSES,
    V2_CLASSES,
    ClubPartObservation,
    CropTransform,
    Visibility,
    mask_landmarks,
    to_v1,
)


def test_v2_merges_body_and_neck_into_the_v1_head_and_keeps_ignore():
    mask = np.array([[0, 1, 2, 3, 4, 5, IGNORE, 9]], dtype=np.uint8)
    assert to_v1(mask).tolist() == [[0, 1, 1, 2, 3, 4, IGNORE, IGNORE]]
    assert V1_CLASSES[1] == "head" and V2_CLASSES[1:3] == ("head_body", "neck")


def test_crop_round_trip_and_identity_scale():
    transform = CropTransform(640, 400, False, x0=100, y0=50, side=120, size=120)
    assert transform.to_crop(np.array([100.0, 50.0])).tolist() == [0.0, 0.0]
    resized = CropTransform(640, 400, False, x0=100, y0=50, side=120, size=160)
    points = np.array([[100.0, 50.0], [219.0, 169.0], [150.25, 77.5]])
    assert np.allclose(resized.to_canonical(resized.to_crop(points)), points)
    assert resized.scale == pytest.approx(0.75)


def test_extract_matches_a_slice_and_mirrored_storage_gives_the_same_canonical_crop():
    rng = np.random.default_rng(3)
    canonical = rng.integers(0, 255, (40, 60), dtype=np.uint8)
    plain = CropTransform(60, 40, False, x0=10, y0=5, side=16, size=16)
    assert np.array_equal(plain.extract(canonical), canonical[5:21, 10:26])
    mirrored = CropTransform(60, 40, True, x0=10, y0=5, side=16, size=16)
    assert np.array_equal(mirrored.extract(canonical[:, ::-1].copy()), canonical[5:21, 10:26])
    assert np.array_equal(
        mirrored.canonical_to_stored(np.array([[0.0, 3.0], [59.0, 4.0]])),
        [[59.0, 3.0], [0.0, 4.0]],
    )


def test_pixels_outside_the_frame_are_zero_or_ignore_and_marked_out_of_frame():
    frame = np.full((20, 20), 7, dtype=np.uint8)
    transform = CropTransform(20, 20, False, x0=15, y0=-2, side=8, size=8)
    assert transform.extract(frame)[0, 0] == 0 and transform.extract(frame)[3, 0] == 7
    assert transform.extract(frame, labels=True)[0, 7] == IGNORE
    inside = transform.in_frame()
    assert not inside[0, 0] and inside[2, 0] and not inside[2, 5] and inside[2, 4]
    with pytest.raises(ValueError):
        transform.extract(np.zeros((21, 20), dtype=np.uint8))


def test_centred_crop_and_invalid_sizes():
    transform = CropTransform.centred(
        640, 400, stored_mirrored=False, centre=(300.4, 200.6), side=120, size=128
    )
    assert (transform.x0, transform.y0) == (240, 141)
    with pytest.raises(ValueError):
        CropTransform(640, 400, False, 0, 0, 0, 128)


def _club_mask() -> np.ndarray:
    mask = np.zeros((40, 40), dtype=np.uint8)
    mask[15:25, 12:30] = 1
    mask[10:16, 9:13] = 2
    mask[2:10, 5:9] = 3
    return mask


def test_landmarks_follow_the_mark_convention_in_canonical_pixels():
    transform = CropTransform(640, 400, False, x0=100, y0=200, side=40, size=40)
    found = mask_landmarks(_club_mask(), transform, toe_direction=1, topline_column=120.0)
    assert found["toe"].visibility is Visibility.VISIBLE
    assert found["toe"].position == (129.0, 220.0)
    assert found["heel"].position == (113.0, 215.0)
    assert found["topline"].position == (120.0, 215.0)


def test_left_handed_toe_and_missing_neck_or_body_are_not_invented():
    transform = CropTransform(640, 400, False, x0=0, y0=0, side=40, size=40)
    mask = _club_mask()
    mask[mask == 2] = 0
    found = mask_landmarks(mask, transform, toe_direction=-1)
    assert found["toe"].position == (12.0, 20.0)
    assert found["heel"].visibility is Visibility.UNKNOWN
    empty = mask_landmarks(np.zeros((40, 40), np.uint8), transform, toe_direction=1)
    assert {item.visibility for item in empty.values()} == {Visibility.UNKNOWN}
    with pytest.raises(ValueError):
        mask_landmarks(mask, transform, toe_direction=0)


def test_landmarks_at_the_crop_edge_or_beside_ignore_are_not_visible():
    transform = CropTransform(640, 400, False, x0=0, y0=0, side=40, size=40)
    mask = _club_mask()
    mask[15:25, 30:40] = 1
    assert mask_landmarks(mask, transform, toe_direction=1)["toe"].visibility is (
        Visibility.OUT_OF_VIEW
    )
    mask = _club_mask()
    mask[14, 18:22] = IGNORE
    found = mask_landmarks(mask, transform, toe_direction=1, topline_column=20.0)
    assert found["topline"].visibility is Visibility.UNKNOWN and found["topline"].position is None


def test_observation_rejects_probabilities_for_the_wrong_label_version():
    transform = CropTransform(640, 400, False, 0, 0, 120, 64)
    common = dict(
        capture_id="clip",
        frame_index=3,
        pixel_sha256="0" * 64,
        sensor_timestamp_ns=None,
        exposure_us=298.0,
        transform=transform,
        model_id="sha256:abc",
    )
    ClubPartObservation(label_version=LABEL_V2, probabilities=np.zeros((6, 64, 64)), **common)
    with pytest.raises(ValueError):
        ClubPartObservation(label_version=LABEL_V1, probabilities=np.zeros((6, 64, 64)), **common)

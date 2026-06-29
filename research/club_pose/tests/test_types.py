import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from club_pose.types import BALL_RADIUS_MM, ClubheadPose, Measurement


def test_ball_radius_constant():
    assert BALL_RADIUS_MM == pytest.approx(21.35)


def test_measurement_holds_fields():
    m = Measurement(value=1.5, confidence=0.9, source="impact")
    assert m.value == 1.5 and m.confidence == 0.9 and m.source == "impact"


def test_identity_pose_roundtrip():
    pose = ClubheadPose(Rotation.identity(), np.zeros(3))
    p = np.array([10.0, -5.0, 3.0])
    np.testing.assert_allclose(pose.world_to_body(pose.body_to_world(p)), p, atol=1e-9)


def test_translation_then_rotation():
    pose = ClubheadPose(Rotation.from_euler("z", 90, degrees=True), np.array([100.0, 0, 0]))
    # body +X -> world +Y (90 deg about +Z), then +translation
    np.testing.assert_allclose(pose.body_to_world([1, 0, 0]), [100.0, 1.0, 0.0], atol=1e-9)


def test_direction_ignores_translation():
    pose = ClubheadPose(Rotation.identity(), np.array([100.0, 0, 0]))
    np.testing.assert_allclose(pose.direction_to_world([1, 0, 0]), [1, 0, 0], atol=1e-9)


def test_from_matrix_rejects_scaled_matrix():
    with pytest.raises(ValueError):
        ClubheadPose.from_matrix(2 * np.eye(3), np.zeros(3))


def test_from_matrix_accepts_valid_rotation():
    R = Rotation.from_euler("y", 30, degrees=True).as_matrix()
    pose = ClubheadPose.from_matrix(R, [1, 2, 3])
    np.testing.assert_allclose(pose.translation, [1, 2, 3])

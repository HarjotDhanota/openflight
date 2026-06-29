import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from club_pose.metrics import attack_angle, club_path, compute_metrics, dynamic_loft, face_angle
from club_pose.template import ClubTemplate, default_template
from club_pose.types import BALL_RADIUS_MM, ClubheadPose


def _flat(loft):
    return ClubTemplate("iron", loft, 80.0, 55.0, None, None, np.array([20.0, 0.0, 0.0]))


def test_dynamic_loft_equals_static_at_identity():
    pose = ClubheadPose(Rotation.identity(), np.zeros(3))
    assert dynamic_loft(pose, _flat(25.0)) == pytest.approx(25.0, abs=1e-6)


def test_square_face_is_zero_face_angle():
    pose = ClubheadPose(Rotation.identity(), np.zeros(3))
    assert face_angle(pose, _flat(10.0)) == pytest.approx(0.0, abs=1e-6)


def test_open_face_is_positive():
    # rotate clubhead 3 deg about +Z (world up): face normal swings toward -Y (right) = open
    pose = ClubheadPose(Rotation.from_euler("z", -3, degrees=True), np.zeros(3))
    assert face_angle(pose, _flat(10.0)) == pytest.approx(3.0, abs=1e-6)


def test_club_path_in_to_out_positive():
    a = ClubheadPose(Rotation.identity(), np.zeros(3))
    b = ClubheadPose(Rotation.identity(), np.array([1000.0, -50.0, 0.0]))  # moving downrange + right
    assert club_path(a, b, dt=0.001) == pytest.approx(
        np.degrees(np.arctan2(50.0, 1000.0)), abs=1e-6
    )


def test_attack_angle_descending_negative():
    a = ClubheadPose(Rotation.identity(), np.zeros(3))
    b = ClubheadPose(Rotation.identity(), np.array([1000.0, 0.0, -50.0]))  # moving down
    assert attack_angle(a, b, dt=0.001) < 0


def test_compute_metrics_no_ball_uses_center_normal():
    pose = ClubheadPose(Rotation.identity(), np.zeros(3))
    m = compute_metrics(pose, _flat(20.0))
    assert m.impact_offset.value is None
    assert m.dynamic_loft.value == pytest.approx(20.0, abs=1e-6)
    assert m.dynamic_loft.source == "center"


def test_compute_metrics_dt_zero_raises():
    a = ClubheadPose(Rotation.identity(), np.zeros(3))
    with pytest.raises(ValueError):
        club_path(a, a, dt=0.0)

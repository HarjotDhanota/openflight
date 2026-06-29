import numpy as np
import pytest

from club_pose.groundtruth import ball_for_impact, pose_for_face_angle_loft, two_poses_for_velocity
from club_pose.metrics import compute_metrics
from club_pose.template import ClubTemplate, default_template


def _flat():
    return ClubTemplate("iron", 0.0, 80.0, 55.0, None, None, np.array([20.0, 0.0, 0.0]))


def test_ball_for_impact_recovers_via_metrics():
    t = default_template("driver")
    pose = pose_for_face_angle_loft(0.0, t.static_loft_deg)
    ball = ball_for_impact(pose, t, 14.0, -9.0)
    m = compute_metrics(pose, t, ball_center_world=ball)
    assert m.impact_offset.value == pytest.approx(14.0, abs=1e-3)
    assert m.impact_height.value == pytest.approx(-9.0, abs=1e-3)


def test_pose_for_face_angle_loft_roundtrips():
    t = _flat()
    pose = pose_for_face_angle_loft(4.0, 12.0)
    m = compute_metrics(pose, t)
    assert m.face_angle.value == pytest.approx(4.0, abs=1e-4)
    assert m.dynamic_loft.value == pytest.approx(12.0, abs=1e-4)


def test_two_poses_recover_velocity_angles():
    a, b = two_poses_for_velocity([1000.0, -50.0, 30.0], dt=0.001)
    t = _flat()
    m = compute_metrics(b, t, prev_pose=a, dt=0.001)
    assert m.club_path.value == pytest.approx(np.degrees(np.arctan2(50.0, 1000.0)), abs=1e-4)
    assert m.attack_angle.value == pytest.approx(
        np.degrees(np.arctan2(30.0, np.hypot(1000.0, 50.0))), abs=1e-4
    )

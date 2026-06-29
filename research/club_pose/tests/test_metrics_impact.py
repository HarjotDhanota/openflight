import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from club_pose.metrics import impact_location
from club_pose.template import default_template
from club_pose.types import BALL_RADIUS_MM, ClubheadPose


def _ball_at(template, pose, u0, v0):
    surf = template.face_center_offset + template.face_to_body_vec(
        [u0, v0, template.surface_height_face(u0, v0)]
    )
    n = template.face_to_body_vec(template.surface_normal_face(u0, v0))
    return pose.body_to_world(surf + BALL_RADIUS_MM * n)


def test_valid_contact_recovers_offset_height():
    t = default_template("driver")
    pose = ClubheadPose(Rotation.identity(), np.zeros(3))
    ball = _ball_at(t, pose, 12.0, -6.0)
    off, hgt, proj, state = impact_location(pose, t, ball)
    assert state == "valid_contact"
    assert off.value == pytest.approx(12.0, abs=1e-3)
    assert hgt.value == pytest.approx(-6.0, abs=1e-3)
    assert off.confidence > 0.9


def test_off_face_is_invalid():
    t = default_template("driver")
    pose = ClubheadPose(Rotation.identity(), np.zeros(3))
    ball = _ball_at(t, pose, 200.0, 0.0)  # past the toe
    off, hgt, proj, state = impact_location(pose, t, ball)
    assert state == "invalid_contact"
    assert off.value is None and off.confidence == 0.0


def test_far_off_surface_is_invalid():
    t = default_template("driver")
    pose = ClubheadPose(Rotation.identity(), np.zeros(3))
    # ball centered far in front of the face (distance >> ball radius + tol)
    ball = pose.body_to_world(t.face_center_offset + np.array([100.0, 0.0, 0.0]))
    off, hgt, proj, state = impact_location(pose, t, ball)
    assert state == "invalid_contact"

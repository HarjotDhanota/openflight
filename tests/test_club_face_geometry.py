"""Known-answer strike frames, independent degrees of freedom and correlations."""

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from openflight.camera.club_face_geometry import (
    face_contact_geometry,
    normal_from_angles,
    propagate_strike_covariance,
    radial_velocity,
    strike_sensitivity,
)


def basis():
    return np.array([[0.0, 0, 1], [1, 0, 0], [0, 1, 0]])


def test_normal_angles_convention_and_normal_does_not_fix_strike_origin():
    assert normal_from_angles(0, 0) == pytest.approx([1, 0, 0])
    assert normal_from_angles(90, 0) == pytest.approx([0, 1, 0], abs=1e-15)
    assert normal_from_angles(0, 90) == pytest.approx([0, 0, 1], abs=1e-15)
    first = face_contact_geometry([21.35, 10, 5], [0, 0, 0], basis())
    moved_origin = face_contact_geometry([21.35, 10, 5], [0, 25, 0], basis())
    assert first["toe_up_mm"] == [10, 5]
    assert moved_origin["toe_up_mm"] == [-15, 5]
    assert first["plane_sphere_gap_mm"] == pytest.approx(0)
    assert first["strike"] is None


def test_gap_is_not_silently_relabelled_contact():
    result = face_contact_geometry([30, 10, 5], [0, 0, 0], basis())
    assert result["plane_sphere_gap_mm"] > 0
    assert result["candidate_sphere_point_mm"][0] > 0
    assert result["strike"] is None


def test_radial_velocity_keeps_rotating_scatterer_reference_point():
    assert radial_velocity([1, 0, 0], [0, 0, 2], [0, 1, 0], [1, 0, 0]) == -1
    assert radial_velocity([1, 0, 0], [0, 0, 2], [0, 0, 0], [1, 0, 0]) == 1


def test_strike_jacobian_matches_cartesian_finite_differences():
    ball, origin, face = np.array([21.35, 10, 5]), np.zeros(3), basis()
    vb, vo, omega = np.array([1.0, 2, 3]), np.array([2.0, 1, 0]), np.array([0.01, 0.02, 0.03])
    jacobian = strike_sensitivity(ball, origin, face, vb, vo, omega)
    baseline = np.asarray(face_contact_geometry(ball, origin, face)["toe_up_mm"])
    for i in range(10):
        step = 1e-6
        b, o, r = ball.copy(), origin.copy(), face.copy()
        if i < 3:
            b[i] += step
        elif i < 6:
            o[i - 3] += step
        elif i < 9:
            rotation = np.zeros(3)
            rotation[i - 6] = step
            r = Rotation.from_rotvec(rotation).as_matrix() @ face
        else:
            b, o = ball + vb * step, origin + vo * step
            r = Rotation.from_rotvec(omega * step).as_matrix() @ face
        value = np.asarray(face_contact_geometry(b, o, r)["toe_up_mm"])
        assert (value - baseline) / step == pytest.approx(jacobian[:, i], abs=2e-5)


def test_shared_translation_and_common_motion_errors_cancel_in_relative_strike():
    jacobian = strike_sensitivity(
        [21.35, 0, 0], [0, 0, 0], basis(), [35000, 0, 0], [35000, 0, 0], [0, 0, 0]
    )
    shared = np.zeros((10, 3))
    shared[:3] = np.eye(3)
    shared[3:6] = np.eye(3)
    covariance = shared @ shared.T
    covariance[9, 9] = 1
    assert propagate_strike_covariance(jacobian, covariance) == pytest.approx(np.zeros((2, 2)))


@pytest.mark.parametrize("bad", [np.full((10, 10), np.nan), -np.eye(10)])
def test_missing_or_invalid_joint_uncertainty_is_not_replaced(bad):
    with pytest.raises(ValueError):
        propagate_strike_covariance(np.zeros((2, 10)), bad)


def test_reflected_face_basis_is_refused():
    with pytest.raises(ValueError, match="proper"):
        face_contact_geometry([0, 0, 0], [0, 0, 0], np.diag([-1, 1, 1]))

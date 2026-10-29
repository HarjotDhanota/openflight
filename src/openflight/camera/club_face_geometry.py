"""Explicit face-frame geometry and joint strike sensitivity, without promotion.

Inputs share one Cartesian frame and physical reference instant. These numerical
primitives cannot establish equipment, range, target identity or timing validity.
Those missing observations remain unavailable at their owning eligibility gates.
"""

from __future__ import annotations

import numpy as np

from ..constants import BALL_DIAMETER_MM
from ..metric_definitions import face_normal


def normal_from_angles(face_angle_deg, dynamic_loft_deg):
    """Normal in target Cartesian axes: forward, right, up; positive right/up."""
    angles = np.asarray([face_angle_deg, dynamic_loft_deg], float)
    if not np.isfinite(angles).all() or abs(dynamic_loft_deg) > 90:
        raise ValueError("finite face angle and physical elevation required")
    return np.asarray(face_normal(dynamic_loft_deg, face_angle_deg))


def radial_velocity(origin_velocity, angular_velocity_rad_s, point_offset, line_of_sight):
    """Project v_origin + omega cross offset at the actual scattering reference."""
    vectors = np.asarray(
        [origin_velocity, angular_velocity_rad_s, point_offset, line_of_sight], float
    )
    if vectors.shape != (4, 3) or not np.isfinite(vectors).all():
        raise ValueError("four finite Cartesian vectors required")
    length = np.linalg.norm(vectors[3])
    if length <= 0:
        raise ValueError("nonzero line of sight required")
    return float((vectors[0] + np.cross(vectors[1], vectors[2])) @ (vectors[3] / length))


def _inputs(ball_mm, face_origin_mm, face_basis):
    ball, origin = np.asarray(ball_mm, float), np.asarray(face_origin_mm, float)
    basis = np.asarray(face_basis, float)
    if (
        ball.shape != (3,)
        or origin.shape != (3,)
        or basis.shape != (3, 3)
        or not np.isfinite(np.r_[ball, origin, basis.ravel()]).all()
    ):
        raise ValueError("finite ball, face origin and toe/up/normal basis required")
    if not np.allclose(basis.T @ basis, np.eye(3), atol=1e-8) or not np.isclose(
        np.linalg.det(basis), 1, atol=1e-8
    ):
        raise ValueError("proper orthonormal toe/up/normal columns required")
    return ball, origin, basis


def face_contact_geometry(
    ball_mm, face_origin_mm, face_basis, *, ball_radius_mm=BALL_DIAMETER_MM / 2
):
    """Candidate sphere tangent point and face coordinates, retaining contact gap.

    A positive/negative gap is separation/penetration. The sphere point lies on
    the face plane only at tangency; this function never invents contact time.
    """
    ball, origin, basis = _inputs(ball_mm, face_origin_mm, face_basis)
    if isinstance(ball_radius_mm, bool) or not np.isfinite(ball_radius_mm) or ball_radius_mm <= 0:
        raise ValueError("positive finite physical ball radius required")
    offset = basis.T @ (ball - origin)
    return {
        "scope": "conditional_geometry_not_qualified_measurement",
        "candidate_sphere_point_mm": (ball - ball_radius_mm * basis[:, 2]).tolist(),
        "toe_up_mm": offset[:2].tolist(),
        "plane_sphere_gap_mm": float(offset[2] - ball_radius_mm),
        "physical_pose": None,
        "strike": None,
    }


def strike_sensitivity(
    ball_mm,
    face_origin_mm,
    face_basis,
    ball_velocity_mm_s,
    face_origin_velocity_mm_s,
    face_angular_velocity_rad_s,
):
    """Jacobian for ball3, origin3, world rotation3 and common time1.

    Shared time errors act on relative motion, including reference-point rotation.
    Covariance must include shared raw evidence and clock/geometry correlations.
    """
    ball, origin, basis = _inputs(ball_mm, face_origin_mm, face_basis)
    velocities = np.asarray(
        [ball_velocity_mm_s, face_origin_velocity_mm_s, face_angular_velocity_rad_s], float
    )
    if velocities.shape != (3, 3) or not np.isfinite(velocities).all():
        raise ValueError("finite velocities at their declared physical reference points required")
    tangent = basis[:, :2].T
    delta = ball - origin
    rotation = np.cross(tangent, delta)
    relative = velocities[0] - velocities[1] - np.cross(velocities[2], delta)
    return np.column_stack((tangent, -tangent, rotation, tangent @ relative))


def propagate_strike_covariance(jacobian, joint_covariance):
    """Propagate the full declared10x10 covariance; never fill absent uncertainty."""
    jacobian, covariance = np.asarray(jacobian, float), np.asarray(joint_covariance, float)
    if (
        jacobian.shape != (2, 10)
        or covariance.shape != (10, 10)
        or not np.isfinite(np.r_[jacobian.ravel(), covariance.ravel()]).all()
    ):
        raise ValueError("finite2x10 Jacobian and full10x10 joint covariance required")
    if (
        not np.allclose(covariance, covariance.T, atol=1e-10)
        or np.linalg.eigvalsh(covariance).min() < -1e-10
    ):
        raise ValueError("joint covariance must be symmetric positive semidefinite")
    return jacobian @ covariance @ jacobian.T

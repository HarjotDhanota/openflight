"""Analytic ground-truth builders used as the test oracle."""
from __future__ import annotations

import numpy as np
from scipy.spatial.transform import Rotation

from .template import ClubTemplate
from .types import BALL_RADIUS_MM, ClubheadPose


def ball_for_impact(pose: ClubheadPose, template: ClubTemplate, u0: float, v0: float) -> np.ndarray:
    surf = template.face_center_offset + template.face_to_body_vec(
        [u0, v0, template.surface_height_face(u0, v0)]
    )
    n = template.face_to_body_vec(template.surface_normal_face(u0, v0))
    return pose.body_to_world(surf + BALL_RADIUS_MM * n)


def pose_for_face_angle_loft(
    face_angle_deg: float, dynamic_loft_deg: float, head_center=(0.0, 0.0, 0.0)
) -> ClubheadPose:
    """Pose that yields the given face angle + dynamic loft on a ZERO-loft flat template.

    The template's zero-loft normal is +X. Apply elevation (loft, +Z up) then azimuth
    (face angle, right/-Y positive) in the WORLD frame so the metric decompositions invert it.
    """
    # elevation: tilt +X up by dynamic_loft about +Y is -loft (see R_loft); in world we want
    # normal -> (cos L, 0, sin L): rotate about +Y by -L.
    r_loft = Rotation.from_rotvec(np.radians(-dynamic_loft_deg) * np.array([0.0, 1.0, 0.0]))
    # azimuth: open (right/-Y) positive -> rotate about +Z by -face_angle.
    r_face = Rotation.from_rotvec(np.radians(-face_angle_deg) * np.array([0.0, 0.0, 1.0]))
    return ClubheadPose(r_face * r_loft, np.asarray(head_center, dtype=float))


def two_poses_for_velocity(vel_world, dt: float, start=(0.0, 0.0, 0.0)):
    a = ClubheadPose(Rotation.identity(), np.asarray(start, dtype=float))
    b = ClubheadPose(
        Rotation.identity(), np.asarray(start, dtype=float) + np.asarray(vel_world, dtype=float) * dt
    )
    return a, b

"""Sensitivity sweeps: how pose/template error propagates to metric error.

Produces the error budget that decides single-camera vs stereo.
"""
from __future__ import annotations

import numpy as np
from scipy.spatial.transform import Rotation

from .frames import nominal_camera
from .groundtruth import ball_for_impact, pose_for_face_angle_loft
from .metrics import dynamic_loft, face_angle, impact_location
from .template import ClubTemplate
from .types import ClubheadPose


def loft_error_to_loft_deg(template: ClubTemplate, loft_errors_deg):
    """(template loft error, resulting dynamic-loft error) -- expect ~1:1."""
    pose = ClubheadPose(Rotation.identity(), np.zeros(3))
    truth = dynamic_loft(pose, template)
    out = []
    for e in loft_errors_deg:
        perturbed = template.with_loft_override(template.static_loft_deg + e)
        out.append((e, dynamic_loft(pose, perturbed) - truth))
    return out


def depth_error_to_impact_mm(template: ClubTemplate, u0: float, v0: float, depth_errors_mm):
    """(depth/translation error along camera axis, resulting impact-offset error mm)."""
    cam = nominal_camera()
    pose = pose_for_face_angle_loft(0.0, template.static_loft_deg)
    ball = ball_for_impact(pose, template, u0, v0)
    out = []
    for e in depth_errors_mm:
        moved = ClubheadPose(pose.rotation, pose.translation + e * cam.view_axis)
        off, _, _, state = impact_location(moved, template, ball)
        err = abs(off.value - u0) if off.value is not None else float("nan")
        out.append((e, err))
    return out


def rotation_error_to_face_deg(template: ClubTemplate, rot_errors_deg):
    """(body yaw error about +Z, resulting face-angle error deg)."""
    base = pose_for_face_angle_loft(0.0, template.static_loft_deg)
    truth = face_angle(base, template)
    out = []
    for e in rot_errors_deg:
        perturbed = ClubheadPose(
            Rotation.from_rotvec(np.radians(-e) * np.array([0.0, 0.0, 1.0])) * base.rotation,
            base.translation,
        )
        out.append((e, abs(face_angle(perturbed, template) - truth)))
    return out


def error_budget(template: ClubTemplate) -> dict:
    """Pose accuracy implied for each target tier, from the local slopes."""
    rot = rotation_error_to_face_deg(template, [1.0])[0][1]  # deg face per deg yaw (~1:1)
    slope = rot if rot > 1e-9 else 1.0
    return {
        "single_camera": {
            "face_loft_target_deg": "3-5",
            "max_body_rotation_deg": round(3.0 / slope, 2),
        },
        "stereo": {
            "face_loft_target_deg": "2",
            "max_body_rotation_deg": round(2.0 / slope, 2),
        },
    }

"""Sensitivity sweeps: how pose/template error propagates to metric error.

Stage 0A is pure pose->metrics geometry, so it yields *sensitivity coefficients*
(metric error per unit pose/template error). The DEPTH/perspective magnitude that
drives the single-vs-stereo decision needs the camera projection model and is a
Stage 0B sweep (see spec section 11), NOT derivable from this pure geometry.

Sweeps use the raw face projection (no contact gate) so injected pose error is
*measured* rather than rejected by impact-location's contact validation.
"""
from __future__ import annotations

import numpy as np
from scipy.spatial.transform import Rotation

from .groundtruth import ball_for_impact, pose_for_face_angle_loft
from .metrics import dynamic_loft, face_angle
from .template import ClubTemplate
from .types import ClubheadPose

DEPTH_AXIS = np.array([1.0, 0.0, 0.0])    # camera view / depth axis (behind ball, looking +X)
LATERAL_AXIS = np.array([0.0, 1.0, 0.0])  # in-plane heel-toe direction
_YAW_AXIS = np.array([0.0, 0.0, 1.0])


def loft_error_to_loft_deg(template: ClubTemplate, loft_errors_deg):
    """(template loft error, resulting dynamic-loft error) -- expect ~1:1."""
    pose = ClubheadPose(Rotation.identity(), np.zeros(3))
    truth = dynamic_loft(pose, template)
    return [
        (e, dynamic_loft(pose, template.with_loft_override(template.static_loft_deg + e)) - truth)
        for e in loft_errors_deg
    ]


def rotation_error_to_face_deg(template: ClubTemplate, rot_errors_deg):
    """(body yaw error about +Z, resulting face-angle error deg)."""
    base = pose_for_face_angle_loft(0.0, template.static_loft_deg)
    truth = face_angle(base, template)
    out = []
    for e in rot_errors_deg:
        perturbed = ClubheadPose(
            Rotation.from_rotvec(np.radians(-e) * _YAW_AXIS) * base.rotation, base.translation
        )
        out.append((e, abs(face_angle(perturbed, template) - truth)))
    return out


def _impact_uv(template: ClubTemplate, pose: ClubheadPose, ball):
    """Raw projected (u, v) on the face -- NO contact gate (sensitivity only)."""
    proj = template.point_to_face_uv(pose.world_to_body(ball))
    return proj.u, proj.v


def translation_error_to_impact_mm(template: ClubTemplate, u0, v0, axis_world, errors_mm):
    """(translation error along axis_world; |offset error|, |height error|) in mm.

    Uses the raw projection (no contact gate) so the geometric (u,v) shift is measured
    instead of being rejected by impact-location's contact validation. NOTE this is the
    pure-3D geometric shift; the perspective/scale amplification of depth error is a
    Stage 0B (camera-model) effect and is NOT captured here.
    """
    axis = np.asarray(axis_world, dtype=float)
    axis = axis / np.linalg.norm(axis)
    pose = pose_for_face_angle_loft(0.0, template.static_loft_deg)
    ball = ball_for_impact(pose, template, u0, v0)
    out = []
    for e in errors_mm:
        moved = ClubheadPose(pose.rotation, pose.translation + e * axis)
        u, v = _impact_uv(template, moved, ball)
        out.append((e, abs(u - u0), abs(v - v0)))
    return out


def rotation_error_to_impact_mm(template: ClubTemplate, u0, v0, rot_errors_deg):
    """(body yaw error about +Z; |offset error|, |height error|) in mm -- lever-arm effect."""
    base = pose_for_face_angle_loft(0.0, template.static_loft_deg)
    ball = ball_for_impact(base, template, u0, v0)
    out = []
    for e in rot_errors_deg:
        perturbed = ClubheadPose(
            Rotation.from_rotvec(np.radians(-e) * _YAW_AXIS) * base.rotation, base.translation
        )
        u, v = _impact_uv(template, perturbed, ball)
        out.append((e, abs(u - u0), abs(v - v0)))
    return out


def error_budget(template: ClubTemplate) -> dict:
    """Stage-0A sensitivity coefficients (metric error per unit pose/template error).

    The depth/perspective pose-error magnitude -- the single-vs-stereo driver -- needs
    the camera projection model and is a Stage 0B sweep (spec section 11), NOT here.
    """
    inplane = translation_error_to_impact_mm(template, 0.0, 0.0, LATERAL_AXIS, [1.0])[0]
    depth = translation_error_to_impact_mm(template, 0.0, 0.0, DEPTH_AXIS, [1.0])[0]
    yaw_impact = rotation_error_to_impact_mm(template, 20.0, 0.0, [1.0])[0]
    return {
        "deg_face_per_deg_yaw": round(rotation_error_to_face_deg(template, [1.0])[0][1], 4),
        "deg_loft_per_deg_template_loft": round(loft_error_to_loft_deg(template, [1.0])[0][1], 4),
        "mm_offset_per_mm_inplane_translation": round(inplane[1], 4),
        "mm_impact_per_mm_depth_translation_pure3d": round(float(np.hypot(depth[1], depth[2])), 4),
        "mm_offset_per_deg_yaw_at_20mm_toe": round(yaw_impact[1], 4),
        "note": (
            "Depth/perspective magnitude (the single-vs-stereo driver) is a Stage-0B "
            "camera-model sweep; not derivable from pure pose->metrics geometry."
        ),
    }

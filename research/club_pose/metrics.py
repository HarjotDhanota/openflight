"""Derive golf metrics from a clubhead body pose + template."""
from __future__ import annotations

from typing import Optional

import numpy as np

from .frames import elevation_angle_deg, horizontal_angle_deg
from .template import ClubTemplate, Projection
from .types import BALL_RADIUS_MM, ClubheadPose, ClubMetrics, Measurement

CONTACT_TOL_MM: float = 3.0


def impact_location(pose: ClubheadPose, template: ClubTemplate, ball_center_world):
    """Return (offset, height, projection, contact_state)."""
    p_body = pose.world_to_body(ball_center_world)
    proj = template.point_to_face_uv(p_body)
    outward = proj.signed_distance_mm > 0
    on_surface = abs(proj.signed_distance_mm - BALL_RADIUS_MM) <= CONTACT_TOL_MM
    if proj.in_patch and outward and on_surface:
        conf = max(0.0, 1.0 - abs(proj.signed_distance_mm - BALL_RADIUS_MM) / CONTACT_TOL_MM)
        return (
            Measurement(proj.u, conf, "impact"),
            Measurement(proj.v, conf, "impact"),
            proj,
            "valid_contact",
        )
    return (
        Measurement(None, 0.0, "invalid"),
        Measurement(None, 0.0, "invalid"),
        proj,
        "invalid_contact",
    )


def face_angle(pose: ClubheadPose, template: ClubTemplate, normal_body=None) -> float:
    if normal_body is None:
        normal_body = template.face_center_normal_body()
    return horizontal_angle_deg(pose.direction_to_world(normal_body))


def dynamic_loft(pose: ClubheadPose, template: ClubTemplate, normal_body=None) -> float:
    if normal_body is None:
        normal_body = template.face_center_normal_body()
    return elevation_angle_deg(pose.direction_to_world(normal_body))


def _head_velocity(pose_a: ClubheadPose, pose_b: ClubheadPose, dt: float) -> np.ndarray:
    if dt <= 0:
        raise ValueError("dt must be positive")
    return (pose_b.translation - pose_a.translation) / dt


def club_path(pose_a: ClubheadPose, pose_b: ClubheadPose, dt: float) -> float:
    return horizontal_angle_deg(_head_velocity(pose_a, pose_b, dt))


def attack_angle(pose_a: ClubheadPose, pose_b: ClubheadPose, dt: float) -> float:
    return elevation_angle_deg(_head_velocity(pose_a, pose_b, dt))


def compute_metrics(
    pose: ClubheadPose,
    template: ClubTemplate,
    ball_center_world=None,
    prev_pose: Optional[ClubheadPose] = None,
    dt: Optional[float] = None,
) -> ClubMetrics:
    if ball_center_world is not None:
        off, hgt, proj, state = impact_location(pose, template, ball_center_world)
        if state == "valid_contact":
            normal_body, src, conf = proj.normal_body, "impact", off.confidence
        else:
            normal_body, src, conf = template.face_center_normal_body(), "center_fallback", 0.5
    else:
        off = Measurement(None, 0.0, "no_ball")
        hgt = Measurement(None, 0.0, "no_ball")
        normal_body, src, conf = template.face_center_normal_body(), "center", 1.0

    fa = Measurement(face_angle(pose, template, normal_body), conf, src)
    dl = Measurement(dynamic_loft(pose, template, normal_body), conf, src)

    if prev_pose is not None and dt is not None:
        cp = Measurement(club_path(prev_pose, pose, dt), 1.0, "two_pose")
        aa = Measurement(attack_angle(prev_pose, pose, dt), 1.0, "two_pose")
    else:
        cp = Measurement(None, 0.0, "insufficient")
        aa = Measurement(None, 0.0, "insufficient")

    return ClubMetrics(off, hgt, fa, dl, cp, aa)

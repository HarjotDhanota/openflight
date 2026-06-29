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

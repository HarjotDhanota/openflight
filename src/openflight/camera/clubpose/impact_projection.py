"""Impact location as one 3-D projection: contact = ball centre − r·n̂.

The shipped impact reading subtracts 2-D image coordinates, which silently
assumes the impact point is the ball's centre, everything sits at one depth,
image millimetres are on-face millimetres, and the camera sits on the ball's
line. Priced on the taped rig (see the impact-point geometry page and
`docs/clubface-impact-location.md`), those assumptions cost: the contact
point sits r·sin(loft) = 10.7–12.5 mm BELOW the ball centre on the face;
vertical on-face distances are foreshortened 23–34 % by the loft and the
camera's height; the topline parallax reads ~2.3 mm high; and face angle
shifts the touch point 0.31 mm per degree.

This module removes all of them in ONE computation instead of term by term:

    1. back-project the ball centre B along its pixel ray at the SOLVED range
       (`rig_geometry.SetupSolution`);
    2. pose the face plane: it touches the ball at C = B − r·n̂, where n̂ is
       the face normal built from loft and face angle;
    3. intersect the carried landmark pixel rays with that plane and express
       C in the face frame the landmarks span.

Everything here is exact GIVEN its inputs; the inputs that are assumptions
say so. Loft comes in as a parameter because the D-plane dynamic loft is
still withheld (`club_metrics`): until it ships, `ASSUMED_DYNAMIC_LOFT_DEG`
carries the same named per-club convention the strike map uses, and the
result's ``assumptions`` names it on every reading. Face angle defaults to
zero with the same honesty. Camera roll is taken as zero until the LIS3DH
readout lands (roadmap 1.3).

CAMERA AXES throughout: +x image right, +y image DOWN, +z forward along the
boresight, origin at the optical centre -- `rig_geometry`'s frame. The camera
is assumed level (the rig's designed boresight); the solved per-shot pitch is
−0.22° ± 0.12 on the taped rig, worth under 0.5 mm here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

BALL_RADIUS_MM = 42.67 / 2.0

# The same named convention the strike map has used since 2026-08-31: a
# mid-iron dynamic loft, NOT a measurement. Replaced by the D-plane dynamic
# loft when the vertical-launch conflict resolves (roadmap Phase 3 → 4.2).
# Sensitivity is small and bounded: ±4° of loft moves the vertical reading
# ±1.3 mm and the horizontal one under 0.2 mm.
ASSUMED_DYNAMIC_LOFT_DEG: dict[str, float] = {
    "7-iron": 30.0,
    "9-iron": 36.0,
}

_MIN_RAY_DOT = 1e-6
_MIN_SPAN_MM = 1.0


@dataclass(frozen=True)
class ImpactProjection:
    """One 3-D impact reading, or the reason there is none.

    ``ball_from_toe_mm`` is along the landmarks' heel→toe axis, negative
    heel-ward -- the same sense as the 2-D channel. ``high_low_mm`` is the
    CONTACT POINT's depth below the topline along the face's up axis,
    positive below -- unlike the 2-D channel this is the touch point, not the
    ball centre, and it is measured on the face plane, not in the image.
    """

    ok: bool
    reason: str
    ball_from_toe_mm: float | None = None
    heel_toe_span_mm: float | None = None
    high_low_mm: float | None = None
    contact_camera_mm: tuple[float, float, float] | None = None
    assumptions: tuple[str, ...] = ()


def face_normal_camera(loft_deg: float, face_angle_deg: float) -> np.ndarray:
    """The face's outward normal in camera axes, for a level camera.

    Zero loft and zero face angle is a face pointing straight downrange
    (+z). Loft pitches the normal UP (−y, since +y is image-down); a positive
    face angle is an OPEN face for a right-handed golfer, pointing right of
    the target (+x). `test_clubpose_impact_projection` pins both signs.
    """
    loft = math.radians(float(loft_deg))
    face = math.radians(float(face_angle_deg))
    return np.array(
        [
            math.cos(loft) * math.sin(face),
            -math.sin(loft),
            math.cos(loft) * math.cos(face),
        ]
    )


def _ray(pixel, focal_px: float, principal_point) -> np.ndarray:
    cx, cy = principal_point
    direction = np.array(
        [
            (float(pixel[0]) - cx) / float(focal_px),
            (float(pixel[1]) - cy) / float(focal_px),
            1.0,
        ]
    )
    return direction / float(np.linalg.norm(direction))


def project_impact(
    ball_px,
    heel_px,
    toe_px,
    topline_px,
    *,
    range_to_ball_mm: float,
    focal_px: float,
    principal_point,
    loft_deg: float,
    face_angle_deg: float = 0.0,
    ball_radius_mm: float = BALL_RADIUS_MM,
) -> ImpactProjection:
    """Project the contact point onto the face the landmarks span.

    ``range_to_ball_mm``, ``focal_px`` and ``principal_point`` come from
    `rig_geometry.solve_setup` and `RigGeometry`; the landmark pixels are the
    carried-to-contact values the 2-D reading already uses. One stated
    geometric assumption beyond the loft/face-angle inputs: the silhouette
    landmarks lie ON the face plane -- the head's ~20 mm of depth puts them
    within a few millimetres of it, and the error enters only through the
    plane's small tilt.
    """
    if not (math.isfinite(float(range_to_ball_mm)) and float(range_to_ball_mm) > 0.0):
        return ImpactProjection(False, f"range_{range_to_ball_mm}_is_not_a_distance")

    normal = face_normal_camera(loft_deg, face_angle_deg)
    ball_centre = float(range_to_ball_mm) * _ray(ball_px, focal_px, principal_point)
    contact = ball_centre - float(ball_radius_mm) * normal

    placed: dict[str, np.ndarray] = {}
    for name, pixel in (("heel", heel_px), ("toe", toe_px), ("topline", topline_px)):
        ray = _ray(pixel, focal_px, principal_point)
        along = float(normal @ ray)
        if abs(along) < _MIN_RAY_DOT:
            return ImpactProjection(False, f"{name}_ray_parallel_to_the_face_plane")
        distance = float(normal @ contact) / along
        if distance <= 0.0:
            return ImpactProjection(False, f"{name}_intersects_the_face_plane_behind_the_camera")
        placed[name] = distance * ray

    span = placed["toe"] - placed["heel"]
    span_mm = float(np.linalg.norm(span))
    if span_mm < _MIN_SPAN_MM:
        return ImpactProjection(False, "landmarks_span_no_face")
    toe_ward = span / span_mm
    up_face = np.cross(toe_ward, normal)
    up_norm = float(np.linalg.norm(up_face))
    if up_norm < _MIN_RAY_DOT:
        return ImpactProjection(False, "face_axes_are_degenerate")
    up_face = up_face / up_norm
    if up_face[1] > 0.0:  # +y is image-down; the face's up axis must point up
        up_face = -up_face

    return ImpactProjection(
        ok=True,
        reason="ok",
        ball_from_toe_mm=float((contact - placed["toe"]) @ toe_ward),
        heel_toe_span_mm=span_mm,
        high_low_mm=float((placed["topline"] - contact) @ up_face),
        contact_camera_mm=tuple(float(value) for value in contact),
        assumptions=(
            f"dynamic_loft_{float(loft_deg):.1f}_deg_assumed_not_measured",
            f"face_angle_{float(face_angle_deg):.1f}_deg"
            + ("_assumed_square" if abs(float(face_angle_deg)) < 1e-9 else ""),
            "camera_roll_zero_until_the_inclinometer_readout",
            "silhouette_landmarks_taken_to_lie_on_the_face_plane",
        ),
    )

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

# Face height -- leading edge to topline, measured ALONG the leaning face -- is
# not published by any official source. The 2026-08-29 sweep of Maltby/
# GolfWorks, Mizuno's irons-comparison chart and Srixon's shape-comparison PDF
# returned blade LENGTH per club and nothing at all for height, so these are
# ASSUMPTIONS carrying the 45-50 mm sole-to-crown band that
# `head_outline.bootstrap_outline_from_category` already seeds with, and every
# reading that uses one says so. A template that carries its own
# topline-to-sole extent is always preferred over this table.
ASSUMED_FACE_HEIGHT_MM: dict[str, float] = {
    "7-iron": 45.0,
    "9-iron": 45.0,
}
ASSUMED_FACE_HEIGHT_PROVENANCE = (
    "midpoint of the 45-50 mm mid-iron sole-to-crown band; face height is "
    "unpublished by every official source checked (2026-08-29), so this is an "
    "ASSUMPTION, not a measurement, and it is not resolved per club"
)

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
    # The two points the SUPPORT-PLANE datum needs, in the same camera axes:
    # the back-projected ball centre, and the topline landmark placed on the
    # face plane. Carried so `read_support_plane` never re-derives them.
    ball_camera_mm: tuple[float, float, float] | None = None
    topline_camera_mm: tuple[float, float, float] | None = None
    loft_deg: float | None = None
    ball_radius_mm: float = BALL_RADIUS_MM
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
    landmarks lie ON the face plane. That is NOT a few-millimetre nicety --
    the 2026-09-01 audit swept landmark depths of 0-20 mm behind the face
    and measured up to 1.6 mm of heel-toe error but up to 15.5 mm of
    high-low error, because a depth-displaced topline ray intersects the
    lofted plane well below the true edge. The heel-toe channel is robust to
    it; the vertical channel is NOT, and its assumption string says so.
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
        ball_camera_mm=tuple(float(value) for value in ball_centre),
        topline_camera_mm=tuple(float(value) for value in placed["topline"]),
        loft_deg=float(loft_deg),
        ball_radius_mm=float(ball_radius_mm),
        assumptions=(
            f"dynamic_loft_{float(loft_deg):.1f}_deg_assumed_not_measured",
            f"face_angle_{float(face_angle_deg):.1f}_deg"
            + ("_assumed_square" if abs(float(face_angle_deg)) < 1e-9 else ""),
            "camera_roll_zero_until_the_inclinometer_readout",
            "silhouette_landmarks_taken_to_lie_on_the_face_plane"
            "_heel_toe_robust_high_low_up_to_15mm_at_20mm_landmark_depth",
        ),
    )


# ---------------------------------------------------------------------------
# the support-plane datum
# ---------------------------------------------------------------------------
#
# THE VERTICAL DATUM IS THE BALL'S OWN SUPPORT PLANE: the plane the ball rests
# on, wherever that is. It sits one ball radius below the ball centre, and it
# is the same plane whether the ball sits on a mat, on grass, or on a tee --
# no mat thickness is ever known, asked for, or assumed. In the image it
# passes through the ball's bottom pixel row at the ball's range; in camera
# axes it is the ball centre lowered by r along the vertical.
#
# For a ball on that plane struck by an iron of dynamic loft L:
#
#   * the face touches the ball at r*(1 - sin L) above the plane -- 10.7 mm
#     for a 7-iron at 30 deg, and rising towards the full radius as the club
#     deloafts, which is what a thin, bladed strike looks like as a NUMBER;
#   * a soled head's topline sits face_height * cos(L) above the plane --
#     39.0 mm for a 45 mm face at 30 deg -- because the face height is
#     measured ALONG the leaning face, not vertically.
#
# Both are exact given their inputs; the inputs that are assumptions say so.


@dataclass(frozen=True)
class SupportPlaneReading:
    """Heights above the ball's support plane, or the named reason there are none.

    ``contact_above_support_mm`` needs only the ball and the loft, so it
    exists whenever the projection does. ``topline_above_support_mm`` and
    ``sole_above_support_mm`` additionally need the template's topline and a
    face height, and each says where its number came from.
    """

    ok: bool
    reason: str
    contact_above_support_mm: float | None = None
    topline_above_support_mm: float | None = None
    sole_above_support_mm: float | None = None
    face_height_mm: float | None = None
    contact_above_support_source: str = ""
    topline_above_support_source: str = ""
    sole_above_support_source: str = ""
    face_height_source: str = ""


def support_plane_camera_y(ball_camera_mm, ball_radius_mm: float = BALL_RADIUS_MM) -> float:
    """The support plane's camera-frame row coordinate (+y is image DOWN).

    The ball's bottom: its centre lowered by one radius along the vertical.
    For the level camera this module already assumes, the vertical is the
    camera's own −y, so the plane sits at ``ball_y + r``.
    """
    return float(ball_camera_mm[1]) + float(ball_radius_mm)


def read_support_plane(
    projection: ImpactProjection,
    *,
    face_height_mm: float | None = None,
    face_height_source: str = "",
) -> SupportPlaneReading:
    """Express one projected impact against the ball's support plane.

    ``face_height_mm`` is the leading-edge-to-topline extent measured ALONG
    the face. Supply the template's own extent where it has one; None leaves
    the sole unreported by name rather than substituting a guess.
    """
    if not projection.ok:
        return SupportPlaneReading(False, f"projection_{projection.reason}")
    if projection.ball_camera_mm is None or projection.contact_camera_mm is None:
        return SupportPlaneReading(False, "projection_carries_no_camera_frame_points")
    if projection.loft_deg is None:
        return SupportPlaneReading(False, "projection_carries_no_loft")

    radius = float(projection.ball_radius_mm)
    plane_y = support_plane_camera_y(projection.ball_camera_mm, radius)
    loft = math.radians(float(projection.loft_deg))
    datum = (
        f"ball centre lowered by one {radius:.3f} mm radius along the vertical "
        "(the ball's own support plane -- mat, grass or tee alike; no surface "
        "thickness is known or assumed); camera taken as level"
    )

    contact_above = plane_y - float(projection.contact_camera_mm[1])
    topline_above: float | None = None
    topline_source = "unavailable: the projection placed no topline on the face plane"
    if projection.topline_camera_mm is not None:
        topline_above = plane_y - float(projection.topline_camera_mm[1])
        topline_source = f"topline landmark on the posed face plane, against {datum}"

    sole_above: float | None = None
    sole_source = "unavailable: no face height, so the sole is not reported"
    height = None if face_height_mm is None else float(face_height_mm)
    if height is not None and not math.isfinite(height):
        height = None
    if topline_above is not None and height is not None:
        sole_above = topline_above - height * math.cos(loft)
        sole_source = (
            f"topline above support minus the {height:.1f} mm face height leaned "
            f"by cos({float(projection.loft_deg):.1f} deg) = "
            f"{height * math.cos(loft):.1f} mm; face height from "
            f"{face_height_source or 'an unstated source'}"
        )

    return SupportPlaneReading(
        ok=True,
        reason="ok",
        contact_above_support_mm=contact_above,
        topline_above_support_mm=topline_above,
        sole_above_support_mm=sole_above,
        face_height_mm=height,
        contact_above_support_source=(
            f"contact point B - r*n_hat against {datum}; equals "
            f"r*(1 - sin(dynamic loft {float(projection.loft_deg):.1f} deg)), so it "
            "moves ONLY with the loft while the loft is the assumed per-club value"
        ),
        topline_above_support_source=topline_source,
        sole_above_support_source=sole_source,
        face_height_source=face_height_source,
    )

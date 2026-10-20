"""TrackMan's public metric definitions, implemented from first principles (D37).

Each function cites the public page its definition comes from, and every formula
here was derived from that wording and plain geometry. Nothing was taken from
TrackMan software or device output. The reference page is
``docs/reference/metric-definitions.md``.

Frame and units
---------------
OpenFlight's frame: **x downrange** along the target line, **y target-right**,
**z up**. Angles are in **degrees**:

- a horizontal angle (club path, face angle, launch direction) is the azimuth
  from +x toward +y, so **positive is right of the target line**;
- a vertical angle (attack angle, dynamic loft) is the elevation above the
  horizontal plane, so **positive is up**.

These match TrackMan's public signs: club path and launch direction are
positive right of the target line "regardless of dexterity"
(https://www.trackman.com/blog/club-path,
https://www.trackman.com/blog/the-ultimate-guide-to-understanding-trackman),
face to path is positive when the face points right of the path
(https://www.trackman.com/blog/face-to-path), and attack angle is positive when
the club moves up (https://www.trackman.com/blog/attack-angle).

The x, y, z components below are plain coordinates; no function depends on the
frame's handedness (x, y-right, z-up is a left-handed set physically).

Missing and bad inputs
----------------------
- A ``None`` input gives ``None``: nothing is filled in.
- A non-finite input (NaN, inf) gives a non-finite result, never a number, so a
  caller's "not measured" reason still names it.
- An input outside the definition's domain gives ``None`` (for example a
  non-positive club speed, an elevation beyond vertical, or a landing point
  farther to the side than the carry).
"""

from __future__ import annotations

import math
from typing import Optional

from .smash import smash_factor  # noqa: F401  (D37's smash factor, re-exported)

Vector = tuple[float, float, float]

# Below this length the cross product of two unit vectors means they are
# parallel to within ~1e-12 rad, and the plane they span is undefined.
_PARALLEL_EPS = 1e-12


def _missing(*values: Optional[float]) -> bool:
    return any(value is None for value in values)


def _non_finite(*values: float) -> bool:
    return any(not math.isfinite(value) for value in values)


def _unit_from_angles(elevation_deg: float, azimuth_deg: float) -> Optional[Vector]:
    """Unit vector at ``elevation_deg`` above horizontal, ``azimuth_deg`` right of +x."""
    if abs(elevation_deg) > 90.0:
        return None
    elevation = math.radians(elevation_deg)
    azimuth = math.radians(azimuth_deg)
    horizontal = math.cos(elevation)
    return (
        horizontal * math.cos(azimuth),
        horizontal * math.sin(azimuth),
        math.sin(elevation),
    )


def _dot(a: Vector, b: Vector) -> float:
    return math.fsum(p * q for p, q in zip(a, b))


def _cross(a: Vector, b: Vector) -> Vector:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def face_to_path_deg(
    face_angle_deg: Optional[float], club_path_deg: Optional[float]
) -> Optional[float]:
    """Face to path in degrees: face angle minus club path.

    Public definition: "The angle difference between FACE ANGLE and CLUB PATH
    as defined (FACE ANGLE minus CLUB PATH)"; "A positive Face to Path means the
    face is pointed to the right of the club path regardless of dexterity"
    (https://www.trackman.com/blog/face-to-path).

    Both angles are horizontal, positive right of the target line, so the
    result is positive when the face points right of the path.
    """
    if _missing(face_angle_deg, club_path_deg):
        return None
    return face_angle_deg - club_path_deg


def club_direction(
    attack_angle_deg: Optional[float], club_path_deg: Optional[float]
) -> Optional[Vector]:
    """Unit vector of the club head's direction of movement at impact.

    Spherical construction: elevation = attack angle, azimuth = club path:
    ``(cos A cos P, cos A sin P, sin A)``. TrackMan defines attack angle as the
    head's "up or down movement" and club path as its "in-to-out or out-to-in
    movement" (https://www.trackman.com/blog/40-trackman-parameters), i.e. the
    vertical and horizontal angles of the same velocity vector.

    Returns None for a missing angle or an elevation beyond +-90 deg; NaN
    components for a non-finite angle.
    """
    if _missing(attack_angle_deg, club_path_deg):
        return None
    if _non_finite(attack_angle_deg, club_path_deg):
        return (math.nan, math.nan, math.nan)
    return _unit_from_angles(attack_angle_deg, club_path_deg)


def face_normal(
    dynamic_loft_deg: Optional[float], face_angle_deg: Optional[float]
) -> Optional[Vector]:
    """Unit outward normal of the club face at impact.

    Spherical construction (our stated choice): the face normal's **elevation
    is the dynamic loft** and the **azimuth of its horizontal projection is the
    face angle**: ``(cos L cos F, cos L sin F, sin L)``. TrackMan defines
    dynamic loft as "the vertical angle of the club face" and face angle as
    "the direction the club face is pointing", both at the centre-point of
    contact (https://www.trackman.com/blog/40-trackman-parameters).

    Returns None for a missing angle or a loft beyond +-90 deg; NaN components
    for a non-finite angle.
    """
    if _missing(dynamic_loft_deg, face_angle_deg):
        return None
    if _non_finite(dynamic_loft_deg, face_angle_deg):
        return (math.nan, math.nan, math.nan)
    return _unit_from_angles(dynamic_loft_deg, face_angle_deg)


def _delivery_vectors(
    dynamic_loft_deg: Optional[float],
    face_angle_deg: Optional[float],
    attack_angle_deg: Optional[float],
    club_path_deg: Optional[float],
) -> Optional[tuple[Vector, Vector] | float]:
    """(club direction, face normal), None, or NaN for a non-finite input."""
    if _missing(dynamic_loft_deg, face_angle_deg, attack_angle_deg, club_path_deg):
        return None
    if _non_finite(dynamic_loft_deg, face_angle_deg, attack_angle_deg, club_path_deg):
        return math.nan
    direction = club_direction(attack_angle_deg, club_path_deg)
    normal = face_normal(dynamic_loft_deg, face_angle_deg)
    if direction is None or normal is None:
        return None
    return direction, normal


def spin_loft_deg(
    dynamic_loft_deg: Optional[float],
    face_angle_deg: Optional[float],
    attack_angle_deg: Optional[float],
    club_path_deg: Optional[float],
) -> Optional[float]:
    """Spin loft in degrees: the 3D angle between club direction and face normal.

    Public definition: "Spin Loft is the three-dimensional angle between the
    direction the club head is moving (both club path and attack angle) and the
    direction the club face is pointing (both face angle and dynamic loft)";
    "This angle is calculated between two 3D vectors"
    (https://www.trackman.com/blog/spin-loft).

    Formula: ``arccos(d . n)`` with d = ``club_direction(attack, path)`` and
    n = ``face_normal(loft, face)``. Expanded,
    ``cos(spin loft) = cos A cos L cos(F - P) + sin A sin L``, so it depends on
    face and path only through face to path. It equals dynamic loft minus
    attack angle only when face to path is zero; otherwise it is larger, as the
    same page notes ("if the face to path value is not equal to zero, then the
    simple subtraction method will vary from the actual Spin Loft").

    Range [0, 180] deg. None for a missing input or an elevation beyond
    +-90 deg; NaN for a non-finite input.
    """
    vectors = _delivery_vectors(dynamic_loft_deg, face_angle_deg, attack_angle_deg, club_path_deg)
    if vectors is None or isinstance(vectors, float):
        return vectors
    direction, normal = vectors
    # the half-angle form keeps full precision near 0 and 180 deg, where acos does not
    chord = math.sqrt(math.fsum((p - q) ** 2 for p, q in zip(direction, normal)))
    return math.degrees(2.0 * math.asin(min(1.0, chord / 2.0)))


def d_plane_tilt_deg(
    dynamic_loft_deg: Optional[float],
    face_angle_deg: Optional[float],
    attack_angle_deg: Optional[float],
    club_path_deg: Optional[float],
) -> Optional[float]:
    """D Plane Tilt in degrees: the tilt of the D plane from the plumb line.

    Public definition: "D Plane Tilt is the tilt of the D Plane relative to the
    plumb line. D Plane Tilt is the expected spin axis disregarding any gear
    effect [...] Thus, if face-to-path is zero, D Plane Tilt will be zero"
    (https://support.trackmangolf.com/hc/en-us/articles/39726207070875-Parameters-D-Plane-Tilt-Tee-to-Green).
    The D plane itself is the plane containing the club head's direction of
    travel and the face normal at impact (T. P. Jorgensen, *The Physics of
    Golf*, 2nd ed., AIP Press/Springer, 1999); TrackMan's pages use the term but
    do not define the plane.

    Formula: the plane's normal is m = d x n (component cross product of
    ``club_direction`` and ``face_normal``), oriented so its horizontal part
    points left of the club's direction of travel (the backspin side for a
    strike with loft above attack). The tilt is the elevation of that normal,
    ``asin(m_z / |m|)``: zero for a vertical plane, positive when the top of the
    plane leans right. On a normal (backspin) strike its sign is the sign of
    face to path, matching TrackMan's spin axis sign (positive curves right,
    https://www.trackman.com/blog/the-ultimate-guide-to-understanding-trackman).

    Range [-90, 90] deg. None for a missing input, an elevation beyond
    +-90 deg, or parallel vectors (zero spin loft: no plane); NaN for a
    non-finite input.
    """
    vectors = _delivery_vectors(dynamic_loft_deg, face_angle_deg, attack_angle_deg, club_path_deg)
    if vectors is None or isinstance(vectors, float):
        return vectors
    direction, normal = vectors
    m = _cross(direction, normal)
    length = math.sqrt(_dot(m, m))
    if length < _PARALLEL_EPS:
        return None
    path = math.radians(club_path_deg)
    right_of_travel = (-math.sin(path), math.cos(path), 0.0)
    if _dot(m, right_of_travel) > 0.0:
        m = (-m[0], -m[1], -m[2])
    return math.degrees(math.asin(max(-1.0, min(1.0, m[2] / length))))


def curve(
    carry: Optional[float],
    carry_side: Optional[float],
    launch_direction_deg: Optional[float],
) -> Optional[float]:
    """Curve: the landing point's side offset from the launch-direction line.

    Public definitions (https://www.trackman.com/blog/40-trackman-parameters
    and https://support.trackmangolf.com/hc/en-us/articles/5089892383515-Practice-Trackman-Data-Parameter-Definitions):

    - Curve: "The horizontal side movement perpendicular from the launch
      direction to carry side."
    - Carry: "The straight-line distance between where the golf ball was
      launched from and where it crosses a point that has the same elevation."
    - Carry Side: "The perpendicular distance between the target line and where
      the trajectory crosses a point that has the same elevation as where the
      golf ball was launched."
    - Launch Direction: "The horizontal angle the golf ball takes off at
      relative to the target line"; positive right of target.

    Frame: the landing point is at launch elevation, so carry is its horizontal
    radial distance from the launch point. With carry C and carry side S
    (positive right), the landing point is ``(sqrt(C^2 - S^2), S)`` in (x, y).
    The launch line has unit direction ``(cos t, sin t)`` and right-hand
    perpendicular ``(-sin t, cos t)``, so

        curve = S cos t - sqrt(C^2 - S^2) sin t

    Positive means the ball landed right of its launch line (it curved right).
    The unit is carry's unit. A landing point on the launch line gives zero.

    None for a missing input, a non-positive carry, ``|S| > C``, or a launch
    direction that is not downrange (``|t| >= 90`` deg); NaN for a non-finite
    input.
    """
    if _missing(carry, carry_side, launch_direction_deg):
        return None
    if _non_finite(carry, carry_side, launch_direction_deg):
        return math.nan
    if carry <= 0.0 or abs(carry_side) > carry or abs(launch_direction_deg) >= 90.0:
        return None
    downrange = math.sqrt((carry - carry_side) * (carry + carry_side))
    launch = math.radians(launch_direction_deg)
    return carry_side * math.cos(launch) - downrange * math.sin(launch)

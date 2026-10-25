"""TrackMan's public metric definitions from first principles (docs/reference/metric-definitions.md).

Frame: x downrange along the target line, y target-right, z up; angles in degrees, horizontal
ones + right of the target line, vertical ones + up (TrackMan's public signs). A None input
gives None, a non-finite one a non-finite result, an out-of-domain one None.
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
    """Face to path (deg): face angle minus club path, + when the face points right of the path.

    https://www.trackman.com/blog/face-to-path
    """
    if _missing(face_angle_deg, club_path_deg):
        return None
    return face_angle_deg - club_path_deg


def club_direction(
    attack_angle_deg: Optional[float], club_path_deg: Optional[float]
) -> Optional[Vector]:
    """Unit vector of the club head's movement at impact: ``(cos A cos P, cos A sin P, sin A)``.

    A = attack angle (elevation), P = club path (azimuth). None for a missing angle or an
    elevation beyond +-90 deg; NaN components for a non-finite angle.
    """
    if _missing(attack_angle_deg, club_path_deg):
        return None
    if _non_finite(attack_angle_deg, club_path_deg):
        return (math.nan, math.nan, math.nan)
    return _unit_from_angles(attack_angle_deg, club_path_deg)


def ball_direction(
    launch_vertical_deg: Optional[float], launch_horizontal_deg: Optional[float]
) -> Optional[Vector]:
    """Unit vector of the ball's launch (elevation = vertical launch, azimuth = direction).

    None for a missing angle or an elevation beyond +-90 deg; NaN for a non-finite one.
    """
    return club_direction(launch_vertical_deg, launch_horizontal_deg)


def face_normal(
    dynamic_loft_deg: Optional[float], face_angle_deg: Optional[float]
) -> Optional[Vector]:
    """Unit outward face normal at impact: ``(cos L cos F, cos L sin F, sin L)``.

    Our stated choice: elevation is dynamic loft L, horizontal azimuth is face angle F.
    None for a missing angle or a loft beyond +-90 deg; NaN for a non-finite one.
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
    """Spin loft (deg): the 3D angle between club direction and face normal, ``arccos(d . n)``.

    https://www.trackman.com/blog/spin-loft. It equals dynamic loft minus attack angle
    only when face to path is zero. Range [0, 180]; None for a missing input or an
    elevation beyond +-90 deg; NaN for a non-finite input.
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
    """D Plane Tilt (deg): the D plane's (club direction, face normal) tilt from the plumb line.

    https://support.trackmangolf.com/hc/en-us/articles/39726207070875-Parameters-D-Plane-Tilt-Tee-to-Green;
    the plane is Jorgensen's (The Physics of Golf, 1999). The normal d x n is oriented left
    of travel and the tilt is its elevation, + when the plane's top leans right (the spin
    axis sign: + curves right). Range [-90, 90]; None for a missing input, an elevation
    beyond +-90 deg or zero spin loft (no plane).
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
    """Curve: the landing point's side offset from the launch-direction line (carry's unit).

    With carry C, carry side S and launch direction t (+ right),
    ``curve = S cos t - sqrt(C^2 - S^2) sin t``: + when it landed right of its launch
    line (https://www.trackman.com/blog/40-trackman-parameters). None for a missing
    input, C <= 0, ``|S| > C`` or ``|t| >= 90`` deg; NaN for a non-finite input.
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

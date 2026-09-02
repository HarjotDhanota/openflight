"""D-plane club-delivery metrics, inverted from launch angles.

UNVALIDATED AGAINST A REFERENCE INSTRUMENT. Nothing here has been scored
against a launch monitor. The model is published and the arithmetic is exact,
but every number it produces is only as good as the launch angles fed to it,
and those have never been checked against truth.

THE MODEL

The ball leaves along a direction between the club's delivery and the face,
fixed by two transfer coefficients::

    vertical launch    V ~= A + k_v * (DL - A)   ->   DL = A + (V - A) / k_v
    horizontal launch  H ~= P + k_h * (F  - P)   ->   F  = P + (H - P) / k_h

with A the attack angle, P the club path, DL the dynamic loft and F the face
angle. Spin loft is the full 3-D angle between the face normal and the club's
motion -- ``acos(cos DL * cos A * cos(F - P) + sin DL * sin A)`` -- which
reduces to ``DL - A`` only when the face matches the path (the 2026-09-01
audit measured the 2-D subtraction 4.9 deg short at 20 deg of face-to-path).
Face-to-path is ``F - P``. The coefficients
are Wood et al.'s 7-iron values, ``k_v = 0.81`` and ``k_h = 0.69``, and every
function reports the coefficient it used so a caller sweeping the bracket can
say which one produced a given figure.

WHY THE INVERSION IS MOSTLY A STATEMENT ABOUT THE LAUNCH ANGLE

Rearranged, ``DL = A * (1 - 1/k_v) + V / k_v``. At k_v = 0.81 the vertical
launch angle carries 1.23x the weight and the attack angle only -0.23x, so an
implausible dynamic loft is very nearly a statement about the launch angle
alone. On the development session, re-running the inversion with an attack
angle measured independently by the 3-D clubhead track moved the median
dynamic loft by less than a quarter of a degree, while the whole plausible
coefficient bracket moved it by 3.6 degrees and still did not rescue it. The
coefficient was never the problem.

WHAT IS WITHHELD, AND WHY

`dplane_from_sources` computes everything it can and then refuses to offer two
of the results:

  * DYNAMIC AND SPIN LOFT are withheld unless a CAMERA-derived vertical launch
    angle is available. The shipped launch angle is not accepted as a
    substitute -- feeding the inversion the very quantity whose bias motivated
    this work would produce something that looks like a measurement and is not
    one. The status says so by name.

  * FACE ANGLE and FACE-TO-PATH are computed, recorded and never offered.
    Centring the development session's face angle on a plausible mean required
    either +2.3 deg on the horizontal launch angle or -7.6 deg on the club
    path: the same unexplained discrepancy read off either instrument, and
    smaller than and opposite in sign to the known camera-versus-radar
    horizontal offset, so that offset does not account for it. Until something
    calibrates it, the value belongs in a log and not on a display.

A withheld value is still stored, on the ``computed_*`` fields, so the question
stays open to study rather than being thrown away.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Mapping

__all__ = [
    "K_HORIZONTAL_DEFAULT",
    "K_VERTICAL_DEFAULT",
    "DPlaneAngle",
    "DPlaneResult",
    "dplane_from_sources",
    "dynamic_loft",
    "face_angle",
    "face_to_path",
    "spin_loft",
    "spin_loft_3d",
]

# Wood et al. 7-iron transfer coefficients. Treated as exact; the plausible
# bracket is k_v in 0.76-0.86 and k_h in 0.66-0.72, and sweeping the whole of
# either moves the answer by less than the disagreement it would need to fix.
K_VERTICAL_DEFAULT = 0.81
K_HORIZONTAL_DEFAULT = 0.69

# Status strings. Every withheld result names the specific evidence it lacks.
STATUS_EXPERIMENTAL = "experimental"
STATUS_WITHHELD_LAUNCH_BIAS = "withheld_lcmf_launch_bias"
STATUS_WITHHELD_DIRECTION_OFFSET = "withheld_launch_direction_offset"
STATUS_WITHHELD_NO_ATTACK = "withheld_no_attack_angle"
STATUS_WITHHELD_NO_PATH = "withheld_no_club_path"
STATUS_WITHHELD_NO_DIRECTION = "withheld_no_launch_direction"


@dataclass(frozen=True)
class DPlaneAngle:
    """One inverted angle, together with the coefficient that produced it."""

    deg: float
    coefficient: float


def _check_angle(name: str, value: float) -> float:
    angle = float(value)
    if not math.isfinite(angle):
        raise ValueError(f"{name} must be finite, got {value!r}")
    return angle


def _check_coefficient(name: str, value: float) -> float:
    coefficient = float(value)
    if not math.isfinite(coefficient) or not 0.0 < coefficient <= 1.0:
        raise ValueError(f"{name} must lie in (0, 1], got {value!r}")
    return coefficient


def dynamic_loft(
    vertical_launch_deg: float,
    attack_angle_deg: float,
    k_v: float = K_VERTICAL_DEFAULT,
) -> DPlaneAngle:
    """Dynamic loft, ``DL = A + (V - A) / k_v``.

    UNVALIDATED AGAINST A REFERENCE INSTRUMENT.

    Args:
        vertical_launch_deg: Vertical launch angle of the ball.
        attack_angle_deg: Attack angle, negative for a descending blow.
        k_v: Fraction of the loft above the attack angle the ball takes.

    Returns:
        The dynamic loft in degrees, with ``k_v`` alongside it.

    Raises:
        ValueError: If an angle is not finite or ``k_v`` is outside (0, 1].
    """
    vertical = _check_angle("vertical_launch_deg", vertical_launch_deg)
    attack = _check_angle("attack_angle_deg", attack_angle_deg)
    coefficient = _check_coefficient("k_v", k_v)
    return DPlaneAngle(attack + (vertical - attack) / coefficient, coefficient)


def spin_loft_3d(
    dynamic_loft_deg: float,
    attack_angle_deg: float,
    face_to_path_deg: float,
) -> float:
    """The 3-D angle between the face normal and the clubhead's velocity.

    ``acos(cos DL * cos A * cos(F - P) + sin DL * sin A)``, in degrees. With
    zero face-to-path this reduces exactly to ``DL - A``; the 2026-09-01
    audit's counterexample -- DL 30, A -5, F-P 20 -- is 39.904 deg where the
    2-D subtraction said 35.0.
    """
    loft = math.radians(_check_angle("dynamic_loft_deg", dynamic_loft_deg))
    attack = math.radians(_check_angle("attack_angle_deg", attack_angle_deg))
    separation = math.radians(_check_angle("face_to_path_deg", face_to_path_deg))
    cosine = math.cos(loft) * math.cos(attack) * math.cos(separation) + math.sin(loft) * math.sin(
        attack
    )
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))


def spin_loft(
    vertical_launch_deg: float,
    attack_angle_deg: float,
    k_v: float = K_VERTICAL_DEFAULT,
    face_to_path_deg: float = 0.0,
) -> DPlaneAngle:
    """Spin loft: the 3-D face-normal-to-velocity angle (`spin_loft_3d`).

    UNVALIDATED AGAINST A REFERENCE INSTRUMENT. With ``face_to_path_deg`` at
    its default this is the old ``DL - A``; a nonzero separation adds the
    horizontal component the 2-D subtraction omitted. Note that the vertical
    part is the SAME quantity as the dynamic loft less a measured attack
    angle, so it cannot corroborate it: if one is implausible the other is
    implausible with it.

    Args:
        vertical_launch_deg: Vertical launch angle of the ball.
        attack_angle_deg: Attack angle, negative for a descending blow.
        k_v: Fraction of the loft above the attack angle the ball takes.
        face_to_path_deg: Horizontal face-to-path separation. A geometry
            input only -- supplying the COMPUTED (withheld) value is fine,
            because it never rides the result as a face angle.

    Returns:
        The spin loft in degrees, with ``k_v`` alongside it.

    Raises:
        ValueError: If an angle is not finite or ``k_v`` is outside (0, 1].
    """
    loft = dynamic_loft(vertical_launch_deg, attack_angle_deg, k_v)
    return DPlaneAngle(
        spin_loft_3d(loft.deg, float(attack_angle_deg), face_to_path_deg),
        loft.coefficient,
    )


def face_angle(
    horizontal_launch_deg: float,
    club_path_deg: float,
    k_h: float = K_HORIZONTAL_DEFAULT,
) -> DPlaneAngle:
    """Face angle, ``F = P + (H - P) / k_h``.

    UNVALIDATED AGAINST A REFERENCE INSTRUMENT, and carrying a known
    uncalibrated offset -- see the module docstring. Callers must not display
    this without deciding that question for themselves.

    Args:
        horizontal_launch_deg: Horizontal launch angle, positive right of the
            target line.
        club_path_deg: Club path, on the same convention.
        k_h: Fraction of the face-to-path angle the ball takes.

    Returns:
        The face angle in degrees, with ``k_h`` alongside it.

    Raises:
        ValueError: If an angle is not finite or ``k_h`` is outside (0, 1].
    """
    horizontal = _check_angle("horizontal_launch_deg", horizontal_launch_deg)
    path = _check_angle("club_path_deg", club_path_deg)
    coefficient = _check_coefficient("k_h", k_h)
    return DPlaneAngle(path + (horizontal - path) / coefficient, coefficient)


def face_to_path(
    horizontal_launch_deg: float,
    club_path_deg: float,
    k_h: float = K_HORIZONTAL_DEFAULT,
) -> DPlaneAngle:
    """Face-to-path, ``F - P``, the angle that sets the ball's curvature.

    UNVALIDATED AGAINST A REFERENCE INSTRUMENT, and inheriting the face
    angle's uncalibrated offset in full.

    Args:
        horizontal_launch_deg: Horizontal launch angle, positive right of the
            target line.
        club_path_deg: Club path, on the same convention.
        k_h: Fraction of the face-to-path angle the ball takes.

    Returns:
        The face-to-path angle in degrees, with ``k_h`` alongside it.

    Raises:
        ValueError: If an angle is not finite or ``k_h`` is outside (0, 1].
    """
    face = face_angle(horizontal_launch_deg, club_path_deg, k_h)
    return DPlaneAngle(face.deg - float(club_path_deg), face.coefficient)


@dataclass(frozen=True)
class DPlaneResult:
    """Inverted delivery angles, each with a status saying whether it may be shown.

    UNVALIDATED AGAINST A REFERENCE INSTRUMENT. A field whose status is not
    ``"experimental"`` has its display value set to ``None``; the corresponding
    ``computed_*`` field still carries what the inversion produced, so a
    withheld result can be logged and studied rather than lost.
    """

    dynamic_loft_deg: float | None = None
    dynamic_loft_status: str = STATUS_WITHHELD_LAUNCH_BIAS
    computed_dynamic_loft_deg: float | None = None
    spin_loft_deg: float | None = None
    spin_loft_status: str = STATUS_WITHHELD_LAUNCH_BIAS
    computed_spin_loft_deg: float | None = None
    face_angle_deg: float | None = None
    face_angle_status: str = STATUS_WITHHELD_DIRECTION_OFFSET
    computed_face_angle_deg: float | None = None
    face_to_path_deg: float | None = None
    face_to_path_status: str = STATUS_WITHHELD_DIRECTION_OFFSET
    computed_face_to_path_deg: float | None = None
    k_vertical: float = K_VERTICAL_DEFAULT
    k_horizontal: float = K_HORIZONTAL_DEFAULT
    sources: Mapping[str, str] = field(default_factory=dict)


def _usable(value: float | None) -> float | None:
    """A sensor may report nothing, or report NaN; both mean the same here."""
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _loft_block(
    vertical: float | None,
    attack: float | None,
    k_v: float,
    face_to_path: float | None,
) -> tuple[float | None, float | None, str]:
    """(dynamic loft, spin loft, status) under the launch-angle policy.

    ``face_to_path`` is the COMPUTED horizontal separation -- a geometry input
    to the 3-D spin loft, not an offered face angle. None (no direction or no
    path) falls back to zero separation, which understates the spin loft by
    the horizontal component and is the best available statement.
    """
    if vertical is None:
        # Deliberately NOT falling back to the shipped launch angle: the loft
        # is 1/k_v times it, so the fallback would manufacture a number.
        return None, None, STATUS_WITHHELD_LAUNCH_BIAS
    if attack is None:
        return None, None, STATUS_WITHHELD_NO_ATTACK
    return (
        dynamic_loft(vertical, attack, k_v).deg,
        spin_loft(vertical, attack, k_v, face_to_path_deg=face_to_path or 0.0).deg,
        STATUS_EXPERIMENTAL,
    )


def _face_block(
    horizontal: float | None,
    path: float | None,
    k_h: float,
) -> tuple[float | None, float | None, str]:
    """(face angle, face-to-path, status). Always withheld; see the docstring."""
    if horizontal is None:
        return None, None, STATUS_WITHHELD_NO_DIRECTION
    if path is None:
        return None, None, STATUS_WITHHELD_NO_PATH
    return (
        face_angle(horizontal, path, k_h).deg,
        face_to_path(horizontal, path, k_h).deg,
        STATUS_WITHHELD_DIRECTION_OFFSET,
    )


def dplane_from_sources(  # pylint: disable=too-many-arguments
    *,
    camera_vertical_launch_deg: float | None,
    horizontal_launch_deg: float | None,
    attack_angle_deg: float | None,
    club_path_deg: float | None,
    vertical_launch_source: str = "unknown",
    horizontal_launch_source: str = "unknown",
    attack_angle_source: str = "unknown",
    club_path_source: str = "unknown",
    k_v: float = K_VERTICAL_DEFAULT,
    k_h: float = K_HORIZONTAL_DEFAULT,
) -> DPlaneResult:
    """Invert one shot's launch angles, and decide which results may be shown.

    UNVALIDATED AGAINST A REFERENCE INSTRUMENT. See the module docstring for
    what is withheld and why; in short, the loft needs a camera-derived launch
    angle and the face angle needs a calibration that does not yet exist.

    Every input may be ``None`` or non-finite, which is treated as absent
    rather than as an error: these are sensor outputs, and a shot that
    measured nothing must produce a named status, not an exception.

    Args:
        camera_vertical_launch_deg: Camera-derived vertical launch angle. The
            shipped launch angle is NOT accepted here.
        horizontal_launch_deg: Horizontal launch angle, positive right.
        attack_angle_deg: Attack angle, negative for a descending blow.
        club_path_deg: Club path, positive right.
        vertical_launch_source: Provenance label, recorded on the result.
        horizontal_launch_source: Provenance label, recorded on the result.
        attack_angle_source: Provenance label, recorded on the result.
        club_path_source: Provenance label, recorded on the result.
        k_v: Vertical transfer coefficient.
        k_h: Horizontal transfer coefficient.

    Returns:
        A `DPlaneResult`.

    Raises:
        ValueError: If either coefficient is outside (0, 1].
    """
    coefficient_v = _check_coefficient("k_v", k_v)
    coefficient_h = _check_coefficient("k_h", k_h)
    vertical = _usable(camera_vertical_launch_deg)
    horizontal = _usable(horizontal_launch_deg)
    attack = _usable(attack_angle_deg)
    path = _usable(club_path_deg)

    face, curve, face_status = _face_block(horizontal, path, coefficient_h)
    loft, spin, loft_status = _loft_block(vertical, attack, coefficient_v, curve)
    offered = loft_status == STATUS_EXPERIMENTAL
    return DPlaneResult(
        dynamic_loft_deg=loft if offered else None,
        dynamic_loft_status=loft_status,
        computed_dynamic_loft_deg=loft,
        spin_loft_deg=spin if offered else None,
        spin_loft_status=loft_status,
        computed_spin_loft_deg=spin,
        # The face angle is never offered, whatever its inputs did.
        face_angle_deg=None,
        face_angle_status=face_status,
        computed_face_angle_deg=face,
        face_to_path_deg=None,
        face_to_path_status=face_status,
        computed_face_to_path_deg=curve,
        k_vertical=coefficient_v,
        k_horizontal=coefficient_h,
        sources={
            "vertical_launch": vertical_launch_source,
            "horizontal_launch": horizontal_launch_source,
            "attack_angle": attack_angle_source,
            "club_path": club_path_source,
        },
    )

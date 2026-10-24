"""One sign convention for the LIS3DH roll and the camera's image roll.

roll = atan2(x_g, hypot(y_g, z_g)) in the enclosure's axes (+Y downrange, +Z up) is +
when the target-right side is up; the calibrated projection takes it as is, the nominal
rays take the clockwise correction -r. Recorded, not applied, until confirmed on a unit.
"""

from __future__ import annotations

import math

# Recorded, not applied, until the sign and mount roll are confirmed on a unit.
LIS3DH_ROLL_APPLIED = False


def lis3dh_roll_deg(x_g: float, y_g: float, z_g: float) -> float:
    """The enclosure's roll from one gravity reading: + when its +X side is up."""
    return math.degrees(math.atan2(float(x_g), math.hypot(float(y_g), float(z_g))))


def roll_applied() -> bool:
    """The gate, read when it is used, so setup and swings always agree."""
    return bool(LIS3DH_ROLL_APPLIED)


def applied_camera_roll_deg(
    measured_roll_deg: float | None,
    expected_roll_deg: float = 0.0,
    *,
    apply: bool | None = None,
) -> float:
    """The camera's roll away from its designed placement, target-right side up +.

    Zero while the roll is not applied; once it is, a missing reading is refused
    rather than taken as level. ``apply`` defaults to the gate at call time.
    """
    if apply is None:
        apply = roll_applied()
    if not apply:
        return 0.0
    if measured_roll_deg is None or not math.isfinite(float(measured_roll_deg)):
        raise ValueError("the LIS3DH reading has no roll")
    return float(measured_roll_deg) - float(expected_roll_deg)


def nominal_roll_correction_deg(camera_roll_deg: float) -> float:
    """The nominal rays' clockwise image-roll correction for a camera roll."""
    return -float(camera_roll_deg)


def calibrated_observed_roll_deg(
    measured_roll_deg: float | None,
    reference_roll_deg: float,
    *,
    apply: bool | None = None,
) -> float:
    """The observed roll the calibrated projection takes against its reference pose."""
    return float(reference_roll_deg) + applied_camera_roll_deg(
        measured_roll_deg, reference_roll_deg, apply=apply
    )


__all__ = [
    "LIS3DH_ROLL_APPLIED",
    "applied_camera_roll_deg",
    "calibrated_observed_roll_deg",
    "lis3dh_roll_deg",
    "nominal_roll_correction_deg",
    "roll_applied",
]

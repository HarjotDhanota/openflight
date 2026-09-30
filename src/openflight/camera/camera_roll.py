"""One convention for the LIS3DH roll and the camera's image roll (wiring audit C8).

Setup-geometry spec item A3. The nominal rays and the calibrated projection used
to take the LIS3DH roll with opposite signs; both now take it from here.

**The reading.** The LIS3DH is read in the enclosure's axes after its mount yaw
(``MountedAccelerometer``; the v3 board is turned 180 deg): +Y toward the
enclosure's front, which faces downrange, and +Z up (the v3 unit reads
z_g = +1.06 standing level, so the board lies component side up). At rest an
accelerometer reads the reaction to gravity, +1 g along whichever axis points
up, so

    roll_deg = atan2(x_g, hypot(y_g, z_g))

is positive when the enclosure's +X side is raised.

**The sign, derived.** If the LIS3DH's axes are right-handed, +X = Y x Z =
target-right, and a positive roll is the target-right side up: the camera,
looking downrange, turned counter-clockwise as seen from behind. Then

- the calibrated projection takes the roll as it is: its
  ``_inclination_rotation`` turns the target-right axis up for a positive roll;
- the nominal rays take a clockwise image-roll correction
  (``deroll_normalized_offsets``), and a camera turned counter-clockwise by r sees
  level lines turned clockwise by r, so the correction is -r.

``tests/test_camera_roll.py`` renders a level line through a rolled camera and
pins exactly this, for both paths.

**Not applied yet.** Two facts are outside what the geometry and code can show:

1. That the board's axes are right-handed. The code only fixes +Y (Harjot's
   pitch check, 23 Sept) and +Z (the unit's +1 g); +X follows from handedness,
   which is the sensor's datasheet convention, not something this repo measures.
2. That the board sits square in the housing. On 28 Sept it read -2.9 deg while
   level lines in the frame showed under 1 deg, so it carries a mount roll the
   rig file does not (``lis3dh_mount_roll_deg`` is 0).

Until then the roll is recorded but not applied in either path. The measurement
that settles both is the setup spec's step B2: a phone level on the enclosure,
noting which side is low, against the LIS3DH roll, plus a level line in the
camera frame at the same placement. B2 fixes the sign; the frame's residual
gives ``lis3dh_mount_roll_deg``. Then set ``LIS3DH_ROLL_APPLIED``.
"""

from __future__ import annotations

import math

# Recorded, not applied, until step B2 confirms the direction on the unit.
LIS3DH_ROLL_APPLIED = False


def lis3dh_roll_deg(x_g: float, y_g: float, z_g: float) -> float:
    """The enclosure's roll from one gravity reading: + when its +X side is up."""
    return math.degrees(math.atan2(float(x_g), math.hypot(float(y_g), float(z_g))))


def applied_camera_roll_deg(
    measured_roll_deg: float | None,
    expected_roll_deg: float = 0.0,
    *,
    apply: bool = LIS3DH_ROLL_APPLIED,
) -> float:
    """The camera's roll away from its designed placement, target-right side up +.

    Zero while the roll is not applied; once it is, a missing reading is refused
    rather than taken as level.
    """
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
    apply: bool = LIS3DH_ROLL_APPLIED,
) -> float:
    """The observed roll the calibrated projection takes against its reference pose.

    The projection turns by observed minus reference; while the roll is not
    applied the observation is the reference itself, so it turns by nothing.
    """
    return float(reference_roll_deg) + applied_camera_roll_deg(
        measured_roll_deg, reference_roll_deg, apply=apply
    )


__all__ = [
    "LIS3DH_ROLL_APPLIED",
    "applied_camera_roll_deg",
    "calibrated_observed_roll_deg",
    "lis3dh_roll_deg",
    "nominal_roll_correction_deg",
]

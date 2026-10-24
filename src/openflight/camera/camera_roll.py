"""One sign convention for the LIS3DH roll and the camera's image roll.

roll = atan2(x_g, hypot(y_g, z_g)) in the enclosure's axes (+Y downrange, +Z up) is +
when the target-right side is up; the calibrated projection takes it as is, the nominal
rays take the clockwise correction -r. Only the departure from the rig's expected roll
is applied, and only on a unit whose tilt check verified the roll (``roll_verified`` in
its LIS3DH mount file); elsewhere it is recorded and padded.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from openflight.inclinometer.models import gravity_roll_deg

ROLL_NOT_VERIFIED = "roll not verified on this unit"
# What is left once the roll is applied: the reading's settling, the board's seat and
# the camera's own roll in the enclosure, none measured on the unit.
APPLIED_ROLL_RESIDUAL_DEG = 0.5


def lis3dh_roll_deg(x_g: float, y_g: float, z_g: float) -> float:
    """The enclosure's roll from one gravity reading: + when its +X side is up."""
    return gravity_roll_deg(x_g, y_g, z_g)


def applied_camera_roll_deg(
    measured_roll_deg: float | None,
    expected_roll_deg: float = 0.0,
    *,
    apply: bool,
) -> float:
    """The camera's roll away from its designed placement, target-right side up +.

    Zero unless ``apply`` (the unit verified its roll); then a missing reading is
    refused rather than taken as level.
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
    apply: bool,
) -> float:
    """The observed roll the calibrated projection takes against its reference pose."""
    return float(reference_roll_deg) + applied_camera_roll_deg(
        measured_roll_deg, reference_roll_deg, apply=apply
    )


def roll_record(
    measured_roll_deg: float | None, expected_roll_deg: float, *, verified: bool
) -> dict[str, Any]:
    """How a camera model takes this roll, for the model and the shot's record.

    Applied (``roll_correction_deg`` for the nominal rays) on a verified unit with a
    reading; otherwise recorded, labelled, and padded by the camera's consumers.
    """
    measured = (
        float(measured_roll_deg)
        if isinstance(measured_roll_deg, (int, float))
        and not isinstance(measured_roll_deg, bool)
        and math.isfinite(float(measured_roll_deg))
        else None
    )
    record: dict[str, Any] = {
        "measured_deg": measured,
        "expected_deg": float(expected_roll_deg),
        "verified": bool(verified),
        "applied": False,
        "camera_roll_deg": 0.0,
        "roll_correction_deg": 0.0,
        "residual_deg": None,
        "status": "not_verified_on_this_unit",
        "label": ROLL_NOT_VERIFIED,
    }
    if not verified:
        return record
    if measured is None:
        record.update(status="not_applied_no_lis3dh_reading", label="no LIS3DH roll reading")
        return record
    camera_roll = applied_camera_roll_deg(measured, expected_roll_deg, apply=True)
    record.update(
        applied=True,
        camera_roll_deg=camera_roll,
        roll_correction_deg=nominal_roll_correction_deg(camera_roll),
        residual_deg=APPLIED_ROLL_RESIDUAL_DEG,
        status="applied",
        label="roll applied: verified on this unit",
    )
    return record


def is_applied(camera: Any) -> bool:
    """Whether this camera model was built with the LIS3DH roll applied."""
    roll = getattr(camera, "roll", None)
    return isinstance(roll, Mapping) and roll.get("applied") is True


__all__ = [
    "APPLIED_ROLL_RESIDUAL_DEG",
    "ROLL_NOT_VERIFIED",
    "applied_camera_roll_deg",
    "calibrated_observed_roll_deg",
    "is_applied",
    "lis3dh_roll_deg",
    "nominal_roll_correction_deg",
    "roll_record",
]

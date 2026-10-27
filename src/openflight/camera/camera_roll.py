"""One sign convention for the LIS3DH roll and the camera's image roll.

roll = atan2(x_g, hypot(y_g, z_g)) in the enclosure's axes (+Y downrange, +Z up) is +
when the target-right side is up; the calibrated projection takes it as is, the nominal
rays take the clockwise correction -r. The roll applied is the reading less the unit's
roll zero and the rig's expected mount roll, and only on a unit whose tilt check
verified the roll's direction and zeroed it by reversal (its LIS3DH mount file's
``roll_calibration``); elsewhere it is recorded and padded.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from openflight.inclinometer.models import gravity_roll_deg

ROLL_NOT_VERIFIED = "roll not verified on this unit"
ROLL_NOT_ZEROED = "roll not zeroed on this unit"


def lis3dh_roll_deg(x_g: float, y_g: float, z_g: float) -> float:
    """The enclosure's roll from one gravity reading: + when its +X side is up."""
    return gravity_roll_deg(x_g, y_g, z_g)


def nominal_roll_correction_deg(camera_roll_deg: float) -> float:
    """The nominal rays' clockwise image-roll correction for a camera roll."""
    return -float(camera_roll_deg)


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(float(value)) else None


def roll_record(
    measured_roll_deg: float | None,
    expected_roll_deg: float,
    calibration: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """How a camera model takes this roll, for the model and the shot's record.

    ``calibration`` is the unit's ``roll_calibration`` (``verified``, ``zero_deg``,
    ``residual_deg``); applied roll = reading - zero - expected.
    """
    calibration = calibration if isinstance(calibration, Mapping) else {}
    measured = _finite(measured_roll_deg)
    verified = calibration.get("verified") is True
    zero = _finite(calibration.get("zero_deg"))
    record: dict[str, Any] = {
        "measured_deg": measured,
        "expected_deg": float(expected_roll_deg),
        "verified": verified,
        "zero_deg": zero,
        "applied": False,
        "camera_roll_deg": 0.0,
        "roll_correction_deg": 0.0,
        "residual_deg": None,
        "status": "not_verified_on_this_unit",
        "label": ROLL_NOT_VERIFIED,
    }
    if not verified:
        return record
    if zero is None:
        record.update(status="not_zeroed_on_this_unit", label=ROLL_NOT_ZEROED)
        return record
    if measured is None:
        record.update(status="not_applied_no_lis3dh_reading", label="no LIS3DH roll reading")
        return record
    camera_roll = measured - zero - float(expected_roll_deg)
    record.update(
        applied=True,
        camera_roll_deg=camera_roll,
        roll_correction_deg=nominal_roll_correction_deg(camera_roll),
        residual_deg=_finite(calibration.get("residual_deg")),
        status="applied",
        label="roll applied: verified and zeroed on this unit",
    )
    return record


def calibrated_observed_roll_deg(roll: Mapping[str, Any], reference_roll_deg: float) -> float:
    """The observed roll the calibrated projection takes against its reference pose,
    from a ``roll_record`` made against that reference."""
    return float(reference_roll_deg) + float(roll.get("camera_roll_deg") or 0.0)


def is_applied(camera: Any) -> bool:
    """Whether this camera model was built with the LIS3DH roll applied."""
    roll = getattr(camera, "roll", None)
    return isinstance(roll, Mapping) and roll.get("applied") is True


def residual_deg(camera: Any) -> float | None:
    """The applied roll's residual on this camera model, or None."""
    roll = getattr(camera, "roll", None)
    return _finite(roll.get("residual_deg")) if is_applied(camera) else None


__all__ = [
    "ROLL_NOT_VERIFIED",
    "ROLL_NOT_ZEROED",
    "calibrated_observed_roll_deg",
    "is_applied",
    "lis3dh_roll_deg",
    "nominal_roll_correction_deg",
    "residual_deg",
    "roll_record",
]

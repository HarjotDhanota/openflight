"""Is the unit standing the way the enclosure was designed to stand?

The v42 enclosure leans its housing back by a fixed angle and every vertical
number downstream -- the IWR's boresight elevation, the camera's ball row,
the D-plane's launch angle -- is measured from that designed pose. The
camera/radar maths absorb a few degrees of placement error; past that the
session's vertical datum is wrong and every shot in it is wrong with it.

This module answers the one question that has to be asked BEFORE the first
shot rather than discovered in the log afterwards: does the enclosure's
measured orientation match the orientation its geometry says it should have?
It is deliberately pure -- a reading, an expectation, a tolerance in, a named
status out -- so the server can run it at startup and again per shot, and so
a replay can run it over logged numbers.

A failed gate NEVER blocks a shot. The shot is logged with the status
attached, because a tilted session that is labelled tilted can still be
re-analysed, while a refused shot is simply lost.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# How far the unit may be from its designed pose before the vertical datum
# breaks. The camera/radar geometry absorbs roughly +-5 deg of placement
# error: at the ~1.4 m tee range the IWR's elevation solve and the camera's
# ball-row height solve both stay inside their own error bars over that span,
# and beyond it the two disagree by more than the launch-angle budget. It is
# an engineering bound on the geometry, not a sensor spec -- the LIS3DH
# itself resolves far better than this.
PLACEMENT_TOLERANCE_DEG = 5.0

#: Every status this module can return, in the order the checks run.
PLACEMENT_STATUSES = (
    "ok",
    "tilted_pitch",
    "tilted_roll",
    "tilted_both",
    "no_reading",
    "no_expected_orientation",
)


@dataclass(frozen=True)
class PlacementAssessment:
    """One verdict on how the unit is standing, with the numbers behind it.

    ``status`` is always one of :data:`PLACEMENT_STATUSES` -- never None, so a
    consumer can branch on a name instead of on an absence. ``pitch_error_deg``
    and ``roll_error_deg`` are signed (reading minus expectation, positive =
    leaning further back / further onto its target-right side) and are None
    exactly when that axis could not be compared.
    """

    status: str
    pitch_error_deg: float | None
    roll_error_deg: float | None
    tolerance_deg: float
    reason: str

    @property
    def ok(self) -> bool:
        """True only for a placement that passed; every other status is a miss."""
        return self.status == "ok"

    def to_dict(self) -> dict:
        """JSON-safe view for the shot payload, session log, and UI event."""
        return {
            "status": self.status,
            "pitch_error_deg": (
                round(self.pitch_error_deg, 3) if self.pitch_error_deg is not None else None
            ),
            "roll_error_deg": (
                round(self.roll_error_deg, 3) if self.roll_error_deg is not None else None
            ),
            "tolerance_deg": round(self.tolerance_deg, 3),
            "reason": self.reason,
        }


def _finite(value: float | None) -> float | None:
    """Return the value as a float, or None when it is absent or not finite."""
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def assess_placement(
    *,
    pitch_deg: float | None,
    roll_deg: float | None,
    expected_pitch_deg: float | None,
    expected_roll_deg: float | None,
    tolerance_deg: float = PLACEMENT_TOLERANCE_DEG,
) -> PlacementAssessment:
    """Compare a measured orientation against the one the rig geometry expects.

    ``pitch_deg`` / ``roll_deg`` are the inclinometer's reading in the same
    sense as the expectation (positive pitch = nose up / leaning back,
    positive roll = target-right side down). A reading that is missing or not
    finite is ``no_reading``; an expectation that is missing -- no rig
    geometry file, or a file without ``housing_tilt_deg`` -- is
    ``no_expected_orientation``. A missing reading is reported first: with no
    number in hand, nothing can be said about the expectation either.

    An error EQUAL to the tolerance passes; the tolerance is the last angle
    the geometry still absorbs, not the first one it does not.
    """
    tolerance = float(tolerance_deg)
    if not math.isfinite(tolerance) or tolerance <= 0.0:
        raise ValueError(f"tolerance_deg must be a positive number of degrees, got {tolerance_deg}")

    pitch = _finite(pitch_deg)
    roll = _finite(roll_deg)
    if pitch is None or roll is None:
        missing = (
            "pitch and roll"
            if pitch is None and roll is None
            else "pitch"
            if pitch is None
            else "roll"
        )
        return PlacementAssessment(
            status="no_reading",
            pitch_error_deg=None,
            roll_error_deg=None,
            tolerance_deg=tolerance,
            reason=f"no usable inclinometer {missing} reading to place the unit with",
        )

    expected_pitch = _finite(expected_pitch_deg)
    expected_roll = _finite(expected_roll_deg)
    if expected_pitch is None or expected_roll is None:
        absent = (
            "pitch and roll"
            if expected_pitch is None and expected_roll is None
            else "pitch"
            if expected_pitch is None
            else "roll"
        )
        return PlacementAssessment(
            status="no_expected_orientation",
            pitch_error_deg=None,
            roll_error_deg=None,
            tolerance_deg=tolerance,
            reason=(
                f"the rig geometry gives no expected {absent}, so "
                f"pitch {pitch:+.2f} deg / roll {roll:+.2f} deg cannot be judged"
            ),
        )

    pitch_error = pitch - expected_pitch
    roll_error = roll - expected_roll
    pitch_tilted = abs(pitch_error) > tolerance
    roll_tilted = abs(roll_error) > tolerance

    if pitch_tilted and roll_tilted:
        status = "tilted_both"
    elif pitch_tilted:
        status = "tilted_pitch"
    elif roll_tilted:
        status = "tilted_roll"
    else:
        status = "ok"

    detail = (
        f"pitch {pitch:+.2f} deg vs expected {expected_pitch:+.2f} deg "
        f"({pitch_error:+.2f} deg), roll {roll:+.2f} deg vs expected "
        f"{expected_roll:+.2f} deg ({roll_error:+.2f} deg), tolerance "
        f"{tolerance:.1f} deg"
    )
    if status == "ok":
        reason = f"placed as designed: {detail}"
    else:
        axes = {
            "tilted_both": "pitch and roll are",
            "tilted_pitch": "pitch is",
            "tilted_roll": "roll is",
        }[status]
        reason = f"{axes} outside the placement tolerance: {detail}"

    return PlacementAssessment(
        status=status,
        pitch_error_deg=pitch_error,
        roll_error_deg=roll_error,
        tolerance_deg=tolerance,
        reason=reason,
    )

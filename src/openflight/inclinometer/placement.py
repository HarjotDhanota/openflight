"""Is the enclosure placed as expected? A check on one LIS3DH reading.

The guard compares the reading's pitch and roll with the expected pose. An
upside-down or unreadable reading is not ready; one beyond the tolerance is
ready with a warning, since the correction's accuracy there is not qualified.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

PLACEMENT_TOLERANCE_DEG = 2.0


def placement_guard(
    reading: Any,
    *,
    expected_pitch_deg: float = 0.0,
    expected_roll_deg: float = 0.0,
    tolerance_deg: float = PLACEMENT_TOLERANCE_DEG,
) -> dict[str, Any]:
    """Check operational enclosure placement without qualifying estimator accuracy."""
    get = (
        reading.get
        if isinstance(reading, Mapping)
        else lambda name, default=None: getattr(reading, name, default)
    )
    pitch = get("calibrated_pitch_deg", get("pitch_deg"))
    x_g, y_g, z_g = (get(axis) for axis in ("x_g", "y_g", "z_g"))
    roll = get("roll_deg")
    values = (pitch, x_g, y_g, z_g, expected_pitch_deg, expected_roll_deg, tolerance_deg)
    finite = all(
        isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        for value in values
    )
    if finite and roll is None:
        roll = math.degrees(math.atan2(x_g, math.hypot(y_g, z_g)))
    finite = (
        finite
        and isinstance(roll, (int, float))
        and not isinstance(roll, bool)
        and math.isfinite(roll)
    )
    upright = bool(finite and z_g > 0.0)
    pitch_error = float(pitch - expected_pitch_deg) if finite else None
    roll_error = float(roll - expected_roll_deg) if finite else None
    within_threshold = bool(
        finite and abs(pitch_error) <= tolerance_deg and abs(roll_error) <= tolerance_deg
    )
    ready = bool(finite and upright)
    reason = None
    warning = None
    if not finite:
        reason = "LIS3DH placement values are missing or nonfinite"
    elif not upright:
        reason = "LIS3DH reports the enclosure is upside down"
    elif not within_threshold:
        warning = (
            f"placement exceeds the {tolerance_deg:.1f} degree flag threshold: "
            f"pitch {float(pitch):+.2f} degrees (delta {pitch_error:+.2f}), "
            f"roll {float(roll):+.2f} degrees (delta {roll_error:+.2f}); "
            "correction accuracy is not qualified"
        )
    return {
        "ready": ready,
        "reason": reason,
        "pitch_deg": float(pitch) if finite else None,
        "roll_deg": float(roll) if finite else None,
        "z_g": float(z_g) if finite else None,
        "upright": upright,
        "expected_pitch_deg": float(expected_pitch_deg),
        "expected_roll_deg": float(expected_roll_deg),
        "tolerance_deg": float(tolerance_deg),
        "pitch_error_deg": pitch_error,
        "roll_error_deg": roll_error,
        "within_threshold": within_threshold,
        "warned": warning is not None,
        "warning": warning,
        "accuracy_qualified": False,
    }

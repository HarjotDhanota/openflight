"""One zero model for every horizontal angle.

Each sensor's angle is first measured from the unit boresight (+ right); one offset,
``-target_yaw`` (target yaw + when the target line is right of the boresight), is then added,
so a ball straight down the target line reads 0. No offset: ``unit_boresight`` frame.
"""

from __future__ import annotations

from typing import Optional

UNIT_BORESIGHT = "unit_boresight"
TARGET_LINE = "target_line"
# a value derived from angles in two different frames
MIXED = "mixed"


def _wrap_deg(angle_deg):
    """Wrap to [-180, 180); works on floats and numpy arrays alike."""
    return (angle_deg + 180.0) % 360.0 - 180.0


def offset_from_target_yaw(target_yaw_deg: float) -> float:
    """The horizontal offset for a target yaw (+ = target line right of the boresight)."""
    return -float(target_yaw_deg)


def frame_for(offset_deg: Optional[float]) -> str:
    """The frame a reported horizontal angle is in: None is unset, 0.0 is a target line."""
    return UNIT_BORESIGHT if offset_deg is None else TARGET_LINE


def to_reported_deg(angle_from_boresight_deg, offset_deg: Optional[float]):
    """An angle from the unit boresight -> the reported angle (plus the offset)."""
    return _wrap_deg(angle_from_boresight_deg + (offset_deg or 0.0))


def to_boresight_deg(reported_deg, offset_deg: Optional[float]):
    """The inverse of ``to_reported_deg``: a reported angle -> from the boresight."""
    return _wrap_deg(reported_deg - (offset_deg or 0.0))

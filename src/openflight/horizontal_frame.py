"""One zero model for every horizontal angle (D36 item 2).

Every horizontal angle the unit reports -- camera and IWR horizontal launch,
IWR and camera club path, the D-plane face angle and the spin-axis proxy --
goes through the same two layers:

1. **Each sensor's own boresight zero.** The IWR's is its board's horizontal
   phase reference (``--iwr6843-horizontal-phase-reference-rad``); the
   camera's is its optical axis. After this layer an angle is measured from
   the unit's boresight, + right.
2. **One horizontal offset, ADDED once to every angle.** It is the negative of
   the target yaw: the target yaw is the target line's direction from the unit
   boresight, + when the target line lies right of it, seen from behind the
   unit, and the offset is ``-yaw`` (equivalently: + when the boresight points
   right of the target line). So a ball hit straight down the target line, which
   reads +yaw from the boresight, reports 0, and + still means right of the
   target line.

The offset reaches the kiosk through ``--camera-capture-horizontal-offset-deg``
and ``--iwr6843-azimuth-offset-deg``, with the same value (the D34 target-line
hand-off passes it to both); two different values are refused
(``resolve_horizontal_offset``). An angle reported with no offset (unset or 0)
is in the ``unit_boresight`` frame; with one, in the ``target_line`` frame.
"""

from __future__ import annotations

from typing import Optional

UNIT_BORESIGHT = "unit_boresight"
TARGET_LINE = "target_line"
# A value derived from angles in two different frames (e.g. face angle from a
# target-line launch and a boresight path).
MIXED = "mixed"

CAMERA_OFFSET_FLAG = "--camera-capture-horizontal-offset-deg"
IWR_OFFSET_FLAG = "--iwr6843-azimuth-offset-deg"


def _wrap_deg(angle_deg):
    """Wrap to [-180, 180); works on floats and numpy arrays alike."""
    return (angle_deg + 180.0) % 360.0 - 180.0


def offset_from_target_yaw(target_yaw_deg: float) -> float:
    """The flag value for a target yaw (+ = target line right of the boresight)."""
    return -float(target_yaw_deg)


def frame_for(offset_deg: Optional[float]) -> str:
    """The frame a reported horizontal angle is in, for this offset."""
    return TARGET_LINE if offset_deg else UNIT_BORESIGHT


def to_reported_deg(angle_from_boresight_deg, offset_deg: Optional[float]):
    """An angle from the unit boresight -> the reported angle (plus the offset)."""
    return _wrap_deg(angle_from_boresight_deg + (offset_deg or 0.0))


def to_boresight_deg(reported_deg, offset_deg: Optional[float]):
    """The inverse of ``to_reported_deg``: a reported angle -> from the boresight."""
    return _wrap_deg(reported_deg - (offset_deg or 0.0))


def combined_frame(*frames: Optional[str]) -> Optional[str]:
    """The one frame a value built from these angles is in, or ``mixed``.

    Unknown (None) frames are ignored; with none known the result is None.
    """
    known = {frame for frame in frames if frame is not None}
    if not known:
        return None
    return known.pop() if len(known) == 1 else MIXED


def resolve_horizontal_offset(
    *, camera_deg: Optional[float], iwr_deg: Optional[float]
) -> Optional[float]:
    """The one horizontal offset from the two kiosk flags.

    Either flag alone sets it for every sensor. Both given must carry the same
    value; anything else is refused rather than silently giving the camera and
    the IWR two different target lines.
    """
    if camera_deg is not None and iwr_deg is not None and camera_deg != iwr_deg:
        raise ValueError(
            f"{CAMERA_OFFSET_FLAG} ({camera_deg:+g}) and {IWR_OFFSET_FLAG} ({iwr_deg:+g}) differ. "
            "Both carry the one horizontal offset, -(target yaw), added to every horizontal "
            "angle (positive = boresight right of the target line), and must be equal."
        )
    return camera_deg if camera_deg is not None else iwr_deg

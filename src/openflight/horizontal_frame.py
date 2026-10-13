"""One zero model for every horizontal angle (D36 item 2).

Every horizontal angle the unit reports -- camera and IWR horizontal launch,
IWR and camera club path, the D-plane face angle and the spin-axis proxy --
goes through the same two layers:

1. **Each sensor's own boresight zero.** The IWR's is its board's horizontal
   phase reference (``--iwr6843-horizontal-phase-reference-rad``); the
   camera's is its optical axis. After this layer an angle is measured from
   the unit's boresight.
2. **One unit-to-target yaw**: how far the target line is from the unit's
   boresight. Positive means the target line lies to the RIGHT of the unit
   boresight, as seen from behind the unit. Every reported angle is its angle
   from the boresight minus this yaw, so + still means right of the target
   line.

The yaw reaches the kiosk through ``--camera-capture-horizontal-offset-deg``
and ``--iwr6843-azimuth-offset-deg``, with the same value and sign; two
different values are refused (``resolve_target_yaw``). An angle reported with
no yaw (unset or 0) is in the ``unit_boresight`` frame; with one, in the
``target_line`` frame.
"""

from __future__ import annotations

from typing import Optional

UNIT_BORESIGHT = "unit_boresight"
TARGET_LINE = "target_line"
# A value derived from angles in two different frames (e.g. face angle from a
# target-line launch and a boresight path).
MIXED = "mixed"

CAMERA_YAW_FLAG = "--camera-capture-horizontal-offset-deg"
IWR_YAW_FLAG = "--iwr6843-azimuth-offset-deg"


def _wrap_deg(angle_deg):
    """Wrap to [-180, 180); works on floats and numpy arrays alike."""
    return (angle_deg + 180.0) % 360.0 - 180.0


def frame_for(target_yaw_deg: Optional[float]) -> str:
    """The frame a reported horizontal angle is in, for this target yaw."""
    return TARGET_LINE if target_yaw_deg else UNIT_BORESIGHT


def to_reported_deg(angle_from_boresight_deg, target_yaw_deg: Optional[float]):
    """An angle from the unit boresight -> the reported angle (minus the yaw)."""
    return _wrap_deg(angle_from_boresight_deg - (target_yaw_deg or 0.0))


def to_boresight_deg(reported_deg, target_yaw_deg: Optional[float]):
    """The inverse of ``to_reported_deg``: a reported angle -> from the boresight."""
    return _wrap_deg(reported_deg + (target_yaw_deg or 0.0))


def combined_frame(*frames: Optional[str]) -> Optional[str]:
    """The one frame a value built from these angles is in, or ``mixed``.

    Unknown (None) frames are ignored; with none known the result is None.
    """
    known = {frame for frame in frames if frame is not None}
    if not known:
        return None
    return known.pop() if len(known) == 1 else MIXED


def resolve_target_yaw(*, camera_deg: Optional[float], iwr_deg: Optional[float]) -> Optional[float]:
    """The one unit-to-target yaw from the two kiosk flags.

    Either flag alone sets the yaw for every sensor. Both given must carry the
    same value and sign; anything else is refused rather than silently giving
    the camera and the IWR two different target lines.
    """
    if camera_deg is not None and iwr_deg is not None and camera_deg != iwr_deg:
        raise ValueError(
            f"{CAMERA_YAW_FLAG} ({camera_deg:+g}) and {IWR_YAW_FLAG} ({iwr_deg:+g}) differ. "
            "Both carry the one unit-to-target yaw (positive = target line right of the "
            "unit boresight, seen from behind the unit) and must have the same value and sign."
        )
    return camera_deg if camera_deg is not None else iwr_deg

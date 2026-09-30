"""Pixel sizes for the resting golf ball in each OV9281 camera mode.

The ball detectors' pixel limits were tuned on the 640x400 mode (2x binned). At
1280x800 (1:1 sampling) the same ball is twice as wide, so linear limits scale
with the focal-length ratio and areas with its square (wiring audit B3, 29 Sept:
a ball 1.0-1.3 m out at 1280x800 is 31-40 px and failed every fixed 9-30 px
gate).

Focal length follows the sensor's binning, not the output width: 1280x800 reads
every pixel, 640x400 bins 2x2, and 320x200 is a crop of the 2x-binned mode, so
it shares 640x400's focal length. The focal length itself comes from the rig
file, converted from the mode the file describes by the binning ratio (wiring
audit C2).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openflight.rig_geometry import RigGeometry

# The mode the limits were tuned in, and the narrowest mode read without binning.
REFERENCE_MODE_WIDTH_PX = 640
FULL_RESOLUTION_MIN_WIDTH_PX = 1280
# The tuned diameter limits at 640x400: about 0.66-2.2 m from the camera.
REFERENCE_BALL_DIAMETER_PX = (9.0, 30.0)


def binning_factor(image_width_px: int) -> int:
    """How many sensor pixels one output pixel spans across, in this mode."""
    return 1 if image_width_px >= FULL_RESOLUTION_MIN_WIDTH_PX else 2


def mode_focal_px(image_width_px: int, rig: "RigGeometry") -> float:
    """This mode's focal length from the rig file's, by the binning ratio.

    The rig file states its focal length for the mode it names
    (``image_width``); a mode binned less sees the same lens with finer pixels.
    """
    return float(rig.focal_px) * binning_factor(rig.image_width) / binning_factor(image_width_px)


def pixel_scale(
    image_width_px: int,
    *,
    focal_px: float | None = None,
    rig: "RigGeometry | None" = None,
) -> float:
    """How much larger the ball is in pixels than in the tuned 640x400 mode.

    Through the same lens that is the binning ratio. An explicit ``focal_px``
    (a calibrated one) is compared with the rig file's 640x400 focal length.
    """
    if focal_px is None:
        return binning_factor(REFERENCE_MODE_WIDTH_PX) / binning_factor(image_width_px)
    if rig is None:
        raise ValueError("an explicit focal length needs the rig file's to compare against")
    return float(focal_px) / mode_focal_px(REFERENCE_MODE_WIDTH_PX, rig)


def ball_diameter_bounds_px(scale: float) -> tuple[float, float]:
    """The resting ball's plausible diameter range at this pixel scale."""
    smallest, largest = REFERENCE_BALL_DIAMETER_PX
    return smallest * scale, largest * scale


__all__ = [
    "REFERENCE_BALL_DIAMETER_PX",
    "ball_diameter_bounds_px",
    "binning_factor",
    "mode_focal_px",
    "pixel_scale",
]

"""Pixel sizes for the resting golf ball in each OV9281 camera mode.

Detector limits were tuned at 640x400 (2x2 binned); linear limits scale with the
focal-length ratio, areas with its square. Focal length follows binning, not output
width: 320x200 is a crop of the binned mode.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openflight.rig_geometry import RigGeometry

REFERENCE_MODE_WIDTH_PX = 640
FULL_RESOLUTION_MIN_WIDTH_PX = 1280
# The tuned diameter limits at 640x400: about 0.66-2.2 m from the camera.
REFERENCE_BALL_DIAMETER_PX = (9.0, 30.0)


def binning_factor(image_width_px: int) -> int:
    """How many sensor pixels one output pixel spans across, in this mode."""
    return 1 if image_width_px >= FULL_RESOLUTION_MIN_WIDTH_PX else 2


def mode_focal_px(image_width_px: int, rig: "RigGeometry") -> float:
    """This mode's focal length from the rig file's (stated for its ``image_width``)."""
    return float(rig.focal_px) * binning_factor(rig.image_width) / binning_factor(image_width_px)


def pixel_scale(
    image_width_px: int,
    *,
    focal_px: float | None = None,
    rig: "RigGeometry | None" = None,
) -> float:
    """How much larger the ball is in pixels than in the tuned 640x400 mode.

    An explicit ``focal_px`` is compared with the rig file's 640x400 focal length.
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

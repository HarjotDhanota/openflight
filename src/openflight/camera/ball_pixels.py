"""Pixel sizes for the resting golf ball in each OV9281 camera mode.

The ball detectors' pixel limits were tuned on the 640x400 mode (2x binned,
nominal focal 466.67 px). At 1280x800 (1:1 sampling, 933.33 px) the same ball
is twice as wide, so linear limits scale with the focal-length ratio and areas
with its square (wiring audit B3, 29 Sept: a ball 1.0-1.3 m out at 1280x800 is
31-40 px and failed every fixed 9-30 px gate).

Until the focal length comes from the rig file (wiring spec C2), each mode's
nominal focal follows from the frame width, as the tester's models already do:
320x200 is a crop of the 640x400 mode and shares its focal length.
"""

from __future__ import annotations

REFERENCE_FOCAL_PX = 466.6667  # the 640x400 mode the limits were tuned in
FULL_RESOLUTION_FOCAL_PX = 933.3333
FULL_RESOLUTION_MIN_WIDTH_PX = 1280
# The tuned diameter limits at 640x400: about 0.66-2.2 m from the camera.
REFERENCE_BALL_DIAMETER_PX = (9.0, 30.0)


def nominal_focal_px(image_width_px: int) -> float:
    """The nominal focal length of the mode that produces frames this wide."""
    if image_width_px >= FULL_RESOLUTION_MIN_WIDTH_PX:
        return FULL_RESOLUTION_FOCAL_PX
    return REFERENCE_FOCAL_PX


def pixel_scale(image_width_px: int, *, focal_px: float | None = None) -> float:
    """How much larger the ball is in pixels than in the tuned 640x400 mode."""
    focal = float(focal_px) if focal_px is not None else nominal_focal_px(image_width_px)
    return focal / REFERENCE_FOCAL_PX


def ball_diameter_bounds_px(scale: float) -> tuple[float, float]:
    """The resting ball's plausible diameter range at this pixel scale."""
    smallest, largest = REFERENCE_BALL_DIAMETER_PX
    return smallest * scale, largest * scale


__all__ = [
    "FULL_RESOLUTION_FOCAL_PX",
    "REFERENCE_BALL_DIAMETER_PX",
    "REFERENCE_FOCAL_PX",
    "ball_diameter_bounds_px",
    "nominal_focal_px",
    "pixel_scale",
]

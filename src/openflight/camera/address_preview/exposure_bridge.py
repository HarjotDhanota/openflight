"""Fit current frames separately across a control change; never mix exposures."""

from __future__ import annotations

import numpy as np

from openflight.camera.ground_patch import point_in_polygon

from .outline_checks import qualify
from .sphere_fit import fit
from .stability import photo_valid, within_rim_tolerance


def measure(frames, camera, patch, controls, parameters):
    """The ball in each of the last two frames alone, when both agree with the last fit."""
    if frames.dtype != np.uint8 or frames.ndim != 3 or len(frames) < 2:
        return None
    if frames.shape[1:] != (camera.image_height_px, camera.image_width_px):
        return None
    pairs = np.asarray(controls, dtype=float)
    if (
        pairs.shape != (len(frames), 2)
        or not np.isfinite(pairs[-2:]).all()
        or not (pairs[-2:] > 0).all()
    ):
        return None
    x, y, radius, azimuth, tilt = parameters
    left = round(x) - 60
    top = round(y) - 60
    if left < 0 or top < 0 or left + 120 > frames.shape[2] or top + 120 > frames.shape[1]:
        return None
    initial = [x - left, y - top, radius, azimuth, tilt]
    measured = []
    for image in frames[-2:]:
        crop = image[top : top + 120, left : left + 120]
        result = qualify(crop, fit(crop, starts=[initial]))
        if not photo_valid(result):
            return None
        measured.append(result)
    circles = [m["circle"] for m in measured]
    checks = [(circles[0], initial[:3]), (circles[1], initial[:3]), (circles[0], circles[1])]
    if not all(within_rim_tolerance(a, b) for a, b in checks):
        return None
    cx, cy, r = circles[-1]
    cx += left
    cy += top
    d = measured[-1]["best_diagnostic"]
    if not point_in_polygon(cx, cy, patch.outline_px):
        return None
    return {
        "selected": {"x_px": cx, "y_px": cy, "radius_px": r},
        "parameters": [cx, cy, r, d["azimuth_rad"], d["tilt_rad"]],
        "endpoint_fits": measured,
        "method": "two_separate_raw_frame_fits_with_actual_readbacks",
        "controls": pairs[-2:].tolist(),
    }

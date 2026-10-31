"""Measure the current whole-ball ROI again; never renew evidence by drawing."""

from __future__ import annotations

import math

import numpy as np

from openflight.camera import ball_model as reference

from .search import refine_candidate


def refresh_local(frames, camera, parameters):
    """A new fit of the current window at the last ball, if it stays that ball."""
    x, y, radius, azimuth, tilt = parameters
    left = round(x) - 60
    top = round(y) - 60
    if left < 0 or top < 0 or left + 120 > frames.shape[2] or top + 120 > frames.shape[1]:
        return None
    cropped = frames[:, top : top + 120, left : left + 120]
    image = np.median(cropped, axis=0).astype(np.uint8)
    candidate = {
        "origin": [0, 0],
        "parameters": [x - left, y - top, radius, azimuth, tilt],
        "native_width_px": camera.image_width_px,
    }
    item = refine_candidate((image, cropped, 0, candidate, True))
    if item["circle"] is None:
        return None
    cx, cy, r = item["circle"]
    cx += left
    cy += top
    tolerance = reference.MAX_RIM_OFFSET_FRACTION
    if math.hypot(cx - x, cy - y) > tolerance * radius or abs(r / radius - 1) > tolerance:
        return None
    d = item["refined"]["best_diagnostic"]
    return {
        "selected": {"x_px": cx, "y_px": cy, "radius_px": r},
        "parameters": [cx, cy, r, d["azimuth_rad"], d["tilt_rad"]],
        "evidence": item,
    }

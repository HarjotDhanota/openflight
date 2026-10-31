"""Compare independently fitted window endpoints when illumination changes."""

from __future__ import annotations

import math

import numpy as np

from openflight.camera import ball_model as reference

from .outline_checks import qualify
from .sphere_fit import fit


def photo_valid(measured) -> bool:
    """A qualified circle lit from the expected side and explained beyond its noise."""
    detail = measured["best_diagnostic"]
    azimuth = math.degrees(detail["azimuth_rad"]) % 360
    low, high = reference.LIGHT_AZIMUTH_DEG
    if azimuth < low:
        azimuth += 360
    return bool(
        measured["circle"]
        and low <= azimuth <= high
        and detail["misfit_dn"] <= reference.MAX_MISFIT_FRACTION * detail["diffuse_dn"]
        and detail["quality"] >= reference.MIN_DIFFUSE_NOISE_MULTIPLE
    )


def within_rim_tolerance(first, second) -> bool:
    """Two circles agree in centre and radius within the rim offset tolerance."""
    tolerance = reference.MAX_RIM_OFFSET_FRACTION
    if math.hypot(first[0] - second[0], first[1] - second[1]) > tolerance * min(
        first[2], second[2]
    ):
        return False
    return abs(first[2] - second[2]) <= tolerance * min(first[2], second[2])


def stable_geometry(frames, origin, refined):
    """Whether the window's first and last three frames each fit the same circle."""
    left, top = origin
    cx, cy, radius = refined["circle"]
    d = refined["best_diagnostic"]
    initial = [cx, cy, radius, d["azimuth_rad"], d["tilt_rad"]]
    result = {"passes": False, "method": "independently_qualified_endpoint_fits", "endpoints": []}
    for endpoint in [frames[:3], frames[-3:]]:
        image = np.median(endpoint[:, top : top + 120, left : left + 120], axis=0).astype(np.uint8)
        measured = qualify(image, fit(image, starts=[initial]))
        valid = photo_valid(measured)
        result["endpoints"].append({"qualified": valid, "fit": measured})
        if not valid:
            return result
    circles = [e["fit"]["circle"] for e in result["endpoints"]]
    pairs = [
        (circles[0], [cx, cy, radius]),
        (circles[1], [cx, cy, radius]),
        (circles[0], circles[1]),
    ]
    if not all(within_rim_tolerance(a, b) for a, b in pairs):
        return result
    result["passes"] = True
    return result

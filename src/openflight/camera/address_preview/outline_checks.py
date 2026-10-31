"""A fitted sphere is kept only with lit-rim or contextual full-outline evidence."""

from __future__ import annotations

import math

import numpy as np
from scipy import ndimage

from openflight.camera import ball_model as reference

from .sphere_fit import context_background


def lit_rim(image, candidate, noise_dn, factor):
    """The existing lit-rim checks, unchanged, on the candidate's circle."""
    cx, cy, r = candidate["circle"]
    azimuth = candidate["azimuth_rad"]
    patch = image.astype(float) if factor == 1 else reference._bin2(image)  # pylint: disable=protected-access
    x, y = (cx + 0.5) / factor - 0.5, (cy + 0.5) / factor - 0.5
    radius = r / factor
    noise = noise_dn / factor
    # pylint: disable-next=protected-access
    turns, offsets = reference._lit_rim_offsets(patch, x, y, radius, azimuth, noise_dn=noise)
    step = reference._lit_rim_step(patch, x, y, radius, azimuth)  # pylint: disable=protected-access
    offset = float(np.median(np.abs(offsets)))
    bulge = reference._rim_flank_bulge(turns, offsets)  # pylint: disable=protected-access
    passed = (
        offset <= reference.MAX_RIM_OFFSET_FRACTION
        and bulge <= reference.MAX_RIM_FLANK_BULGE_FRACTION
        and step
        >= max(
            reference.MIN_RIM_STEP_NOISE_MULTIPLE * noise,
            reference.MIN_RIM_STEP_FRACTION * candidate["diffuse_dn"],
        )
    )
    return {
        "binning": factor,
        "step_dn": step,
        "median_offset_fraction": offset,
        "flank_bulge_fraction": bulge,
        "passes_existing_rim_checks": bool(passed),
    }


def context_rim(image, candidate, noise_dn):  # pylint: disable=too-many-locals
    """The outline traced against the contextual background, with radial-normal evidence."""
    x, y, radius = candidate["circle"]
    half = math.radians(180 - reference.RIM_SIZE_SHADOW_HALF_ARC_DEG)
    angles = -math.pi / 2 + np.arange(
        -half, half + 1e-9, math.radians(reference.RIM_SIZE_RAY_STEP_DEG)
    )
    distances = np.arange(0.5 * radius, 1.4 * radius, 0.25)
    xs = x + distances[None, :] * np.cos(angles)[:, None]
    ys = y + distances[None, :] * np.sin(angles)[:, None]
    background = context_background(image)
    boundary_background = ndimage.map_coordinates(
        background,
        [y + radius * np.sin(angles), x + radius * np.cos(angles)],
        order=1,
        mode="nearest",
    )
    tilt = candidate["tilt_rad"]
    azimuth = candidate["azimuth_rad"]
    light = np.maximum(math.sin(tilt) * np.cos(angles - azimuth), 0)
    boundary_ball = np.clip(candidate["ambient_dn"] + candidate["diffuse_dn"] * light, 0, 255)
    contrast = boundary_ball - boundary_background
    threshold = max(
        reference.MIN_RIM_STEP_NOISE_MULTIPLE * noise_dn,
        reference.MIN_RIM_STEP_FRACTION * candidate["diffuse_dn"],
    )
    sign = np.where(np.abs(contrast) >= threshold, np.sign(contrast), 0)
    smooth = ndimage.gaussian_filter(image.astype(float), reference.EDGE_BLUR_PX)
    values = ndimage.map_coordinates(
        smooth, [ys.ravel(), xs.ravel()], order=1, mode="nearest"
    ).reshape(xs.shape)
    falls = -np.gradient(values, 0.25, axis=1) * sign[:, None]
    before, here, after = falls[:, :-2], falls[:, 1:-1], falls[:, 2:]
    floor = max(reference.RIM_SIZE_MIN_EDGE_FRACTION * candidate["diffuse_dn"], noise_dn)
    rays, indices = np.nonzero((here >= floor) & (here >= before) & (here > after))
    indices += 1
    edges = np.column_stack([rays, xs[rays, indices], ys[rays, indices], falls[rays, indices]])
    # A genuine silhouette turns with the circle. A long mat/door edge can
    # intersect circular rays without having that curvature. The 30-degree
    # maximum normal error is a research assumption, not calibrated truth.
    gy, gx = np.gradient(smooth)
    coords = [edges[:, 2], edges[:, 1]]
    dx = ndimage.map_coordinates(gx, coords, order=1, mode="nearest")
    dy = ndimage.map_coordinates(gy, coords, order=1, mode="nearest")
    outward = angles[edges[:, 0].astype(int)]
    alignment = np.abs(dx * np.cos(outward) + dy * np.sin(outward)) / np.maximum(
        np.hypot(dx, dy), 1e-12
    )
    normal_count = len(edges)
    edges = edges[alignment >= math.cos(math.pi / 6)]
    normal_evidence = {
        "maximum_error_deg": 30,
        "assumption": "research silhouette-normal tolerance",
        "input_edges": normal_count,
        "aligned_edges": len(edges),
    }
    circle = reference._outline_circle(edges, len(angles), x, y, radius)  # pylint: disable=protected-access
    if circle is None:
        return {"circle": None, "passes_context_rim": False, "normal_evidence": normal_evidence}
    # pylint: disable-next=protected-access
    chosen = reference._rays_agreeing(
        edges,
        len(angles),
        circle,
        max(reference.RIM_SIZE_TOLERANCE_PX, reference.RIM_SIZE_TOLERANCE_FRACTION * radius),
    )
    support = np.flatnonzero(chosen >= 0)
    thirds = [
        int(
            np.count_nonzero(
                (support * 3 >= i * len(angles)) & (support * 3 < (i + 1) * len(angles))
            )
        )
        for i in range(3)
    ]
    center_difference = math.hypot(circle[0] - x, circle[1] - y) / radius
    radius_difference = abs(circle[2] / radius - 1)
    return {
        "circle": list(circle),
        "rays": int(len(support)),
        "thirds": thirds,
        "normal_evidence": normal_evidence,
        "center_difference_fraction": center_difference,
        "radius_difference_fraction": radius_difference,
        "passes_context_rim": bool(
            center_difference <= reference.MAX_RIM_OFFSET_FRACTION
            and radius_difference <= reference.MAX_RIM_OFFSET_FRACTION
            and len(support) >= reference.RIM_SIZE_MIN_RAYS
            and min(thirds) >= 2
        ),
    }


def qualify(image, proposal):
    """The proposal, its circle withdrawn unless a lit rim or a full outline supports it."""
    result = dict(proposal)
    result["raw_circle"] = result["circle"]
    result["rim_checks"] = None
    result["context_rim_checks"] = None
    result["outline_evidence"] = None
    if result["circle"] is not None:
        factor = 2 if result["circle"][2] > 12 else 1
        checks = lit_rim(image, result["best_diagnostic"], result["noise_dn"], factor)
        result["rim_checks"] = checks
        if checks["passes_existing_rim_checks"]:
            result["outline_evidence"] = "original_lit_rim"
        else:
            context = context_rim(image, result["best_diagnostic"], result["noise_dn"])
            result["context_rim_checks"] = context
            if context["passes_context_rim"]:
                result["outline_evidence"] = "context_polarity_full_outline"
            else:
                result["circle"] = None
    return result

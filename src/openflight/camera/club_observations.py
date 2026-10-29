"""Experimental current-image part evidence, independent of metric-pose eligibility.

Ports the part-region and weak-region kernels from the 28 October research.
No template, anatomical hosel length, prior outline or temporal prediction is used.
"""

from __future__ import annotations

from typing import Mapping

import cv2
import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import breadth_first_order, maximum_flow

from .impact_location import MOVING_FLOOR_DN, MOVING_SIGMA

# pylint: disable=no-member,too-many-locals


def _geometry(record):
    anchor = np.asarray(record["anchor"], dtype=float)
    axis = np.asarray(record["axis"], dtype=float)
    band, radius = float(record["shaft_band_px"]), float(record["search_radius_px"])
    if (
        anchor.shape != (2,)
        or axis.shape != (2,)
        or not np.isfinite(np.r_[anchor, axis, band, radius]).all()
        or np.linalg.norm(axis) <= 0
        or band <= 0
        or radius <= 0
    ):
        raise ValueError("finite anchor, nonzero axis and positive ROI dimensions required")
    veto = record.get("ball_veto")
    if veto is not None:
        veto = np.asarray(veto, float)
        if veto.shape != (3,) or not np.isfinite(veto).all() or veto[2] <= 0:
            raise ValueError("ball veto must be a finite current disk with positive radius")
    return anchor, axis / np.linalg.norm(axis), band, radius, veto


def part_regions(shape, record: Mapping) -> dict[str, np.ndarray]:
    """Search regions in the current shaft frame; names are association hypotheses."""
    anchor, axis, band, radius, veto = _geometry(record)
    yy, xx = np.indices(shape)
    along = (xx - anchor[0]) * axis[0] + (yy - anchor[1]) * axis[1]
    across = (xx - anchor[0]) * axis[1] - (yy - anchor[1]) * axis[0]
    valid = (xx - anchor[0]) ** 2 + (yy - anchor[1]) ** 2 <= radius**2
    if veto is not None:
        valid &= (xx - veto[0]) ** 2 + (yy - veto[1]) ** 2 > veto[2] ** 2
    for disk in record.get("additional_unknown_disks", ()):
        disk = np.asarray(disk, float)
        if disk.shape != (3,) or not np.isfinite(disk).all() or disk[2] <= 0:
            raise ValueError("additional unknown disks must be finite with positive radius")
        valid &= (xx - disk[0]) ** 2 + (yy - disk[1]) ** 2 > disk[2] ** 2
    return {
        "head": valid & ~((abs(across) <= band) & (along < -2 * band)),
        "neck": valid & (abs(across) <= band) & (along >= -4 * band) & (along <= band),
        "shaft": valid
        & (abs(across) <= band)
        & (along >= max(-radius, -8 * band))
        & (along < -4 * band),
    }


def _cut_region(frame, foreground_cost, background_cost):
    """The frozen region kernel's four-neighbour objective and integer capacities."""
    height, width = frame.shape
    count = height * width
    nodes = np.arange(count).reshape(frame.shape)
    rows, cols = [np.full(count, count), nodes.ravel()], [nodes.ravel(), np.full(count, count + 1)]
    capacities = [background_cost.ravel(), foreground_cost.ravel()]
    for a, b, ia, ib in (
        (nodes[:, :-1], nodes[:, 1:], frame[:, :-1], frame[:, 1:]),
        (nodes[:-1], nodes[1:], frame[:-1], frame[1:]),
    ):
        difference = ia.astype(float) - ib.astype(float)
        weights = 1.5 * np.exp(-(difference**2) / (2 * 8**2)) + 0.05
        rows.extend((a.ravel(), b.ravel()))
        cols.extend((b.ravel(), a.ravel()))
        capacities.extend((weights.ravel(), weights.ravel()))
    graph = coo_matrix(
        (
            np.maximum(1, np.round(np.concatenate(capacities) * 1000)).astype(np.int64),
            (np.concatenate(rows), np.concatenate(cols)),
        ),
        shape=(count + 2, count + 2),
    ).tocsr()
    residual = graph - maximum_flow(graph, count, count + 1).flow
    residual.data = (residual.data > 0).astype(np.int64)
    residual.eliminate_zeros()
    reachable = breadth_first_order(residual, count, directed=True, return_predecessors=False)
    selected = np.zeros(count + 2, bool)
    selected[reachable] = True
    return selected[:count].reshape(frame.shape)


def _region(frame, background, variation, strong_points, valid):
    empty = np.zeros(frame.shape, bool)
    refusal = {
        "status": "withheld_no_current_strong_support",
        "curve_is_inferred": True,
        "outlines": [],
        "inferred_area_px": 0,
        "components": [],
    }
    points = np.asarray(strong_points, int).reshape(-1, 2)
    points = points[valid[points[:, 1], points[:, 0]]]
    if len(points) < 3 or not valid.any():
        return refusal, empty
    yy, xx = np.nonzero(valid)
    x0, x1, y0, y1 = xx.min(), xx.max() + 1, yy.min(), yy.max() + 1
    residual = cv2.GaussianBlur((frame.astype(float) - background).astype(np.float32), (5, 5), 0.7)
    change = abs(residual) / np.maximum(variation, 1.0)
    z = np.clip(change[y0:y1, x0:x1] - 2.0, -6.0, 6.0)
    foreground_cost, background_cost = np.logaddexp(0, -z), np.logaddexp(0, z)
    seeds = np.zeros(frame.shape, bool)
    seeds[points[:, 1], points[:, 0]] = True
    seeds &= change >= 2.0
    local_seeds = seeds[y0:y1, x0:x1]
    foreground_cost[local_seeds], background_cost[local_seeds] = 0, 100000
    invalid = ~valid[y0:y1, x0:x1]
    foreground_cost[invalid], background_cost[invalid] = 100000, 0
    mask = np.zeros(frame.shape, bool)
    mask[y0:y1, x0:x1] = (
        _cut_region(residual[y0:y1, x0:x1], foreground_cost, background_cost) & ~invalid
    )
    count, labels = cv2.connectedComponents(mask.astype(np.uint8), 8)
    kept = empty.copy()
    components = []
    for label in range(1, count):
        component = labels == label
        support = int((component & seeds).sum())
        if support < 3:
            continue
        outlines = [
            c[:, 0].tolist()
            for c in cv2.findContours(
                component.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE
            )[0]
            if len(c) >= 3
        ]
        if not outlines:
            continue
        kept |= component
        components.append(
            {
                "outlines": outlines,
                "pixels": int(component.sum()),
                "current_strong_support": support,
            }
        )
    return {
        "status": "inferred_current_region" if components else "withheld_no_supported_region",
        "curve_is_inferred": True,
        "outlines": [o for c in components for o in c["outlines"]],
        "inferred_area_px": int(kept.sum()),
        "components": components,
    }, kept


def observe_parts(frame, background, variation, record: Mapping) -> dict:
    """Separate current strong/weak pixels from conditional part-region completion.

    Caller supplies one current image-space axis/ROI and the current ball veto.
    Capture identity and raw-coordinate mapping belong to the calling loader.
    """
    if (
        frame.ndim != 2
        or frame.dtype != np.uint8
        or background.shape != frame.shape
        or variation.shape != frame.shape
        or not np.isfinite(background).all()
        or not np.isfinite(variation).all()
        or (variation < 0).any()
    ):
        raise ValueError(
            "one uint8 frame and matching finite background/nonnegative variation required"
        )
    regions = part_regions(frame.shape, record)
    allowed = regions["head"] | regions["neck"]
    all_parts = allowed | regions["shaft"]
    anchor, _, _, radius, _ = _geometry(record)
    delta = frame.astype(float) - background
    threshold = np.maximum(MOVING_SIGMA * variation, MOVING_FLOOR_DN)
    strong = (abs(delta) > threshold) & all_parts
    sy, sx = np.nonzero(strong)
    points = np.column_stack((sx, sy)).tolist()
    smooth = cv2.GaussianBlur(delta.astype(np.float32), (5, 5), 1.0)
    encoded = np.clip(128 + 4 * smooth, 0, 255).astype(np.uint8)
    edges = cv2.Canny(encoded, 9, 18, L2gradient=True) > 0
    near_change = (
        cv2.dilate(
            (abs(delta) > 2 * np.maximum(variation, 1)).astype(np.uint8), np.ones((3, 3), np.uint8)
        )
        > 0
    )
    near_strong = cv2.dilate(strong.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    weak = edges & near_change & all_parts & ~near_strong
    gx = cv2.Sobel(smooth, cv2.CV_64F, 1, 0, ksize=3, scale=1 / 8)
    gy = cv2.Sobel(smooth, cv2.CV_64F, 0, 1, ksize=3, scale=1 / 8)
    weak_points = []
    for y, x in zip(*np.nonzero(weak)):
        gradient = np.array([gx[y, x], gy[y, x]])
        norm = np.linalg.norm(gradient)
        if norm <= 1e-9:
            continue
        weak_points.append(
            {
                "xy": [int(x), int(y)],
                "gradient_unit_image": (gradient / norm).tolist(),
                "change_sign": int(np.sign(smooth[y, x])),
                "variation_normalized_change": float(abs(delta[y, x]) / max(variation[y, x], 1)),
                "source": "weak_current_change_edge",
            }
        )
    region, _ = _region(frame, background, variation, points, allowed)
    memberships = {}
    for name, search in regions.items():
        ys, xs = np.nonzero(search & (abs(delta) > threshold))
        memberships[name] = {
            "strong_points": np.column_stack((xs, ys)).tolist(),
            "weak_points": [p["xy"] for p in weak_points if search[p["xy"][1], p["xy"][0]]],
            "identity": "search_region_hypothesis_not_anatomy",
        }
    height, width = frame.shape
    return {
        "schema": "openflight.club_part_observations.v1",
        "strong_points": points,
        "weak_points": weak_points,
        "parts": memberships,
        "region": region,
        "strong_floor_dn": MOVING_FLOOR_DN,
        "weak_sigma": 2.0,
        "search_touches_image_border": bool(
            anchor[0] - radius < 0
            or anchor[1] - radius < 0
            or anchor[0] + radius >= width
            or anchor[1] + radius >= height
        ),
        "measurement_eligible": False,
        "qualified_outline": None,
        "physical_pose": None,
        "strike": None,
        "uses_future_frames": False,
    }

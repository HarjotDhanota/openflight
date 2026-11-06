"""Current-edge completion hypotheses, preserving region and neck competitors."""

from __future__ import annotations

import cv2
import numpy as np
from scipy.spatial import cKDTree

from ._club_contour_topology import repair
from ._club_edge_path import PARAMETERS, trace_column
from .club_observations import _geometry, part_regions

# pylint: disable=no-member,too-many-locals


def _pixels(shape, points):
    mask = np.zeros(shape, np.uint8)
    xy = np.asarray(points, int).reshape(-1, 2)
    if len(xy):
        if (xy < 0).any() or (xy[:, 0] >= shape[1]).any() or (xy[:, 1] >= shape[0]).any():
            raise ValueError("current pixel outside image")
        mask[xy[:, 1], xy[:, 0]] = 1
    return mask


def _groups(mask):
    count, labels = cv2.connectedComponents(mask, 8)
    return [np.column_stack(np.nonzero(labels == i)[::-1]) for i in range(1, count)]


def _arc(points, start, stop, step):
    indices = [start]
    while indices[-1] != stop:
        indices.append((indices[-1] + step) % len(points))
    return points[indices]


def _join(initial, chain, tip, anchor, turn, band):
    endpoints = np.array([chain["path"][0], tip])
    gaps, indices = cKDTree(initial).query(endpoints)
    if indices[0] == indices[1] or (gaps > 3 * band).any():
        return {"status": "withheld_endpoint_disagreement", "outline": None}
    arcs = [_arc(initial, int(indices[0]), int(indices[1]), step) for step in (1, -1)]
    upper = int(np.argmin([((a - anchor) @ turn)[:, 1].mean() for a in arcs]))
    combined = np.vstack(
        (initial[indices[0]], chain["path"], initial[indices[1]], arcs[1 - upper][-2:0:-1])
    )
    topology = repair(combined)
    return {**topology, "endpoint_gap_px": gaps.tolist()}


def contour_hypotheses(shape, evidence, geometry, normalized_change):
    """Retain each current region/neck hypothesis; never select a physical outline.

    Strong pixels and weak edges are evidence. Chains and distal completion remain
    inferred even when every path node is close to a current edge candidate.
    """
    if len(shape) != 2 or normalized_change.shape != tuple(shape):
        raise ValueError("matching two-dimensional image geometry required")
    if not np.isfinite(normalized_change).all() or (normalized_change < 0).any():
        raise ValueError("finite nonnegative current change required")
    anchor, axis, band, _, _ = _geometry(geometry)
    turn = np.column_stack(([axis[1], -axis[0]], axis))
    regions = part_regions(shape, geometry)
    valid = regions["head"] | regions["neck"]
    strong = _pixels(shape, evidence["strong_points"]) & valid
    weak = _pixels(shape, [p["xy"] for p in evidence["weak_points"]]) & valid
    edges = (strong & ~cv2.erode(strong, np.ones((3, 3), np.uint8))) | weak
    necks = _groups((strong | weak) & regions["neck"].astype(np.uint8))
    necks = [g for g in necks if len(g) >= 3]
    candidates = []
    for body_id, body in enumerate(evidence["region"]["components"]):
        for outline_id, outline in enumerate(body["outlines"]):
            initial = np.asarray(outline, float)
            mask = np.zeros(shape, np.uint8)
            cv2.fillPoly(mask, [initial.astype(np.int32)], 1)
            points = np.column_stack(np.nonzero(strong & mask)[::-1])
            baseline = repair(initial)
            item = {
                "body_id": body_id,
                "outline_id": outline_id,
                "source": "current_region_completion",
                "outline": baseline["outline"],
                "topology_status": baseline["status"],
                "curve_is_inferred": True,
                "current_strong_points": points.tolist(),
                "chains": [],
            }
            if len(points) >= 3:
                local = (points - anchor) @ turn
                for side in (-1, 1):
                    tips = points[side * local[:, 0] > 1.5 * band]
                    if len(tips) < 3:
                        continue
                    along = ((tips - anchor) @ turn)[:, 1]
                    tip = np.median(tips[along <= np.quantile(along, 0.05)], axis=0)
                    for neck_id, neck in enumerate(necks):
                        chain = trace_column(
                            tip,
                            neck,
                            anchor,
                            turn,
                            side,
                            band,
                            edges.astype(bool),
                            normalized_change,
                            valid,
                        )
                        result = {
                            "status": "withheld_missing_connected_edge_support",
                            "outline": None,
                        }
                        if (
                            chain
                            and chain["current_edge_fraction"]
                            >= PARAMETERS["minimum_current_edge_fraction"]
                        ):
                            result = _join(initial, chain, tip, anchor, turn, band)
                        item["chains"].append(
                            {
                                "neck_id": neck_id,
                                "side": side,
                                "tip_current_xy": tip.tolist(),
                                "current_neck_points": neck.tolist(),
                                "chain": chain,
                                "completion": result,
                                "curve_is_inferred": True,
                            }
                        )
            candidates.append(item)
    return {
        "schema": "openflight.club_contour_hypotheses.v1",
        "status": "inferred_current_contour_hypotheses"
        if candidates
        else "withheld_no_current_region",
        "candidates": candidates,
        "current_edge_points": np.column_stack(np.nonzero(edges)[::-1]).tolist(),
        "neck_hypotheses": len(necks),
        "selected_candidate": None,
        "measurement_eligible": False,
        "qualified_outline": None,
        "physical_pose": None,
        "strike": None,
        "uses_future_frames": False,
    }

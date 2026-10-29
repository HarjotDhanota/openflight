"""Continuous containment of current pixels under a fixed object-assignment branch."""

import numpy as np
from scipy.spatial import cKDTree

OWNERS = ("head", "ball", "shaft", "clutter", "unassigned")


def _distances(points, triangles):
    edges = np.roll(triangles, -1, axis=-2) - triangles
    offsets = points[..., None, :] - triangles
    cross = edges[..., 0] * offsets[..., 1] - edges[..., 1] * offsets[..., 0]
    first, second = (
        triangles[..., 1, :] - triangles[..., 0, :],
        triangles[..., 2, :] - triangles[..., 0, :],
    )
    area = first[..., 0] * second[..., 1] - first[..., 1] * second[..., 0]
    inside = ((cross >= 0).all(axis=-1) | (cross <= 0).all(axis=-1)) & (abs(area) > 1e-12)
    along = np.clip(
        np.sum(offsets * edges, axis=-1) / np.maximum(np.sum(edges**2, axis=-1), 1e-24), 0, 1
    )
    distance = np.linalg.norm(offsets - along[..., None] * edges, axis=-1).min(axis=-1)
    return np.where(inside, 0, distance)


def triangle_union_distance(points, triangles):
    """Exact outside distance, zero inside any triangle, without a convex hull.

    Nearest-centroid triangles provide an upper bound. Any closer triangle must
    have its centroid within that bound plus the maximum triangle radius.
    """
    points, triangles = np.asarray(points, float), np.asarray(triangles, float)
    if (
        points.ndim != 2
        or points.shape[1:] != (2,)
        or triangles.ndim != 3
        or triangles.shape[1:] != (3, 2)
        or triangles.size == 0
        or not np.isfinite(points).all()
        or not np.isfinite(triangles).all()
    ):
        raise ValueError("finite current points and nonempty projected full triangles required")
    if points.size == 0:
        return np.empty(0)
    centers = triangles.mean(axis=1)
    radii = np.linalg.norm(triangles - centers[:, None], axis=-1).max(axis=1)
    radius = radii.max()
    tree = cKDTree(centers)
    _, nearest = tree.query(points, k=min(8, len(triangles)))
    nearest = np.asarray(nearest).reshape(len(points), -1)
    best = _distances(points[:, None], triangles[nearest]).min(axis=1)
    for index in np.flatnonzero(best > 0):
        limit = np.nextafter(best[index] + radius, np.inf)
        candidates = np.asarray(tree.query_ball_point(points[index], limit), int)
        lower_bound = (
            np.linalg.norm(centers[candidates] - points[index], axis=1) - radii[candidates]
        )
        kept = candidates[lower_bound <= np.nextafter(best[index], np.inf)]
        if len(kept):
            best[index] = min(best[index], _distances(points[index], triangles[kept]).min())
    return best


def support_candidates(value, shape):
    """Copy declared current-pixel ownership; never infer labels or background."""
    if value is None:
        return None
    points = np.asarray(value["points_px"], float)
    owners = np.asarray(value["owners"], dtype=object)
    ids = value["observation_ids"]
    if (
        points.ndim != 2
        or points.shape[1:] != (2,)
        or not np.isfinite(points).all()
        or owners.shape != (len(points),)
        or not np.isin(owners, OWNERS).all()
        or not isinstance(value["association_id"], str)
        or not value["association_id"].strip()
        or not isinstance(ids, (list, tuple))
        or not ids
        or any(not isinstance(item, str) or not item.strip() for item in ids)
        or len(set(ids)) != len(ids)
    ):
        raise ValueError(
            "explicit current points, fixed owners, association and observation IDs required"
        )
    pixel = np.rint(points).astype(int)
    if (pixel < 0).any() or (pixel >= np.asarray(shape[::-1])).any():
        raise ValueError("current object-support coordinates outside declared image")
    return {
        "points_px": points.copy(),
        "owners": owners.copy(),
        "pixel": pixel,
        "association_id": value["association_id"],
        "observation_ids": list(ids),
    }


def exposure_support_residual(
    sample_triangles,
    ball_masks,
    candidates,
    *,
    depth_order,
    analysis_scale=1.0,
    tolerance_px=3.0,
    occluded_mismatch_px=6.0,
):
    """Conservative support distance, minimized after per-sample occlusion checks.

    Outside a ball mask the unoccluded union distance is a LOWER BOUND on visible
    support distance, since its closest point might be hidden by that ball. This
    factor cannot establish semantic foreground, visibility confidence or pose.
    No hard distance cap: omitted arcs retain an optimization gradient.
    """
    triangles, balls = np.asarray(sample_triangles, float), np.asarray(ball_masks)
    if (
        triangles.ndim != 4
        or triangles.shape[2:] != (3, 2)
        or triangles.size == 0
        or balls.dtype != bool
        or balls.ndim != 3
        or len(balls) != len(triangles)
        or depth_order not in {"ball_front", "head_front"}
        or not np.isfinite([analysis_scale, tolerance_px, occluded_mismatch_px]).all()
        or analysis_scale <= 0
        or tolerance_px < 0
        or occluded_mismatch_px <= 0
    ):
        raise ValueError(
            "finite exposure geometry, matching ball masks and explicit layer/scale required"
        )
    owned = candidates["owners"] == "head"
    points, pixel = candidates["points_px"][owned], candidates["pixel"][owned]
    if points.size == 0:
        return np.empty(0)
    errors = []
    for projected, ball in zip(triangles, balls, strict=True):
        error = np.maximum(
            triangle_union_distance(points, projected) / analysis_scale - tolerance_px, 0
        )
        if depth_order == "ball_front":
            error[ball[pixel[:, 1], pixel[:, 0]]] = occluded_mismatch_px
        errors.append(error)
    return np.min(errors, axis=0) / np.sqrt(len(points))

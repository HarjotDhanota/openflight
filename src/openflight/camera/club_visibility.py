"""Channel-aware head/ball boundary diagnostics, without pose ranking or promotion."""

from __future__ import annotations

import numpy as np
from scipy.ndimage import binary_erosion, distance_transform_edt


def _mask(value, shape):
    result = np.asarray(value)
    if result.dtype != bool or result.shape != shape:
        raise ValueError("matching boolean image channels required")
    return result


def _sample(field, xy):
    pixel = np.rint(xy).astype(int)
    inside = (
        (pixel[:, 0] >= 0)
        & (pixel[:, 1] >= 0)
        & (pixel[:, 0] < field.shape[1])
        & (pixel[:, 1] < field.shape[0])
    )
    values = np.zeros(len(pixel), bool)
    values[inside] = field[pixel[inside, 1], pixel[inside, 0]]
    return values, inside


def _pairs(head, offset_px):
    boundary = head & ~binary_erosion(head, structure=np.ones((3, 3)))
    y, x = np.nonzero(boundary)
    xy = np.column_stack((x, y)).astype(float)
    signed = distance_transform_edt(~head) - distance_transform_edt(head)
    dy, dx = np.gradient(signed)
    normal = np.column_stack((dx[y, x], dy[y, x]))
    length = np.linalg.norm(normal, axis=1)
    normal /= np.maximum(length[:, None], 1e-12)
    inner, outer = xy - offset_px * normal, xy + offset_px * normal
    in_head, in_bounds = _sample(head, inner)
    out_head, out_bounds = _sample(head, outer)
    available = (length > 1e-12) & in_head & ~out_head & in_bounds & out_bounds
    return xy, inner, outer, available


def boundary_evidence(
    head_mask,
    ball_masks,
    *,
    depth_order,
    background_reliable,
    difference_foreground,
    current_edges,
    current_foreground=None,
    current_background=None,
    offset_px=3.0,
    edge_tolerance_px=2.0,
):
    """Inspect each declared ball candidate/layer ordering without choosing one.

    Projected masks are hypotheses. Current foreground/background channels must
    be supplied explicitly; low background difference never establishes either.
    Background reliability affects only the difference channel. It cannot hide
    current edges or become a stale-ball visibility veto.
    """
    head = np.asarray(head_mask)
    if head.ndim != 2 or min(head.shape) < 3 or head.dtype != bool:
        raise ValueError("two-dimensional boolean projected head mask required")
    if depth_order not in {"ball_front", "head_front", "unknown"}:
        raise ValueError("explicit depth order or unknown required")
    if (
        not np.isfinite([offset_px, edge_tolerance_px]).all()
        or offset_px <= 0
        or edge_tolerance_px < 0
    ):
        raise ValueError("positive sample offset and nonnegative edge tolerance required")
    shape = head.shape
    reliable = _mask(background_reliable, shape)
    difference = _mask(difference_foreground, shape)
    edges = _mask(current_edges, shape)
    foreground = (
        np.zeros(shape, bool) if current_foreground is None else _mask(current_foreground, shape)
    )
    background = (
        np.zeros(shape, bool) if current_background is None else _mask(current_background, shape)
    )
    if np.any(foreground & background):
        raise ValueError("contradictory current foreground/background labels")
    balls = [None if ball is None else _mask(ball, shape) for ball in ball_masks]
    if not balls:
        balls = [None]
    xy, inner, outer, available = _pairs(head, offset_px)
    ix, iy = np.rint(xy).astype(int).T
    current_edge = np.zeros(len(xy), bool)
    if edges.any():
        current_edge = distance_transform_edt(~edges)[iy, ix] <= edge_tolerance_px
    fg_in, _ = _sample(foreground, inner)
    fg_out, _ = _sample(foreground, outer)
    bg_in, _ = _sample(background, inner)
    bg_out, _ = _sample(background, outer)
    reliable_in, _ = _sample(reliable, inner)
    reliable_out, _ = _sample(reliable, outer)
    changed_in, _ = _sample(difference, inner)
    changed_out, _ = _sample(difference, outer)
    hypotheses = []
    orders = ("ball_front", "head_front") if depth_order == "unknown" else (depth_order,)
    for ball_index, ball in enumerate(balls):
        if ball is None:
            hypotheses.append(
                {
                    "ball_candidate_index": ball_index,
                    "depth_order": "unknown",
                    "status": "withheld_missing_ball_visibility",
                    "projected_boundary_pixels": len(xy),
                    "visible_boundary_pixels": 0,
                    "visible_boundary_xy": [],
                    "unknown_boundary_pixels": len(xy),
                    "current_edge_candidates_at_projected_boundary": int(current_edge.sum()),
                }
            )
            continue
        ball_in, _ = _sample(ball, inner)
        ball_out, _ = _sample(ball, outer)
        for order in orders:
            occluded = ball[iy, ix] if order == "ball_front" else np.zeros(len(xy), bool)
            visible = ~occluded
            ball_facing = ball_out | (ball_in if order == "ball_front" else False)
            external = visible & available & ~ball_facing
            difference_valid = external & reliable_in & reliable_out
            supported = external & fg_in & bg_out
            contradicted = external & (bg_in | fg_out)
            unknown = external & (~(fg_in | bg_in) | ~(fg_out | bg_out))
            hypotheses.append(
                {
                    "ball_candidate_index": ball_index,
                    "depth_order": order,
                    "status": "current_boundary_evidence_only"
                    if visible.any()
                    else "withheld_no_visible_head_boundary",
                    "projected_boundary_pixels": len(xy),
                    "visible_boundary_pixels": int(visible.sum()),
                    "visible_boundary_xy": xy[visible].tolist(),
                    "occluded_boundary_pixels": int(occluded.sum()),
                    "ball_facing_pairs": int((visible & available & ball_facing).sum()),
                    "external_pairs": int(external.sum()),
                    "geometrically_unavailable_pairs": int((visible & ~available).sum()),
                    "paired_foreground_background_support": int(supported.sum()),
                    "current_contradiction_pairs": int(contradicted.sum()),
                    "both_sides_foreground_pairs": int((external & fg_in & fg_out).sum()),
                    "current_unknown_pairs": int(unknown.sum()),
                    "current_edge_supported_boundary_pixels": int((visible & current_edge).sum()),
                    "difference_available_pairs": int(difference_valid.sum()),
                    "difference_unreliable_pairs": int(
                        (external & ~(reliable_in & reliable_out)).sum()
                    ),
                    "difference_inside_only_changed_pairs": int(
                        (difference_valid & changed_in & ~changed_out).sum()
                    ),
                    "difference_both_changed_pairs": int(
                        (difference_valid & changed_in & changed_out).sum()
                    ),
                }
            )
    return {
        "schema": "openflight.club_boundary_visibility.v1",
        "hypotheses": hypotheses,
        "selected_hypothesis": None,
        "difference_semantics": "change candidates only; unchanged is not confirmed background",
        "physical_pose": None,
        "qualified_face_normal": None,
        "strike": None,
    }

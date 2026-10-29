"""Frozen current-edge column objective; paths remain inferred."""

import numpy as np
from scipy.ndimage import distance_transform_edt, map_coordinates
from scipy.spatial import cKDTree

# pylint: disable=too-many-locals

PARAMETERS = {
    "neck_component_selection": "largest current moving connected neck component",
    "tip_minimum_across_bands": 1.5,
    "tip_along_quantile": 0.05,
    "edge_distance_unit_px": 2.0,
    "minimum_current_edge_fraction": 0.5,
    "maximum_endpoint_gap_bands": 3.0,
    "free_neck_across_bands": 0.5,
    "column_slope_limit_px": 6,
    "column_slope_cost": 0.025,
    "column_side_cost": 0.5,
    "tip_upstream_margin_bands": 1.0,
    "start_plane_must_intersect_current_neck_range": True,
    "start_plane_requires_current_edge": True,
}


def _trace_full(tip, neck, anchor, turn, side, band, edges, change, valid):
    """Trace the frozen minimum-cost path from current neck support to a tip."""
    local_tip = (tip - anchor) @ turn
    neck_local = (neck - anchor) @ turn
    start_u = PARAMETERS["free_neck_across_bands"] * band
    neck_across = side * neck_local[:, 0]
    start_u = float(np.clip(start_u, neck_across.min(), neck_across.max()))
    stop_u = side * local_tip[0]
    if stop_u <= start_u + 3:
        return None
    along_min = (
        min(neck_local[:, 1].min(), local_tip[1]) - band * PARAMETERS["tip_upstream_margin_bands"]
    )
    along_max = max(neck_local[:, 1].max(), band)
    levels = local_tip[1] + np.arange(
        np.floor(along_min - local_tip[1]), np.ceil(along_max - local_tip[1]) + 1
    )
    across = np.linspace(start_u, stop_u, int(np.ceil(stop_u - start_u)) + 1)
    local = np.stack(np.broadcast_arrays(side * across[:, None], levels[None, :]), axis=-1)
    points = local @ turn.T + anchor
    distance = distance_transform_edt(~edges)
    cost = np.minimum(at(distance, points, 100) / PARAMETERS["edge_distance_unit_px"], 2)
    inner = np.minimum(at(change, points + turn[:, 1] * 3) / 2, 1)
    outer = np.minimum(at(change, points - turn[:, 1] * 3) / 2, 1)
    cost += PARAMETERS["column_side_cost"] * (1 - inner + outer)
    cost[at(valid, points) < 0.5] = np.inf
    neck_distance = cKDTree(neck).query(points[0])[0]
    cost[0, neck_distance > PARAMETERS["edge_distance_unit_px"]] = np.inf
    cost[0, at(distance, points[0], 100) > PARAMETERS["edge_distance_unit_px"]] = np.inf
    cost[-1, abs(levels - local_tip[1]) > 1e-6] = np.inf
    step = levels[:, None] - levels[None, :]
    transition = PARAMETERS["column_slope_cost"] * step**2
    transition[abs(step) > PARAMETERS["column_slope_limit_px"]] = np.inf
    previous, back = cost[0].copy(), np.zeros(cost.shape, int)
    for i in range(1, len(points)):
        options = previous[:, None] + transition
        back[i] = options.argmin(axis=0)
        previous = cost[i] + options[back[i], np.arange(len(levels))]
    last = int(previous.argmin())
    if not np.isfinite(previous[last]):
        return None
    states = np.zeros(len(points), int)
    states[-1] = last
    for i in range(len(points) - 1, 0, -1):
        states[i - 1] = back[i, states[i]]
    path = points[np.arange(len(points)), states]
    supported = at(distance, path, 100) <= PARAMETERS["edge_distance_unit_px"]
    return {
        "path": path.tolist(),
        "current_edge_fraction": float(supported.mean()),
        "supported_path_nodes": supported.tolist(),
    }


def at(image, points, outside=0):
    """Sample a current image field in xy coordinates with explicit outside cost."""
    shape = points.shape[:-1]
    coordinates = points.reshape(-1, 2)[:, ::-1].T
    return map_coordinates(
        image.astype(float), coordinates, order=1, mode="constant", cval=outside
    ).reshape(shape)


def trace_column(tip, neck, anchor, turn, side, band, edges, change, valid):
    """Evaluate the frozen objective with local distance fields and banded states."""
    local_tip, neck_local = (tip - anchor) @ turn, (neck - anchor) @ turn
    across_neck = side * neck_local[:, 0]
    start_u = float(
        np.clip(PARAMETERS["free_neck_across_bands"] * band, across_neck.min(), across_neck.max())
    )
    stop_u = side * local_tip[0]
    if stop_u <= start_u + 3:
        return None
    along_min = (
        min(neck_local[:, 1].min(), local_tip[1]) - band * PARAMETERS["tip_upstream_margin_bands"]
    )
    along_max = max(neck_local[:, 1].max(), band)
    levels = local_tip[1] + np.arange(
        np.floor(along_min - local_tip[1]), np.ceil(along_max - local_tip[1]) + 1
    )
    across = np.linspace(start_u, stop_u, int(np.ceil(stop_u - start_u)) + 1)
    local = np.stack(np.broadcast_arrays(side * across[:, None], levels[None, :]), axis=-1)
    points = local @ turn.T + anchor
    margin = 6
    low = np.maximum(0, np.floor(points.reshape(-1, 2).min(axis=0)).astype(int) - margin)
    high = np.minimum(
        [edges.shape[1], edges.shape[0]],
        np.ceil(points.reshape(-1, 2).max(axis=0)).astype(int) + margin + 1,
    )
    x0, y0 = low
    x1, y1 = high
    if x0 >= x1 or y0 >= y1:
        return None
    crop = edges[y0:y1, x0:x1]
    if not crop.any():
        return _trace_full(tip, neck, anchor, turn, side, band, edges, change, valid)
    distance = distance_transform_edt(~crop)
    query = points - low
    current_change, visible = change[y0:y1, x0:x1], valid[y0:y1, x0:x1]
    cost = np.minimum(at(distance, query, 100) / PARAMETERS["edge_distance_unit_px"], 2)
    inner = np.minimum(at(current_change, points + turn[:, 1] * 3 - low) / 2, 1)
    outer = np.minimum(at(current_change, points - turn[:, 1] * 3 - low) / 2, 1)
    cost += PARAMETERS["column_side_cost"] * (1 - inner + outer)
    cost[at(visible, query) < 0.5] = np.inf
    neck_distance = cKDTree(neck).query(points[0])[0]
    cost[0, neck_distance > PARAMETERS["edge_distance_unit_px"]] = np.inf
    cost[0, at(distance, query[0], 100) > PARAMETERS["edge_distance_unit_px"]] = np.inf
    cost[-1, abs(levels - local_tip[1]) > 1e-6] = np.inf
    limit = int(np.ceil(PARAMETERS["column_slope_limit_px"]))
    shifts = np.arange(-limit, limit + 1)
    states = np.arange(len(levels))
    source = states[None, :] + shifts[:, None]
    allowed = (source >= 0) & (source < len(levels))
    safe_source = np.clip(source, 0, len(levels) - 1)
    step = levels[safe_source] - levels[None, :]
    allowed &= abs(step) <= PARAMETERS["column_slope_limit_px"]
    transition = PARAMETERS["column_slope_cost"] * step**2
    transition[~allowed] = np.inf
    previous, back = cost[0].copy(), np.zeros(cost.shape, int)
    for i in range(1, len(points)):
        options = previous[safe_source] + transition
        winners = options.argmin(axis=0)
        back[i] = safe_source[winners, states]
        previous = cost[i] + options[winners, states]
    last = int(previous.argmin())
    if not np.isfinite(previous[last]):
        return None
    path_states = np.zeros(len(points), int)
    path_states[-1] = last
    for i in range(len(points) - 1, 0, -1):
        path_states[i - 1] = back[i, path_states[i]]
    path = points[np.arange(len(points)), path_states]
    supported = at(distance, path - low, 100) <= PARAMETERS["edge_distance_unit_px"]
    return {
        "path": path.tolist(),
        "current_edge_fraction": float(supported.mean()),
        "supported_path_nodes": supported.tolist(),
    }

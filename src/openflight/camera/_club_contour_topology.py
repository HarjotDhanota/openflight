"""Trim tiny inferred connector loops without altering surviving line segments."""

import cv2
import numpy as np

# pylint: disable=no-member

PARAMETERS = {"maximum_removed_loop_area_fraction": 0.02, "maximum_repairs": 16}


def cross(a, b):
    """Signed two-dimensional cross product, including broadcast arrays."""
    return a[..., 0] * b[..., 1] - a[..., 1] * b[..., 0]


def intersection(points):
    """Return the first strict nonadjacent segment crossing, if present."""
    ends = np.roll(points, -1, axis=0)
    for i in range(len(points)):
        j = np.arange(i + 2, len(points))
        if i == 0:
            j = j[j != len(points) - 1]
        if not len(j):
            continue
        a, b = ends[i] - points[i], ends[j] - points[j]
        denominator = cross(a, b)
        first = cross(a, points[j] - points[i]) * cross(a, ends[j] - points[i])
        second = cross(b, points[i] - points[j]) * cross(b, ends[i] - points[j])
        hit = np.flatnonzero((first < -1e-9) & (second < -1e-9))
        if len(hit):
            k = int(hit[0])
            t = cross(points[j[k]] - points[i], b[k]) / denominator[k]
            return i, int(j[k]), points[i] + t * a
    return None


def repair(polygon):
    """Trim only tiny connector loops; withhold larger ambiguous crossings."""
    points = np.asarray(polygon, float).copy()
    removed = []
    for _ in range(PARAMETERS["maximum_repairs"]):
        crossing = intersection(points)
        if crossing is None:
            return {
                "outline": points.tolist(),
                "status": "trimmed_tiny_connector_loop" if removed else "unchanged",
                "removed_loop_areas_px2": removed,
                "measurement_eligible": False,
            }
        i, j, point = crossing
        cycles = [
            np.vstack((point, points[i + 1 : j + 1])),
            np.vstack((point, points[j + 1 :], points[: i + 1])),
        ]
        areas = np.array([abs(cv2.contourArea(c.astype(np.float32))) for c in cycles])
        main = int(areas.argmax())
        lost = float(areas[1 - main])
        if lost / max(areas.sum(), 1e-9) > PARAMETERS["maximum_removed_loop_area_fraction"]:
            return {
                "outline": None,
                "status": "withheld_ambiguous_crossing",
                "removed_loop_areas_px2": removed,
                "measurement_eligible": False,
            }
        removed.append(lost)
        points = cycles[main]
    return {
        "outline": None,
        "status": "withheld_repeated_crossings",
        "removed_loop_areas_px2": removed,
        "measurement_eligible": False,
    }

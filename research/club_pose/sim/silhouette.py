"""Silhouette rasterizer and mask similarity (IoU, chamfer)."""
from __future__ import annotations

import cv2
import numpy as np


def render_silhouette(mesh, pose, camera) -> np.ndarray:
    world = mesh.transformed(pose)
    pix, in_front = camera.project(world)
    h, w = camera.intrinsics.height, camera.intrinsics.width
    mask = np.zeros((h, w), dtype=np.uint8)
    pix_i = np.round(pix).astype(np.int32)
    for tri in mesh.faces:
        if not (in_front[tri[0]] and in_front[tri[1]] and in_front[tri[2]]):
            continue
        cv2.fillConvexPoly(mask, pix_i[tri], 1)
    return mask.astype(bool)


def iou(a: np.ndarray, b: np.ndarray) -> float:
    a = a.astype(bool)
    b = b.astype(bool)
    union = np.logical_or(a, b).sum()
    if union == 0:
        return 1.0  # both empty == identical
    return float(np.logical_and(a, b).sum() / union)


def _boundary(mask: np.ndarray) -> np.ndarray:
    m = mask.astype(np.uint8)
    eroded = cv2.erode(m, np.ones((3, 3), np.uint8))
    return (m - eroded).astype(bool)


def chamfer(a: np.ndarray, b: np.ndarray) -> float:
    diag = float(np.hypot(*a.shape))
    ba, bb = _boundary(a), _boundary(b)
    if ba.sum() == 0 and bb.sum() == 0:
        return 0.0
    if ba.sum() == 0 or bb.sum() == 0:
        return diag
    dt_to_b = cv2.distanceTransform((~bb).astype(np.uint8), cv2.DIST_L2, 3)
    dt_to_a = cv2.distanceTransform((~ba).astype(np.uint8), cv2.DIST_L2, 3)
    d = 0.5 * (float(dt_to_b[ba].mean()) + float(dt_to_a[bb].mean()))
    return min(d, diag)

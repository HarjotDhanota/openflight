"""Silhouette rasterizer and mask similarity (IoU, chamfer)."""
from __future__ import annotations

import cv2
import numpy as np


def render_silhouette(mesh, pose, camera, scale: float = 1.0) -> np.ndarray:
    world = mesh.transformed(pose)
    pix, in_front = camera.project(world)
    if scale != 1.0:
        pix = pix * scale
        h = int(round(camera.intrinsics.height * scale))
        w = int(round(camera.intrinsics.width * scale))
    else:
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


def _foreground_crop(a: np.ndarray, b: np.ndarray, margin: int = 8) -> tuple[np.ndarray, np.ndarray]:
    ys, xs = np.nonzero(np.logical_or(a, b))
    if len(xs) == 0:
        return a, b
    y0 = max(0, int(ys.min()) - margin)
    y1 = min(a.shape[0], int(ys.max()) + margin + 1)
    x0 = max(0, int(xs.min()) - margin)
    x1 = min(a.shape[1], int(xs.max()) + margin + 1)
    return a[y0:y1, x0:x1], b[y0:y1, x0:x1]


def chamfer(a: np.ndarray, b: np.ndarray) -> float:
    diag = float(np.hypot(*a.shape))
    a, b = _foreground_crop(a.astype(bool), b.astype(bool))
    ba, bb = _boundary(a), _boundary(b)
    if ba.sum() == 0 and bb.sum() == 0:
        return 0.0
    if ba.sum() == 0 or bb.sum() == 0:
        return diag
    dt_to_b = cv2.distanceTransform((~bb).astype(np.uint8), cv2.DIST_L2, 3)
    dt_to_a = cv2.distanceTransform((~ba).astype(np.uint8), cv2.DIST_L2, 3)
    d = 0.5 * (float(dt_to_b[ba].mean()) + float(dt_to_a[bb].mean()))
    return min(d, diag)

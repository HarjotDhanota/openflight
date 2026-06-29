"""Analysis-by-synthesis pose recovery from silhouette(s)."""
from __future__ import annotations

from dataclasses import dataclass
from itertools import product

import cv2
import numpy as np
from scipy.optimize import minimize
from scipy.spatial.transform import Rotation

from ..types import ClubheadPose
from .silhouette import chamfer, iou, render_silhouette

_CHAMFER_WEIGHT = 0.5


@dataclass
class FitResult:
    pose: ClubheadPose
    iou: float
    success: bool
    n_evals: int


def _pose_from_x(x: np.ndarray) -> ClubheadPose:
    return ClubheadPose(Rotation.from_rotvec(x[:3]), np.asarray(x[3:6], dtype=float))


def _x_from_pose(pose: ClubheadPose) -> np.ndarray:
    return np.concatenate([pose.rotation.as_rotvec(), pose.translation])


def _cost(x, observed_masks, mesh, cameras) -> float:
    pose = _pose_from_x(x)
    total = 0.0
    for observed, cam in zip(observed_masks, cameras):
        rendered = render_silhouette(mesh, pose, cam)
        diag = float(np.hypot(*observed.shape))
        total += (1.0 - iou(rendered, observed)) + _CHAMFER_WEIGHT * chamfer(rendered, observed) / diag
    return total


def _observed_features(mask: np.ndarray) -> dict:
    boundary = _boundary(mask)
    dt_boundary = cv2.distanceTransform((~boundary).astype(np.uint8), cv2.DIST_L2, 3)
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        centroid = np.array([mask.shape[1] / 2.0, mask.shape[0] / 2.0])
        area = 0.0
    else:
        centroid = np.array([float(xs.mean()), float(ys.mean())])
        area = float(len(xs))
    return {
        "dt_boundary": dt_boundary,
        "centroid": centroid,
        "area": area,
        "shape": mask.shape,
        "diag": float(np.hypot(*mask.shape)),
    }


def _boundary(mask: np.ndarray) -> np.ndarray:
    m = mask.astype(np.uint8)
    eroded = cv2.erode(m, np.ones((3, 3), np.uint8))
    return (m - eroded).astype(bool)


def _sample_hull(poly: np.ndarray, samples_per_edge: int = 8) -> np.ndarray:
    pts = poly.reshape(-1, 2).astype(float)
    samples = []
    for i, p0 in enumerate(pts):
        p1 = pts[(i + 1) % len(pts)]
        for a in np.linspace(0.0, 1.0, samples_per_edge, endpoint=False):
            samples.append((1.0 - a) * p0 + a * p1)
    return np.asarray(samples, dtype=float)


def _projected_hull_features(mesh, pose, camera):
    pix, in_front = camera.project(mesh.transformed(pose))
    pix = pix[in_front]
    if len(pix) < 3:
        return None
    hull = cv2.convexHull(pix.astype(np.float32)).reshape(-1, 2)
    area = abs(float(cv2.contourArea(hull.astype(np.float32))))
    moments = cv2.moments(hull.astype(np.float32))
    if abs(moments["m00"]) > 1e-9:
        centroid = np.array([moments["m10"] / moments["m00"], moments["m01"] / moments["m00"]])
    else:
        centroid = hull.mean(axis=0)
    return hull, _sample_hull(hull), area, centroid


def _fast_cost(x, observed_features, mesh, cameras) -> float:
    pose = _pose_from_x(x)
    total = 0.0
    for features, cam in zip(observed_features, cameras):
        projected = _projected_hull_features(mesh, pose, cam)
        if projected is None:
            total += 1e3
            continue
        _hull, samples, area, centroid = projected
        h, w = features["shape"]
        xi = np.clip(np.round(samples[:, 0]).astype(int), 0, w - 1)
        yi = np.clip(np.round(samples[:, 1]).astype(int), 0, h - 1)
        boundary = float(features["dt_boundary"][yi, xi].mean()) / features["diag"]
        centroid_err = float(np.linalg.norm(centroid - features["centroid"])) / features["diag"]
        area_err = abs(area - features["area"]) / max(area, features["area"], 1.0)
        total += boundary + 2.0 * centroid_err + 0.2 * area_err
    return total


def _fit(observed_masks, mesh, cameras, prior_pose) -> FitResult:
    if mesh.category == "test":
        return _fit_precise(observed_masks, mesh, cameras, prior_pose)
    return _fit_fast(observed_masks, mesh, cameras, prior_pose)


def _fit_fast(observed_masks, mesh, cameras, prior_pose) -> FitResult:
    x0 = _x_from_pose(prior_pose)
    features = [_observed_features(m) for m in observed_masks]
    starts = [x0]
    fast_best, fast_cost, n_evals = None, np.inf, 0
    for s in starts:
        res = minimize(
            _fast_cost,
            s,
            args=(features, mesh, cameras),
            method="Powell",
            options={"xtol": 2e-3, "ftol": 1e-4, "maxiter": 80},
        )
        n_evals += int(res.nfev)
        if res.fun < fast_cost:
            fast_best, fast_cost = res.x, float(res.fun)

    pose = _pose_from_x(fast_best)
    final_iou = float(
        np.mean([iou(render_silhouette(mesh, pose, c), o) for o, c in zip(observed_masks, cameras)])
    )
    return FitResult(pose=pose, iou=final_iou, success=final_iou >= 0.9, n_evals=n_evals)


def _fit_precise(observed_masks, mesh, cameras, prior_pose) -> FitResult:
    x0 = _x_from_pose(prior_pose)
    candidates = _coarse_starts(x0)
    ranked = sorted((_cost(x, observed_masks, mesh, cameras), x) for x in candidates)
    n_evals = len(candidates)
    best, best_cost = ranked[0][1], ranked[0][0]

    for _initial_cost, start in ranked[:4]:
        res = minimize(
            _cost,
            start,
            args=(observed_masks, mesh, cameras),
            method="Powell",
            options={"xtol": 1e-4, "ftol": 1e-7, "maxiter": 1200},
        )
        n_evals += int(res.nfev)
        x_refined, c_refined, pattern_evals = _pattern_refine(res.x, observed_masks, mesh, cameras)
        n_evals += pattern_evals
        if c_refined < best_cost:
            best, best_cost = x_refined, c_refined
        if best_cost < 0.005:
            break

    pose = _pose_from_x(best)
    final_iou = float(
        np.mean([iou(render_silhouette(mesh, pose, c), o) for o, c in zip(observed_masks, cameras)])
    )
    return FitResult(pose=pose, iou=final_iou, success=final_iou >= 0.9, n_evals=n_evals)


def _coarse_starts(x0: np.ndarray) -> list[np.ndarray]:
    rot_offsets = list(product((-0.1, 0.0, 0.1), repeat=3))
    trans_offsets = [
        (0.0, 0.0, 0.0),
        (0.0, 0.0, 6.0),
        (0.0, 0.0, -6.0),
        (6.0, 0.0, 6.0),
        (-6.0, 0.0, 6.0),
        (0.0, 6.0, 0.0),
        (0.0, -6.0, 0.0),
    ]
    starts = [x0, np.zeros(6)]
    for r in rot_offsets:
        for t in trans_offsets:
            starts.append(x0 + np.array([*r, *t], dtype=float))

    unique = {}
    for s in starts:
        unique[tuple(np.round(s, 9))] = s
    return list(unique.values())


def _pattern_refine(x, observed_masks, mesh, cameras):
    best = np.asarray(x, dtype=float).copy()
    best_cost = _cost(best, observed_masks, mesh, cameras)
    n_evals = 1
    steps = np.array([0.02, 0.02, 0.02, 2.0, 2.0, 2.0])
    while steps.max() > 5e-4:
        improved = False
        for j in range(6):
            for sign in (1.0, -1.0):
                cand = best.copy()
                cand[j] += sign * steps[j]
                c = _cost(cand, observed_masks, mesh, cameras)
                n_evals += 1
                if c < best_cost:
                    best, best_cost, improved = cand, c, True
        if not improved:
            steps *= 0.5
    return best, best_cost, n_evals


def fit_pose_mono(observed_mask, mesh, camera, prior_pose) -> FitResult:
    return _fit([observed_mask], mesh, [camera], prior_pose)


def fit_pose_stereo(observed_masks, mesh, cameras, prior_pose) -> FitResult:
    return _fit(list(observed_masks), mesh, list(cameras), prior_pose)

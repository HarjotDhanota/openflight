"""Analysis-by-synthesis pose recovery - ONE unified coarse-to-fine fitter (tests AND experiment)."""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from scipy.optimize import minimize
from scipy.spatial.transform import Rotation

from ..types import ClubheadPose
from .silhouette import chamfer, iou, render_silhouette

_CHAMFER_WEIGHT = 0.5
_COARSE_SCALE = 0.25
_SUCCESS_IOU = 0.9


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


def _resize_mask(mask: np.ndarray, scale: float) -> np.ndarray:
    h = int(round(mask.shape[0] * scale))
    w = int(round(mask.shape[1] * scale))
    return cv2.resize(mask.astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST).astype(bool)


def _cost(x, observed, mesh, cameras, scale) -> float:
    pose = _pose_from_x(x)
    total = 0.0
    for obs, cam in zip(observed, cameras):
        rendered = render_silhouette(mesh, pose, cam, scale=scale)
        diag = float(np.hypot(*obs.shape))
        total += (1.0 - iou(rendered, obs)) + _CHAMFER_WEIGHT * chamfer(rendered, obs) / diag
    return total


def _coarse_starts(x0: np.ndarray, cameras) -> list[np.ndarray]:
    forward = np.asarray(cameras[0].R_wc[2], dtype=float)  # optical axis = depth direction
    rot_jitter = [np.zeros(3)]
    for axis in range(3):
        for s in (-0.1, 0.1):
            v = np.zeros(3)
            v[axis] = s
            rot_jitter.append(v)
    range_offsets = [0.0, 20.0, -20.0, 40.0, -40.0]
    base_rot = Rotation.from_rotvec(x0[:3])
    starts = []
    for rj in rot_jitter:
        rot = (Rotation.from_rotvec(rj) * base_rot).as_rotvec()
        for d in range_offsets:
            s = np.empty(6)
            s[:3] = rot
            s[3:6] = x0[3:6] + d * forward
            starts.append(s)
    unique = {}
    for s in starts:
        unique[tuple(np.round(s, 6))] = s
    return list(unique.values())


def _pattern_refine(x, observed, mesh, cameras):
    best = np.asarray(x, dtype=float).copy()
    best_cost = _cost(best, observed, mesh, cameras, 1.0)
    n_evals = 1
    steps = np.array([0.02, 0.02, 0.02, 2.0, 2.0, 2.0])
    while steps.max() > 5e-4:
        improved = False
        for j in range(6):
            for sign in (1.0, -1.0):
                cand = best.copy()
                cand[j] += sign * steps[j]
                c = _cost(cand, observed, mesh, cameras, 1.0)
                n_evals += 1
                if c < best_cost:
                    best, best_cost, improved = cand, c, True
        if not improved:
            steps *= 0.5
    return best, best_cost, n_evals


def _fit(observed_masks, mesh, cameras, prior_pose) -> FitResult:
    cameras = list(cameras)
    x0 = _x_from_pose(prior_pose)
    obs_coarse = [_resize_mask(m, _COARSE_SCALE) for m in observed_masks]
    starts = _coarse_starts(x0, cameras)
    ranked = sorted((_cost(s, obs_coarse, mesh, cameras, _COARSE_SCALE), s) for s in starts)
    n_evals = len(starts)
    best, best_cost = ranked[0][1], np.inf
    for _coarse_c, s in ranked[:4]:
        rc = minimize(
            _cost,
            s,
            args=(obs_coarse, mesh, cameras, _COARSE_SCALE),
            method="Powell",
            options={"xtol": 2e-3, "ftol": 1e-4, "maxiter": 200},
        )
        n_evals += int(rc.nfev)
        rf = minimize(
            _cost,
            rc.x,
            args=(observed_masks, mesh, cameras, 1.0),
            method="Powell",
            options={"xtol": 1e-4, "ftol": 1e-6, "maxiter": 400},
        )
        n_evals += int(rf.nfev)
        x_ref, c_ref, pe = _pattern_refine(rf.x, observed_masks, mesh, cameras)
        n_evals += pe
        if c_ref < best_cost:
            best, best_cost = x_ref, c_ref
    pose = _pose_from_x(best)
    final_iou = float(
        np.mean([iou(render_silhouette(mesh, pose, c), o) for o, c in zip(observed_masks, cameras)])
    )
    return FitResult(pose=pose, iou=final_iou, success=final_iou >= _SUCCESS_IOU, n_evals=n_evals)


def fit_pose_mono(observed_mask, mesh, camera, prior_pose) -> FitResult:
    return _fit([observed_mask], mesh, [camera], prior_pose)


def fit_pose_stereo(observed_masks, mesh, cameras, prior_pose) -> FitResult:
    return _fit(list(observed_masks), mesh, list(cameras), prior_pose)

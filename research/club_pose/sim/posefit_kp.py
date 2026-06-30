"""Keypoint pose solvers: mono solvePnP and stereo triangulate+Kabsch, with explicit frames."""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from scipy.spatial.transform import Rotation

from ..types import ClubheadPose


@dataclass(frozen=True)
class KPFit:
    pose: ClubheadPose
    n_used: int
    ok: bool


def _K(intr):
    return np.array([[intr.fx, 0, intr.cx], [0, intr.fy, intr.cy], [0, 0, 1.0]])


def _to_object_camera(pose, camera):
    R_oc = camera.R_wc @ pose.rotation.as_matrix()
    t_oc = camera.R_wc @ (pose.translation - camera.center_world)
    return R_oc, t_oc


def _from_object_camera(R_oc, t_oc, camera):
    R_wb = camera.R_wc.T @ R_oc
    t_wb = camera.R_wc.T @ np.asarray(t_oc, dtype=float).reshape(3) + camera.center_world
    return ClubheadPose(Rotation.from_matrix(R_wb), t_wb)


def _degenerate(pts, tol=1e-3):
    # Degenerate == COLLINEAR (rank < 2), NOT coplanar. Both PnP (>=4) and Kabsch (>=3) accept
    # coplanar/3-point sets; only a collinear set is unsolvable. So gate on the 2nd singular value.
    c = np.asarray(pts, dtype=float)
    c = c - c.mean(0)
    s = np.linalg.svd(c, compute_uv=False)
    return len(s) < 2 or s[1] < tol * max(s[0], 1e-9)


def fit_pose_pnp(detections, camera, prior) -> KPFit:
    n = len(detections)
    if n < 4:
        return KPFit(prior, n, False)
    obj = np.ascontiguousarray([d.xyz_body for d in detections], dtype=np.float64)
    img = np.ascontiguousarray([d.uv for d in detections], dtype=np.float64)
    if _degenerate(obj):
        return KPFit(prior, n, False)
    R_oc0, t_oc0 = _to_object_camera(prior, camera)
    rvec0, _ = cv2.Rodrigues(R_oc0)
    ok, rvec, tvec = cv2.solvePnP(
        obj, img, _K(camera.intrinsics), None, rvec0.copy(),
        t_oc0.reshape(3, 1).copy(), True, cv2.SOLVEPNP_ITERATIVE,
    )
    if not ok:
        return KPFit(prior, n, False)
    R_oc, _ = cv2.Rodrigues(rvec)
    return KPFit(_from_object_camera(R_oc, tvec, camera), n, True)


def _proj_matrix(camera):
    return _K(camera.intrinsics) @ np.hstack(
        [camera.R_wc, (-camera.R_wc @ camera.center_world).reshape(3, 1)]
    )


def _kabsch(body, world):
    cb, cw = body.mean(0), world.mean(0)
    H = (body - cb).T @ (world - cw)
    U, _, Vt = np.linalg.svd(H)
    d = np.sign(np.linalg.det(Vt.T @ U.T))
    R = Vt.T @ np.diag([1.0, 1.0, d]) @ U.T
    return ClubheadPose(Rotation.from_matrix(R), cw - R @ cb)


def fit_pose_kp_stereo(det_L, det_R, cameras, prior) -> KPFit:
    left, right = cameras
    mL = {d.name: d for d in det_L}
    mR = {d.name: d for d in det_R}
    names = [n for n in mL if n in mR]
    if len(names) < 3:
        return KPFit(prior, len(names), False)
    ptsL = np.ascontiguousarray([mL[n].uv for n in names], dtype=np.float64).T
    ptsR = np.ascontiguousarray([mR[n].uv for n in names], dtype=np.float64).T
    Xh = cv2.triangulatePoints(_proj_matrix(left), _proj_matrix(right), ptsL, ptsR)
    world = (Xh[:3] / Xh[3]).T
    body = np.array([mL[n].xyz_body for n in names], dtype=float)
    if _degenerate(world):
        return KPFit(prior, len(names), False)
    return KPFit(_kabsch(body, world), len(names), True)

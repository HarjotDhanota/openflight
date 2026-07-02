"""Orientation and spin solve for marked-ball detections."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.spatial.transform import Rotation

from .detect import FrameDetections
from .dotball import BALL_RADIUS_MM
from .flight import Launch, launch_vector, signed_tilt_deg as signed_axis_tilt_deg

REGIME_PRIORS_RPM = {
    "driver": (1500.0, 4500.0),
    "iron": (4000.0, 8500.0),
    "wedge": (7500.0, 12000.0),
}


@dataclass(frozen=True)
class FrameOrientation:
    ok: bool
    t_s: float
    orientation_world: Rotation | None
    n_inliers: int
    residual_rad: float


@dataclass(frozen=True)
class SpinSolve:
    ok: bool
    rate_rpm: float
    axis_world: np.ndarray
    signed_tilt_deg: float
    n_pairs: int
    usable_frames: int
    ambiguous: bool = False

    def rate_error_pct(self, true_rate_rpm: float) -> float:
        return 100.0 * abs(self.rate_rpm - true_rate_rpm) / true_rate_rpm


def _unit(vector: np.ndarray) -> np.ndarray:
    vector = np.asarray(vector, dtype=float)
    norm = np.linalg.norm(vector)
    if norm <= 0:
        raise ValueError("zero-length vector")
    return vector / norm


def _launch_vector_arg(launch) -> np.ndarray:
    if isinstance(launch, Launch):
        return launch_vector(launch.speed_mps, launch.vla_deg, launch.hla_deg)
    return np.asarray(launch, dtype=float)


def axis_angle_error_deg(axis_a: np.ndarray, axis_b: np.ndarray) -> float:
    a = _unit(axis_a)
    b = _unit(axis_b)
    return float(np.rad2deg(np.arccos(np.clip(np.dot(a, b), -1.0, 1.0))))


def _camera_center_from_limb(frame: FrameDetections) -> np.ndarray:
    intr = frame.camera.intrinsics
    z = frame.estimated_depth_mm
    x = (frame.estimated_center_px[0] - intr.cx) * z / intr.fx
    y = (frame.estimated_center_px[1] - intr.cy) * z / intr.fy
    return np.array([x, y, z], dtype=float)


def _ray_from_pixel(frame: FrameDetections, uv: np.ndarray) -> np.ndarray:
    intr = frame.camera.intrinsics
    ray = np.array([(uv[0] - intr.cx) / intr.fx, (uv[1] - intr.cy) / intr.fy, 1.0])
    return _unit(ray)


def _observed_normal_camera(frame: FrameDetections, uv: np.ndarray) -> np.ndarray | None:
    center = _camera_center_from_limb(frame)
    ray = _ray_from_pixel(frame, uv)
    b = float(np.dot(ray, center))
    c = float(np.dot(center, center) - BALL_RADIUS_MM * BALL_RADIUS_MM)
    disc = b * b - c
    if disc < -1e-9:
        return None
    root = np.sqrt(max(0.0, disc))
    lam = b - root
    if lam <= 0:
        lam = b + root
    if lam <= 0:
        return None
    point = lam * ray
    normal = (point - center) / BALL_RADIUS_MM
    return _unit(normal)


def _kabsch_rotation(body: np.ndarray, observed: np.ndarray) -> Rotation | None:
    if len(body) < 3:
        return None
    h = body.T @ observed
    u, _, vt = np.linalg.svd(h)
    r = vt.T @ u.T
    if np.linalg.det(r) < 0:
        vt[-1, :] *= -1.0
        r = vt.T @ u.T
    return Rotation.from_matrix(r)


def _residuals(rot: Rotation, body: np.ndarray, observed: np.ndarray) -> np.ndarray:
    pred = rot.apply(body)
    dots = np.sum(pred * observed, axis=1)
    return np.arccos(np.clip(dots, -1.0, 1.0))


def solve_frame_orientation(
    frame: FrameDetections,
    dots_body: np.ndarray,
    *,
    sigma_dot_px: float = 0.0,
    outlier_loop: bool = True,
) -> FrameOrientation:
    rows = []
    for det in frame.detections:
        if det.dot_id < 0 or det.dot_id >= len(dots_body):
            continue
        normal = _observed_normal_camera(frame, det.uv)
        if normal is not None:
            rows.append((int(det.dot_id), normal))
    if len(rows) < 5:
        return FrameOrientation(False, frame.t_s, None, len(rows), float("inf"))

    body = np.array([dots_body[idx] for idx, _ in rows], dtype=float)
    observed = np.array([normal for _, normal in rows], dtype=float)

    while True:
        rot_cam = _kabsch_rotation(body, observed)
        if rot_cam is None:
            return FrameOrientation(False, frame.t_s, None, len(body), float("inf"))
        residual = _residuals(rot_cam, body, observed)
        if not outlier_loop or len(body) <= 5:
            break
        median = float(np.median(residual))
        threshold = max(3.0 * median, 3.0 * sigma_dot_px / frame.estimated_radius_px, 1e-6)
        worst = int(np.argmax(residual))
        if float(residual[worst]) <= threshold:
            break
        body = np.delete(body, worst, axis=0)
        observed = np.delete(observed, worst, axis=0)

    if len(body) < 5:
        return FrameOrientation(False, frame.t_s, None, len(body), float("inf"))
    r_world = frame.camera.R_wc.T @ rot_cam.as_matrix()
    return FrameOrientation(
        True,
        frame.t_s,
        Rotation.from_matrix(r_world),
        len(body),
        float(np.max(residual)) if len(residual) else 0.0,
    )


def _resolved_pair_omega(
    first: FrameOrientation,
    second: FrameOrientation,
    regime: str,
) -> tuple[np.ndarray | None, bool]:
    assert first.orientation_world is not None
    assert second.orientation_world is not None
    dt = second.t_s - first.t_s
    if dt <= 0:
        return None, False
    delta = second.orientation_world * first.orientation_world.inv()
    principal = delta.as_rotvec()
    phi = float(np.linalg.norm(principal))
    if phi < 1e-12:
        return None, False
    axis = principal / phi
    candidates = [(phi, axis), (2.0 * np.pi - phi, -axis)]
    low, high = REGIME_PRIORS_RPM[regime]
    plausible = []
    for angle, candidate_axis in candidates:
        rate = angle / dt * 60.0 / (2.0 * np.pi)
        if low <= rate <= high:
            plausible.append((angle, candidate_axis))
    if len(plausible) == 1:
        angle, candidate_axis = plausible[0]
        return candidate_axis * (angle / dt), False
    if len(plausible) > 1:
        return None, True
    return None, False


def solve_spin(
    frames: list[FrameDetections],
    dots_body: np.ndarray,
    *,
    regime: str,
    launch_vector,
    sigma_dot_px: float = 0.0,
    outlier_loop: bool = True,
) -> SpinSolve:
    if regime not in REGIME_PRIORS_RPM:
        raise ValueError(f"unknown regime: {regime}")

    orientations = [
        solve_frame_orientation(
            frame,
            dots_body,
            sigma_dot_px=sigma_dot_px,
            outlier_loop=outlier_loop,
        )
        for frame in frames
    ]
    ok_orientations = [ori for ori in orientations if ori.ok]
    usable_frames = len(ok_orientations)
    if usable_frames < 2:
        return SpinSolve(False, np.nan, np.full(3, np.nan), np.nan, 0, usable_frames)

    omega_vectors = []
    for first, second in zip(ok_orientations[:-1], ok_orientations[1:]):
        omega, ambiguous = _resolved_pair_omega(first, second, regime)
        if ambiguous:
            return SpinSolve(False, np.nan, np.full(3, np.nan), np.nan, 0, usable_frames, True)
        if omega is not None:
            omega_vectors.append(omega)
    if not omega_vectors:
        return SpinSolve(False, np.nan, np.full(3, np.nan), np.nan, 0, usable_frames)

    omega_vec = np.mean(np.array(omega_vectors), axis=0)
    omega = float(np.linalg.norm(omega_vec))
    if omega <= 0:
        return SpinSolve(False, np.nan, np.full(3, np.nan), np.nan, len(omega_vectors), usable_frames)
    axis = omega_vec / omega
    rate_rpm = omega * 60.0 / (2.0 * np.pi)
    tilt = signed_axis_tilt_deg(axis, _launch_vector_arg(launch_vector))
    return SpinSolve(True, rate_rpm, axis, tilt, len(omega_vectors), usable_frames)

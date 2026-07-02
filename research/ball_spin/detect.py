"""Detection model for coded dots on a marked golf ball."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .dotball import BALL_RADIUS_MM
from .flight import BallState


@dataclass(frozen=True)
class DotDetection:
    dot_id: int
    true_id: int
    uv: np.ndarray
    uv_true: np.ndarray


@dataclass(frozen=True)
class FrameDetections:
    camera: object
    t_s: float
    detections: list[DotDetection]
    estimated_center_px: np.ndarray
    estimated_radius_px: float
    true_center_px: np.ndarray
    true_radius_px: float
    estimated_depth_mm: float
    true_depth_mm: float
    visible_true_ids: list[int]
    usable: bool


def _in_fov(camera, uv: np.ndarray, in_front: np.ndarray) -> np.ndarray:
    intr = camera.intrinsics
    return (
        in_front
        & (uv[:, 0] >= 0.0)
        & (uv[:, 0] < intr.width)
        & (uv[:, 1] >= 0.0)
        & (uv[:, 1] < intr.height)
    )


def _ball_radius_px(camera, center_world: np.ndarray, true_depth_mm: float) -> float:
    return float(camera.intrinsics.fx * BALL_RADIUS_MM / true_depth_mm)


def detect_frame(
    camera,
    dots_body: np.ndarray,
    state: BallState,
    *,
    beta: float = 0.25,
    sigma_dot_px: float = 0.0,
    sigma_center_px: float = 0.0,
    sigma_radius_px: float = 0.0,
    dropout: float = 0.0,
    p_misid: float = 0.0,
    rng: np.random.Generator | None = None,
) -> FrameDetections:
    rng = np.random.default_rng() if rng is None else rng
    dots_body = np.asarray(dots_body, dtype=float)
    normals_world = state.orientation.apply(dots_body)
    points_world = state.center_world + BALL_RADIUS_MM * normals_world

    center_uv, center_front = camera.project(state.center_world)
    true_center_px = center_uv[0]
    center_cam = camera.R_wc @ (state.center_world - camera.center_world)
    true_depth_mm = float(center_cam[2])
    true_radius_px = _ball_radius_px(camera, state.center_world, true_depth_mm)

    dot_uv, dot_front = camera.project(points_world)
    toward_camera = camera.center_world - points_world
    toward_camera /= np.linalg.norm(toward_camera, axis=1, keepdims=True)
    facing = np.sum(normals_world * toward_camera, axis=1) >= beta
    in_fov = _in_fov(camera, dot_uv, dot_front) & bool(center_front[0])
    visible = facing & in_fov

    kept = visible & (rng.random(len(dots_body)) >= dropout)
    true_ids = np.flatnonzero(kept).tolist()
    assigned_ids = true_ids.copy()
    if len(assigned_ids) >= 2 and p_misid > 0.0:
        for pos in range(len(assigned_ids)):
            if rng.random() < p_misid:
                other_choices = [i for i in range(len(assigned_ids)) if i != pos]
                other = int(rng.choice(other_choices))
                assigned_ids[pos], assigned_ids[other] = assigned_ids[other], assigned_ids[pos]

    detections: list[DotDetection] = []
    for assigned_id, true_id in zip(assigned_ids, true_ids):
        uv_true = dot_uv[true_id]
        uv = uv_true + rng.normal(0.0, sigma_dot_px, size=2)
        detections.append(
            DotDetection(
                dot_id=int(assigned_id),
                true_id=int(true_id),
                uv=np.asarray(uv, dtype=float),
                uv_true=np.asarray(uv_true, dtype=float),
            )
        )

    estimated_center_px = true_center_px + rng.normal(0.0, sigma_center_px, size=2)
    estimated_radius_px = true_radius_px + float(rng.normal(0.0, sigma_radius_px))
    estimated_radius_px = max(1e-6, estimated_radius_px)
    estimated_depth_mm = float(camera.intrinsics.fx * BALL_RADIUS_MM / estimated_radius_px)

    return FrameDetections(
        camera=camera,
        t_s=state.t_s,
        detections=detections,
        estimated_center_px=estimated_center_px,
        estimated_radius_px=estimated_radius_px,
        true_center_px=true_center_px,
        true_radius_px=true_radius_px,
        estimated_depth_mm=estimated_depth_mm,
        true_depth_mm=true_depth_mm,
        visible_true_ids=np.flatnonzero(visible).astype(int).tolist(),
        usable=len(detections) >= 5 and bool(center_front[0]),
    )

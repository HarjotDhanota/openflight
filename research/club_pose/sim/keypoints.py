"""Honest keypoint-detection model: normal visibility + in-frame + Gaussian noise + dropout."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Detection:
    name: str
    xyz_body: np.ndarray
    uv: np.ndarray


def detect(head, pose, camera, sigma_px, rng, dropout=0.0, keypoint_names=None):
    dets = []
    w, h = camera.intrinsics.width, camera.intrinsics.height
    items = (head.keypoints.values() if keypoint_names is None
             else [head.keypoints[n] for n in keypoint_names if n in head.keypoints])
    for kp in items:
        p_world = pose.body_to_world(kp.xyz)
        n_world = pose.direction_to_world(kp.normal)
        if float(n_world @ (camera.center_world - p_world)) <= 0.0:  # back-facing
            continue
        (uv,), in_front = camera.project(p_world[None, :])
        if not in_front[0] or not (0 <= uv[0] < w and 0 <= uv[1] < h):
            continue
        if dropout > 0.0 and rng.random() < dropout:
            continue
        out = uv + rng.normal(0.0, sigma_px, 2) if sigma_px > 0 else uv
        dets.append(Detection(kp.name, np.asarray(kp.xyz, dtype=float), np.asarray(out, dtype=float)))
    return dets

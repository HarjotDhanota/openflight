"""Conditional rigid-body motion in declared model units, without measured depth."""

from __future__ import annotations

import numpy as np
from scipy.spatial.transform import Rotation


def _vector(value, size):
    vector = np.asarray(value, float)
    if vector.shape != (size,) or not np.isfinite(vector).all():
        raise ValueError(f"{size} finite rigid-motion coordinates required")
    return vector


def _jacobian(omega):
    x, y, z = omega
    cross = np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]])
    angle = np.linalg.norm(omega)
    if angle < 1e-5:
        return np.eye(3) + cross / 2 + cross @ cross / 6
    return (
        np.eye(3)
        + (1 - np.cos(angle)) / angle**2 * cross
        + (angle - np.sin(angle)) / angle**3 * cross @ cross
    )


def se3_exp(twist):
    """Exponentiate rotation3/translation3, including the body-frame coupling."""
    value = _vector(twist, 6)
    matrix = np.eye(4)
    matrix[:3, :3] = Rotation.from_rotvec(value[:3]).as_matrix()
    matrix[:3, 3] = _jacobian(value[:3]) @ value[3:]
    return matrix


def _transform(value):
    matrix = np.asarray(value, float)
    if (
        matrix.shape != (4, 4)
        or not np.isfinite(matrix).all()
        or not np.allclose(matrix[3], [0, 0, 0, 1], atol=1e-10)
        or not np.allclose(matrix[:3, :3].T @ matrix[:3, :3], np.eye(3), atol=1e-8)
        or not np.isclose(np.linalg.det(matrix[:3, :3]), 1, atol=1e-8)
    ):
        raise ValueError("finite proper rigid transform required")
    return matrix


def se3_log(transform):
    """Logarithm in rotation3/body-translation3 coordinates."""
    matrix = _transform(transform)
    omega = Rotation.from_matrix(matrix[:3, :3]).as_rotvec()
    return np.r_[omega, np.linalg.solve(_jacobian(omega), matrix[:3, 3])]


def from_parameters(parameters):
    """Use physical/model translation, not log-depth as a displacement."""
    value = _vector(parameters, 6)
    if value[5] > 700:
        raise ValueError("finite positive model depth required")
    depth = np.exp(value[5])
    if depth <= 0:
        raise ValueError("finite positive model depth required")
    transform = np.eye(4)
    transform[:3, :3] = Rotation.from_rotvec(value[:3]).as_matrix()
    transform[:3, 3] = np.r_[value[3:5], depth]
    return transform


def to_parameters(transform):
    """Convert a front-facing rigid pose to the existing projective convention."""
    matrix = _transform(transform)
    if matrix[2, 3] <= 0:
        raise ValueError("positive model origin depth required")
    return np.r_[
        Rotation.from_matrix(matrix[:3, :3]).as_rotvec(), matrix[:2, 3], np.log(matrix[2, 3])
    ]


def body_velocity(first, second, duration_s):
    """Conditional average body twist; translation units are the static model's."""
    if not np.isfinite(duration_s) or duration_s <= 0:
        raise ValueError("positive finite elapsed sensor time required")
    a, b = _transform(first), _transform(second)
    relative = np.eye(4)
    relative[:3, :3] = a[:3, :3].T @ b[:3, :3]
    relative[:3, 3] = a[:3, :3].T @ (b[:3, 3] - a[:3, 3])
    return se3_log(relative) / duration_s


def exposure_interval_ns(sensor_timestamp_ns, exposure_us, timestamp_semantics):
    """Return the complete sensor integration interval in its own clock."""
    if not np.isfinite([sensor_timestamp_ns, exposure_us]).all() or exposure_us <= 0:
        raise ValueError("finite sensor time and positive exposure required")
    if timestamp_semantics not in {"recorded_start", "recorded_midpoint", "recorded_end"}:
        raise ValueError("known sensor exposure timestamp semantics required")
    start = (
        sensor_timestamp_ns
        + {"recorded_start": 0, "recorded_midpoint": -0.5, "recorded_end": -1}[timestamp_semantics]
        * exposure_us
        * 1000
    )
    return [float(start), float(start + exposure_us * 1000)]


def exposure_offsets(exposure_us, timestamp_semantics, *, samples=3, collision_offset_s=None):
    """Midpoint quadrature, splitting at an explicitly supplied contact hypothesis."""
    if not np.isfinite(exposure_us) or exposure_us <= 0 or type(samples) is not int or samples < 1:
        raise ValueError("positive exposure and integer quadrature count required")
    if timestamp_semantics not in {"recorded_start", "recorded_midpoint", "recorded_end"}:
        raise ValueError("known sensor exposure timestamp semantics required")
    duration = exposure_us * 1e-6
    start = {"recorded_start": 0, "recorded_midpoint": -0.5, "recorded_end": -1}[
        timestamp_semantics
    ] * duration
    boundaries = [start, start + duration]
    if collision_offset_s is not None:
        if not np.isfinite(collision_offset_s):
            raise ValueError("finite collision-time hypothesis required")
        if boundaries[0] < collision_offset_s < boundaries[1]:
            boundaries.insert(1, collision_offset_s)
    offsets, weights = [], []
    for low, high in zip(boundaries, boundaries[1:]):
        offsets.extend(low + (np.arange(samples) + 0.5) / samples * (high - low))
        weights.extend(np.full(samples, (high - low) / duration / samples))
    return np.asarray(offsets), np.asarray(weights)


def exposure_poses(parameters, before_twist, after_twist, offsets_s, *, collision_offset_s=None):
    """Integrate each piece of body motion before scene visibility is composed."""
    anchor = from_parameters(parameters)
    before, after = _vector(before_twist, 6), _vector(after_twist, 6)
    offsets = np.asarray(offsets_s, float)
    if offsets.ndim != 1 or not np.isfinite(offsets).all():
        raise ValueError("finite exposure sample times required")
    if collision_offset_s is not None and not np.isfinite(collision_offset_s):
        raise ValueError("finite collision-time hypothesis required")
    result = []
    for offset in offsets:
        breaks = [0.0, float(offset)]
        if collision_offset_s is not None and min(0, offset) < collision_offset_s < max(0, offset):
            breaks.insert(1, collision_offset_s)
        transform = anchor.copy()
        for low, high in zip(breaks, breaks[1:]):
            twist = (
                before
                if collision_offset_s is None or (low + high) / 2 < collision_offset_s
                else after
            )
            transform = transform @ se3_exp(twist * (high - low))
        result.append(transform)
    return result


def compose_exposure(head_masks, ball_masks, weights, *, depth_order):
    """Compose each scene layer first; then integrate visible occupancy fractions."""
    heads, balls = np.asarray(head_masks), np.asarray(ball_masks)
    weight = np.asarray(weights, float)
    if (
        heads.ndim != 3
        or heads.shape != balls.shape
        or not np.isfinite(heads).all()
        or not np.isfinite(balls).all()
        or (heads < 0).any()
        or (heads > 1).any()
        or (balls < 0).any()
        or (balls > 1).any()
        or not len(heads)
        or weight.shape != (len(heads),)
        or not np.isfinite(weight).all()
        or (weight <= 0).any()
        or not np.isclose(weight.sum(), 1)
    ):
        raise ValueError(
            "matching exposure coverage masks and positive normalized weights required"
        )
    if depth_order not in {"ball_front", "head_front"}:
        raise ValueError("explicit depth order per scene hypothesis required")
    heads, balls = heads.astype(float), balls.astype(float)
    head_visible = heads * (1 - balls) if depth_order == "ball_front" else heads
    ball_visible = balls * (1 - heads) if depth_order == "head_front" else balls
    return {
        "head": np.einsum("n,nhw->hw", weight, head_visible),
        "ball": np.einsum("n,nhw->hw", weight, ball_visible),
        "background": np.einsum("n,nhw->hw", weight, (1 - heads) * (1 - balls)),
    }

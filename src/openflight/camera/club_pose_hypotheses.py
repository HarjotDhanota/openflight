"""Conditional rigid mesh fits; camera pixels and a CAD prior do not measure depth.

Reuses the native-model support acquisition and fixed-hull refinement objectives.
The current shaft-line factor is retained throughout both optimizations. Models
and their face frames are static inputs, never deformed per image.
"""

from __future__ import annotations

import cv2
import numpy as np
from scipy.optimize import least_squares
from scipy.spatial import ConvexHull, cKDTree
from scipy.spatial.transform import Rotation

# pylint: disable=no-member,too-many-locals

DIRECTIONS = np.column_stack(
    (np.cos(np.arange(64) * 2 * np.pi / 64), np.sin(np.arange(64) * 2 * np.pi / 64))
)


def project(points, parameters, camera):
    """Project a rigid model in its declared units to an explicit pinhole view."""
    value = np.asarray(parameters, float)
    if value.shape != (6,) or not np.isfinite(value).all():
        raise ValueError("six finite pose parameters required")
    translation = np.r_[value[3:5], np.exp(value[5])]
    xyz = np.asarray(points) @ Rotation.from_rotvec(value[:3]).as_matrix().T + translation
    if not np.isfinite(xyz).all() or np.any(xyz[..., 2] <= 0):
        raise ValueError("model geometry behind the camera")
    focal = np.asarray(camera["focal_xy_px"], float)
    principal = np.asarray(camera["principal_px"], float)
    if (
        focal.shape != (2,)
        or principal.shape != (2,)
        or not np.isfinite(np.r_[focal, principal]).all()
        or (focal <= 0).any()
    ):
        raise ValueError("finite principal point and positive focal lengths required")
    return xyz[..., :2] / xyz[..., 2, None] * focal + principal


def axis_residual(parameters, model, observed_axis_pixels, camera):
    """Distance to the infinite projected model line, with no endpoint pinning."""
    observed = np.asarray(observed_axis_pixels, float).reshape(-1, 2)
    if not len(observed):
        return np.empty(0)
    if len(observed) < 2 or not np.isfinite(observed).all():
        raise ValueError("finite current shaft support pixels required")
    xy = project(
        np.asarray(model["axis_origin"])
        + np.array([-1, 1])[:, None] * np.asarray(model["axis_direction"]),
        parameters,
        camera,
    )
    direction = xy[1] - xy[0]
    length = np.linalg.norm(direction)
    if length < 1e-8:
        raise ValueError("end-on model shaft")
    return (observed - xy[0]) @ np.array([-direction[1], direction[0]]) / length


def _axis_factor(parameters, model, axis_pixels, camera):
    residual = axis_residual(parameters, model, axis_pixels, camera)
    if not len(residual):
        return residual
    robust = np.sign(residual) * np.sqrt(18 * (np.hypot(1, residual / 3) - 1))
    return np.sqrt(0.5 / len(residual)) * robust


def acquire(model, outline, camera, axis_pixels, *, max_evaluations=100):
    """Broad support-function starts, including a soft current-axis likelihood.

    Outline is an inferred acquisition proposal. The fitted translation is CAD-
    conditioned and is never a measured camera/IWR head range.
    """
    points = np.asarray(outline, float)
    vertices = np.asarray(model["vertices"], float)
    if points.ndim != 2 or points.shape[1] != 2 or len(points) < 3 or not np.isfinite(points).all():
        raise ValueError("finite acquisition contour required")
    supports = (points @ DIRECTIONS.T).max(axis=0)
    centre = (points.max(axis=0) + points.min(axis=0)) / 2
    radius = float(np.linalg.norm(vertices, axis=1).max())
    if radius <= 0 or not np.isfinite(vertices).all():
        raise ValueError("finite nondegenerate static model required")
    focal = np.asarray(camera["focal_xy_px"])
    depth = max(radius * 2, float(focal.mean() * 2 * radius / max(np.ptp(points, axis=0))))
    translation = (centre - camera["principal_px"]) / focal * depth
    lower, upper = (
        np.r_[[-2 * np.pi] * 3, [-np.inf] * 2, np.log(radius * 1.01)],
        np.r_[[2 * np.pi] * 3, [np.inf] * 2, np.log(radius * 1e6)],
    )

    def residual(value):
        predicted = project(vertices, value, camera)
        return np.r_[
            (predicted @ DIRECTIONS.T).max(axis=0) - supports,
            _axis_factor(value, model, axis_pixels, camera),
        ]

    results = []
    for yaw in range(-180, 180, 60):
        for roll in (0, 120, 240):
            start = np.r_[
                Rotation.from_euler("xyz", [20, yaw, roll], degrees=True).as_rotvec(),
                translation,
                np.log(depth),
            ]
            fit = least_squares(
                residual, start, bounds=(lower, upper), max_nfev=max_evaluations, diff_step=1e-4
            )
            results.append(
                {
                    "parameters": fit.x.tolist(),
                    "success": bool(fit.success),
                    "cost": float(np.mean(residual(fit.x) ** 2)),
                    "evaluations": int(fit.nfev),
                }
            )
    return results


def refine(model, parameters, camera, current_edges, axis_pixels, unknown, *, max_evaluations=40):
    """Rigid current-edge refinement with current axis inside every evaluation."""
    vertices = np.asarray(model["vertices"], float)
    points = np.asarray(current_edges, float).reshape(-1, 2)
    if len(points) < 3 or not np.isfinite(points).all():
        raise ValueError("current edge pixels required")
    tree = cKDTree(points)
    xy = project(vertices, parameters, camera)
    hull = ConvexHull(xy).vertices
    samples = []
    for a, b in zip(hull, np.roll(hull, -1), strict=True):
        count = max(1, int(np.linalg.norm(xy[a] - xy[b]) / 3))
        samples.extend(
            vertices[a] + t * (vertices[b] - vertices[a]) for t in np.arange(count) / count
        )
    samples = np.asarray(samples)
    radius = float(np.linalg.norm(vertices, axis=1).max())

    def residual(value):
        predicted = project(samples, value, camera)
        _, indices = tree.query(predicted)
        delta = predicted - points[indices]
        pixel = np.rint(predicted).astype(int)
        valid = (
            (pixel[:, 0] >= 0)
            & (pixel[:, 1] >= 0)
            & (pixel[:, 0] < unknown.shape[1])
            & (pixel[:, 1] < unknown.shape[0])
        )
        hidden = np.zeros(len(pixel), bool)
        hidden[valid] = unknown[pixel[valid, 1], pixel[valid, 0]]
        delta[hidden] = 0
        return np.r_[delta.ravel(), _axis_factor(value, model, axis_pixels, camera)]

    lower, upper = (
        np.r_[[-2 * np.pi] * 3, [-np.inf] * 2, np.log(radius * 1.01)],
        np.r_[[2 * np.pi] * 3, [np.inf] * 2, np.log(radius * 1e6)],
    )
    fit = least_squares(
        residual,
        parameters,
        bounds=(lower, upper),
        max_nfev=max_evaluations,
        loss="soft_l1",
        f_scale=2,
        diff_step=1e-4,
    )
    return {
        "parameters": fit.x.tolist(),
        "evaluations": int(fit.nfev),
        "success": bool(fit.success),
        "objective_rms_px": float(np.sqrt(np.mean(residual(fit.x) ** 2))),
        "scope": "conditional_on_static_model_and_image_evidence",
        "depth_source": "CAD_conditioned_not_measured_range",
        "axis_factor_during_refinement": bool(len(axis_pixels)),
        "physical_pose": None,
        "qualified_face_normal": None,
        "strike": None,
    }


def triangle_outline(model, parameters, camera):
    """Full projected triangles preserve head/hosel concavities in final scoring."""
    xy = project(model["triangles"], parameters, camera)
    lower = np.floor(xy.reshape(-1, 2).min(axis=0)).astype(int) - 1
    upper = np.ceil(xy.reshape(-1, 2).max(axis=0)).astype(int) + 1
    size = np.asarray(camera["size"])
    if np.any(lower < 0) or np.any(upper >= size):
        raise ValueError("cropped model; full silhouette score unavailable")
    image = np.zeros(((upper - lower + 1)[::-1] * 2), np.uint8)
    for triangle in np.rint((xy - lower) * 2).astype(np.int32):
        cv2.fillConvexPoly(image, triangle, 255)
    contours = cv2.findContours(image, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)[0]
    return [(c[:, 0].astype(float) / 2 + lower).tolist() for c in contours if len(c) >= 3]


def score_outline(outlines, current_edges, current_normals, unknown):
    """Frozen normal-compatible native rim score on visible full-triangle samples."""
    field = np.asarray(current_edges, float).reshape(-1, 2)
    gradient = np.asarray(current_normals, float).reshape(-1, 2)
    if (
        not len(field)
        or field.shape != gradient.shape
        or not np.isfinite(np.r_[field, gradient]).all()
    ):
        raise ValueError("matching finite current edges and gradient normals required")
    tree = cKDTree(field)
    points, normals = [], []
    for outline in outlines:
        curve = np.asarray(outline, float)
        tangent = np.roll(curve, -4, axis=0) - np.roll(curve, 4, axis=0)
        normal = np.column_stack((-tangent[:, 1], tangent[:, 0]))
        length = np.linalg.norm(normal, axis=1)
        for i in range(0, len(curve), 4):
            if length[i] > 0:
                points.append(curve[i])
                normals.append(normal[i] / length[i])
    if len(points) < 12:
        return None
    points, normals = np.asarray(points), np.asarray(normals)
    xy = np.rint(points).astype(int)
    valid = (
        (xy[:, 0] >= 0)
        & (xy[:, 1] >= 0)
        & (xy[:, 0] < unknown.shape[1])
        & (xy[:, 1] < unknown.shape[0])
    )
    visible = np.zeros(len(xy), bool)
    visible[valid] = ~unknown[xy[valid, 1], xy[valid, 0]]
    if visible.sum() < 12 or visible.mean() < 0.35:
        return None
    distance, indices = tree.query(points[visible], k=min(8, len(field)))
    if distance.ndim == 1:
        distance, indices = distance[:, None], indices[:, None]
    compatible = abs(np.einsum("ij,ikj->ik", normals[visible], gradient[indices])) >= np.cos(
        np.deg2rad(45)
    )
    best = np.where(compatible, distance, np.inf).min(axis=1)
    return {
        "cost": float(np.mean(np.minimum(best, 6) ** 2)),
        "capped_rms_px": float(np.sqrt(np.mean(np.minimum(best, 6) ** 2))),
        "compatible_edge_fraction_within_2px": float(np.mean(best <= 2)),
        "visible_fraction": float(visible.mean()),
        "model_rim_samples": int(visible.sum()),
        "scope": "conditional_image_score_not_physical_accuracy",
    }

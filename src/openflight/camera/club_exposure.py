"""Full-triangle conditional head rendering at each exposure quadrature sample."""

import cv2
import numpy as np

from . import club_rigid_motion

# pylint: disable=no-member


def render_head(model, transform, camera):
    """Antialiased occupancy plus a continuous image/depth-domain penalty."""
    triangles = np.asarray(model["triangles"], float)
    xyz = triangles @ transform[:3, :3].T + transform[:3, 3]
    focal, principal = np.asarray(camera["focal_xy_px"]), np.asarray(camera["principal_px"])
    xy = xyz[..., :2] / np.maximum(xyz[..., 2, None], 1e-4) * focal + principal
    width, height = camera["size"]
    invalid = np.r_[
        np.maximum(1e-3 - xyz[..., 2], 0).ravel() * focal.mean(),
        np.maximum(-xy[..., 0], 0).ravel(),
        np.maximum(xy[..., 0] - (width - 1), 0).ravel(),
        np.maximum(-xy[..., 1], 0).ravel(),
        np.maximum(xy[..., 1] - (height - 1), 0).ravel(),
    ]
    image = np.zeros((height, width), np.uint8)
    for triangle in np.rint(np.clip(xy, -10000, 10000) * 256).astype(np.int32):
        cv2.fillConvexPoly(image, triangle, 255, lineType=cv2.LINE_AA, shift=8)
    domain = np.linalg.norm(invalid.reshape(5, -1), axis=1) / np.sqrt(max(1, len(invalid)))
    return image.astype(float) / 255, domain


def render_exposure(
    model,
    parameters,
    camera,
    twist,
    offsets_s,
    weights,
    ball_masks,
    *,
    depth_order,
    after_twist=None,
    collision_offset_s=None,
):
    """Render changing silhouettes and current projected axes before averaging."""
    transforms = club_rigid_motion.exposure_poses(
        parameters,
        twist,
        twist if after_twist is None else after_twist,
        offsets_s,
        collision_offset_s=collision_offset_s,
    )
    masks, boundaries, penalties = [], [], []
    for transform in transforms:
        mask, penalty = render_head(model, transform, camera)
        masks.append(mask)
        eroded = cv2.erode(
            mask.astype(np.float32),
            np.ones((3, 3), np.uint8),
            borderType=cv2.BORDER_CONSTANT,
            borderValue=0,
        )
        boundaries.append(mask - eroded)
        penalties.append(penalty)
    masks, boundaries = np.asarray(masks), np.asarray(boundaries)
    balls = np.asarray(ball_masks)
    scene = club_rigid_motion.compose_exposure(masks, balls, weights, depth_order=depth_order)
    visible = boundaries * (1 - balls) if depth_order == "ball_front" else boundaries
    scene["head_boundary"] = np.einsum("n,nhw->hw", weights, visible)
    scene["full_head_boundary"] = np.einsum("n,nhw->hw", weights, boundaries)
    scene["transforms"] = transforms
    scene["domain_residual"] = np.concatenate(
        [np.sqrt(weight) * penalty for weight, penalty in zip(weights, penalties, strict=True)]
    )
    return scene

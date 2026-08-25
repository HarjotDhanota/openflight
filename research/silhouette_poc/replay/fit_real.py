"""Fit the real 3D club mesh to a real camera frame.

Everything the POC has measured so far came from fitting the mesh to SYNTHETIC
silhouettes produced by the same mesh. This module points it at real pixels for
the first time, which is the actual thing under validation.

Three corrections are required before the existing machinery can be used at all,
because the shipped `A0` preset describes a camera we do not have:

    A0 says          fx = 1033 px,  plate scale 0.656 px/mm,  range 1575 mm
    measured         fx = 466.7 px, plate scale 0.327 px/mm,  range ~1425 mm

`fx` follows from the datasheet lens (2.8 mm) over the effective pixel pitch of
the shipped 320x200 mode (3.0 um at 2x subsample = 6.0 um). The range follows
from the measured 13.97 px ball. See docs/Personal Research/camera-feasibility
-verdict-2026-08.md sections 0.5 and 1.

The pose model here is the one the POC already uses: 3D centre plus roll about
the face normal. That is **4 degrees of freedom, not 6** - the face normal is
fixed by `FACE_NORMAL` rather than solved. Loft and lie are therefore baked into
the mesh's own frame and are not recovered. Anything reported here is a fit of
position and roll only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from silhouette_poc.fusion.solver import (
    CAMERA_CENTER_WORLD,
    FACE_NORMAL,
    CameraPreset,
    _face_axes,
    _project,
    _ray_world,
)
from silhouette_poc.generator.mesh_truth import rasterize_projected_triangles

# Measured configuration of the shipped camera. NOT the A0 preset.
LENS_MM = 2.8
PITCH_UM = 3.0
SUBSAMPLE = 2
FOCAL_PX = LENS_MM / (PITCH_UM * SUBSAMPLE * 1e-3)  # 466.7


def measured_camera(width: int = 320, height: int = 200) -> CameraPreset:
    """The camera we actually have, from datasheet optics and measured range."""
    return CameraPreset(
        name="MEASURED",
        width=width,
        height=height,
        fx=FOCAL_PX,
        fy=FOCAL_PX,
        cx=width / 2.0,
        cy=height / 2.0,
        plate_scale_px_per_mm=FOCAL_PX / 1425.0,
        sensor_crop=(336, 150, 816, 516),
        sampling_increment=(SUBSAMPLE, SUBSAMPLE),
        isp_offset=(4, 4),
        orientation="rot180",
        gate_b1_passed=False,
        physical_status="measured_from_real_capture",
    )


def render_mask(mesh, center_world, roll_rad, camera) -> tuple[np.ndarray, np.ndarray] | None:
    """Project and rasterise the mesh, against an explicitly supplied camera.

    The evaluation copy of this resolves the camera by preset NAME, which hard-
    codes the wrong intrinsics for real data. This one takes the camera object.
    """
    axis_u, axis_v = _face_axes(float(roll_rad))
    local = mesh.vertices_local_mm
    world = (
        np.asarray(center_world, dtype=float)[None, :]
        + local[:, 0, None] * FACE_NORMAL[None, :]
        + local[:, 1, None] * axis_u[None, :]
        + local[:, 2, None] * axis_v[None, :]
    )
    uv, front = _project(world, camera)
    center_uv, center_front = _project(np.asarray(center_world, dtype=float)[None, :], camera)
    if not bool(center_front[0]) or not front.any():
        return None
    faces = mesh.faces[np.all(front[mesh.faces], axis=1)]
    if not len(faces):
        return None
    mask = rasterize_projected_triangles(uv, faces, width=camera.width, height=camera.height)
    return mask, center_uv[0]


def iou(a: np.ndarray, b: np.ndarray) -> float:
    a = a.astype(bool)
    b = b.astype(bool)
    union = np.count_nonzero(a | b)
    return float(np.count_nonzero(a & b) / union) if union else 0.0


@dataclass
class RealFit:
    ok: bool
    reason: str
    iou: float
    center_world: np.ndarray | None
    roll_deg: float | None
    range_mm: float | None
    observed_px: int
    rendered_px: int


def fit_frame(
    mesh,
    observed_mask: np.ndarray,
    camera: CameraPreset,
    *,
    range_grid_mm=np.arange(1250.0, 1651.0, 50.0),
    roll_grid_deg=np.arange(-90.0, 90.0, 7.5),
    refine: bool = True,
) -> RealFit:
    """Best 3D centre + roll for one observed club silhouette, by direct IoU.

    The centre is constrained to the pixel ray through the observed centroid, so
    the search is over range and roll rather than free 3D translation. That is
    the same constraint the radar range sphere would impose, standing in for a
    radar measurement this camera-only capture does not have.
    """
    observed = observed_mask.astype(bool)
    n_obs = int(observed.sum())
    if n_obs < 40:
        return RealFit(False, "observed_mask_too_small", 0.0, None, None, None, n_obs, 0)

    ys, xs = np.nonzero(observed)
    centroid = np.array([xs.mean(), ys.mean()], dtype=float)
    ray = _ray_world(centroid, camera)

    def point_at(range_mm: float) -> np.ndarray:
        # The world origin is the IMPACT POINT and the camera sits away from it,
        # so a point at range R along the ray is measured FROM THE CAMERA, not
        # from the origin. Getting this wrong put every hypothesis in the wrong
        # place and rendered the mesh at a fraction of its true size.
        return CAMERA_CENTER_WORLD + ray * float(range_mm)

    best = (0.0, None, None, None, 0)
    for range_mm in range_grid_mm:
        center = point_at(range_mm)
        for roll_deg in roll_grid_deg:
            out = render_mask(mesh, center, math.radians(float(roll_deg)), camera)
            if out is None:
                continue
            score = iou(out[0], observed)
            if score > best[0]:
                best = (score, center, float(roll_deg), float(range_mm), int(out[0].sum()))

    if best[1] is None:
        return RealFit(False, "no_pose_projected", 0.0, None, None, None, n_obs, 0)

    if refine:
        score, center, roll_deg, range_mm, n_ren = best
        for _ in range(3):
            improved = False
            for d_range in (-25.0, -10.0, 10.0, 25.0):
                for d_roll in (-4.0, -1.5, 1.5, 4.0):
                    cand_center = point_at(range_mm + d_range)
                    out = render_mask(mesh, cand_center, math.radians(roll_deg + d_roll), camera)
                    if out is None:
                        continue
                    cand = iou(out[0], observed)
                    if cand > score:
                        score, center, roll_deg, range_mm = (
                            cand,
                            cand_center,
                            roll_deg + d_roll,
                            range_mm + d_range,
                        )
                        n_ren, improved = int(out[0].sum()), True
            if not improved:
                break
        best = (score, center, roll_deg, range_mm, n_ren)

    score, center, roll_deg, range_mm, n_ren = best
    return RealFit(True, "ok", score, center, roll_deg, range_mm, n_obs, n_ren)

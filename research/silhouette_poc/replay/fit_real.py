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


# --------------------------------------------------------------------------
# 6-DOF pose fitting.
#
# The POC's pose model is 4-DOF: three position and ONE rotation. `FACE_NORMAL`
# is a hardcoded constant and `_face_axes(roll)` only spins u/v within the plane
# perpendicular to it, so the clubface always points the same world direction and
# the head can only slide along a ray and rotate like a propeller.
#
# That cannot represent loft, lie or face angle - and face orientation is exactly
# what impact location needs. Every "the mesh does not match" result should be
# read against this limitation before blaming the mesh.
# --------------------------------------------------------------------------


def _rot(axis: np.ndarray, angle_rad: float) -> np.ndarray:
    """Rodrigues rotation about an arbitrary axis."""
    a = np.asarray(axis, dtype=float)
    a = a / np.linalg.norm(a)
    K = np.array([[0.0, -a[2], a[1]], [a[2], 0.0, -a[0]], [-a[1], a[0], 0.0]])
    return np.eye(3) + math.sin(angle_rad) * K + (1.0 - math.cos(angle_rad)) * (K @ K)


def triad(yaw_deg: float, pitch_deg: float, roll_deg: float) -> tuple[np.ndarray, ...]:
    """Full orientation as an orthonormal (normal, u, v) triad.

    yaw   - about world up, i.e. FACE ANGLE (open/closed)
    pitch - about world right, i.e. DYNAMIC LOFT
    roll  - about the face normal, i.e. LIE / toe-up rotation
    """
    from silhouette_poc.fusion.solver import WORLD_RIGHT, WORLD_UP

    R = _rot(WORLD_UP, math.radians(yaw_deg)) @ _rot(WORLD_RIGHT, math.radians(pitch_deg))
    n = R @ FACE_NORMAL
    u = R @ WORLD_RIGHT
    v = R @ WORLD_UP
    Rr = _rot(n, math.radians(roll_deg))
    return n, Rr @ u, Rr @ v


def render_mask_6dof(mesh, center_world, yaw_deg, pitch_deg, roll_deg, camera):
    """Project and rasterise with a FULL orientation rather than roll alone."""
    n, u, v = triad(yaw_deg, pitch_deg, roll_deg)
    local = mesh.vertices_local_mm
    world = (
        np.asarray(center_world, dtype=float)[None, :]
        + local[:, 0, None] * n[None, :]
        + local[:, 1, None] * u[None, :]
        + local[:, 2, None] * v[None, :]
    )
    uv, front = _project(world, camera)
    _, center_front = _project(np.asarray(center_world, dtype=float)[None, :], camera)
    if not bool(center_front[0]) or not front.any():
        return None
    faces = mesh.faces[np.all(front[mesh.faces], axis=1)]
    if not len(faces):
        return None
    return rasterize_projected_triangles(uv, faces, width=camera.width, height=camera.height)


def fit_frame_6dof(
    mesh,
    observed_mask,
    camera,
    *,
    range_grid_mm=(1300.0, 1425.0, 1550.0),
    yaw_grid=(-40.0, -20.0, 0.0, 20.0, 40.0),
    pitch_grid=(-40.0, -20.0, 0.0, 20.0, 40.0),
    roll_grid=(-60.0, -30.0, 0.0, 30.0, 60.0, 90.0),
):
    """Best 6-DOF pose by direct IoU. Coarse grid, then local refinement."""
    observed = observed_mask.astype(bool)
    if int(observed.sum()) < 40:
        return {"ok": False, "reason": "observed_mask_too_small", "iou": 0.0}
    ys, xs = np.nonzero(observed)
    ray = _ray_world(np.array([xs.mean(), ys.mean()], dtype=float), camera)

    def score(rng, yaw, pitch, roll):
        m = render_mask_6dof(mesh, CAMERA_CENTER_WORLD + ray * rng, yaw, pitch, roll, camera)
        return (0.0, None) if m is None else (iou(m, observed), m)

    best = (0.0, None)
    for rng in range_grid_mm:
        for yaw in yaw_grid:
            for pitch in pitch_grid:
                for roll in roll_grid:
                    s, _ = score(rng, yaw, pitch, roll)
                    if s > best[0]:
                        best = (s, (rng, yaw, pitch, roll))
    if best[1] is None:
        return {"ok": False, "reason": "no_pose_projected", "iou": 0.0}

    rng, yaw, pitch, roll = best[1]
    step = [60.0, 10.0, 10.0, 15.0]
    for _ in range(4):
        improved = False
        for k, deltas in enumerate(step):
            for d in (-deltas, deltas):
                cand = [rng, yaw, pitch, roll]
                cand[k] += d
                s, _ = score(*cand)
                if s > best[0]:
                    best, (rng, yaw, pitch, roll), improved = (s, tuple(cand)), cand, True
        if not improved:
            step = [x / 2.0 for x in step]
    return {
        "ok": True,
        "reason": "ok",
        "iou": best[0],
        "range_mm": rng,
        "yaw_deg": yaw,
        "pitch_deg": pitch,
        "roll_deg": roll,
    }

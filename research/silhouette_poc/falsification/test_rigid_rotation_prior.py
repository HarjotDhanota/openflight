"""Does a SWING motion model beat free per-frame orientation?

The maintainer's point: a clubhead does not take an arbitrary new orientation
every 2.1 ms. Through the impact zone it is in rigid rotation -- the face
sweeps from open toward closed at a roughly steady rate about a roughly fixed
axis. So stop letting the fit choose three fresh angles per frame.

`fit_real.fit_sequence` already exists and is a step in this direction, but it
is a SMOOTHNESS PENALTY: it charges IoU for any change between adjacent poses.
That biases toward a club that is not moving, which is the wrong prior for an
object whose whole job is to rotate. It also stays 3 free angles per frame.

This is the stronger form. One initial orientation and one angular velocity for
the WHOLE sequence:

    R(t) = expm([omega] x (t - t0)) @ R0        6 parameters, not 3N

For a six-frame run that is 6 parameters instead of 18, and the flat +-11 deg
basins in individual frames have to agree on one consistent motion.

The honest test is whether summed IoU SURVIVES the constraint. If it holds near
the per-frame optimum, the per-frame wobble was noise and the prior recovers
real motion. If it collapses, the model is wrong and the wobble was signal.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
from scipy.optimize import minimize

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "research"))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from silhouette_poc.fusion.solver import _project  # noqa: E402
from silhouette_poc.generator.mesh_truth import (  # noqa: E402
    TriangleMesh,
    rasterize_projected_triangles,
)
from silhouette_poc.replay.fit_real import (  # noqa: E402
    CAMERA_CENTER_WORLD,
    _ray_world,
    iou,
    measured_camera,
    triad,
)
from test_meshfit_depth_ab import EXCLUDE, SESSION, club_masks  # noqa: E402

PINNED_MM = 1581.0
FRAME_S = 2.1385e-3


def rodrigues(axis_deg_per_s, dt):
    """Rotation over dt at a constant angular velocity, degrees/second."""
    w = np.asarray(axis_deg_per_s, float) * dt
    theta = math.radians(np.linalg.norm(w))
    if theta < 1e-12:
        return np.eye(3)
    k = np.asarray(w, float) / np.linalg.norm(w)
    kx = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + math.sin(theta) * kx + (1 - math.cos(theta)) * (kx @ kx)


def render(mesh, basis, centre, cam):
    local = mesh.vertices_local_mm
    world = centre[None, :] + local @ basis.T
    uv, front = _project(world, cam)
    if not front.any():
        return None
    faces = mesh.faces[np.all(front[mesh.faces], axis=1)]
    if not len(faces):
        return None
    return rasterize_projected_triangles(uv, faces, width=cam.width, height=cam.height)


def sequence_score(mesh, cam, frames, masks, rays, params):
    """Mean IoU over the run under one initial pose + one angular velocity."""
    yaw, pitch, roll = params[:3]
    omega = params[3:]
    n, u, v = triad(yaw, pitch, roll)
    r0 = np.column_stack([n, u, v])
    total, t0 = 0.0, frames[0]
    for k, f in enumerate(frames):
        basis = rodrigues(omega, (f - t0) * FRAME_S) @ r0
        centre = CAMERA_CENTER_WORLD + rays[k] * PINNED_MM
        m = render(mesh, basis, centre, cam)
        total += 0.0 if m is None else iou(m, masks[k])
    return total / len(frames)


def main():
    import csv  # noqa: PLC0415

    d = np.load(ROOT / "research/silhouette_poc/meshes/assets/poc_7iron.npz")
    mesh = TriangleMesh(d["vertices_local_mm"], d["faces"], "poc_7iron", "x" * 64)
    per_frame = {
        r["shot"]: r
        for r in json.loads(
            (Path(__file__).parent / "meshfit_arm_C.json").read_text(encoding="utf-8")
        )
    }
    with open(SESSION / "shots.csv", newline="", encoding="utf-8") as h:
        rows = [r for r in csv.DictReader(h) if int(r["shot_number"]) not in EXCLUDE]

    print(
        f"{'shot':>5} {'n':>3} {'per-frame IoU':>14} {'rigid-rotation IoU':>19} "
        f"{'cost':>8} {'|omega| deg/s':>14} {'closure':>9}"
    )
    keep = []
    for row in rows:
        shot = int(row["shot_number"])
        rec = per_frame.get(shot)
        if rec is None or len(rec["frames"]) < 4:
            continue
        frames_arr = np.load(SESSION / row["archive_frames_npz"])["frames"][:, :, ::-1]
        cam = measured_camera(frames_arr.shape[2], frames_arr.shape[1])
        all_masks = club_masks(frames_arr)
        idx = [int(f) for f in rec["frames"] if int(f) in all_masks]
        if len(idx) < 4:
            continue
        masks = [all_masks[i].astype(bool) for i in idx]
        rays = []
        for m in masks:
            ys, xs = np.nonzero(m)
            rays.append(_ray_world(np.array([xs.mean(), ys.mean()], float), cam))

        base_iou = float(np.mean(rec["ious"][: len(idx)]))
        seed_pose = np.median(np.asarray(rec["poses"][: len(idx)], float), axis=0)

        best = None
        for w0 in (
            np.zeros(3),
            np.array([0.0, 0.0, 600.0]),
            np.array([0.0, 0.0, -600.0]),
            np.array([300.0, 0.0, 300.0]),
            np.array([-300.0, 0.0, -300.0]),
        ):
            x0 = np.concatenate([seed_pose, w0])
            res = minimize(
                lambda p: -sequence_score(mesh, cam, idx, masks, rays, p),
                x0,
                method="Nelder-Mead",
                options=dict(maxiter=900, xatol=0.4, fatol=2e-4),
            )
            if best is None or res.fun < best.fun:
                best = res
        rigid_iou = -best.fun
        omega = best.x[3:]
        span = (idx[-1] - idx[0]) * FRAME_S
        print(
            f"{shot:>5} {len(idx):>3} {base_iou:>14.4f} {rigid_iou:>19.4f} "
            f"{rigid_iou - base_iou:>+8.4f} {np.linalg.norm(omega):>14.0f} "
            f"{np.linalg.norm(omega) * span:>8.1f}deg"
        )
        keep.append((base_iou, rigid_iou, np.linalg.norm(omega), span))

    if not keep:
        print("no shot had enough tracked frames")
        return
    k = np.asarray([(a, b, c, d) for a, b, c, d in keep])
    print(f"\n=== {len(k)} shots with 4+ pre-impact frames ===")
    print(
        f"  per-frame free orientation   mean IoU {k[:, 0].mean():.4f}  "
        f"(3 angles x n frames)"
    )
    print(
        f"  one rigid rotation           mean IoU {k[:, 1].mean():.4f}  "
        f"(6 parameters total)"
    )
    print(f"  IoU given up for the constraint: {(k[:, 1] - k[:, 0]).mean():+.4f}")
    print(
        f"  fitted closure rate: median {np.median(k[:, 2]):.0f} deg/s "
        f"= {np.median(k[:, 2]) * np.median(k[:, 3]):.1f} deg over the tracked run"
    )
    print("\n  A real clubhead closes at roughly 1500-2500 deg/s near impact.")
    print("  Pose coherence is 100% by construction: the motion IS the model.")


if __name__ == "__main__":
    main()

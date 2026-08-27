"""Does the resolution extrapolation actually hold on REAL pixels?

Every "more pixels gives N degrees" figure on this page rests on one untested
assumption: that face-angle leverage scales linearly with plate scale, with the
edge-noise floor staying put. Both halves of that were asserted from clean
renders of the mesh, never checked against real segmented edges.

It can be checked without new hardware, by going the other way. Halve the
resolution of the footage already in hand and re-measure. If the objective's
flat basin in yaw DOUBLES when the plate scale halves, the extrapolation
upwards is credible. If it widens by less, something else dominates the noise
floor and adding pixels will buy less than predicted.

Run on the real pre-impact masks, not on renders.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "research"))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from dataclasses import replace  # noqa: E402

import cv2  # noqa: E402
from silhouette_poc.generator.mesh_truth import TriangleMesh  # noqa: E402
from silhouette_poc.replay.fit_real import (  # noqa: E402
    CAMERA_CENTER_WORLD,
    _ray_world,
    iou,
    measured_camera,
    render_mask_6dof,
)
from test_meshfit_depth_ab import (  # noqa: E402
    SESSION,
    club_masks,
)

OFFSETS = np.arange(-40.0, 40.1, 2.5)
DROP = 0.05


def score_pose(mesh, cam, mask, pose):
    obs = mask.astype(bool)
    ys, xs = np.nonzero(obs)
    ray = _ray_world(np.array([xs.mean(), ys.mean()], dtype=float), cam)
    centre = CAMERA_CENTER_WORLD + ray * 1581.0
    rendered = render_mask_6dof(mesh, centre, *pose, cam)
    return 0.0 if rendered is None else iou(rendered, obs)


def refine_from_seed(mesh, cam, mask, seed):
    """Cheap half-scale refit seeded by the corresponding full-scale optimum."""
    pose = list(np.asarray(seed, dtype=float))
    best = score_pose(mesh, cam, mask, pose)
    step = [5.0, 5.0, 7.5]
    for _ in range(4):
        improved = False
        for axis, delta in enumerate(step):
            for direction in (-1.0, 1.0):
                candidate = pose.copy()
                candidate[axis] += direction * delta
                score = score_pose(mesh, cam, mask, candidate)
                if score > best:
                    pose, best, improved = candidate, score, True
        if not improved:
            step = [value / 2.0 for value in step]
    return pose, best


def basin_width(mesh, cam, mask, fit, param="yaw"):
    """Half-width in degrees within 5 % of the peak, around this frame's best."""
    obs = mask.astype(bool)
    ys, xs = np.nonzero(obs)
    ray = _ray_world(np.array([xs.mean(), ys.mean()], dtype=float), cam)
    centre = CAMERA_CENTER_WORLD + ray * 1581.0
    base = dict(zip(("yaw", "pitch", "roll"), fit, strict=True))
    vals = []
    for off in OFFSETS:
        p = dict(base)
        p[param] += off
        r = render_mask_6dof(mesh, centre, p["yaw"], p["pitch"], p["roll"], cam)
        vals.append(0.0 if r is None else iou(r, obs))
    vals = np.asarray(vals)
    ok = np.nonzero(vals >= vals.max() * (1.0 - DROP))[0]
    return float(OFFSETS[ok[-1]] - OFFSETS[ok[0]]) / 2.0


def halve(mask):
    """Downsample a binary mask by 2, keeping majority occupancy."""
    h, w = mask.shape
    m = mask[: h // 2 * 2, : w // 2 * 2].astype(np.float32)
    small = cv2.resize(m, (w // 2, h // 2), interpolation=cv2.INTER_AREA)
    return (small >= 0.5).astype(np.uint8)


def main():
    d = np.load(ROOT / "research/silhouette_poc/meshes/assets/poc_7iron.npz")
    mesh = TriangleMesh(d["vertices_local_mm"], d["faces"], "poc_7iron", "x" * 64)
    records = json.loads(
        Path(__file__).with_name("meshfit_arm_C.json").read_text(encoding="utf-8")
    )
    by_shot = {int(record["shot"]): record for record in records}

    full_cam = measured_camera(320, 200)
    half_cam = replace(
        measured_camera(160, 100),
        fx=full_cam.fx / 2.0,
        fy=full_cam.fy / 2.0,
        cx=(full_cam.cx + 0.5) / 2.0 - 0.5,
        cy=(full_cam.cy + 0.5) / 2.0 - 0.5,
    )

    # Face-angle/yaw is the decision variable behind the lens recommendation.
    # Pitch and roll were checked in the first completed run and behaved the
    # same way (ratios 0.73 and 0.88), but retaining only yaw makes the
    # reproducible checkpoint three times faster.
    res = {"yaw": ([], [])}
    n = 0
    for shot, record in sorted(by_shot.items()):
        if not record["frames"]:
            print(f"  shot {shot:>3} ({n} frames)", flush=True)
            continue
        shot_dirs = list((SESSION / "shots").glob(f"shot_{shot:03d}_*"))
        if len(shot_dirs) != 1:
            raise RuntimeError(f"shot {shot} resolved to {len(shot_dirs)} directories")
        frames = np.load(shot_dirs[0] / "frames.npz")["frames"][:, :, ::-1]
        masks = club_masks(frames)
        for frame_id, full_pose in zip(record["frames"], record["poses"], strict=True):
            if frame_id not in masks:
                continue
            m = masks[frame_id]
            small = halve(m)
            if small.sum() < 40:
                continue
            half_pose, _half_score = refine_from_seed(mesh, half_cam, small, full_pose)
            for param in res:
                a = basin_width(mesh, full_cam, m, full_pose, param)
                b = basin_width(mesh, half_cam, small, half_pose, param)
                if a is not None and b is not None:
                    res[param][0].append(a)
                    res[param][1].append(b)
            n += 1
        print(f"  shot {shot:>3} ({n} frames)", flush=True)

    print(f"\n=== flat-basin half-width, real masks, {n} frames ===")
    print(
        f"{'':>10} {'320x200 (0.295 px/mm)':>24} {'160x100 (0.148 px/mm)':>24} "
        f"{'ratio':>8} {'predicted':>10}"
    )
    for param, (full, half) in res.items():
        f_med, h_med = np.median(full), np.median(half)
        print(
            f"{param:>10} {f_med:>21.2f} deg {h_med:>21.2f} deg "
            f"{h_med / max(f_med, 1e-9):>8.2f} {2.00:>10.2f}"
        )
    print("\n  A ratio near 2.0 means the basin scales with plate scale, so the")
    print("  extrapolation UPWARD is credible. A ratio near 1.0 means something")
    print("  other than pixel count sets the floor and more pixels will not help.")
    output = Path(__file__).with_name("resolution_scaling_check.json")
    output.write_text(
        json.dumps(
            {
                param: {
                    "full_half_width_deg": full,
                    "half_half_width_deg": half,
                    "median_ratio": float(np.median(half) / np.median(full)),
                }
                for param, (full, half) in res.items()
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"wrote {output}")


if __name__ == "__main__":
    main()

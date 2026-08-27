"""Does the fitted mesh actually render a shaft stub, or only a head?

Section 11f claimed the observed silhouette's thin tail is "shaft and hosel,
which a head-only model cannot cover at any range". That assumed the mesh is
head-only. It is not obviously so: the mesh bounding box is 107.6 x 96.9 mm
while the manifest records the HEAD as 79.7 x 42.5 mm, which leaves roughly
58 mm of something else -- and `render_mask_6dof` projects EVERY triangle.

So check it instead of asserting it: split the mesh's own triangles into head
and stub along the measured shaft axis, render each separately at a real fitted
pose, and measure how many pixels each contributes.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "research"))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from silhouette_poc.generator.mesh_truth import TriangleMesh  # noqa: E402
from silhouette_poc.replay.fit_real import (  # noqa: E402
    CAMERA_CENTER_WORLD,
    _ray_world,
    measured_camera,
    render_mask_6dof,
)
from test_meshfit_depth_ab import SESSION, club_masks, fit_frame_pinned  # noqa: E402

SHAFT = np.array([-0.245, 0.295, -0.924])
SHAFT /= np.linalg.norm(SHAFT)


def main(shot_dir="shot_029_9-iron"):
    d = np.load(ROOT / "research/silhouette_poc/meshes/assets/poc_7iron.npz")
    v = np.asarray(d["vertices_local_mm"], float)
    f = np.asarray(d["faces"], int)

    t = v @ SHAFT
    cent_t = t[f].mean(1)
    # The head is the thick end. Split at the point where the cross-section
    # collapses, found by the same slicing used to measure the shaft axis.
    edges = np.linspace(np.percentile(t, 1), np.percentile(t, 99), 40)
    radii, mids = [], []
    for a, b in zip(edges[:-1], edges[1:]):
        sel = (t >= a) & (t < b)
        if sel.sum() < 30:
            continue
        p = v[sel]
        cm = p.mean(0)
        radii.append(
            float(
                np.percentile(
                    np.linalg.norm(
                        (p - cm) - np.outer((p - cm) @ SHAFT, SHAFT), axis=1
                    ),
                    90,
                )
            )
        )
        mids.append(0.5 * (a + b))
    radii, mids = np.asarray(radii), np.asarray(mids)
    thin = radii < max(radii.min() * 2.2, 9.0)
    cut = float(mids[thin].min()) if thin.any() else float(mids.max())
    print(
        f"cross-section radius along the shaft axis: "
        f"{radii.min():.1f} -> {radii.max():.1f} mm"
    )
    print(f"head/stub cut at {cut:.1f} mm along the axis")

    head_f = f[cent_t < cut]
    stub_f = f[cent_t >= cut]
    print(
        f"triangles: head {len(head_f)}, stub {len(stub_f)} "
        f"({100 * len(stub_f) / len(f):.1f} % of the mesh)"
    )
    stub_len = float(np.ptp(t[np.unique(stub_f)])) if len(stub_f) else 0.0
    print(f"stub extent along the shaft axis: {stub_len:.1f} mm")

    full = TriangleMesh(v, f, "full", "x" * 64)
    head = TriangleMesh(v, head_f, "head", "x" * 64)
    stub = TriangleMesh(v, stub_f, "stub", "x" * 64) if len(stub_f) else None

    frames = np.load(SESSION / "shots" / shot_dir / "frames.npz")["frames"][:, :, ::-1]
    cam = measured_camera(frames.shape[2], frames.shape[1])
    masks = club_masks(frames)
    print(
        f"\n{'frame':>6} {'obs px':>8} {'full':>8} {'head':>8} {'stub':>8} "
        f"{'stub %':>8} {'full/obs':>9}"
    )
    for k in sorted(masks):
        m = masks[k]
        fit = fit_frame_pinned(full, m, cam)
        if not fit.get("ok"):
            continue
        ys, xs = np.nonzero(m.astype(bool))
        ray = _ray_world(np.array([xs.mean(), ys.mean()], dtype=float), cam)
        centre = CAMERA_CENTER_WORLD + ray * fit["range_mm"]
        args = (fit["yaw_deg"], fit["pitch_deg"], fit["roll_deg"], cam)
        r_full = render_mask_6dof(full, centre, *args)
        r_head = render_mask_6dof(head, centre, *args)
        r_stub = render_mask_6dof(stub, centre, *args) if stub else None
        nf = int(r_full.sum())
        nh = int(r_head.sum())
        ns = int(r_stub.sum()) if r_stub is not None else 0
        print(
            f"{k:>6} {int(m.sum()):>8} {nf:>8} {nh:>8} {ns:>8} "
            f"{100 * ns / max(nf, 1):>7.1f}% {100 * nf / max(int(m.sum()), 1):>8.1f}%"
        )
    print("\nIf the stub contributes pixels, the rendered outline is NOT head-only")
    print("and the section 11f wording needs correcting.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "shot_029_9-iron")

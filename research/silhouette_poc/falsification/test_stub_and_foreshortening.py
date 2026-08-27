"""Two checks the maintainer asked for.

1. WHAT IS THE STUB? I called the mesh's 61.8 mm protrusion a "shaft stub".
   The maintainer says there is no shaft on this model -- it is the hosel and
   ferrule. Diameter settles it: an iron shaft tapers to about 9-10 mm at the
   tip, a hosel/ferrule is more like 12-15 mm, and a real shaft is ~900 mm long
   rather than 62 mm.

2. DOES FORESHORTENING GIVE YAW? The proposal: measure heel-to-toe distance in
   the image, compare it against the same distance in a square reference, and
   read the rotation off the shortening. Same idea for topline-to-sole.

   The physics is real -- a rotated face projects shorter. The question is
   sensitivity, because the projected length goes as cos(yaw), whose derivative
   is ZERO at yaw = 0, which is where golf shots live. Measure the leverage the
   same way the shaft leverage was measured, and compare it against what the
   pixels can actually resolve.

   Measured robustly: the silhouette's second-moment ellipse (major/minor axes
   and orientation), which is what the shipped segmentation already computes,
   rather than two hand-picked landmark points.
"""

from __future__ import annotations

import math
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
    measured_camera,
    render_mask_6dof,
)

SHAFT_AXIS = np.array([-0.245, 0.295, -0.924])
SHAFT_AXIS /= np.linalg.norm(SHAFT_AXIS)
PLATE_PX_PER_MM = 466.67 / 1581.0  # 0.295 at the measured range


def stub_identity(v, f):
    t = v @ SHAFT_AXIS
    edges = np.linspace(np.percentile(t, 1), np.percentile(t, 99), 60)
    print(f"{'position along axis':>21} {'diameter':>10} {'n verts':>9}")
    prof = []
    for a, b in zip(edges[:-1], edges[1:]):
        sel = (t >= a) & (t < b)
        if sel.sum() < 15:
            continue
        p = v[sel]
        cm = p.mean(0)
        perp = (p - cm) - np.outer((p - cm) @ SHAFT_AXIS, SHAFT_AXIS)
        # true diameter = full width, not a radius percentile
        dia = (
            float(np.ptp(perp @ np.array([1.0, 0, 0])))
            if False
            else float(2.0 * np.percentile(np.linalg.norm(perp, axis=1), 95))
        )
        prof.append((0.5 * (a + b), dia, int(sel.sum())))
    prof = np.asarray([(a, b, c) for a, b, c in prof])
    thin = prof[prof[:, 1] < 30.0]
    for row in thin[::2]:
        print(f"{row[0]:>21.1f} {row[1]:>10.1f} {int(row[2]):>9}")
    if len(thin):
        print(
            f"\n  thin section: {np.ptp(thin[:, 0]):.1f} mm long, "
            f"diameter {thin[:, 1].min():.1f}-{thin[:, 1].max():.1f} mm"
        )
    print("  reference: iron shaft tip ~9-10 mm and ~900 mm long;")
    print("             hosel + ferrule ~12-16 mm and ~40-60 mm long.")


def ellipse(mask):
    """Second-moment major/minor axis lengths and orientation of a silhouette."""
    ys, xs = np.nonzero(mask)
    if len(xs) < 20:
        return None
    p = np.stack([xs - xs.mean(), ys - ys.mean()])
    cov = (p @ p.T) / len(xs)
    w, vec = np.linalg.eigh(cov)
    order = np.argsort(w)[::-1]
    w, vec = w[order], vec[:, order]
    major, minor = 4.0 * np.sqrt(np.maximum(w, 0))
    return major, minor, math.degrees(math.atan2(vec[1, 0], vec[0, 0]))


def foreshortening(mesh, cam):
    centre = CAMERA_CENTER_WORLD + np.array([1581.0, 0.0, 0.0])
    bases = [
        (y, p, r)
        for y in (-20.0, 0.0, 20.0)
        for p in (-20.0, 0.0, 20.0)
        for r in (-30.0, 0.0, 30.0)
    ]
    eps = 5.0
    print(
        f"\n{'rotation':>22} {'major axis':>12} {'minor axis':>12} "
        f"{'aspect ratio':>14} {'ellipse angle':>14}"
    )
    print(
        f"{'':>22} {'px per deg':>12} {'px per deg':>12} "
        f"{'% per deg':>14} {'deg per deg':>14}"
    )
    for i, name in enumerate(
        ("yaw   (FACE ANGLE)", "pitch (DYNAMIC LOFT)", "roll  (lie / toe-up)")
    ):
        dmaj, dmin, dasp, dang = [], [], [], []
        for base in bases:
            lo, hi = list(base), list(base)
            lo[i] -= eps
            hi[i] += eps
            a = ellipse(render_mask_6dof(mesh, centre, *lo, cam))
            b = ellipse(render_mask_6dof(mesh, centre, *hi, cam))
            if a is None or b is None:
                continue
            dmaj.append(abs(b[0] - a[0]) / (2 * eps))
            dmin.append(abs(b[1] - a[1]) / (2 * eps))
            asp_a, asp_b = a[1] / max(a[0], 1e-9), b[1] / max(b[0], 1e-9)
            dasp.append(100.0 * abs(asp_b - asp_a) / max(asp_a, 1e-9) / (2 * eps))
            d = (b[2] - a[2] + 90.0) % 180.0 - 90.0
            dang.append(abs(d) / (2 * eps))
        print(
            f"{name:>22} {np.median(dmaj):>12.3f} {np.median(dmin):>12.3f} "
            f"{np.median(dasp):>14.3f} {np.median(dang):>14.3f}"
        )
    print(
        "\n  Head is ~80 mm heel-toe; at the measured plate scale that is "
        f"{80 * PLATE_PX_PER_MM:.1f} px."
    )
    print("  Segmentation on this data localises an edge to roughly 0.5-1 px,")
    print("  so a cue must move MORE than that per degree to be usable.")


def main():
    d = np.load(ROOT / "research/silhouette_poc/meshes/assets/poc_7iron.npz")
    v = np.asarray(d["vertices_local_mm"], float)
    f = np.asarray(d["faces"], int)
    print("=== 1. what is the 62 mm protrusion? ===")
    stub_identity(v, f)
    print("\n=== 2. foreshortening leverage (second-moment ellipse) ===")
    foreshortening(TriangleMesh(v, f, "poc_7iron", "x" * 64), measured_camera(320, 200))


if __name__ == "__main__":
    main()

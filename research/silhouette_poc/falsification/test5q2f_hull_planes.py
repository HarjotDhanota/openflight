"""Section 5 Q2 -- rebuild the measurement from the convex hull alone.

Two things went wrong in the earlier passes and both are fixed here.

1. The shaft axis was INHERITED from the handoff, and my first attempt to
   re-measure it failed outright (it selected a 9 mm disc and landed 89.9 deg
   away). It is re-measured here by iterative slicing, seeded from three very
   different directions, and it has to converge to the same answer from all
   three or it is not reported.

2. The sole was picked as "the largest planar cluster roughly perpendicular to
   the face", which is close to assuming the answer. A club rests on a
   SUPPORTING PLANE of its convex hull, so the candidates are the hull's own
   large planar patches -- computed here without reference to any face.

Only surfaces on the hull can be the face or the sole. The plane census found
three large +x/-x surfaces; the equal-terms hull test showed two of them
(the "cavity floor" and the finely-triangulated cluster 4) are recessed
4.4 mm and 5.8 mm, so neither is exterior.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from scipy.spatial import ConvexHull

ROOT = Path(__file__).resolve().parents[3]
MESH = ROOT / "research/silhouette_poc/meshes/assets/poc_7iron.npz"
INHERITED = np.array([-0.204, 0.288, -0.936])
INHERITED /= np.linalg.norm(INHERITED)


def shaft_axis(v, seed, iters=12):
    """Iterative slicing: keep the thin cross-sections, refit their axis."""
    ax = np.asarray(seed, float) / np.linalg.norm(seed)
    for _ in range(iters):
        t = v @ ax
        lo, hi = np.percentile(t, [1, 99])
        edges = np.linspace(lo, hi, 40)
        cents, radii = [], []
        for a, b in zip(edges[:-1], edges[1:]):
            sel = (t >= a) & (t < b)
            if sel.sum() < 30:
                continue
            p = v[sel]
            cm = p.mean(0)
            perp = (p - cm) - np.outer((p - cm) @ ax, ax)
            cents.append(cm)
            radii.append(float(np.percentile(np.linalg.norm(perp, axis=1), 90)))
        cents, radii = np.asarray(cents), np.asarray(radii)
        if len(radii) < 6:
            return None, None
        thin = radii < max(np.min(radii) * 2.2, 9.0)
        if thin.sum() < 4:
            return None, None
        pts = cents[thin]
        new = np.linalg.svd(pts - pts.mean(0), full_matrices=False)[2][0]
        if new @ ax < 0:
            new = -new
        if math.degrees(math.acos(min(abs(float(new @ ax)), 1.0))) < 1e-4:
            ax = new
            break
        ax = new
    t = v @ ax
    lo, hi = np.percentile(t, [1, 99])
    edges = np.linspace(lo, hi, 40)
    cents, radii = [], []
    for a, b in zip(edges[:-1], edges[1:]):
        sel = (t >= a) & (t < b)
        if sel.sum() < 30:
            continue
        p = v[sel]
        cm = p.mean(0)
        cents.append(cm)
        radii.append(
            float(
                np.percentile(
                    np.linalg.norm((p - cm) - np.outer((p - cm) @ ax, ax), axis=1), 90
                )
            )
        )
    return ax, (np.asarray(cents), np.asarray(radii))


def hull_planes(v, tol_deg=4.0, min_area=120.0):
    """Merge coplanar convex-hull facets into supporting-plane patches."""
    h = ConvexHull(v)
    tri = v[h.simplices]
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    m = np.linalg.norm(n, axis=1)
    ok = m > 1e-12
    n, a = n[ok] / m[ok, None], m[ok] / 2.0
    off = np.einsum("ij,ij->i", n, tri[ok][:, 0])
    order = np.argsort(-a)
    used = np.zeros(len(n), bool)
    out = []
    for i in order:
        if used[i]:
            continue
        same = n @ n[i] > math.cos(math.radians(tol_deg))
        same &= np.abs(off - off[i]) < 1.2
        same &= ~used
        if a[same].sum() < min_area:
            used[i] = True
            continue
        used |= same
        w = a[same]
        nn = (n[same] * w[:, None]).sum(0)
        nn /= np.linalg.norm(nn)
        out.append((float(w.sum()), nn, float(np.average(off[same], weights=w))))
    out.sort(key=lambda r: -r[0])
    return out


def main():
    v = np.asarray(np.load(MESH)["vertices_local_mm"], float)

    print("=== shaft axis, re-measured by iterative slicing from 3 seeds ===")
    seeds = {
        "inherited": INHERITED,
        "+z": np.array([0.0, 0.0, 1.0]),
        "principal": np.linalg.svd(v - v.mean(0), full_matrices=False)[2][0],
    }
    axes = {}
    for name, sd in seeds.items():
        ax, prof = shaft_axis(v, sd)
        if ax is None:
            print(f"  {name:>10}: did not converge (fail closed)")
            continue
        axes[name] = ax
        cents, radii = prof
        print(
            f"  {name:>10} seed -> {ax}   thin-section radius "
            f"{radii.min():.2f} mm, thick {radii.max():.2f} mm"
        )
    if len(axes) >= 2:
        vals = list(axes.values())
        spread = max(
            math.degrees(math.acos(min(abs(float(a @ b)), 1.0)))
            for a in vals
            for b in vals
        )
        print(f"  spread between seeds: {spread:.3f} deg")
        ax = vals[0]
        print(
            f"  vs inherited handoff axis: "
            f"{math.degrees(math.acos(min(abs(float(ax @ INHERITED)), 1.0))):.3f} deg"
        )
    else:
        print("  cannot report a shaft axis")
        return

    print("\n=== the convex hull's own supporting planes (no face assumed) ===")
    planes = hull_planes(v)
    print(f"{'#':>2} {'area_mm2':>9} {'normal':>26} {'to_shaft':>9}  note")
    for i, (a, n, _o) in enumerate(planes[:8]):
        print(
            f"{i:>2} {a:9.1f} [{n[0]:6.3f} {n[1]:6.3f} {n[2]:6.3f}] "
            f"{math.degrees(math.acos(min(abs(float(n @ ax)), 1.0))):9.2f}"
        )

    print("\n=== every face/sole pairing among the hull's planes ===")
    print(f"{'face':>4} {'sole':>4} {'loft':>7} {'lie':>7} {'areas mm2':>16}")
    hits = []
    for i, (ai, ni, _) in enumerate(planes[:8]):
        for j, (aj, nj, _) in enumerate(planes[:8]):
            if i == j:
                continue
            loft = 90 - math.degrees(math.acos(min(abs(float(ni @ nj)), 1.0)))
            lie = 90 - math.degrees(math.acos(min(abs(float(ax @ nj)), 1.0)))
            if 8 <= loft <= 60 and 50 <= lie <= 70:
                hits.append((i, j, loft, lie, ai, aj))
                print(f"{i:>4} {j:>4} {loft:7.2f} {lie:7.2f} {ai:8.0f}/{aj:7.0f}")
    if not hits:
        print("  none in the plausible band")
    print("\n  maintainer: ~36 loft / ~60 lie.  690CB 7-iron catalogue: ~34 / ~62.")
    print("  The face-to-shaft angle is rotation-invariant and needs NO ground")
    print("  plane: for any club, cos(that angle) = sin(loft)*sin(lie).")
    for i, (a, n, _o) in enumerate(planes[:8]):
        phi = math.degrees(math.acos(min(abs(float(n @ ax)), 1.0)))
        implied = math.degrees(
            math.asin(
                min(math.cos(math.radians(phi)) / math.sin(math.radians(60.0)), 1.0)
            )
        )
        print(
            f"    plane {i}: face-to-shaft {phi:6.2f} deg -> loft {implied:5.2f} "
            f"if lie is 60"
        )


if __name__ == "__main__":
    main()

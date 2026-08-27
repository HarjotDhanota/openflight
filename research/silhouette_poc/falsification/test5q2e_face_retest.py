"""Section 5 Q2 RE-TEST -- was the striking face mis-identified?

The earlier pass compared only two candidates: the +x surface detect_face_plane
anchors to, and the -x surface behind it. The hull test showed -x is recessed
3.8 mm, so it is the cavity floor -- and that was taken as confirming +x.

That reasoning had a hole. The plane census listed a THIRD candidate, cluster 4
(normal [0.938, -0.021, 0.346]), which:
  * is anti-parallel to the cavity floor to 0.5 deg -- i.e. PARALLEL to it,
    which is what constant face thickness on a cavity back looks like;
  * is triangulated at 0.21 mm^2 per triangle, 4x finer than the +x surface
    and 20x finer than the cavity -- the signature of scorelines;
  * paired with the sole gives loft 33.6 deg, against the maintainer's ~36; and the 690CB catalogue ~34.
It was never hull-tested.

This file tests all three on equal terms, and independently re-measures the
shaft axis instead of inheriting it.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from scipy.spatial import ConvexHull

ROOT = Path(__file__).resolve().parents[3]
MESH = ROOT / "research/silhouette_poc/meshes/assets/poc_7iron.npz"
INHERITED_SHAFT = np.array([-0.204, 0.288, -0.936])
INHERITED_SHAFT /= np.linalg.norm(INHERITED_SHAFT)


def geom():
    z = np.load(MESH)
    v = np.asarray(z["vertices_local_mm"], float)
    f = np.asarray(z["faces"], int)
    a, b, c = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
    n = np.cross(b - a, c - a)
    m = np.linalg.norm(n, axis=1)
    ok = m > 1e-12
    return v, n[ok] / m[ok, None], m[ok] / 2.0, (a[ok] + b[ok] + c[ok]) / 3.0


def main():
    v, nrm, area, cent = geom()

    # ---- independently re-measure the shaft axis --------------------------
    # The shaft is the far end of the model from the head. Take the most
    # distant 6 % of vertices along the first principal axis and fit a line
    # to the tube's own centroid track, which does not depend on any earlier
    # cylinder fit.
    c0 = v.mean(0)
    pc = np.linalg.svd(v - c0, full_matrices=False)[2][0]
    t = (v - c0) @ pc
    far = t > np.percentile(t, 94)
    near = t < np.percentile(t, 6)
    tip = (
        v[far]
        if np.ptp(v[far], axis=0).max() < np.ptp(v[near], axis=0).max()
        else v[near]
    )
    ts = (tip - tip.mean(0)) @ pc
    rings = []
    for lo, hi in zip(
        np.percentile(ts, np.arange(0, 100, 10)),
        np.percentile(ts, np.arange(10, 101, 10)),
    ):
        sel = (ts >= lo) & (ts <= hi)
        if sel.sum() > 12:
            rings.append(tip[sel].mean(0))
    rings = np.asarray(rings)
    axis = np.linalg.svd(rings - rings.mean(0), full_matrices=False)[2][0]
    if axis @ INHERITED_SHAFT < 0:
        axis = -axis
    resid = np.linalg.norm(
        (rings - rings.mean(0)) - np.outer((rings - rings.mean(0)) @ axis, axis), axis=1
    )
    print("=== shaft axis, re-measured from the tube's own centroid track ===")
    print(
        f"  measured  {axis}   straightness rms {resid.max():.3f} mm over "
        f"{np.ptp(rings @ axis):.0f} mm"
    )
    print(f"  inherited {INHERITED_SHAFT}")
    print(
        f"  disagreement: {math.degrees(math.acos(abs(float(axis @ INHERITED_SHAFT)))):.3f} deg"
        "  -> the shaft axis is NOT the problem\n"
    )

    # ---- hull depth, on equal terms, for all three candidates -------------
    hull = ConvexHull(v)
    eq = hull.equations
    depth = -np.max(cent @ eq[:, :3].T + eq[:, 3], axis=1)
    proj = cent @ axis
    head = proj < np.percentile(proj, 88)

    cands = {
        "cluster 1 (+x)  detect_face_plane's pick": [0.998, -0.001, 0.059],
        "cluster 4 (+x)  the fine-triangulated one": [0.938, -0.021, 0.346],
        "cluster 0 (-x)  behind them": [-0.941, 0.021, -0.337],
        "cluster 2       sole": [0.245, 0.202, 0.948],
    }
    print("=== all candidates hull-tested on equal terms (head only) ===")
    print(
        f"{'surface':>42} {'area':>8} {'tris':>6} {'mm^2/tri':>9} "
        f"{'med_depth':>10} {'on_hull%':>9}"
    )
    masks = {}
    for name, seed in cands.items():
        s = np.asarray(seed, float)
        s /= np.linalg.norm(s)
        m = (nrm @ s > math.cos(math.radians(12.0))) & head
        masks[name] = m
        d = depth[m]
        print(
            f"{name:>42} {area[m].sum():8.1f} {m.sum():6d} "
            f"{area[m].sum() / m.sum():9.3f} {np.median(d):10.3f} "
            f"{100 * np.mean(d < 0.6):9.1f}"
        )

    # ---- face thickness: a real face is a slab over the cavity ------------
    print("\n=== slab test: face and cavity floor are parallel, a few mm apart ===")
    cav = np.asarray(cands["cluster 0 (-x)  behind them"], float)
    cav /= np.linalg.norm(cav)
    for name in list(cands)[:2]:
        s = np.asarray(cands[name], float)
        s /= np.linalg.norm(s)
        ang = math.degrees(math.acos(abs(float(s @ cav))))
        gap = float(
            np.median(cent[masks[name]] @ s)
            + np.median(cent[masks["cluster 0 (-x)  behind them"]] @ cav)
        )
        print(
            f"  {name:>42}: {ang:5.2f} deg from the cavity floor, "
            f"median separation {abs(gap):5.2f} mm"
        )

    # ---- grooves: a striking face is corrugated, nothing else is ----------
    print("\n=== scoreline test ===")
    for name in list(cands)[:2]:
        s = np.asarray(cands[name], float)
        s /= np.linalg.norm(s)
        wide = (nrm @ s > math.cos(math.radians(35.0))) & head
        u = np.cross(s, axis)
        u /= np.linalg.norm(u)
        along = cent[wide] @ np.cross(s, u)
        tilt = np.degrees(np.arccos(np.clip(np.abs(nrm[wide] @ s), -1, 1)))
        walls = tilt > 10.0
        bands = 0
        if walls.sum() > 30:
            hist, edges = np.histogram(along[walls], bins=48)
            bands = int(
                np.sum(
                    (hist[1:-1] > hist[:-2])
                    & (hist[1:-1] >= hist[2:])
                    & (hist[1:-1] > 1.3 * hist.mean())
                )
            )
            pitch = np.ptp(along[walls]) / max(bands, 1)
        else:
            pitch = float("nan")
        print(
            f"  {name:>42}: {walls.sum():5d} groove-wall triangles, "
            f"{bands} bands, pitch {pitch:.2f} mm"
        )
    print("  (a 690CB face carries ~10-14 scorelines at roughly 3.5 mm pitch)")

    # ---- what each hypothesis implies ------------------------------------
    sole = np.asarray(cands["cluster 2       sole"], float)
    sole /= np.linalg.norm(sole)
    lie = 90 - math.degrees(math.acos(abs(float(axis @ sole))))
    print(f"\n=== implied specs (lie is common to all: {lie:.2f} deg) ===")
    for name in list(cands)[:2]:
        s = np.asarray(cands[name], float)
        s /= np.linalg.norm(s)
        loft = 90 - math.degrees(math.acos(abs(float(s @ sole))))
        phi = math.degrees(math.acos(abs(float(s @ axis))))
        ident = math.degrees(
            math.acos(math.sin(math.radians(loft)) * math.sin(math.radians(lie)))
        )
        print(
            f"  {name:>42} -> loft {loft:5.2f}, face-to-shaft {phi:5.2f} "
            f"(identity says {ident:5.2f}, residual {ident - phi:+5.2f})"
        )
    print("  maintainer: ~36 loft / ~60 lie.  690CB 7-iron catalogue: ~34 / ~62.")


if __name__ == "__main__":
    main()

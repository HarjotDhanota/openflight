"""Why detect_face_plane anchors the mesh frame to the BACK of the club.

Rendering the mesh settled by eye what geometry alone had got wrong twice:

  the surface detect_face_plane picks  = the CAVITY RIM on the back
  the surface I called "the cavity"    = the STRIKING FACE (grooves visible)
  the surface I called "the sole"      = the SOLE (correct, it carries the stamping)

This file confirms that computationally, finds which of detect_face_plane's
three gates rejects the real face, and recomputes loft and lie.

It also repairs the earlier hull test, which was invalid: the convex hull was
built over ALL vertices INCLUDING THE SHAFT, so hull facets spanning from the
shaft tip across the head cut the corner over the striking face and made every
point on it read as several millimetres "inside". The hull must be built on; head-only vertices.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
from scipy.spatial import ConvexHull

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "research"))
from silhouette_poc.generator.mesh_truth import (  # noqa: E402
    _triangle_adjacency,
    _welded_faces,
)

MESH = ROOT / "research/silhouette_poc/meshes/assets/poc_7iron.npz"
SHAFT = np.array([-0.245, 0.295, -0.924])
SHAFT /= np.linalg.norm(SHAFT)
FACE_REAL = np.array([-0.941, 0.021, -0.337])
FACE_REAL /= np.linalg.norm(FACE_REAL)
RIM_PICKED = np.array([0.998, -0.001, 0.059])
RIM_PICKED /= np.linalg.norm(RIM_PICKED)
SOLE = np.array([0.245, 0.202, 0.948])
SOLE /= np.linalg.norm(SOLE)

ASPECT_BOUNDS = (0.35, 0.65)
NORMAL_TOL_DEG = 15.0


def main():
    z = np.load(MESH)
    v = np.asarray(z["vertices_local_mm"], float)
    f = np.asarray(z["faces"], int)
    tri = v[f]
    cross = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    da = np.linalg.norm(cross, axis=1)
    valid = da > 1e-10
    nrm = np.zeros_like(cross)
    nrm[valid] = cross[valid] / da[valid, None]
    area = da / 2.0
    cent = tri.mean(1)

    # ---- 1. repair the hull test: head-only hull -------------------------
    proj = cent @ SHAFT
    head_tri = proj < np.percentile(proj, 88)
    head_v = np.unique(f[head_tri].reshape(-1))
    for label, pts in (
        ("ALL vertices (what I used before)", v),
        ("HEAD ONLY (correct)", v[head_v]),
    ):
        hull = ConvexHull(pts)
        eq = hull.equations
        d = -np.max(cent @ eq[:, :3].T + eq[:, 3], axis=1)
        print(f"\n=== hull depth, {label} ===")
        for name, seed in (
            ("STRIKING FACE (purple)", FACE_REAL),
            ("cavity rim (what is picked)", RIM_PICKED),
            ("sole (green)", SOLE),
        ):
            m = (nrm @ seed > math.cos(math.radians(12.0))) & head_tri
            print(
                f"  {name:>30}: median {np.median(d[m]):7.3f} mm inside, "
                f"{100 * np.mean(d[m] < 0.6):5.1f}% on hull, area {area[m].sum():7.1f} mm2"
            )

    # ---- 2. reproduce detect_face_plane's regions ------------------------
    print("\n=== detect_face_plane's own candidate regions ===")
    _, welded = _welded_faces(v, f)
    adj = _triangle_adjacency(welded)
    cos_lim = math.cos(math.radians(NORMAL_TOL_DEG))
    unassigned = set(np.flatnonzero(valid).tolist())
    mesh_c = v.mean(0)
    rows = []
    while unassigned:
        seed = max(unassigned, key=lambda i: float(area[i]))
        sn = nrm[seed]
        region, pending = set(), [seed]
        while pending:
            cur = pending.pop()
            if cur not in unassigned:
                continue
            if abs(float(nrm[cur] @ sn)) < cos_lim:
                continue
            unassigned.remove(cur)
            region.add(cur)
            pending.extend(adj[cur] & unassigned)
        if not region:
            continue
        idx = np.asarray(sorted(region), dtype=np.int32)
        al = nrm[idx] * np.sign(nrm[idx] @ sn)[:, None]
        n = (al * area[idx, None]).sum(0)
        n /= np.linalg.norm(n)
        vid = np.unique(f[idx].reshape(-1))
        pts = v[vid]
        c = np.average(cent[idx], weights=area[idx], axis=0)
        if float(n @ (c - mesh_c)) < 0:
            n = -n
        cc = pts - c
        planar = cc - np.outer(cc @ n, n)
        axes = np.linalg.svd(planar, full_matrices=False)[2]
        wa = axes[0] - n * float(axes[0] @ n)
        wa /= np.linalg.norm(wa)
        ha = np.cross(n, wa)
        spans = np.array([np.ptp(pts @ wa), np.ptp(pts @ ha)])
        if spans[1] > spans[0]:
            spans = spans[::-1]
        aspect = float(spans[1] / max(spans[0], 1e-12))
        pj = v @ n
        extremity = min(
            abs(float(c @ n) - float(pj.min())), abs(float(pj.max()) - float(c @ n))
        )
        depth = float(np.ptp(pj))
        flat = float(np.max(np.abs(cc @ n)))
        ok_a = ASPECT_BOUNDS[0] <= aspect <= ASPECT_BOUNDS[1]
        ok_e = extremity <= max(1.0, 0.10 * depth)
        ok_f = flat <= max(1.0, 0.04 * spans[0])
        rows.append(
            (
                float(area[idx].sum()),
                n,
                aspect,
                extremity,
                flat,
                spans[0],
                ok_a,
                ok_e,
                ok_f,
                len(idx),
            )
        )
    rows.sort(key=lambda r: -r[0])

    def whose(n):
        for lbl, s in (("FACE", FACE_REAL), ("rim", RIM_PICKED), ("sole", SOLE)):
            if abs(float(n @ s)) > math.cos(math.radians(15.0)):
                return lbl
        return "-"

    print(
        f"{'area':>8} {'tris':>6} {'what':>5} {'aspect':>7} {'extrem':>7} {'flat':>7} "
        f"{'gates a/e/f':>12}  accepted"
    )
    for a, n, asp, ext, flat, span, oa, oe, of, nt in rows[:14]:
        print(
            f"{a:8.1f} {nt:6d} {whose(n):>5} {asp:7.3f} {ext:7.2f} {flat:7.2f} "
            f"{str(oa)[0]}/{str(oe)[0]}/{str(of)[0]:>6}  {oa and oe and of}"
        )
    acc = [r for r in rows if r[6] and r[7] and r[8]]
    print(
        f"\n  {len(acc)} regions pass all three gates; the winner is the largest of THOSE."
    )
    if acc:
        print(f"  winner: area {acc[0][0]:.1f} mm2, which is the {whose(acc[0][1])}")
    face_regions = [r for r in rows if whose(r[1]) == "FACE"]
    print(
        f"\n  regions belonging to the REAL striking face: {len(face_regions)}, "
        f"total area {sum(r[0] for r in face_regions):.1f} mm2"
    )
    for a, n, asp, ext, flat, span, oa, oe, of, nt in face_regions[:6]:
        why = [
            g
            for g, ok in (("aspect", oa), ("extremity", oe), ("flatness", of))
            if not ok
        ]
        print(
            f"    area {a:7.1f} ({nt:5d} tris) aspect {asp:.3f} -> "
            f"rejected by {', '.join(why) if why else 'nothing'}"
        )

    # ---- 3. the corrected specs -----------------------------------------
    print("\n=== corrected specs, using the face the render identifies ===")
    for label, fn in (
        ("STRIKING FACE (purple, grooved)", FACE_REAL),
        ("cavity rim (currently used as FACE_NORMAL)", RIM_PICKED),
    ):
        loft = 90 - math.degrees(math.acos(abs(float(fn @ SOLE))))
        lie = 90 - math.degrees(math.acos(abs(float(SHAFT @ SOLE))))
        phi = math.degrees(math.acos(abs(float(fn @ SHAFT))))
        print(
            f"  {label:>44}: loft {loft:6.2f}  lie {lie:6.2f}  face-to-shaft {phi:6.2f}"
        )
    print(
        f"  {'a 36 deg / 60 deg club requires':>44}: "
        f"loft  36.00  lie  60.00  face-to-shaft  59.40"
    )
    print(
        f"  {'the 690CB catalogue (34/62) requires':>44}: "
        f"loft  34.00  lie  62.00  face-to-shaft  60.41"
    )
    print(
        f"\n  angle between the picked rim and the real face: "
        f"{math.degrees(math.acos(abs(float(RIM_PICKED @ FACE_REAL)))):.2f} deg "
        f"(and they point OPPOSITE ways: dot = {float(RIM_PICKED @ FACE_REAL):+.3f})"
    )


if __name__ == "__main__":
    main()

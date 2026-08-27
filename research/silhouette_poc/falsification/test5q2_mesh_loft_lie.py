"""Handoff section 5 Q2 -- settle the mesh's face-to-shaft angle without CAD.

Step 1 (already established from the manifest): the 7-iron was admitted with
``source_units_mm: true``, so ``normalize_clubhead`` skipped its ANISOTROPIC
``local *= target / ptp(local)`` rescale. The recorded face span is identical
before and after normalisation, so the transform was rigid and every angle in
the mesh is faithful to the source CAD. The measured 77.89 deg is therefore a
real property of the model, not a normalisation artefact.

Step 2 (this file): 77.89 deg is inconsistent with 36 deg loft / 60 deg lie.
Loft and lie are DEFINED with the club soled and the face square, and in that
frame the face-normal-to-shaft angle is exactly arccos(sin(loft) sin(lie)) --
59.4 deg for 36/60. That is a definition, not an extra assumption, so the
18.5 deg disagreement is real. What is NOT yet known is which end is wrong:
the head or the shaft stub.

So measure the head on its own. The SOLE gives the ground plane, and against
that plane the face normal gives loft and the shaft axis gives lie, with no
reference to each other. If the loft measures ~34-36 deg the head is right and
the shaft stub is mis-oriented; if it measures ~14 deg the model is not a
36 deg club at all.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "research"))

MESH = ROOT / "research/silhouette_poc/meshes/assets/poc_7iron.npz"


def load():
    z = np.load(MESH)
    keys = list(z.keys())
    verts = z[[k for k in keys if "vert" in k.lower()][0]]
    faces = z[[k for k in keys if "face" in k.lower()][0]]
    return np.asarray(verts, float).reshape(-1, 3), np.asarray(faces, int)


def face_normals(v, f):
    a, b, c = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
    n = np.cross(b - a, c - a)
    area = np.linalg.norm(n, axis=1)
    ok = area > 1e-12
    return n[ok] / area[ok, None], area[ok] / 2.0, ok


def largest_planar_region(v, f, n, area, seed_dir, tol_deg=8.0):
    """Total-area-weighted plane whose normal is nearest ``seed_dir``."""
    cos = n @ (seed_dir / np.linalg.norm(seed_dir))
    keep = cos > math.cos(math.radians(tol_deg))
    if keep.sum() < 5:
        return None
    w = area[keep]
    mean = (n[keep] * w[:, None]).sum(0)
    return mean / np.linalg.norm(mean), float(w.sum()), int(keep.sum())


def main():
    v, f = load()
    n, area, ok = face_normals(v, f)
    f = f[ok]
    print(f"mesh: {len(v)} vertices, {len(f)} triangles")
    print(f"bbox extents (mm): {np.ptp(v, axis=0)}")

    # Face plane: the normalised frame anchors the face normal to +x.
    face_n = np.array([1.0, 0.0, 0.0])
    fn, fa, fc = largest_planar_region(v, f, n, area, face_n, tol_deg=10.0)
    print(f"\nFACE  normal {fn}  area {fa:.1f} mm^2  ({fc} triangles)")

    # Sole: the club rests on it. Search all directions on a hemisphere for the
    # supporting plane with the largest near-tangent area -- the flat the club
    # actually sits on -- restricted to directions roughly perpendicular to the
    # face normal, which is what "soled with a square face" means.
    best = None
    for theta in np.arange(60.0, 121.0, 1.0):  # angle from the face normal
        for phi in np.arange(0.0, 360.0, 1.0):
            st, ct = math.sin(math.radians(theta)), math.cos(math.radians(theta))
            d = np.array(
                [ct, st * math.cos(math.radians(phi)), st * math.sin(math.radians(phi))]
            )
            got = largest_planar_region(v, f, n, area, d, tol_deg=12.0)
            if got is None:
                continue
            nn, aa, cc = got
            if best is None or aa > best[1]:
                best = (nn, aa, cc, theta, phi)
    sole_n, sole_a, sole_c, th, ph = best
    print(
        f"SOLE  normal {sole_n}  area {sole_a:.1f} mm^2  ({sole_c} triangles) "
        f"[theta {th:.0f} phi {ph:.0f}]"
    )

    # Shaft axis as recorded in the handoff, measured from the hosel cylinder.
    shaft = np.array([-0.204, 0.288, -0.936])
    shaft = shaft / np.linalg.norm(shaft)

    def ang(a, b):
        return math.degrees(math.acos(abs(float(np.clip(a @ b, -1, 1)))))

    print("\n=== rotation-invariant angles in the mesh ===")
    phi_fs = math.degrees(math.acos(float(np.clip(abs(fn @ shaft), -1, 1))))
    print(
        f"  face normal  <-> shaft axis : {phi_fs:6.2f} deg   (handoff measured 77.89)"
    )
    print(f"  face normal  <-> sole normal: {ang(fn, sole_n):6.2f} deg")
    print(f"  shaft axis   <-> sole normal: {ang(shaft, sole_n):6.2f} deg")

    print("\n=== implied specs, measured against the SOLE (the ground plane) ===")
    loft = 90.0 - ang(fn, sole_n)
    lie = 90.0 - ang(shaft, sole_n)
    print(f"  LOFT (90 - angle(face normal, ground normal)) = {loft:6.2f} deg")
    print(f"  LIE  (90 - angle(shaft axis,  ground normal)) = {lie:6.2f} deg")
    print("  maintainer states the CAD is ~36 deg loft, ~60 deg lie")
    print("\n  consistency identity: arccos(sin loft * sin lie) must equal the")
    print("  face-to-shaft angle for ANY club (this is the definition of loft")
    print("  and lie, not an added assumption):")
    pred = math.degrees(
        math.acos(
            max(min(math.sin(math.radians(loft)) * math.sin(math.radians(lie)), 1), -1)
        )
    )
    print(f"    from measured loft/lie -> {pred:6.2f} deg")
    print(f"    directly measured       -> {phi_fs:6.2f} deg")
    print(f"    residual                -> {pred - phi_fs:+6.2f} deg")
    for L, La, label in (
        (36.0, 60.0, "maintainer's stated 36/60"),
        (34.0, 62.0, "Titleist 690CB 7-iron catalogue"),
    ):
        p = math.degrees(
            math.acos(math.sin(math.radians(L)) * math.sin(math.radians(La)))
        )
        print(f"    {label:34s} -> {p:6.2f} deg")


if __name__ == "__main__":
    main()

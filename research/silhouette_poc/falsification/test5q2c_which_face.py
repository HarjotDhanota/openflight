"""Section 5 Q2, decisive check: WHICH surface is the striking face?

The plane census found two candidates on opposite sides of the head:

  cluster 1  normal +x, 1268 mm^2, span 78.2 x 46.0 mm  <- what detect_face_plane
  picked, and what the
  normalised frame is
  anchored to
  cluster 0  normal -x, 2965 mm^2

Paired with the sole they imply 17.5 deg and 33.1 deg of loft respectively.
The maintainer states ~36 deg; the Titleist 690CB 7-iron catalogue is ~34 deg.
So the answer decides whether the mesh frame is anchored to the right side of
the club.

The 690CB is a CAVITY BACK. Its striking face lies on the convex hull over its
whole area; its cavity floor is recessed several millimetres inside the hull.
That is a clean discriminator that needs no catalogue value: measure how far; each cluster sits inside the head's own convex hull.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from scipy.spatial import ConvexHull

ROOT = Path(__file__).resolve().parents[3]
MESH = ROOT / "research/silhouette_poc/meshes/assets/poc_7iron.npz"
SHAFT = np.array([-0.204, 0.288, -0.936])
SHAFT /= np.linalg.norm(SHAFT)


def main():
    z = np.load(MESH)
    v = np.asarray(z["vertices_local_mm"], float)
    f = np.asarray(z["faces"], int)
    a, b, c = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
    nrm = np.cross(b - a, c - a)
    mag = np.linalg.norm(nrm, axis=1)
    ok = mag > 1e-12
    nrm, area = nrm[ok] / mag[ok, None], mag[ok] / 2.0
    cent = (a[ok] + b[ok] + c[ok]) / 3.0

    # Isolate the HEAD: drop triangles far along the shaft. The shaft stub
    # inflates cluster areas and spans and must not decide a face question.
    proj = cent @ SHAFT
    head = proj < np.percentile(proj, 88)
    print(
        f"head selection keeps {head.sum()}/{len(cent)} triangles "
        f"({area[head].sum():.0f} of {area.sum():.0f} mm^2)"
    )

    hull = ConvexHull(v)
    # signed distance of each centroid INSIDE the hull (0 = on the surface)
    eqs = hull.equations
    depth = -np.max(cent @ eqs[:, :3].T + eqs[:, 3], axis=1)

    def cluster(seed, tol=12.0):
        cos = nrm @ (seed / np.linalg.norm(seed))
        return (cos > math.cos(math.radians(tol))) & head

    cands = {
        "cluster 0 (-x)": np.array([-0.941, 0.021, -0.337]),
        "cluster 1 (+x)": np.array([0.998, -0.001, 0.059]),
        "cluster 2 (sole)": np.array([0.245, 0.202, 0.948]),
    }
    print(
        f"\n{'surface':>18} {'area_mm2':>9} {'tris':>6} {'median_depth':>13} "
        f"{'p90_depth':>10} {'on_hull_%':>10} {'tri_mm2':>8}"
    )
    for name, seed in cands.items():
        m = cluster(seed)
        if m.sum() == 0:
            print(f"{name:>18}  none")
            continue
        d = depth[m]
        on = float(np.mean(d < 0.6) * 100.0)
        print(
            f"{name:>18} {area[m].sum():9.1f} {m.sum():6d} {np.median(d):13.3f} "
            f"{np.percentile(d, 90):10.3f} {on:10.1f} "
            f"{area[m].sum() / m.sum():8.3f}"
        )
    print("\n  median_depth is millimetres INSIDE the convex hull.")
    print("  A striking face sits on the hull (~0). A cavity floor is recessed.")
    print("  tri_mm2 is mean triangle area: a grooved face is finely triangulated.")

    # groove test: scorelines are a periodic corrugation across the face
    print("\n=== scoreline test (an iron face is grooved; a cavity floor is not) ===")
    for name, seed in list(cands.items())[:2]:
        m = cluster(seed, tol=25.0)  # wider: groove walls tilt away
        n_mean = (nrm[m] * area[m][:, None]).sum(0)
        n_mean /= np.linalg.norm(n_mean)
        # in-plane axes
        u = np.cross(n_mean, SHAFT)
        u /= np.linalg.norm(u)
        w = np.cross(n_mean, u)
        s = cent[m] @ w  # position across the face (groove axis)
        tilt = np.degrees(np.arccos(np.clip(np.abs(nrm[m] @ n_mean), -1, 1)))
        # count triangles tilted >8 deg out of the mean plane: groove walls
        walls = tilt > 8.0
        if walls.sum() > 20:
            hist, edges = np.histogram(s[walls], bins=40)
            peaks = int(
                np.sum(
                    (hist[1:-1] > hist[:-2])
                    & (hist[1:-1] > hist[2:])
                    & (hist[1:-1] > hist.mean())
                )
            )
        else:
            peaks = 0
        print(
            f"  {name:>16}: {m.sum():5d} tris, {walls.sum():5d} tilted >8 deg "
            f"({100 * walls.mean():.0f}%), {peaks} periodic bands across the face"
        )

    print("\n=== implied specs under each hypothesis ===")
    sole = cands["cluster 2 (sole)"] / np.linalg.norm(cands["cluster 2 (sole)"])
    lie = 90 - math.degrees(math.acos(abs(float(SHAFT @ sole))))
    for name, seed in list(cands.items())[:2]:
        s = seed / np.linalg.norm(seed)
        loft = 90 - math.degrees(math.acos(abs(float(s @ sole))))
        phi = math.degrees(math.acos(abs(float(s @ SHAFT))))
        print(
            f"  {name:>16} as the face -> loft {loft:5.2f}, lie {lie:5.2f}, "
            f"face-to-shaft {phi:5.2f} deg"
        )
    print("  maintainer: ~36 loft / ~60 lie.  690CB 7-iron catalogue: ~34 / ~62.")


if __name__ == "__main__":
    main()

"""Section 5 Q2, final check: adjacency. On an iron the FACE meets the SOLE at; a sharp leading edge; the back CAVITY never touches the sole -- sole metal and
the muscle sit between them. Distance settles which surface is which without; any catalogue value."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

ROOT = Path(__file__).resolve().parents[3]
MESH = ROOT / "research/silhouette_poc/meshes/assets/poc_7iron.npz"
SHAFT = np.array([-0.204, 0.288, -0.936])
SHAFT /= np.linalg.norm(SHAFT)

z = np.load(MESH)
v = np.asarray(z["vertices_local_mm"], float)
f = np.asarray(z["faces"], int)
a, b, c = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
nr = np.cross(b - a, c - a)
mg = np.linalg.norm(nr, axis=1)
ok = mg > 1e-12
nr, ar, cn = nr[ok] / mg[ok, None], mg[ok] / 2, (a[ok] + b[ok] + c[ok]) / 3
head = (cn @ SHAFT) < np.percentile(cn @ SHAFT, 88)


def cl(seed, tol=12.0):
    s = np.asarray(seed, float)
    s /= np.linalg.norm(s)
    return (nr @ s > math.cos(math.radians(tol))) & head


named = {
    "face(+x)": [0.998, -0.001, 0.059],
    "cavity(-x)": [-0.941, 0.021, -0.337],
    "soleA": [0.245, 0.202, 0.948],
    "soleB": [0.390, 0.143, -0.910],
}
pts = {k: cn[cl(sd)] for k, sd in named.items()}
print(f"{'surface':>11} " + " ".join(f"{k:>12}" for k in named))
for k, p in pts.items():
    row = []
    for k2, q in pts.items():
        row.append(0.0 if k == k2 else float(cKDTree(q).query(p)[0].min()))
    print(f"{k:>11} " + " ".join(f"{x:12.3f}" for x in row))
print("\nminimum surface-to-surface distance, mm. The face TOUCHES the sole at")
print("the leading edge (~0). The cavity floor cannot -- metal lies between.")
sole = np.array(named["soleA"], float)
sole /= np.linalg.norm(sole)
lie = 90 - math.degrees(math.acos(abs(float(SHAFT @ sole))))
for k in ("face(+x)", "cavity(-x)"):
    s = np.array(named[k], float)
    s /= np.linalg.norm(s)
    print(
        f"  {k:>11} as face -> loft {90 - math.degrees(math.acos(abs(float(s @ sole)))):5.2f}  lie {lie:5.2f}"
    )

"""Section 5 Q2, check: enumerate the mesh's dominant planes.

The loft result rests on having identified the striking face and the sole
correctly. Rather than assert those, census every dominant planar region by
area-weighted normal clustering and report each one's angle to the shaft and
to the others. The true face and sole should be identifiable on their own
terms, and if some OTHER large plane sits 54 deg from the sole normal -- the
signature of a 36 deg face -- this will surface it.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
MESH = ROOT / "research/silhouette_poc/meshes/assets/poc_7iron.npz"
SHAFT = np.array([-0.204, 0.288, -0.936])
SHAFT = SHAFT / np.linalg.norm(SHAFT)


def main():
    z = np.load(MESH)
    v = np.asarray(z["vertices_local_mm"], float)
    f = np.asarray(z["faces"], int)
    a, b, c = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
    nrm = np.cross(b - a, c - a)
    mag = np.linalg.norm(nrm, axis=1)
    ok = mag > 1e-12
    nrm, area, f = nrm[ok] / mag[ok, None], mag[ok] / 2.0, f[ok]
    cent = (a[ok] + b[ok] + c[ok]) / 3.0

    # greedy area-weighted clustering on the sphere (12 deg cone)
    order = np.argsort(-area)
    used = np.zeros(len(nrm), bool)
    clusters = []
    for i in order:
        if used[i]:
            continue
        cos = nrm @ nrm[i]
        grp = (cos > math.cos(math.radians(12.0))) & ~used
        if area[grp].sum() < 60.0:
            used[i] = True
            continue
        used |= grp
        w = area[grp]
        mean = (nrm[grp] * w[:, None]).sum(0)
        mean /= np.linalg.norm(mean)
        clusters.append(
            dict(
                n=mean,
                area=float(w.sum()),
                count=int(grp.sum()),
                centroid=(cent[grp] * w[:, None]).sum(0) / w.sum(),
                span=np.ptp(cent[grp], axis=0),
            )
        )
    clusters.sort(key=lambda d: -d["area"])
    clusters = clusters[:10]

    print(f"total mesh area {area.sum():.0f} mm^2, {len(f)} triangles")
    print(
        f"\n{'#':>2} {'area_mm2':>9} {'tris':>6} {'normal':>26} {'to_shaft':>9} "
        f"{'span (mm)':>26}"
    )
    for i, cl in enumerate(clusters):
        ang = math.degrees(math.acos(abs(float(np.clip(cl["n"] @ SHAFT, -1, 1)))))
        print(
            f"{i:>2} {cl['area']:9.1f} {cl['count']:6d} "
            f"[{cl['n'][0]:6.3f} {cl['n'][1]:6.3f} {cl['n'][2]:6.3f}] {ang:9.2f} "
            f"[{cl['span'][0]:6.1f} {cl['span'][1]:6.1f} {cl['span'][2]:6.1f}]"
        )

    print("\npairwise angle between cluster normals (deg, unsigned):")
    print("    " + " ".join(f"{i:>6}" for i in range(len(clusters))))
    for i, ci in enumerate(clusters):
        row = []
        for cj in clusters:
            row.append(
                math.degrees(math.acos(abs(float(np.clip(ci["n"] @ cj["n"], -1, 1)))))
            )
        print(f"{i:>3} " + " ".join(f"{x:6.1f}" for x in row))

    print("\nIf cluster A is the FACE and cluster B is the SOLE then")
    print("  loft = 90 - angle(A, B)  and  lie = 90 - angle(shaft, B).")
    print("A 36 deg face would show angle(face, sole) = 54 deg.")
    print(
        f"\n{'face':>5} {'sole':>5} {'loft':>7} {'lie':>7}   (only plausible pairings shown)"
    )
    for i, ci in enumerate(clusters):
        for j, cj in enumerate(clusters):
            if i == j:
                continue
            ang_fs = math.degrees(
                math.acos(abs(float(np.clip(ci["n"] @ cj["n"], -1, 1))))
            )
            loft = 90.0 - ang_fs
            lie = 90.0 - math.degrees(
                math.acos(abs(float(np.clip(SHAFT @ cj["n"], -1, 1))))
            )
            if 5.0 <= loft <= 65.0 and 45.0 <= lie <= 75.0:
                print(
                    f"{i:>5} {j:>5} {loft:7.2f} {lie:7.2f}"
                    f"   (areas {ci['area']:.0f} / {cj['area']:.0f} mm^2)"
                )


if __name__ == "__main__":
    main()

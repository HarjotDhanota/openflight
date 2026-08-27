"""Render the 7-iron mesh so a human can identify its surfaces by eye.

The blocker on the loft question is not precision -- the shaft axis converges
to 2.5 deg from three unrelated seeds -- it is IDENTIFICATION. Nothing in an
STL says which surface is the striking face and which is the sole, and
inferring it from geometry alone is what produced the withdrawn 17.5 deg
result.

So draw the model's own triangles, coloured by which candidate surface they
belong to, from several viewpoints. Every pixel here is the mesh
the only; added geometry is the fitted shaft axis, which is itself a measurement.

Z-buffered orthographic rasteriser rather than matplotlib's painter algorithm,
which mis-orders interpenetrating geometry at this triangle count.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
MESH = ROOT / "research/silhouette_poc/meshes/assets/poc_7iron.npz"
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")

# candidate surfaces, from the plane census + hull test
CANDIDATES = {
    "face": ([0.998, -0.001, 0.059], (232, 92, 62)),  # on hull, 0.17 mm deep
    "rival_fine": ([0.938, -0.021, 0.346], (86, 148, 214)),  # recessed 5.76 mm
    "rival_back": ([-0.941, 0.021, -0.337], (150, 110, 190)),  # recessed 4.38 mm
    "sole_cand": ([0.245, 0.202, 0.948], (86, 176, 110)),  # the assumed sole
}
BASE = (176, 176, 168)


def load():
    z = np.load(MESH)
    v = np.asarray(z["vertices_local_mm"], float)
    f = np.asarray(z["faces"], int)
    a, b, c = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
    n = np.cross(b - a, c - a)
    m = np.linalg.norm(n, axis=1)
    ok = m > 1e-12
    return v, f[ok], n[ok] / m[ok, None]


def colour_by_cluster(nrm, tol_deg=12.0):
    col = np.tile(np.array(BASE, float), (len(nrm), 1))
    for name, (seed, rgb) in CANDIDATES.items():
        s = np.asarray(seed, float)
        s /= np.linalg.norm(s)
        m = nrm @ s > math.cos(math.radians(tol_deg))
        col[m] = rgb
    return col


def render(v, f, nrm, col, view_dir, up, size=760, pad=1.10):
    """Z-buffered orthographic render. view_dir points FROM camera TO model."""
    w = np.asarray(view_dir, float)
    w /= np.linalg.norm(w)
    u = np.cross(up, w)
    if np.linalg.norm(u) < 1e-6:
        u = np.cross([1.0, 0, 0], w)
    u /= np.linalg.norm(u)
    vv = np.cross(w, u)
    R = np.stack([u, vv, w])
    p = v @ R.T
    lo, hi = p[:, :2].min(0), p[:, :2].max(0)
    ctr, span = (lo + hi) / 2, (hi - lo).max() * pad
    scale = size / span
    xy = (p[:, :2] - ctr) * scale + size / 2
    xy[:, 1] = size - xy[:, 1]
    depth = p[:, 2]

    img = np.full((size, size, 3), 250, float)
    zbuf = np.full((size, size), np.inf)
    light = np.array([-0.35, 0.45, -1.0])
    light /= np.linalg.norm(light)
    shade = np.clip(-(nrm @ R.T) @ np.array([0.0, 0.0, 1.0]), 0, 1)
    shade = 0.30 + 0.70 * np.clip(
        0.45 * shade + 0.55 * np.clip(nrm @ -light, 0, 1), 0, 1
    )

    tri = xy[f]
    tz = depth[f]
    xmin = np.floor(tri[:, :, 0].min(1)).astype(int)
    xmax = np.ceil(tri[:, :, 0].max(1)).astype(int)
    ymin = np.floor(tri[:, :, 1].min(1)).astype(int)
    ymax = np.ceil(tri[:, :, 1].max(1)).astype(int)
    order = np.argsort(tz.mean(1))
    for i in order:
        x0, x1 = max(xmin[i], 0), min(xmax[i] + 1, size)
        y0, y1 = max(ymin[i], 0), min(ymax[i] + 1, size)
        if x1 <= x0 or y1 <= y0:
            continue
        ax, ay = tri[i, 0]
        bx, by = tri[i, 1]
        cx, cy = tri[i, 2]
        den = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        if abs(den) < 1e-9:
            continue
        ys, xs = np.mgrid[y0:y1, x0:x1]
        px, py = xs + 0.5, ys + 0.5
        l1 = ((by - cy) * (px - cx) + (cx - bx) * (py - cy)) / den
        l2 = ((cy - ay) * (px - cx) + (ax - cx) * (py - cy)) / den
        l3 = 1.0 - l1 - l2
        inside = (l1 >= 0) & (l2 >= 0) & (l3 >= 0)
        if not inside.any():
            continue
        z = l1 * tz[i, 0] + l2 * tz[i, 1] + l3 * tz[i, 2]
        sub = zbuf[y0:y1, x0:x1]
        win = inside & (z < sub)
        if not win.any():
            continue
        sub[win] = z[win]
        img[y0:y1, x0:x1][win] = col[i] * shade[i]
    return np.clip(img, 0, 255).astype(np.uint8)


def main():
    v, f, nrm = load()
    col = colour_by_cluster(nrm)
    ctr = v.mean(0)
    v = v - ctr

    face = np.array(CANDIDATES["face"][0], float)
    face /= np.linalg.norm(face)
    sole = np.array(CANDIDATES["sole_cand"][0], float)
    sole /= np.linalg.norm(sole)
    shaft = np.array([-0.245, 0.295, -0.924])
    shaft /= np.linalg.norm(shaft)

    views = {
        "1_face_on": (-face, sole),
        "2_from_behind": (face, sole),
        "3_from_below_sole": (-sole, face),
        "4_from_above": (sole, face),
        "5_toe_on": (np.cross(face, sole), sole),
        "6_three_quarter": (
            -(face * 0.72 + sole * 0.45 + np.cross(face, sole) * 0.30),
            sole,
        ),
    }
    try:
        from PIL import Image
    except ImportError:
        print("Pillow not available; install pillow to write PNGs")
        return
    OUT.mkdir(parents=True, exist_ok=True)
    for name, (d, up) in views.items():
        img = render(v, f, nrm, col, d, up)
        p = OUT / f"mesh_{name}.png"
        Image.fromarray(img).save(p)
        print(f"wrote {p}")
    print("\ncolour key:")
    print(
        "  ORANGE  = the surface detect_face_plane anchors to (on the hull, 0.17 mm deep)"
    )
    print("  BLUE    = rival candidate, recessed 5.76 mm  -> would give loft 33.6 deg")
    print("  PURPLE  = the surface behind them, recessed 4.38 mm")
    print("  GREEN   = the plane assumed to be the sole (lie 61.5 deg came from this)")
    print("  GREY    = everything else")
    print(f"\nshaft axis used: {shaft}")
    print(
        f"  angle(orange face, shaft) = "
        f"{math.degrees(math.acos(abs(float(face @ shaft)))):.2f} deg"
    )
    print("  a 36 deg / 60 deg club requires 59.40 deg")


if __name__ == "__main__":
    main()

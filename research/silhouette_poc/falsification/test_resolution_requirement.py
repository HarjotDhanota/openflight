"""Would more resolution actually fix face angle? Find the requirement.

Every route tried so far lands at 6-11 deg of face angle, and the natural
conclusion is "we need more pixels". That is testable rather than assumable.

Two things decide it:

  1. Does the sensitivity VANISH near square? A projected length goes as
     cos(rotation), so its slope is zero at zero. If the leverage collapses as
     the face approaches square -- which is where golf shots live -- then more
     pixels multiply a signal that is heading to zero anyway, and resolution
     buys less than a linear scaling suggests.

  2. What plate scale reaches a useful face angle? Sweep it and read off the
     requirement, then check that against what the hardware can actually do.

Reported against an edge-localisation noise floor, swept too, because that
number was asserted earlier and deserves to be a parameter.
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

RANGE_MM = 1581.0
BASE_FX = 466.67  # nominal 2.8 mm lens, 2x subsampled
HEAD_MM = 80.0  # heel to toe


def major_axis(mesh, cam, yaw, pitch, roll, centre):
    m = render_mask_6dof(mesh, centre, yaw, pitch, roll, cam)
    if m is None:
        return None
    ys, xs = np.nonzero(m)
    if len(xs) < 20:
        return None
    p = np.stack([xs - xs.mean(), ys - ys.mean()])
    w = np.linalg.eigvalsh((p @ p.T) / len(xs))
    return 4.0 * math.sqrt(max(w.max(), 0.0))


def leverage(mesh, cam, centre, yaw0, eps=2.0):
    """px of major-axis change per degree of yaw, at a given yaw offset."""
    vals = []
    for pitch in (-10.0, 0.0, 10.0):
        for roll in (-20.0, 0.0, 20.0):
            a = major_axis(mesh, cam, yaw0 - eps, pitch, roll, centre)
            b = major_axis(mesh, cam, yaw0 + eps, pitch, roll, centre)
            if a is None or b is None:
                continue
            vals.append(abs(b - a) / (2 * eps))
    return float(np.median(vals)) if vals else float("nan")


def main():
    d = np.load(ROOT / "research/silhouette_poc/meshes/assets/poc_7iron.npz")
    mesh = TriangleMesh(d["vertices_local_mm"], d["faces"], "poc_7iron", "x" * 64)
    cam = measured_camera(320, 200)
    centre = CAMERA_CENTER_WORLD + np.array([RANGE_MM, 0.0, 0.0])

    print("=== 1. does the leverage vanish near square? ===")
    print(f"{'face angle':>12} {'major-axis leverage':>22} {'yaw resolution':>18}")
    print(f"{'(deg open)':>12} {'px per deg':>22} {'at 0.5 px noise':>18}")
    for yaw0 in (0.0, 2.0, 5.0, 10.0, 20.0, 30.0):
        lev = leverage(mesh, cam, centre, yaw0)
        res = 0.5 / lev if lev > 1e-9 else float("inf")
        print(f"{yaw0:>12.0f} {lev:>22.4f} {res:>15.1f} deg")
    print("  A real shot is within a few degrees of square, which is the top of")
    print("  this table -- exactly where the cue is weakest.")

    print("\n=== 2. what plate scale would reach a useful face angle? ===")
    # leverage scales linearly with plate scale; measure it once, then scale
    lev_ref = leverage(mesh, cam, centre, 5.0)
    scale_ref = BASE_FX / RANGE_MM
    print(
        f"reference: {lev_ref:.4f} px/deg at {scale_ref:.3f} px/mm "
        f"(head {HEAD_MM * scale_ref:.1f} px), measured at 5 deg open"
    )
    print(
        f"\n{'multiple':>9} {'plate scale':>12} {'head':>8} "
        f"{'face angle at edge noise':>34}"
    )
    print(
        f"{'of today':>9} {'px/mm':>12} {'px':>8} "
        f"{'0.3 px':>11} {'0.5 px':>11} {'1.0 px':>10}"
    )
    for mult in (1, 2, 4, 6, 8, 12):
        sc = scale_ref * mult
        lev = lev_ref * mult
        row = "  ".join(f"{n / lev:>9.2f}" for n in (0.3, 0.5, 1.0))
        print(f"{mult:>8}x {sc:>12.3f} {HEAD_MM * sc:>8.1f}   {row}")

    print("\n=== 3. what the hardware can actually reach ===")
    px_um, _lens_mm = 3.0, 2.8
    for label, sub, lens in (
        ("today: 320x200, 2x subsample", 2, 2.8),
        ("1:1 readout, same lens", 1, 2.8),
        ("1:1 readout, 6 mm lens", 1, 6.0),
        ("1:1 readout, 12 mm lens", 1, 12.0),
    ):
        fx = lens / (px_um * sub * 1e-3)
        sc = fx / RANGE_MM
        lev = lev_ref * (sc / scale_ref)
        hfov = math.degrees(2 * math.atan((1280 / sub) / 2 / fx))
        width_m = 2 * RANGE_MM * math.tan(math.radians(hfov / 2)) / 1000.0
        print(
            f"  {label:>30}: {sc:.3f} px/mm, head {HEAD_MM * sc:>5.1f} px, "
            f"face angle {0.5 / lev:>5.2f} deg, "
            f"HFOV {hfov:>4.1f} deg = {width_m:.2f} m wide"
        )

    print("\n  For comparison, the ball-direction route (section 11h):")
    print("    club path known to 5 deg -> face angle 2.26 deg")
    print("    club path known to 2 deg -> face angle 0.94 deg")


if __name__ == "__main__":
    main()

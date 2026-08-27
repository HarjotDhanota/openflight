"""Would modelling the shaft fix the orientation problem?

The silhouette leaves every rotational degree of freedom flat to +-10-14 deg
(test_dof_sensitivity). The maintainer's proposal is to match the shaft, which
is long, high-contrast and unambiguous in the image, instead of relying on the; head's outline.

A long lever arm should help -- but only for rotations that actually SWING the
shaft. Rotation ABOUT the shaft axis leaves its projection untouched no matter
how long it is. So measure the leverage per degree, for each degree of freedom,
in the fitter's own convention.

Reported as: degrees of change in the shaft's PROJECTED image direction per
degree of pose change. ~1 means the shaft tracks the rotation one-for-one and
pins it; ~0 means the shaft is blind to it.
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

from silhouette_poc.fusion.solver import CAMERA_CENTER_WORLD, _project  # noqa: E402
from silhouette_poc.replay.fit_real import measured_camera, triad  # noqa: E402

# Measured in the mesh frame (falsification/test5q2*).
SHAFT_LOCAL = np.array([-0.245, 0.295, -0.924])
SHAFT_LOCAL /= np.linalg.norm(SHAFT_LOCAL)
FACE_LOCAL = np.array([-0.941, 0.021, -0.337])
FACE_LOCAL /= np.linalg.norm(FACE_LOCAL)
SHAFT_LEN_MM = 900.0  # a real iron shaft, vs the 62 mm stub the mesh carries


def shaft_image_angle(yaw, pitch, roll, centre, cam):
    """Direction of the shaft's projection in the image, in degrees."""
    n, u, v = triad(yaw, pitch, roll)
    basis = np.column_stack([n, u, v])  # mesh local -> world
    world_dir = basis @ SHAFT_LOCAL
    a = _project(centre[None, :], cam)[0][0]
    b = _project((centre + world_dir * SHAFT_LEN_MM)[None, :], cam)[0][0]
    return math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))


def face_normal_world(yaw, pitch, roll):
    n, u, v = triad(yaw, pitch, roll)
    return np.column_stack([n, u, v]) @ FACE_LOCAL


def main():
    cam = measured_camera(320, 200)
    centre = CAMERA_CENTER_WORLD + np.array([1581.0, 0.0, 0.0])
    # a spread of plausible deliveries rather than one lucky pose
    bases = [
        (y, p, r)
        for y in (-20.0, 0.0, 20.0)
        for p in (-20.0, 0.0, 20.0)
        for r in (-30.0, 0.0, 30.0)
    ]
    eps = 1.0

    print(f"shaft modelled at {SHAFT_LEN_MM:.0f} mm (the mesh carries a 62 mm stub)")
    print(f"\n{'rotation':>22} {'shaft image dir':>17} {'face normal':>14}")
    print(f"{'':>22} {'deg per deg':>17} {'deg per deg':>14}")
    for i, name in enumerate(
        ("yaw   (FACE ANGLE)", "pitch (DYNAMIC LOFT)", "roll  (lie / toe-up)")
    ):
        d_shaft, d_face = [], []
        for base in bases:
            lo = list(base)
            hi = list(base)
            lo[i] -= eps
            hi[i] += eps
            a0 = shaft_image_angle(*lo, centre, cam)
            a1 = shaft_image_angle(*hi, centre, cam)
            delta = (a1 - a0 + 180.0) % 360.0 - 180.0
            d_shaft.append(abs(delta) / (2 * eps))
            f0, f1 = face_normal_world(*lo), face_normal_world(*hi)
            d_face.append(
                math.degrees(math.acos(max(min(float(f0 @ f1), 1.0), -1.0))) / (2 * eps)
            )
        print(f"{name:>22} {np.median(d_shaft):>17.3f} {np.median(d_face):>14.3f}")

    # the direction the shaft is blind to
    print("\n=== rotation ABOUT the shaft axis ===")
    d_shaft, d_face = [], []
    for base in bases:
        n, u, v = triad(*base)
        basis = np.column_stack([n, u, v])
        axis = basis @ SHAFT_LOCAL
        k = np.array(
            [[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]]
        )
        rot = (
            np.eye(3)
            + math.sin(math.radians(eps)) * k
            + (1 - math.cos(math.radians(eps))) * (k @ k)
        )
        a0 = _project(centre[None, :], cam)[0][0]
        s0 = basis @ SHAFT_LOCAL
        s1 = rot @ s0
        p0 = _project((centre + s0 * SHAFT_LEN_MM)[None, :], cam)[0][0]
        p1 = _project((centre + s1 * SHAFT_LEN_MM)[None, :], cam)[0][0]
        ang0 = math.degrees(math.atan2(p0[1] - a0[1], p0[0] - a0[0]))
        ang1 = math.degrees(math.atan2(p1[1] - a0[1], p1[0] - a0[0]))
        d_shaft.append(abs((ang1 - ang0 + 180.0) % 360.0 - 180.0) / eps)
        f0 = basis @ FACE_LOCAL
        f1 = rot @ f0
        d_face.append(
            math.degrees(math.acos(max(min(float(f0 @ f1), 1.0), -1.0))) / eps
        )
    print(
        f"{'about the shaft':>22} {np.median(d_shaft):>17.3f} "
        f"{np.median(d_face):>14.3f}"
    )
    print("\n  The shaft cannot see rotation about its own axis, by construction.")
    print(
        "  That rotation swings the face normal on a cone of half-angle "
        f"{math.degrees(math.acos(abs(float(SHAFT_LOCAL @ FACE_LOCAL)))):.1f} deg,"
    )
    print("  so it IS face angle and loft -- the two quantities impact location needs.")


if __name__ == "__main__":
    main()

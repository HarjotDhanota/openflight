"""Convert a fitted pose into the three angles a golfer would recognise.

The fit reports yaw, pitch and roll, which are deviations applied to the mesh's
own frame. Those are not checkable by eye and they are not what the product
reports. Worse, the mesh's frame does NOT sit square: its local +x axis points
out the BACK of the club, so `triad(0,0,0)` is a club facing the camera, and any
plausibility envelope written in those coordinates is measured from the wrong
zero.

This converts instead into world angles, using the club axes measured directly
off the mesh in `test5q2g_face_plane_bug.py`:

    dynamic loft  elevation of the striking-face normal above horizontal
    face angle    azimuth of that normal about vertical; 0 is square
    lie           elevation of the shaft above horizontal

Those have known physical envelopes, so they validate a fit without a reference
instrument. A 7-iron delivered with 60 degrees of dynamic loft, or a shaft 20
degrees off its lie, is wrong whatever it scores.

The pose is evaluated AT IMPACT rather than at the first fitted frame, since
that is the instant the product reports and the instant the envelopes describe.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "research"))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from silhouette_poc.replay.fit_real import triad  # noqa: E402
from silhouette_poc.replay.rigid_motion import rotation_from_omega_deg_s  # noqa: E402

from test_fused_refit import (  # noqa: E402
    DEFAULT_SWING_RADIUS_M,
    MPH_PER_MPS,
    axis_basis,
    build_context,
    omega_from_phase,
)
from test_meshfit_depth_ab import EXCLUDE, SESSION, club_masks  # noqa: E402

# Club axes in the mesh's own coordinates, measured off the mesh rather than
# assumed. Loft 33.10 deg, lie 61.19 deg -- consistent with a 690CB (34/62).
FACE_NORMAL_LOCAL = np.array([-0.941, 0.021, -0.337])
FACE_NORMAL_LOCAL /= np.linalg.norm(FACE_NORMAL_LOCAL)
SHAFT_LOCAL = np.array([-0.245, 0.295, -0.924])
SHAFT_LOCAL /= np.linalg.norm(SHAFT_LOCAL)

# Generous envelopes -- wider than any real delivery, so a pose outside one is
# wrong rather than merely unusual.
ENVELOPE = {
    "dynamic_loft_deg": (15.0, 50.0),
    "face_angle_deg": (-25.0, 25.0),
    "lie_deg": (45.0, 78.0),
}


def delivered_angles(basis_world):
    """Dynamic loft, face angle and lie in degrees from a local-to-world basis."""
    face = basis_world @ FACE_NORMAL_LOCAL
    shaft = basis_world @ SHAFT_LOCAL
    face = face / np.linalg.norm(face)
    shaft = shaft / np.linalg.norm(shaft)
    return {
        "dynamic_loft_deg": math.degrees(
            math.asin(max(-1.0, min(1.0, float(face[2]))))
        ),
        "face_angle_deg": math.degrees(math.atan2(float(face[1]), float(face[0]))),
        "lie_deg": math.degrees(math.asin(max(-1.0, min(1.0, abs(float(shaft[2])))))),
    }


def in_envelope(angles):
    return all(lo <= angles[key] <= hi for key, (lo, hi) in ENVELOPE.items())


def basis_at_impact(params, ctx):
    """The local-to-world basis at the moment of contact."""
    yaw, pitch, roll, phase = params
    normal, width, height = triad(yaw, pitch, roll)
    basis0 = np.column_stack((normal, width, height))
    omega = omega_from_phase(ctx["axis_basis"], phase, ctx["omega_mag"])
    impact_elapsed = float(
        np.interp(
            ctx["impact_frame"],
            np.arange(len(ctx["frame_ids"])) + ctx["frame_ids"][0],
            ctx["elapsed_s"],
        )
    )
    return rotation_from_omega_deg_s(omega, impact_elapsed) @ basis0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--fits", type=Path, default=Path(__file__).with_name("fused_refit.json")
    )
    parser.add_argument(
        "--shipped", action="store_true", help="also score the shipped 5-parameter fit"
    )
    parser.add_argument(
        "--output", type=Path, default=Path(__file__).with_name("delivered_angles.json")
    )
    args = parser.parse_args()

    fits = {
        int(r["shot"]): r for r in json.loads(args.fits.read_text(encoding="utf-8"))
    }
    with open(SESSION / "shots.csv", newline="", encoding="utf-8") as handle:
        rows = {
            int(r["shot_number"]): r
            for r in csv.DictReader(handle)
            if int(r["shot_number"]) not in EXCLUDE
        }

    results = []
    print(
        f"{'shot':>4} {'club':>7} {'dyn loft':>9} {'face ang':>9} {'lie':>7}   verdict"
    )
    for shot in sorted(fits):
        record = fits[shot]
        ctx, _camera, why = build_context(rows[shot], club_masks, record)
        if ctx is None:
            print(f"{shot:>4}  skipped -- {why}")
            continue
        angles = delivered_angles(basis_at_impact(record["params"], ctx))
        ok = in_envelope(angles)
        entry = {"shot": shot, "club": record["club"], "fused": angles, "fused_ok": ok}

        if args.shipped:
            shipped = record["shipped_params"]
            speed_ms = float(rows[shot]["club_speed_mph"]) / MPH_PER_MPS
            basis = axis_basis(ctx["velocity_mm_s"])
            shipped_ctx = dict(ctx)
            shipped_ctx["axis_basis"] = basis
            shipped_ctx["omega_mag"] = math.degrees(speed_ms / DEFAULT_SWING_RADIUS_M)
            shipped_angles = delivered_angles(
                basis_at_impact(list(shipped[:3]) + [0.0], shipped_ctx)
            )
            entry["shipped"] = shipped_angles
            entry["shipped_ok"] = in_envelope(shipped_angles)

        results.append(entry)
        print(
            f"{shot:>4} {record['club']:>7} {angles['dynamic_loft_deg']:>8.1f}d "
            f"{angles['face_angle_deg']:>8.1f}d {angles['lie_deg']:>6.1f}d   "
            f"{'IN ENVELOPE' if ok else 'outside'}",
            flush=True,
        )

    if not results:
        raise SystemExit("no fits scored")
    args.output.write_text(json.dumps(results, indent=2), encoding="utf-8")

    print(f"\n=== {len(results)} shots ===")
    for key, (lo, hi) in ENVELOPE.items():
        values = np.array([r["fused"][key] for r in results])
        inside = int(((values >= lo) & (values <= hi)).sum())
        print(
            f"  {key:<17} mean {values.mean():+7.1f}  sd {values.std():5.1f}  "
            f"range {values.min():+7.1f} to {values.max():+7.1f}   "
            f"in [{lo:.0f},{hi:.0f}]: {inside}/{len(values)}"
        )
    ok = sum(1 for r in results if r["fused_ok"])
    print(f"\n  all three angles physically possible: {ok}/{len(results)}")
    if args.shipped:
        was = sum(1 for r in results if r.get("shipped_ok"))
        print(f"  shipped 5-parameter fit, same test:   {was}/{len(results)}")
    print("\n  Static mesh geometry, for reference: loft 33.10 deg, lie 61.19 deg.")
    print("  A real iron delivery sits within a few degrees of its static lie and")
    print("  a little under its static loft, because the shaft leans forward.")
    print(f"\nwrote {args.output}")


if __name__ == "__main__":
    main()

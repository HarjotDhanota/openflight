"""Falsification test #2c -- decompose WHICH speed contract carries the bias.

``estimate_lcmf_v1`` derives two different quantities from one input:

  phase_velocity_ms   -> tdm_phase = sign*4pi*v*tau/lambda
                         PHYSICALLY WANTS: instantaneous RADIAL range rate.
                         Feeding OPS radial speed is roughly RIGHT.

  model_geometry["speed_ms"] -> ballistic_trajectory_from_range; vx = v cos L,  vz = v sin L - g t
                         PHYSICALLY WANTS: TOTAL launch speed.
                         Feeding OPS radial speed is WRONG (2.4-3.1% low).

Arms:
  A  both radial            (shipped)
  C  phase radial, ballistic total   <-- the actual correct fix
  B  both total             (naive "just correct the input" fix)
"""

from __future__ import annotations

import csv
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from session_path import find_session  # noqa: E402

from openflight.iwr6843 import lcmf  # noqa: E402
from openflight.iwr6843.calibration import Calibration  # noqa: E402
from openflight.iwr6843.multipath import (
    ballistic_trajectory_from_range as _true_ballistic,  # noqa: E402
)
from openflight.speed_correction import correct_ball_speed  # noqa: E402

SESSION = find_session()
EXCLUDE = {1}
TEE_RANGE_M, BALL_HEIGHT_M = 1.575, 0.040
_OVERRIDE = {"speed_ms": None}


def _patched_ballistic(launch_rad, range_m, *, speed_ms, **kw):
    """Substitute the TOTAL launch speed for the ballistic contract only."""
    if _OVERRIDE["speed_ms"] is not None:
        speed_ms = _OVERRIDE["speed_ms"]
    return _true_ballistic(launch_rad, range_m, speed_ms=speed_ms, **kw)


def f(row, key):
    v = row.get(key, "")
    return float(v) if v not in ("", None) else None


def stats(name, recs, key):
    for club in ("7-iron", "9-iron"):
        vals = np.array(
            [r[key] for r in recs if r["club"] == club and r[key] is not None]
        )
        print(
            f"    {club}: n={len(vals):2d} mean {vals.mean():6.3f} median {np.median(vals):6.3f}"
        )
    v7 = np.array(
        [r[key] for r in recs if r["club"] == "7-iron" and r[key] is not None]
    )
    v9 = np.array(
        [r[key] for r in recs if r["club"] == "9-iron" and r[key] is not None]
    )
    gap_mean = v9.mean() - v7.mean()
    gap_med = np.median(v9) - np.median(v7)
    print(f"    GAP  mean {gap_mean:+.3f}   median {gap_med:+.3f}")
    return gap_mean, gap_med


def main():
    cal = Calibration.load(str(ROOT / "config" / "iwr6843_calibration_reference.json"))
    cal = replace(cal, tee_range_m=TEE_RANGE_M, tee_ball_height_m=BALL_HEIGHT_M)
    dist_ft = TEE_RANGE_M * 3.28084
    above_ft = (cal.tee_ball_height_m - cal.radar_height_m) * 3.28084

    with open(SESSION / "shots.csv", newline="", encoding="utf-8") as h:
        rows = [r for r in csv.DictReader(h) if int(r["shot_number"]) not in EXCLUDE]

    lcmf.ballistic_trajectory_from_range = _patched_ballistic
    recs = []
    print(
        f"{'shot':>5} {'club':>8} {'ship':>7} {'A':>7} {'C':>7} {'B':>7} "
        f"{'C-A':>7} {'B-C':>7}"
    )
    for row in rows:
        shot, club = int(row["shot_number"]), row["club"]
        raw = (SESSION / row["archive_iwr_file"]).read_bytes()
        v_rad = f(row, "iwr_ball_speed_mph")
        ship = f(row, "iwr_measurement_launch_angle_deg")

        _OVERRIDE["speed_ms"] = None
        a = lcmf.estimate_lcmf_v1(raw, cal, ball_speed_mph=v_rad, club=club)
        seed = a.angle_deg if a.angle_deg is not None else (ship or 20.0)
        v_tot = correct_ball_speed(v_rad, seed, dist_ft, above_ft)

        _OVERRIDE["speed_ms"] = v_tot / lcmf.MPH_PER_MS  # arm C: ballistic only
        c = lcmf.estimate_lcmf_v1(raw, cal, ball_speed_mph=v_rad, club=club)
        _OVERRIDE["speed_ms"] = None
        b = lcmf.estimate_lcmf_v1(
            raw, cal, ball_speed_mph=v_tot, club=club
        )  # arm B: both

        recs.append(
            dict(
                shot=shot,
                club=club,
                ship=ship,
                v_rad=v_rad,
                v_tot=v_tot,
                A=a.angle_deg,
                C=c.angle_deg,
                B=b.angle_deg,
                sA=a.status,
                n_frames=a.n_frames,
            )
        )
        r = recs[-1]
        print(
            f"{shot:>5} {club:>8} {ship:7.3f} {r['A']:7.3f} {r['C']:7.3f} {r['B']:7.3f} "
            f"{r['C'] - r['A']:+7.3f} {r['B'] - r['C']:+7.3f}"
        )

    print("\n=== CONTROL: arm A vs shipped ===")
    e = np.array([r["A"] - r["ship"] for r in recs])
    print(
        f"  all 21: mean {e.mean():+.4f}  median {np.median(e):+.4f}  max|.| {np.abs(e).max():.4f}"
    )
    worst = max(recs, key=lambda r: abs(r["A"] - r["ship"]))
    keep = [r for r in recs if r["shot"] != worst["shot"]]
    e2 = np.array([r["A"] - r["ship"] for r in keep])
    print(
        f"  drop shot_{worst['shot']:03d} (delta {worst['A'] - worst['ship']:+.3f}, "
        f"shipped ran _ops_guided rescue): mean {e2.mean():+.4f}  max|.| {np.abs(e2).max():.4f}"
    )

    print("\n=== ARM A  (shipped: both contracts fed RADIAL) ===")
    ga = stats("A", recs, "A")
    print("\n=== ARM C  (FIX: phase radial, ballistic TOTAL) ===")
    gc = stats("C", recs, "C")
    print("\n=== ARM B  (naive: both fed TOTAL) ===")
    gb = stats("B", recs, "B")

    dCA = np.array([r["C"] - r["A"] for r in recs])
    dBC = np.array([r["B"] - r["C"] for r in recs])
    print("\n=== WHERE THE SHIFT LIVES ===")
    print(
        f"  ballistic contract alone (C-A): {dCA.mean():+.4f} +- {dCA.std(ddof=1):.4f} deg"
    )
    print(
        f"  phase contract alone     (B-C): {dBC.mean():+.4f} +- {dBC.std(ddof=1):.4f} deg"
    )
    print("\n=== GAP (9i - 7i) ===")
    print(f"  A shipped : mean {ga[0]:+.3f}  median {ga[1]:+.3f}")
    print(
        f"  C fixed   : mean {gc[0]:+.3f}  median {gc[1]:+.3f}   change {gc[0] - ga[0]:+.3f}"
    )
    print(
        f"  B naive   : mean {gb[0]:+.3f}  median {gb[1]:+.3f}   change {gb[0] - ga[0]:+.3f}"
    )

    np.save(
        ROOT / "research/silhouette_poc/falsification/test2_decompose.npy",
        np.array(recs, dtype=object),
        allow_pickle=True,
    )


if __name__ == "__main__":
    main()

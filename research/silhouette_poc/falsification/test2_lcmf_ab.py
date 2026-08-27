"""Falsification test #2b -- re-run LCMF-v1 with radial vs cosine-corrected speed.

Arm A reproduces the shipped pipeline (LCMF fed the RAW OPS radial speed).
Arm B feeds the cosine-corrected TOTAL launch speed, which is what
``ballistic_trajectory_from_range`` actually consumes (vx = v cos L,
vz = v sin L - g t).  The question is whether the difference is
club-dependent enough to move the 7-iron/9-iron launch gap.
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

from openflight.iwr6843.calibration import Calibration  # noqa: E402
from openflight.iwr6843.lcmf import estimate_lcmf_v1  # noqa: E402
from openflight.speed_correction import correct_ball_speed  # noqa: E402

SESSION = find_session()
EXCLUDE = {1}
TEE_RANGE_M = 1.575  # server.py --iwr6843-tee-m default
BALL_HEIGHT_M = 0.040  # server.py --iwr6843-ball-height-m default


def f(row, key):
    v = row.get(key, "")
    return float(v) if v not in ("", None) else None


def main():
    cal = Calibration.load(str(ROOT / "config" / "iwr6843_calibration_reference.json"))
    cal = replace(cal, tee_range_m=TEE_RANGE_M, tee_ball_height_m=BALL_HEIGHT_M)
    dist_ft = TEE_RANGE_M * 3.28084
    above_ft = (cal.tee_ball_height_m - cal.radar_height_m) * 3.28084
    print(
        f"cal: tilt {np.degrees(cal.tilt_rad):.3f} deg  radar_h {cal.radar_height_m:.4f} m "
        f"tee {TEE_RANGE_M} m  ball_h {BALL_HEIGHT_M} m"
    )
    print(
        f"cosine-correction geometry: dist {dist_ft:.3f} ft  ball_above_radar {above_ft:+.4f} ft\n"
    )

    with open(SESSION / "shots.csv", newline="", encoding="utf-8") as h:
        rows = [r for r in csv.DictReader(h) if int(r["shot_number"]) not in EXCLUDE]

    out = []
    print(
        f"{'shot':>5} {'club':>8} {'vA':>7} {'vB':>7} {'shipped':>8} "
        f"{'armA':>8} {'armB':>8} {'B-A':>7}  status"
    )
    for row in rows:
        shot = int(row["shot_number"])
        club = row["club"]
        dump = SESSION / row["archive_iwr_file"]
        raw = dump.read_bytes()
        v_radial = f(row, "iwr_ball_speed_mph")  # what shipped LCMF got
        shipped = f(row, "iwr_measurement_launch_angle_deg")

        a = estimate_lcmf_v1(raw, cal, ball_speed_mph=v_radial, club=club)
        # Arm B: correct using arm A's own launch angle (the pipeline's angle),
        # which is exactly what server.py does downstream -- just applied first.
        la_seed = a.angle_deg if a.angle_deg is not None else (shipped or 20.0)
        v_total = correct_ball_speed(v_radial, la_seed, dist_ft, above_ft)
        b = estimate_lcmf_v1(raw, cal, ball_speed_mph=v_total, club=club)

        rec = dict(
            shot=shot,
            club=club,
            v_radial=v_radial,
            v_total=v_total,
            shipped=shipped,
            a=a.angle_deg,
            b=b.angle_deg,
            sa=a.status,
            sb=b.status,
        )
        out.append(rec)
        d = (
            (b.angle_deg - a.angle_deg)
            if (a.angle_deg and b.angle_deg)
            else float("nan")
        )
        print(
            f"{shot:>5} {club:>8} {v_radial:7.2f} {v_total:7.2f} "
            f"{(shipped if shipped is not None else float('nan')):8.3f} "
            f"{(a.angle_deg if a.angle_deg is not None else float('nan')):8.3f} "
            f"{(b.angle_deg if b.angle_deg is not None else float('nan')):8.3f} "
            f"{d:+7.3f}  {a.status}"
        )

    np.save(
        ROOT / "research/silhouette_poc/falsification/test2_ab.npy",
        np.array(out, dtype=object),
        allow_pickle=True,
    )

    # --- control: does arm A reproduce the shipped angle? ----------------
    ok = [r for r in out if r["a"] is not None and r["shipped"] is not None]
    err = np.array([r["a"] - r["shipped"] for r in ok])
    print(f"\n=== CONTROL: arm A vs shipped ({len(ok)}/{len(out)} shots) ===")
    print(f"  mean {err.mean():+.4f} deg   max|.| {np.abs(err).max():.4f} deg")

    # --- effect ----------------------------------------------------------
    both = [r for r in out if r["a"] is not None and r["b"] is not None]
    print(f"\n=== EFFECT of the speed contract ({len(both)} shots) ===")
    for club in ("7-iron", "9-iron"):
        sub = [r for r in both if r["club"] == club]
        a = np.array([r["a"] for r in sub])
        b = np.array([r["b"] for r in sub])
        print(
            f"  {club}: n={len(sub):2d}  armA {a.mean():6.3f}  armB {b.mean():6.3f}  "
            f"delta {(b - a).mean():+.4f} +- {(b - a).std(ddof=1):.4f}"
        )
    a7 = np.array([r["a"] for r in both if r["club"] == "7-iron"])
    a9 = np.array([r["a"] for r in both if r["club"] == "9-iron"])
    b7 = np.array([r["b"] for r in both if r["club"] == "7-iron"])
    b9 = np.array([r["b"] for r in both if r["club"] == "9-iron"])
    print(f"\n  7i/9i GAP  armA (shipped contract): {a9.mean() - a7.mean():+.3f} deg")
    print(f"  7i/9i GAP  armB (fixed contract)   : {b9.mean() - b7.mean():+.3f} deg")
    print(
        f"  GAP CHANGE                          : "
        f"{(b9.mean() - b7.mean()) - (a9.mean() - a7.mean()):+.3f} deg"
    )


if __name__ == "__main__":
    main()

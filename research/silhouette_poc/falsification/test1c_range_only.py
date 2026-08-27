"""Test #1c -- launch angle from the RAW radar range walk alone.

The camera route (test 1) and LCMF disagree by about +5 deg on every shot.
Those two rest on different angular data: the camera on its boresight pitch,
LCMF on the radar's 10.405 deg tilt calibration plus the elevation DOA. A
constant error in either reproduces the whole offset one-for-one, so neither
can arbitrate the other.

This estimator uses NEITHER. A ball launched at angle theta from a known tee,
watched by a radar at a known position, traces a slant range whose CURVATURE
depends on theta: the steeper the launch, the faster the ball climbs off the
line of sight and the faster the range rate rises toward the true speed. No
antenna angle is involved -- only |P(t) - radar| against time.

The catch is conditioning: over a 40 ms window the curvature signal is only
about 18 mm, against roughly 12 mm of range noise. Error bars are reported
from the Jacobian rather than assumed small, and both a speed-free and a
speed-pinned variant are run.
"""

from __future__ import annotations

import csv
import math
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "research"))
sys.path.insert(0, str(Path(__file__).parent))

import test1_vertical_trajectory as T1  # noqa: E402

from openflight.iwr6843 import tracking  # noqa: E402
from openflight.iwr6843.calibration import Calibration  # noqa: E402
from openflight.iwr6843.dump import parse_dump, project_tx_pair  # noqa: E402
from openflight.iwr6843.shot import (  # noqa: E402
    TX2_LOOP_PERIOD_S,
    impact_time_s,
    prepare_shot_dump,
    process_dump,
)
from openflight.iwr6843.tracking import _detections, loop_power  # noqa: E402
from openflight.speed_correction import correct_ball_speed  # noqa: E402

G = 9.81


def raw_ranges(dump: Path, club: str):
    """Raw per-loop ball range detections, inliers to the fitted walk."""
    cal = Calibration.load(str(ROOT / "config" / "iwr6843_calibration_reference.json"))
    cal = replace(cal, tee_range_m=T1.TEE_RANGE_M, tee_ball_height_m=T1.BALL_H)
    raw = dump.read_bytes()
    # Reproduce process_dump's own preparation: these captures are 3-TX and
    # must be projected onto the (0, 2) pair with the TX2 loop period before
    # the geometry's per-frame range-bin starts line up.
    meta0, _cube0 = parse_dump(raw)
    loop_period_s = tracking.LOOP_PRI_S
    if meta0["n_tx"] == 3:
        raw = project_tx_pair(raw, (0, 2))
        loop_period_s = TX2_LOOP_PERIOD_S
    prepared = prepare_shot_dump(raw, loop_period_s=loop_period_s)
    shot = process_dump(raw, cal, club=club, net_range_m=4.6, prepared=prepared)
    if shot.track is None:
        return None
    t_imp = impact_time_s(
        shot.track, shot.geometry, cal.tee_range_m, range_bias_m=cal.range_bias_m
    )
    if t_imp is None:
        return None
    geo = shot.geometry
    scope = "window" if shot.notch_recovered else "burst"
    power = loop_power(prepared.mti(scope))
    loops, bins = _detections(power, geo, max_range_m=4.6)
    if not len(loops):
        return None
    times = np.array(
        [geo.loop_time(int(i) // geo.n_loops, int(i) % geo.n_loops) for i in loops]
    )
    ranges = cal.true_range(bins * geo.range_res_m)
    # keep only detections the accepted walk claims, with a tight gate
    pred = cal.true_range(shot.track.bin_at(times) * geo.range_res_m)
    keep = (
        (np.abs(ranges - pred) < 3.0 * geo.range_res_m)
        & (times >= shot.track.t_first - 1e-3)
        & (times <= shot.track.t_last + 1e-3)
    )
    if keep.sum() < 12:
        return None
    return times[keep], ranges[keep], float(t_imp), shot.track


def fit_range_only(times, ranges, t_imp, tee, phi_rad, v_fixed=None):
    """Fit (theta[, v], t0) to raw slant ranges. Returns angle + 1-sigma."""
    radar = T1.RADAR_ORIGIN

    def predict(theta, v, t0):
        tau = times - t0
        vel = v * np.array(
            [
                math.sin(phi_rad) * math.cos(theta),
                math.cos(phi_rad) * math.cos(theta),
                math.sin(theta),
            ]
        )
        pos = tee[None, :] + vel[None, :] * tau[:, None]
        pos = pos.copy()
        pos[:, 2] -= 0.5 * G * tau**2
        return np.linalg.norm(pos - radar, axis=1)

    if v_fixed is None:

        def resid(p):
            return predict(p[0], p[1], p[2]) - ranges

        p0 = [math.radians(21.0), 48.0, t_imp]
    else:

        def resid(p):
            return predict(p[0], v_fixed, p[1]) - ranges

        p0 = [math.radians(21.0), t_imp]

    sol = least_squares(resid, p0, method="lm", max_nfev=6000)
    n, k = len(times), len(p0)
    dof = max(n - k, 1)
    s2 = float(sol.fun @ sol.fun) / dof
    try:
        cov = s2 * np.linalg.inv(sol.jac.T @ sol.jac)
        sigma = math.degrees(math.sqrt(max(cov[0, 0], 0.0)))
    except np.linalg.LinAlgError:
        sigma = float("nan")
    return (
        math.degrees(sol.x[0]),
        sigma,
        (sol.x[1] if v_fixed is None else v_fixed),
        float(np.sqrt(s2)),
        n,
    )


def main():
    with open(T1.SESSION / "shots.csv", newline="", encoding="utf-8") as h:
        rows = [r for r in csv.DictReader(h) if int(r["shot_number"]) not in T1.EXCLUDE]
    cam = {
        int(r["shot"]): r
        for r in np.load(
            ROOT / "research/silhouette_poc/falsification/test1_recs.npy",
            allow_pickle=True,
        )
    }

    dist_ft = T1.TEE_RANGE_M * 3.28084
    above_ft = (T1.BALL_H - T1.RADAR_H) * 3.28084
    print(
        f"{'shot':>5} {'club':>8} {'n':>4} {'free_th':>8} {'+-':>6} {'free_v':>7} "
        f"{'pin_th':>7} {'+-':>6} {'rms_mm':>7} {'camera':>7} {'LCMF':>7}"
    )
    recs = []
    for row in rows:
        shot, club = int(row["shot_number"]), row["club"]
        out = raw_ranges(T1.SESSION / row["archive_iwr_file"], club)
        if out is None or shot not in cam:
            print(f"{shot:>5} {club:>8}  --- no usable raw ranges (fail closed)")
            continue
        times, ranges, t_imp, _trk = out
        c = cam[shot]
        tee = np.array(
            [
                0.0,
                math.sqrt(T1.TEE_RANGE_M**2 - (T1.BALL_H - T1.RADAR_H) ** 2),
                T1.BALL_H,
            ]
        )
        phi = math.radians(c["horizontal_deg"])
        th_free, sd_free, v_free, rms, n = fit_range_only(
            times, ranges, t_imp, tee, phi
        )
        # speed-pinned: OPS radial corrected to total using the FREE angle,
        # so the pinned variant does not inherit the camera's angle
        v_ops = (
            correct_ball_speed(
                float(row["iwr_ball_speed_mph"]), th_free, dist_ft, above_ft
            )
            / 2.23694
        )
        th_pin, sd_pin, _v, rms_pin, _n = fit_range_only(
            times, ranges, t_imp, tee, phi, v_fixed=v_ops
        )
        lcmf = float(row["iwr_measurement_launch_angle_deg"])
        recs.append(
            dict(
                shot=shot,
                club=club,
                free=th_free,
                sd_free=sd_free,
                v_free=v_free,
                pin=th_pin,
                sd_pin=sd_pin,
                camera=c["vertical_deg"],
                lcmf=lcmf,
                n=n,
            )
        )
        print(
            f"{shot:>5} {club:>8} {n:>4} {th_free:8.3f} {sd_free:6.3f} {v_free:7.2f} "
            f"{th_pin:7.3f} {sd_pin:6.3f} {rms * 1000:7.1f} "
            f"{c['vertical_deg']:7.3f} {lcmf:7.3f}"
        )

    if not recs:
        return
    print(f"\n=== {len(recs)} shots ===")
    for key, label in (
        ("free", "range-only, speed free"),
        ("pin", "range-only, speed pinned to OPS"),
        ("camera", "camera rays + range"),
        ("lcmf", "LCMF (radar tilt + DOA)"),
    ):
        v7 = np.array([r[key] for r in recs if r["club"] == "7-iron"])
        v9 = np.array([r[key] for r in recs if r["club"] == "9-iron"])
        print(
            f"  {label:>32}: 7i {v7.mean():6.2f}  9i {v9.mean():6.2f}  "
            f"GAP mean {v9.mean() - v7.mean():+.3f}  median "
            f"{np.median(v9) - np.median(v7):+.3f}"
        )
    sdf = np.array([r["sd_free"] for r in recs])
    sdp = np.array([r["sd_pin"] for r in recs])
    print(
        f"\n  per-shot 1-sigma: speed-free {np.median(sdf):.2f} deg, "
        f"speed-pinned {np.median(sdp):.2f} deg"
    )
    print(
        f"  scatter of the range-only estimate within club "
        f"(pinned): 7i {np.std([r['pin'] for r in recs if r['club'] == '7-iron'], ddof=1):.2f}, "
        f"9i {np.std([r['pin'] for r in recs if r['club'] == '9-iron'], ddof=1):.2f} deg"
    )
    np.save(
        ROOT / "research/silhouette_poc/falsification/test1c_recs.npy",
        np.array(recs, dtype=object),
        allow_pickle=True,
    )


if __name__ == "__main__":
    main()

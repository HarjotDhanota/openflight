"""Falsification test #1 -- independent vertical trajectory.

Reconstruct the ball's vertical launch from calibrated camera rays plus the
IWR6843 RANGE WALK, gravity-aware, with the LCMF elevation estimator entirely
excluded.  If the 7-iron/9-iron gap comes out near LCMF's, the "dynamic loft
is compressed" reading is dead.  If it comes out materially larger, LCMF is; compressing the gap.

What this uses:   camera pixels, the IWR BallTrack range walk, the tee tape.
What it excludes: lcmf.py's DOA/multipath grid search in every form.
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

from flight_track import track_flight  # noqa: E402
from session_path import find_session  # noqa: E402

from openflight.iwr6843.calibration import Calibration  # noqa: E402
from openflight.iwr6843.shot import impact_time_s, process_dump  # noqa: E402

SESSION = find_session()
EXCLUDE = {1}

FOCAL_PX = 2.8 / (3.0 * 2 * 1e-3)  # 466.67, datasheet optics only
CX, CY = 160.0, 100.0
CAM_H, CAM_LAT = 0.2032, -0.060325  # kiosk log camera config
RADAR_H = 0.1524  # cal json
TEE_RANGE_M, BALL_H = 1.575, 0.040  # server.py defaults
G = 9.81
SIGMA_PX, SIGMA_R = 0.7, 0.010

RADAR_ORIGIN = np.array([0.0, 0.0, RADAR_H])
CAM_ORIGIN = np.array([CAM_LAT, 0.0, CAM_H])


def camera_ray(u, v, pitch_rad):
    """Unit ray in world axes (x lateral, y downrange, z up)."""
    ix = (np.asarray(u, dtype=float) - CX) / FOCAL_PX
    iz = -(np.asarray(v, dtype=float) - CY) / FOCAL_PX
    c, s = math.cos(pitch_rad), math.sin(pitch_rad)
    ray = np.stack([ix, c - iz * s, s + iz * c], axis=-1)
    return ray / np.linalg.norm(ray, axis=-1, keepdims=True)


def project(points, pitch_rad):
    """World points -> pixels.  Exact inverse of camera_ray."""
    d = np.asarray(points, float).reshape(-1, 3) - CAM_ORIGIN
    c, s = math.cos(pitch_rad), math.sin(pitch_rad)
    fwd = d[:, 1] * c + d[:, 2] * s  # along boresight
    up = -d[:, 1] * s + d[:, 2] * c  # perpendicular to it, up
    return np.column_stack([CX + FOCAL_PX * d[:, 0] / fwd, CY - FOCAL_PX * up / fwd])


def tee_position(u, v, pitch_rad):
    """Tee centre in world axes.

    Forward distance and height come from the TAPE (radar slant tee range and
    ball-centre height) so that this point sits exactly on the range anchor
    ``impact_time_s`` back-extrapolates the radar walk to. Only the lateral
    coordinate is taken from the camera, where the tape says nothing.
    Deriving forward distance from the pixel row instead put the tee 21 mm
    inside the radar anchor and no free parameter could absorb it.
    """
    forward = math.sqrt(TEE_RANGE_M**2 - (BALL_H - RADAR_H) ** 2)
    ray = camera_ray(u, v, pitch_rad)
    scale = forward / ray[1]
    lateral = CAM_ORIGIN[0] + scale * ray[0]
    return np.array([lateral, forward, BALL_H])


def radar_range_walk(dump: Path, club: str):
    """The IWR ball range walk and its own back-extrapolated impact instant."""
    cal = Calibration.load(str(ROOT / "config" / "iwr6843_calibration_reference.json"))
    cal = replace(cal, tee_range_m=TEE_RANGE_M, tee_ball_height_m=BALL_H)
    shot = process_dump(dump.read_bytes(), cal, club=club, net_range_m=4.6)
    if shot.track is None:
        return None
    t_imp = impact_time_s(
        shot.track, shot.geometry, cal.tee_range_m, range_bias_m=cal.range_bias_m
    )
    if t_imp is None:
        return None
    res = shot.geometry.range_res_m
    trk = shot.track

    def range_fn(t):
        return cal.true_range(trk.range_at(float(t), res))

    return (
        range_fn,
        float(t_imp),
        float(trk.t_first),
        float(trk.t_last),
        float(trk.speed_ms),
    )


def fit_shot(track, ranges, t_cam, pitch_rad):
    """Fit (speed, vertical, horizontal, t0) to camera pixels + radar range."""
    range_fn, t_imp, t_first, t_last, _speed = ranges
    tee = tee_position(track.tee_uv[0], track.tee_uv[1], pitch_rad)
    obs_uv = track.uv

    def model(p):
        v, th, ph, t0 = p
        tau = t_cam - t0
        vel = v * np.array(
            [math.sin(ph) * math.cos(th), math.cos(ph) * math.cos(th), math.sin(th)]
        )
        pos = tee[None, :] + vel[None, :] * tau[:, None]
        pos = pos.copy()
        pos[:, 2] -= 0.5 * G * tau**2
        return pos, tau

    def resid(p):
        pos, tau = model(p)
        uv = project(pos, pitch_rad)
        r_pred = np.linalg.norm(pos - RADAR_ORIGIN, axis=1)
        # No clipping. The walk is a fitted low-order polynomial and
        # impact_time_s already relies on back-extrapolating it to the tee;
        # clipping flattened the earliest frames, which are the ones nearest
        # launch and therefore the ones that matter most.
        r_obs = np.array([range_fn(t) for t in t_imp + tau])
        return np.concatenate(
            [((uv - obs_uv) / SIGMA_PX).ravel(), (r_pred - r_obs) / SIGMA_R]
        )

    p0 = [50.0, math.radians(20.0), 0.0, float(t_cam[0]) - 0.006]
    sol = least_squares(resid, p0, method="lm", max_nfev=8000)
    v, th, ph, t0 = sol.x
    n = len(t_cam)
    return dict(
        speed_ms=float(v),
        vertical_deg=math.degrees(th),
        horizontal_deg=math.degrees(ph),
        t0=float(t0),
        n=n,
        px_rms=float(np.sqrt(np.mean((sol.fun[: 2 * n] * SIGMA_PX) ** 2))),
        r_rms=float(np.sqrt(np.mean((sol.fun[2 * n :] * SIGMA_R) ** 2))),
    )


def run(pitch_deg: float = -0.185, quiet: bool = False):
    pitch = math.radians(pitch_deg)
    with open(SESSION / "shots.csv", newline="", encoding="utf-8") as h:
        rows = [r for r in csv.DictReader(h) if int(r["shot_number"]) not in EXCLUDE]

    if not quiet:
        print(
            f"camera pitch {pitch_deg:+.3f} deg (measured, test1a)   fx {FOCAL_PX:.2f} px"
        )
        print(
            f"{'shot':>5} {'club':>8} {'n':>3} {'cam_vert':>9} {'lcmf':>7} {'diff':>7} "
            f"{'cam_horiz':>10} {'cam_v':>7} {'ops_v':>7} {'pxrms':>6} {'Rrms_mm':>8}"
        )
    recs = []
    for row in rows:
        shot, club = int(row["shot_number"]), row["club"]
        blob = np.load(SESSION / row["archive_frames_npz"])
        frames = blob["frames"][:, :, ::-1]  # UN-MIRROR the frames
        ts = blob["sensor_timestamp_ns"].astype(float) * 1e-9
        tr = track_flight(frames)
        if tr is None:
            if not quiet:
                print(f"{shot:>5} {club:>8}  --- no flight track (fail closed)")
            continue
        ranges = radar_range_walk(SESSION / row["archive_iwr_file"], club)
        if ranges is None:
            if not quiet:
                print(f"{shot:>5} {club:>8}  --- no radar range walk (fail closed)")
            continue
        t_cam = ts[tr.frames.astype(int)] - ts[0]
        fit = fit_shot(tr, ranges, t_cam, pitch)
        lcmf = float(row["iwr_measurement_launch_angle_deg"])
        ops = float(row["ball_speed_mph"]) / 2.23694
        fit.update(shot=shot, club=club, lcmf=lcmf, ops_ms=ops)
        recs.append(fit)
        if not quiet:
            print(
                f"{shot:>5} {club:>8} {fit['n']:>3} {fit['vertical_deg']:9.3f} {lcmf:7.3f} "
                f"{fit['vertical_deg'] - lcmf:+7.3f} {fit['horizontal_deg']:10.3f} "
                f"{fit['speed_ms']:7.2f} {ops:7.2f} {fit['px_rms']:6.2f} "
                f"{fit['r_rms'] * 1000:8.1f}"
            )
    return recs


def summarise(recs, label=""):
    c7 = np.array([r["vertical_deg"] for r in recs if r["club"] == "7-iron"])
    c9 = np.array([r["vertical_deg"] for r in recs if r["club"] == "9-iron"])
    l7 = np.array([r["lcmf"] for r in recs if r["club"] == "7-iron"])
    l9 = np.array([r["lcmf"] for r in recs if r["club"] == "9-iron"])
    print(f"\n=== {len(recs)} shots reconstructed {label} ===")
    for club, cam, lc in (("7-iron", c7, l7), ("9-iron", c9, l9)):
        print(
            f"  {club}: n={len(cam):2d}  camera+range {cam.mean():6.3f} "
            f"(med {np.median(cam):6.3f})   LCMF {lc.mean():6.3f} "
            f"(med {np.median(lc):6.3f})   cam-LCMF {np.mean(cam - lc):+.3f}"
        )
    print("  7i -> 9i LAUNCH GAP")
    print(
        f"    camera + IWR range : mean {c9.mean() - c7.mean():+.3f}   "
        f"median {np.median(c9) - np.median(c7):+.3f}"
    )
    print(
        f"    LCMF               : mean {l9.mean() - l7.mean():+.3f}   "
        f"median {np.median(l9) - np.median(l7):+.3f}"
    )
    return (c9.mean() - c7.mean(), np.median(c9) - np.median(c7))


if __name__ == "__main__":
    pitch = float(sys.argv[1]) if len(sys.argv) > 1 else -0.185
    out = run(pitch)
    if out:
        summarise(out)
        np.save(
            ROOT / "research/silhouette_poc/falsification/test1_recs.npy",
            np.array(out, dtype=object),
            allow_pickle=True,
        )

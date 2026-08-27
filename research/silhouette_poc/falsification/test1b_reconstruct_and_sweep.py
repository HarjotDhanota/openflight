"""Test #1b -- show the reconstructed 3D track, then sweep every assumption.

Two questions:
  1. Is the +5 deg common-mode offset between the camera reconstruction and
     LCMF a real feature of the data, or an artefact of the fit trading
     parameters against each other?
  2. Is the 7i/9i GAP -- the actual deliverable of test 1 -- stable against
  every geometric assumption the reconstruction rests on?

A constant camera-pitch error is common-mode and must cancel in the gap.
That is the property that makes the gap trustworthy even while the absolute
value is not; this file verifies it numerically instead of asserting it.

The pixel tracks and the radar walks are cached because NONE of the swept
parameters change them -- they are properties of the frames and the dump.
Recomputing the O(n^3) track RANSAC once per variant made the sweep take
hours for no change in its input.
"""

from __future__ import annotations

import csv
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "research"))
sys.path.insert(0, str(Path(__file__).parent))

import test1_vertical_trajectory as T1  # noqa: E402
from flight_track import track_flight  # noqa: E402

_TRACKS: dict[int, object] = {}
_WALKS: dict[tuple, object] = {}


def rows():
    with open(T1.SESSION / "shots.csv", newline="", encoding="utf-8") as h:
        return [r for r in csv.DictReader(h) if int(r["shot_number"]) not in T1.EXCLUDE]


def cached_track(row):
    shot = int(row["shot_number"])
    if shot not in _TRACKS:
        blob = np.load(T1.SESSION / row["archive_frames_npz"])
        F = blob["frames"][:, :, ::-1]
        ts = blob["sensor_timestamp_ns"].astype(float) * 1e-9
        tr = track_flight(F)
        _TRACKS[shot] = None if tr is None else (tr, ts - ts[0])
    return _TRACKS[shot]


def cached_walk(row):
    key = (int(row["shot_number"]), T1.TEE_RANGE_M, T1.BALL_H)
    if key not in _WALKS:
        _WALKS[key] = T1.radar_range_walk(
            T1.SESSION / row["archive_iwr_file"], row["club"]
        )
    return _WALKS[key]


def run_cached(pitch_deg):
    pitch = math.radians(pitch_deg)
    recs = []
    for row in rows():
        got = cached_track(row)
        if got is None:
            continue
        tr, ts = got
        walk = cached_walk(row)
        if walk is None:
            continue
        t_cam = ts[tr.frames.astype(int)]
        fit = T1.fit_shot(tr, walk, t_cam, pitch)
        fit.update(
            shot=int(row["shot_number"]),
            club=row["club"],
            lcmf=float(row["iwr_measurement_launch_angle_deg"]),
        )
        recs.append(fit)
    return recs


def reconstruct(row, pitch_deg):
    """Per-frame 3D ball position from camera ray + radar range sphere."""
    pitch = math.radians(pitch_deg)
    got = cached_track(row)
    walk = cached_walk(row)
    if got is None or walk is None:
        return None
    tr, ts = got
    t_cam = ts[tr.frames.astype(int)]
    fit = T1.fit_shot(tr, walk, t_cam, pitch)
    range_fn, t_imp = walk[0], walk[1]
    tau = t_cam - fit["t0"]
    pts = []
    for uv, tt in zip(tr.uv, tau):
        ray = T1.camera_ray(uv[0], uv[1], pitch)
        off = T1.CAM_ORIGIN - T1.RADAR_ORIGIN
        b = float(off @ ray)
        disc = b * b - float(off @ off) + range_fn(t_imp + tt) ** 2
        pts.append(
            T1.CAM_ORIGIN + (-b + math.sqrt(disc)) * ray
            if disc >= 0
            else np.full(3, np.nan)
        )
    return tr, np.asarray(tau), np.asarray(pts), fit


def show(row, pitch_deg=-0.185):
    out = reconstruct(row, pitch_deg)
    if out is None:
        print("no track")
        return
    tr, tau, pts, fit = out
    tee = T1.tee_position(tr.tee_uv[0], tr.tee_uv[1], math.radians(pitch_deg))
    print(f"\n=== shot {row['shot_number']} {row['club']} ===")
    print(
        f"  fit v={fit['speed_ms']:.2f} m/s vert={fit['vertical_deg']:.3f} "
        f"horiz={fit['horizontal_deg']:.3f} pxrms={fit['px_rms']:.2f} "
        f"Rrms={fit['r_rms'] * 1000:.1f} mm"
    )
    print(f"  {'tau_ms':>8} {'y_mm':>9} {'z_mm':>9} {'elev_deg':>9}")
    for tt, p in zip(tau, pts):
        dy, dz = p[1] - tee[1], p[2] - tee[2] + 0.5 * T1.G * tt**2
        print(
            f"  {tt * 1000:8.3f} {p[1] * 1000:9.1f} {p[2] * 1000:9.1f} "
            f"{math.degrees(math.atan2(dz, dy)) if dy else float('nan'):9.3f}"
        )


def sweep():
    base = dict(pitch=-0.185, ball_h=0.040, tee=1.575, cam_h=0.2032, focal=466.67)
    variants = [
        ("baseline (all measured)", {}),
        ("camera pitch -3 deg", dict(pitch=-3.185)),
        ("camera pitch +3 deg", dict(pitch=2.815)),
        ("camera pitch +10 deg (enclosure tilt)", dict(pitch=9.815)),
        ("ball centre 21.3 mm (ball on mat)", dict(ball_h=0.0213)),
        ("tee range 1.525 m (-50 mm)", dict(tee=1.525)),
        ("tee range 1.625 m (+50 mm)", dict(tee=1.625)),
        ("camera height 7.5 in", dict(cam_h=0.1905)),
        ("camera height 8.5 in", dict(cam_h=0.2159)),
        ("focal -2% (457.3 px)", dict(focal=457.3)),
        ("focal +2% (476.0 px)", dict(focal=476.0)),
        ("focal 473.1 px (from ball diameter)", dict(focal=473.1)),
    ]
    print(
        f"{'variant':>39} {'7i':>7} {'9i':>7} {'GAPmean':>8} {'GAPmed':>7} "
        f"{'d(gap)':>7} {'pxrms':>6} {'n':>3}"
    )
    base_gap = None
    for label, over in variants:
        cfg = {**base, **over}
        T1.BALL_H, T1.TEE_RANGE_M = cfg["ball_h"], cfg["tee"]
        T1.FOCAL_PX, T1.CAM_H = cfg["focal"], cfg["cam_h"]
        T1.CAM_ORIGIN = np.array([T1.CAM_LAT, 0.0, cfg["cam_h"]])
        recs = run_cached(cfg["pitch"])
        c7 = np.array([r["vertical_deg"] for r in recs if r["club"] == "7-iron"])
        c9 = np.array([r["vertical_deg"] for r in recs if r["club"] == "9-iron"])
        gm, gd = c9.mean() - c7.mean(), np.median(c9) - np.median(c7)
        if base_gap is None:
            base_gap = gm
        px = np.mean([r["px_rms"] for r in recs])
        print(
            f"{label:>39} {c7.mean():7.3f} {c9.mean():7.3f} {gm:8.3f} {gd:7.3f} "
            f"{gm - base_gap:+7.3f} {px:6.2f} {len(recs):3d}"
        )
    l7 = np.array([r["lcmf"] for r in recs if r["club"] == "7-iron"])
    l9 = np.array([r["lcmf"] for r in recs if r["club"] == "9-iron"])
    print(
        f"{'LCMF (for comparison)':>39} {l7.mean():7.3f} {l9.mean():7.3f} "
        f"{l9.mean() - l7.mean():8.3f} {np.median(l9) - np.median(l7):7.3f}"
    )
    T1.BALL_H, T1.TEE_RANGE_M = base["ball_h"], base["tee"]
    T1.FOCAL_PX, T1.CAM_H = base["focal"], base["cam_h"]
    T1.CAM_ORIGIN = np.array([T1.CAM_LAT, 0.0, base["cam_h"]])


if __name__ == "__main__":
    idx = {int(r["shot_number"]): r for r in rows()}
    if "--sweep" in sys.argv:
        sweep()
    else:
        for s in (2, 14):
            show(idx[s])

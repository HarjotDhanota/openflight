"""The ball's straight flight after impact, shot 1, fitted to its measured image positions on frames 44, 45, 46.

Unknowns: the ball's velocity (3 numbers) and the instant t_dep at which that straight line passes through the resting
ball's centre (1 number). t_dep is "mid-contact": the ball leaves the face having already moved speed x duration / 2.
Data, all from pixels and all taken as DIFFERENCES from the resting ball measured by the same routine:
   f45, f46  centre of the ball (full arc)               sigma 0.4 px each axis
   f44       centre x and the row of the ball's TOP edge  sigma 0.6 px (the head hides its lower part; an upper arc fixes
             the top edge well and trades centre height against radius, so the top edge is what is used)
   optional  radar ball speed 36.51 m/s, sigma 2.2 m/s (6 %: the radar reads along its own line of sight)
Two fits are run and both reported: PIXELS ONLY, and PIXELS + RADAR SPEED. Nothing is taken from the application's
contact instant or from the radar's launch angle; they are compared afterwards.
Writes results/BALL-TRACK.txt, results/ball-track.json. Usage: ball_track.py"""
import json
import math

import numpy as np
from scipy.optimize import least_squares

import ball_flight_lib as bf
import diag_lib as dl
import geom
from common import AUDIT

OUT = []


def say(t=""):
    OUT.append(t)
    print(t)


s = dl.setup(1)
prep = s["prep"]
SCENE = s["scene"]
times, t_c = prep["frame_time_ms"], prep["contact_ms"]
ball0_px = np.array([prep["ball"]["x"], prep["ball"]["y"]])
r0_px = prep["ball"]["diameter_px"] / 2.0
P0, R_MM, FOCAL = SCENE.ball, geom.BALL_RADIUS_MM, SCENE.focal
SPEED = 81.66409309716798 * 0.44704

obs = {}
for f, arc in ((44, (15.0, 165.0)), (45, (-10.0, 190.0)), (46, (-10.0, 190.0))):
    guess = {44: ball0_px + np.array([4.0, -11.0]), 45: ball0_px + np.array([15.0, -32.0]), 46: ball0_px + np.array([23.0, -50.0])}[f]
    m = bf.measure(s["frames"][f], guess, r0_px, arc)
    r = bf.measure(s["ref"], ball0_px, r0_px, arc)
    obs[f] = {"t": times[f] - t_c, "dx": float(m["centre"][0] - r["centre"][0]), "dy": float(m["centre"][1] - r["centre"][1]),
              "dtop": float((m["centre"][1] - m["radius"]) - (r["centre"][1] - r["radius"])), "rays": len(m["points"]), "rms": m["rms"],
              "raw": [float(m["centre"][0]), float(m["centre"][1]), float(m["radius"])], "rest": [float(r["centre"][0]), float(r["centre"][1]), float(r["radius"])]}
    say("f%d (%+.3f ms from the application's contact): ball centre moved %+.2f, %+.2f px from rest; top edge moved %+.2f px; %d edge rays, circle rms %.2f px" % (
        f, obs[f]["t"], obs[f]["dx"], obs[f]["dy"], obs[f]["dtop"], obs[f]["rays"], obs[f]["rms"]))
say("image speed f45 -> f46: %.2f px right and %.2f px up per frame (3.617 ms); f44 -> f45: %.2f right" % (obs[46]["dx"] - obs[45]["dx"], -(obs[46]["dy"] - obs[45]["dy"]), obs[45]["dx"] - obs[44]["dx"]))


def model(p, t):
    v, t_dep = np.asarray(p[:3]), p[3]
    pos = P0 + v * (t - t_dep)
    px = SCENE.project(pos)[0]
    return pos, px, FOCAL * R_MM / pos[2]


def residuals(p, use_speed):
    out = []
    for f in (45, 46):
        _pos, px, _r = model(p, obs[f]["t"])
        out += [(px[0] - ball0_px[0] - obs[f]["dx"]) / 0.4, (px[1] - ball0_px[1] - obs[f]["dy"]) / 0.4]
    _pos, px, r = model(p, obs[44]["t"])
    out += [(px[0] - ball0_px[0] - obs[44]["dx"]) / 0.6, ((px[1] - r) - (ball0_px[1] - r0_px) - obs[44]["dtop"]) / 0.6]
    if use_speed:
        out.append((float(np.linalg.norm(p[:3])) - SPEED) / 2.2)
    return np.asarray(out)


results = {}
for name, use_speed in (("PIXELS ONLY", False), ("PIXELS + RADAR SPEED", True)):
    best = None
    for vz0 in (20.0, 35.0, 50.0):
        for td0 in (-2.0, -1.0, 0.0):
            sol = least_squares(residuals, [4.5, -9.5, vz0, td0], args=(use_speed,), x_scale=[2.0, 2.0, 10.0, 0.5])
            if best is None or sol.cost < best.cost:
                best = sol
    p = best.x
    jac = best.jac
    dof = max(len(best.fun) - 4, 1)
    cov = np.linalg.pinv(jac.T @ jac)
    sig = np.sqrt(np.diag(cov))
    v = p[:3]
    speed = float(np.linalg.norm(v))
    launch = math.degrees(math.asin(-v[1] / speed))
    azim = math.degrees(math.atan2(v[0], v[2]))
    res = residuals(p, use_speed)
    results[name] = {"velocity_mm_per_ms": v.tolist(), "t_dep_ms": float(p[3]), "t_dep_sigma_ms": float(sig[3]), "speed": speed, "speed_sigma": float(np.sqrt(v @ cov[:3, :3] @ v) / speed),
                     "launch_deg": launch, "azimuth_deg": azim, "chi2": float(res @ res), "dof": dof}
    say()
    say("%s: mid-contact %+.2f ms from the application's contact instant (+-%.2f, formal); ball speed %.1f m/s (+-%.1f); launch %.1f deg above the camera axis, %.1f deg right of it; chi2 %.1f on %d" % (
        name, p[3], sig[3], speed, results[name]["speed_sigma"], launch, azim, res @ res, dof))
    for f in (44, 45, 46):
        pos, px, r = model(p, obs[f]["t"])
        results[name]["f%d" % f] = {"position_mm": pos.tolist(), "pixel": px.tolist(), "radius_px": r}
        say("   f%d: ball at (%.1f, %.1f, %.1f) mm = %.1f mm right, %.1f mm up, %.1f mm farther than at rest; fit minus measured: %+.2f, %+.2f px" % (
            f, *pos, pos[0] - P0[0], -(pos[1] - P0[1]), pos[2] - P0[2], px[0] - ball0_px[0] - obs[f]["dx"],
            (px[1] - ball0_px[1] - obs[f]["dy"]) if f != 44 else ((px[1] - r) - (ball0_px[1] - r0_px) - obs[f]["dtop"])))
say()
a, b = results["PIXELS ONLY"], results["PIXELS + RADAR SPEED"]
say("COMPARE  radar record: speed 36.5 m/s, launch 19.6 deg, horizontal 4.9 deg (uncalibrated; the session aim is +2.07 deg)")
say("         application's contact instant: 0 by definition, its own sigma 0.14 ms. The ball's track puts mid-contact %.2f ms (pixels only) or %.2f ms (with radar speed) from it." % (a["t_dep_ms"], b["t_dep_ms"]))
say("         frame 43 is then %.2f ms before mid-contact (application: 2.44) and frame 44 %.2f ms after (application: 1.18)." % (obs[44]["t"] - 3.617 - b["t_dep_ms"] if False else (times[43] - t_c - b["t_dep_ms"]) * -1, obs[44]["t"] - b["t_dep_ms"]))
say("note: launch angles here are against the camera's axis, which the application treats as level; a camera pitched down by p degrees lowers them by p.")
(AUDIT / "results" / "BALL-TRACK.txt").write_text("\n".join(OUT) + "\n")
(AUDIT / "results" / "ball-track.json").write_text(json.dumps({"observations": obs, "fits": results, "rest_mm": P0.tolist(), "frame_times_ms": {str(f): times[f] - t_c for f in (43, 44, 45, 46)}}, indent=1))

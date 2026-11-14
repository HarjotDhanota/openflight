"""The ball's straight flight after impact for any shot: velocity and the mid-contact instant, from its measured image
positions on the three frames after contact and the radar ball speed. Same method as ball_track.py (shot 1), as a
function. Differences from the resting ball only; on the first frame after contact centre x and the TOP EDGE are used
(the head may hide the lower ball); radar speed is a soft datum (sigma 2.2 m/s)."""
import json
import math

import cv2
import numpy as np
from scipy.optimize import least_squares

import ball_flight_lib as bf
import geom
from common import AUDIT


def radar_speed(shot):
    replay = json.loads((AUDIT / "results" / ("shot%d" % shot) / "replay.json").read_text())
    return float(replay["stages"]["ops"]["result"]["ball_speed_mph"]) * 0.44704


OPS_MM = np.array([-85.0, 47.0, -20.0])  # the OPS in the camera frame (rig file ops_offset_mm: x right, y down, z forward)


def track(s, shot, verbose=print, speed_datum="total", los_after_ms=0.0):
    """speed_datum "total" (the original behaviour, kept so earlier results reproduce): the radar speed is taken as
    the ball's full speed. "radial" (PROTOCOL addendum 9, T1): it is the velocity's component along the line from
    the OPS to the ball, the ball taken los_after_ms after mid-contact."""
    prep, scene = s["prep"], s["scene"]
    times, t_c = prep["frame_time_ms"], prep["contact_ms"]
    ball0 = np.array([prep["ball"]["x"], prep["ball"]["y"]])
    r0 = prep["ball"]["diameter_px"] / 2.0
    P0, R, F = scene.ball, geom.BALL_RADIUS_MM, scene.focal
    speed = radar_speed(shot)
    fc = s["d"]["bracket_frames"][1]
    frames = [fc, fc + 1, fc + 2]
    obs, guess, prev = {}, None, ball0
    for i, f in enumerate(frames):
        diff = s["frames"][f].astype(np.float32) - s["ref"]
        n, lab, st, cen = cv2.connectedComponentsWithStats((diff > 45.0).astype(np.uint8), connectivity=8)
        cands = [(st[k, cv2.CC_STAT_AREA], cen[k]) for k in range(1, n) if cen[k][1] < ball0[1] + 4 and abs(cen[k][0] - ball0[0]) < 90 and st[k, cv2.CC_STAT_AREA] > 25]
        if cands:
            guess = np.array(max(cands, key=lambda c: c[0])[1])
        elif guess is None:
            guess = ball0 + np.array([3.0, -8.0])
        arc = (15.0, 165.0) if i == 0 else (-10.0, 190.0)
        r = bf.measure(s["ref"], ball0, r0, arc)
        # the first frame after contact: the ball may still overlap where it rested, so try a near guess as well
        tries = [guess] + ([ball0 + np.array([4.0, -11.0]), ball0 + np.array([2.0, -6.0])] if i == 0 else [])
        m = None
        for g0 in tries:
            cand = bf.measure(s["frames"][f], g0, r0, arc)
            if cand is not None and cand["rms"] < 0.7 and np.linalg.norm(cand["centre"] - prev) <= 60 and (m is None or len(cand["points"]) > len(m["points"])):
                m = cand
        if m is None or r is None or np.linalg.norm(m["centre"] - prev) > 60:
            verbose("   ball not measurable on f%d" % f)
            continue
        obs[f] = {"t": times[f] - t_c, "dx": float(m["centre"][0] - r["centre"][0]), "dy": float(m["centre"][1] - r["centre"][1]),
                  "dtop": float((m["centre"][1] - m["radius"]) - (r["centre"][1] - r["radius"])), "first": i == 0, "rays": len(m["points"]), "rms": m["rms"]}
        prev = m["centre"]
        verbose("   ball f%d (%+.2f ms): moved %+.2f px right; centre %+.2f, top edge %+.2f px up; %d rays, rms %.2f px" % (f, obs[f]["t"], obs[f]["dx"], -obs[f]["dy"], -obs[f]["dtop"], obs[f]["rays"], m["rms"]))
    if len(obs) < 2:
        return None

    def model(p, t):
        pos = P0 + np.asarray(p[:3]) * (t - p[3])
        return pos, scene.project(pos)[0], F * R / pos[2]

    def residuals(p, use_speed=True):
        out = []
        for f, o in obs.items():
            _pos, px, r = model(p, o["t"])
            if o["first"]:
                out += [(px[0] - ball0[0] - o["dx"]) / 0.6, ((px[1] - r) - (ball0[1] - r0) - o["dtop"]) / 0.6]
            else:
                out += [(px[0] - ball0[0] - o["dx"]) / 0.4, (px[1] - ball0[1] - o["dy"]) / 0.4]
        if use_speed and speed_datum == "radial":
            v = np.asarray(p[:3])
            los = P0 + v * los_after_ms - OPS_MM
            out.append((float(v @ los / np.linalg.norm(los)) - speed) / 2.2)
        elif use_speed:
            out.append((float(np.linalg.norm(p[:3])) - speed) / 2.2)
        return np.asarray(out)

    fits = {}
    for name, use in (("pixels + radar", True), ("pixels only", False)):
        best = None
        for vz0 in (20.0, 35.0, 50.0):
            for td0 in (-2.0, -1.0, 0.0, 1.0):
                sol = least_squares(residuals, [4.0, -9.0, vz0, td0], args=(use,), x_scale=[2.0, 2.0, 10.0, 0.5])
                if best is None or sol.cost < best.cost:
                    best = sol
        p = best.x
        cov = np.linalg.pinv(best.jac.T @ best.jac)
        v = p[:3]
        res = residuals(p, use)
        fits[name] = {"t_mid": float(p[3]), "t_mid_sigma": float(math.sqrt(cov[3, 3])), "velocity": v.tolist(), "speed": float(np.linalg.norm(v)),
                      "launch_deg": math.degrees(math.asin(-v[1] / np.linalg.norm(v))), "azimuth_deg": math.degrees(math.atan2(v[0], v[2])),
                      "worst_px": float(np.max(np.abs(res[:2 * len(obs)] * np.repeat([0.6 if o["first"] else 0.4 for o in obs.values()], 2)))),
                      "positions": {str(f): model(p, o["t"])[0].tolist() for f, o in obs.items()}}
    return {"radar_speed": speed, "frames": list(obs), "fits": fits}

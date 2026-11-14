"""Where the ball is after impact, shot 1, in the camera's 3-D frame.

Measured on pixels (frames 44, 45, 46): the ball's centre, from a circle fitted to the sub-pixel half-level edge of its
visible upper arc (the head hides its lower part on f44). That fixes the DIRECTION from the camera to the ball well.
The DEPTH along that direction is not measurable from one view to better than the size of the ball in pixels allows
(about +-4 %), so it is solved from two recorded numbers instead, stated here before running:
   radar ball speed 81.66 mph (OPS243; not corrected for the launch angle here), the application's contact instant,
   and an assumed contact duration of 0.45 ms (ball displaced speed x duration / 2 when it leaves the face).
   |P44 - P_rest| = speed x (t44 - t_contact - duration/2), with P44 on the measured ray.
Checks that use nothing fitted: the same straight flight must hit the measured centres on f45 and f46, and the
apparent ball size on each frame must agree with the solved depth.
Writes results/BALL-FLIGHT.txt, results/ball-flight.json, figures/ball-flight-shot1.png. Usage: ball_flight.py"""
import json
import math

import cv2
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import MultipleLocator
from scipy.optimize import least_squares

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
P0 = SCENE.ball
R_MM = geom.BALL_RADIUS_MM
SPEED = 81.66409309716798 * 0.44704      # m/s = mm/ms
DURATION = 0.45                           # ms, assumed
FOCAL = SCENE.focal
CX, CY = SCENE.centre


def measure(image, guess, arc=(-10.0, 190.0), step_min=15.0):
    """Circle through the half-level edge of the ball over one arc (0 = image right, 90 = up). Returns centre, radius,
    edge points, rms. The ball is shaded (bright top, dark underside), so the half-level edge sits a little inside the
    true edge: the same routine over the same arc is run on the RESTING ball and only differences are used."""
    img = cv2.GaussianBlur(image.astype(np.float32), (0, 0), 0.6)
    centre, radius = np.array(guess, dtype=float), r0_px
    pts = None
    for _ in range(4):
        inside = float(np.median([img[int(round(centre[1] - 0.35 * radius * math.sin(a))), int(round(centre[0] + 0.35 * radius * math.cos(a)))] for a in np.linspace(0.3, math.pi - 0.3, 9)]))
        pts = []
        for ang in np.arange(arc[0], arc[1] + 0.1, 5.0):
            d = np.array([math.cos(math.radians(ang)), -math.sin(math.radians(ang))])
            rs = np.arange(0.45 * radius, 1.9 * radius, 0.25)
            prof = np.array([img[int(round(centre[1] + r * d[1])), int(round(centre[0] + r * d[0]))] if 0 <= int(round(centre[1] + r * d[1])) < img.shape[0] and 0 <= int(round(centre[0] + r * d[0])) < img.shape[1] else np.nan for r in rs])
            if np.isnan(prof).any():
                continue
            outside = float(np.min(prof[rs > 1.25 * radius])) if (rs > 1.25 * radius).any() else float(prof[-1])
            if inside - outside < step_min:
                continue                                       # no clean ball-to-background step on this ray (occluded or washed out)
            half = 0.5 * (inside + outside)
            below = np.nonzero(prof < half)[0]
            if len(below) == 0 or below[0] == 0:
                continue
            i = below[0]
            r_edge = rs[i - 1] + (prof[i - 1] - half) / (prof[i - 1] - prof[i]) * (rs[i] - rs[i - 1])
            if 0.6 * radius < r_edge < 1.5 * radius:
                pts.append(centre + r_edge * d)
        pts = np.asarray(pts)
        if len(pts) < 6:
            return None

        def res(p):
            return np.hypot(pts[:, 0] - p[0], pts[:, 1] - p[1]) - p[2]

        sol = least_squares(res, [centre[0], centre[1], radius], loss="soft_l1", f_scale=0.5)
        centre, radius = sol.x[:2], float(sol.x[2])
    r = res(sol.x)
    span = 5.0 * (len(pts) - 1)
    return {"centre": centre, "radius": radius, "points": pts, "rms": float(np.sqrt(np.mean(r ** 2))), "arc_deg": span}


def ray(px):
    v = np.array([(px[0] - CX) / FOCAL, (px[1] - CY) / FOCAL, 1.0])
    return v / np.linalg.norm(v)


def solve(centre_px, tau, speed=SPEED):
    r = ray(centre_px)
    b = float(r @ P0)
    disc = b * b - float(P0 @ P0) + (speed * tau) ** 2
    if disc < 0:
        return None
    return (b + math.sqrt(disc)) * r


# ---------------------------------------------------------------- measure the ball on the frames after contact
ref, _sig = s["ref"], s["sigma"]
ARCS = {44: (15.0, 165.0), 45: (-10.0, 190.0), 46: (-10.0, 190.0)}   # f44: only the arc clear of the head and the turf haze
found, rest = {}, {}
guess = ball0_px + np.array([4.0, -11.0])
for f in (44, 45, 46):
    diff = s["frames"][f].astype(np.float32) - ref
    bright = (diff > 45.0).astype(np.uint8)
    n, lab, st, cen = cv2.connectedComponentsWithStats(bright, connectivity=8)
    if n > 1:
        cands = [(st[i, cv2.CC_STAT_AREA], cen[i]) for i in range(1, n) if cen[i][1] < ball0_px[1] + 4 and abs(cen[i][0] - ball0_px[0]) < 80 and st[i, cv2.CC_STAT_AREA] > 25]
        if cands and f != 44:
            guess = np.array(max(cands, key=lambda c: c[0])[1])
    m = measure(s["frames"][f], guess, ARCS[f])
    r = measure(ref, ball0_px, ARCS[f])
    if m is None or r is None:
        say("f%d: ball not measurable (too few clean edge rays)" % f)
        continue
    rest[f] = r
    raw_centre, raw_radius = m["centre"].copy(), m["radius"]
    # differential: the same routine on the resting ball over the same arc removes the shading bias
    m["centre"] = ball0_px + (raw_centre - r["centre"])
    m["radius"] = r0_px * raw_radius / r["radius"]
    m["points"] = m["points"] + (m["centre"] - raw_centre)
    found[f] = m
    say("f%d (%+.3f ms): raw fit centre (%.2f, %.2f) r %.2f px over %d rays, rms %.2f | resting ball by the same routine: (%.2f, %.2f) r %.2f (detector: %.2f, %.2f, r %.2f) | corrected centre (%.2f, %.2f), radius %.2f px" % (
        f, times[f] - t_c, *raw_centre, raw_radius, len(m["points"]), m["rms"], *r["centre"], r["radius"], *ball0_px, r0_px, *m["centre"], m["radius"]))
    guess = raw_centre + (raw_centre - (guess if f == 44 else raw_centre)) * 0 + np.array([8.0, -17.0])

# ---------------------------------------------------------------- depth from radar speed and the contact instant
tau44 = times[44] - t_c - DURATION / 2.0
P44 = solve(found[44]["centre"], tau44)
V = (P44 - P0) / tau44
launch = math.degrees(math.asin(-V[1] / np.linalg.norm(V)))
azim = math.degrees(math.atan2(V[0], V[2]))
say()
say("resting ball: (%.1f, %.1f, %.1f) mm; ball speed %.2f m/s; flight time to f44 %.3f ms (contact %.3f ms before the frame, less half of %.2f ms on the face)" % (*P0, SPEED, tau44, times[44] - t_c, DURATION))
say("BALL ON FRAME 44: (%.1f, %.1f, %.1f) mm in the camera frame = %.1f mm right, %.1f mm up, %.1f mm farther than at rest; %.1f mm from rest in all" % (
    *P44, P44[0] - P0[0], -(P44[1] - P0[1]), P44[2] - P0[2], np.linalg.norm(P44 - P0)))
say("   implied launch in the camera's level frame: %.1f deg up, %.1f deg to the right of the camera axis (radar record: %.1f deg up, %.1f deg horizontal, uncalibrated)" % (launch, azim, 19.594995581378374, 4.944333871006876))
size_depth = FOCAL * R_MM / found[44]["radius"]
say("   apparent size check: radius %.2f px puts the ball at %.0f mm; solved depth %.0f mm (difference %+.0f mm = %+.1f %%; 0.3 px of radius is %.0f mm)" % (
    found[44]["radius"], size_depth, P44[2], size_depth - P44[2], 100 * (size_depth / P44[2] - 1), 0.3 / found[44]["radius"] * P44[2]))
record = {"rest_mm": P0.tolist(), "radius_mm": R_MM, "speed_mm_per_ms": SPEED, "duration_ms": DURATION, "velocity_mm_per_ms": V.tolist(),
          "launch_deg": launch, "azimuth_deg": azim, "frames": {}}
record["frames"]["f44"] = {"t_ms": times[44] - t_c, "centre_px": found[44]["centre"].tolist(), "radius_px": found[44]["radius"], "position_mm": P44.tolist(), "measured": True}
say()
say("CHECK: the same straight flight, carried to the later frames, against the measured centres there")
for f in (45, 46):
    if f not in found:
        continue
    tau = times[f] - t_c - DURATION / 2.0
    pred = P0 + V * tau
    pred_px = SCENE.project(pred)[0]
    pred_r = FOCAL * R_MM / pred[2]
    d = found[f]["centre"] - pred_px
    # where on the measured ray the flight would put it (closest point), for drawing
    r = ray(found[f]["centre"])
    on_ray = float(pred @ r) * r
    say("   f%d (%+.3f ms): predicted centre (%.2f, %.2f) px, measured (%.2f, %.2f): off by %+.2f, %+.2f px = %.1f mm across the view | predicted radius %.2f px, measured %.2f" % (
        f, times[f] - t_c, *pred_px, *found[f]["centre"], d[0], d[1], float(np.hypot(*d)) * pred[2] / FOCAL, pred_r, found[f]["radius"]))
    record["frames"]["f%d" % f] = {"t_ms": times[f] - t_c, "centre_px": found[f]["centre"].tolist(), "radius_px": found[f]["radius"], "position_mm": on_ray.tolist(),
                                   "predicted_mm": pred.tolist(), "miss_px": d.tolist(), "measured": True}
say()
say("SENSITIVITY of the ball's position on f44 (direction from the camera fixed by the pixels; these move it along that ray)")
for label, kw in (("contact 0.14 ms earlier", {"tau": tau44 + 0.14}), ("contact 0.14 ms later", {"tau": tau44 - 0.14}), ("contact duration 0.35 ms", {"tau": tau44 + 0.05}), ("contact duration 0.55 ms", {"tau": tau44 - 0.05}),
                  ("speed +6 % (radar reads along its own line)", {"tau": tau44, "speed": SPEED * 1.06}), ("speed -3 %", {"tau": tau44, "speed": SPEED * 0.97})):
    alt = solve(found[44]["centre"], **kw)
    say("   %-44s %+5.1f mm in depth, %+4.1f mm up, %+4.1f mm right" % (label, alt[2] - P44[2], -(alt[1] - P44[1]), alt[0] - P44[0]))
for dpx in ((0.3, 0.0), (0.0, 0.3)):
    alt = solve(found[44]["centre"] + np.array(dpx), tau44)
    say("   centre %.1f px %s: %+5.1f mm in depth, %+4.1f mm up, %+4.1f mm right" % (0.3, "right" if dpx[0] else "down", alt[2] - P44[2], -(alt[1] - P44[1]), alt[0] - P44[0]))
depth_lo, depth_hi = solve(found[44]["centre"], tau44 - 0.19, SPEED * 0.97), solve(found[44]["centre"], tau44 + 0.19, SPEED * 1.06)
record["frames"]["f44"]["depth_band_mm"] = [depth_lo.tolist(), depth_hi.tolist()]
say("   band drawn in the viewer (timing 0.19 ms and speed together): %.0f mm nearer to %.0f mm farther along the ray" % (np.linalg.norm(P44 - depth_lo), np.linalg.norm(depth_hi - P44)))
(AUDIT / "results" / "BALL-FLIGHT.txt").write_text("\n".join(OUT) + "\n")
(AUDIT / "results" / "ball-flight.json").write_text(json.dumps(record, indent=1))

# ---------------------------------------------------------------- figure
frames_shown = [f for f in (44, 45, 46) if f in found]
fig, axs = plt.subplots(1, len(frames_shown), figsize=(11 * len(frames_shown), 9.5), squeeze=False)
for a, f in zip(axs[0], frames_shown):
    m = found[f]
    c = m["centre"]
    x0, x1, y0, y1 = int(c[0] - 34), int(c[0] + 34), int(c[1] - 30), int(c[1] + 30)
    y0 = max(y0, 0)
    a.imshow(s["frames"][f][y0:y1, x0:x1], cmap="gray", vmin=0, vmax=200, interpolation="nearest", extent=(x0 - .5, x1 - .5, y1 - .5, y0 - .5))
    t = np.linspace(0, 2 * math.pi, 200)
    a.plot(c[0] + m["radius"] * np.cos(t), c[1] + m["radius"] * np.sin(t), color="#39ff14", linewidth=1.2, label="fitted circle, radius %.2f px" % m["radius"])
    a.scatter(m["points"][:, 0], m["points"][:, 1], s=14, c="#ff2bd6", label="half-level edge points used (%d)" % len(m["points"]))
    a.scatter([c[0]], [c[1]], s=80, marker="+", c="#39ff14")
    tau = times[f] - t_c - DURATION / 2.0
    pp = SCENE.project(P0 + V * tau)[0]
    a.scatter([pp[0]], [pp[1]], s=80, marker="x", c="#ffe600", label="straight flight from f44's solution" if f != 44 else "solved position (same point by construction)")
    a.add_patch(plt.Circle(ball0_px, r0_px, fill=False, color="w", linewidth=0.7, linestyle=":"))
    a.xaxis.set_major_locator(MultipleLocator(5))
    a.yaxis.set_major_locator(MultipleLocator(5))
    a.tick_params(labelsize=7)
    a.set_xlim(x0 - .5, x1 - .5)
    a.set_ylim(y1 - .5, y0 - .5)
    a.legend(fontsize=8, loc="lower left")
    a.set_title("shot 1 frame %d, %+.2f ms from contact: ball centre (%.1f, %.1f) px" % (f, times[f] - t_c, *c), fontsize=10)
fig.tight_layout()
fig.savefig(AUDIT / "figures" / "ball-flight-shot1.png", dpi=58)

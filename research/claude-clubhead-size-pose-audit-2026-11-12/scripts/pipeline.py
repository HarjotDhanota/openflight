"""One pipeline, any shot, either club model. Research reading on development shots; no photo is read here.

  1. ball track  -> mid-contact instant (ball_track_lib; pixels + radar speed)
  2. club across the view on the last frame before contact and the first after: shaft line (across the shaft) + the
     head's top edge (along it). On the frame after, the top edge is searched within +-8 px of the earlier model
     carried by the ferrule's motion and must have the dark cavity under it; a shaft-only placement (shaft line +
     ferrule at the hosel length seen on the frame before) is the second opinion.
  3. depth: the face touches the ball at mid-contact; the two frames follow from club speed x time (0.83 after).
     Steps 2 and 3 are repeated once so each placement is made at its final depth.
  4. reading: the ball's contact point on the face, reported as mm from the TOE EXTREME along the score lines and mm
     above the LEADING EDGE, which mean the same thing on either model (each model's own face centre differs).
Models: "generic" = the application's drawing with one size number (fitted to the owner's trace on shot 1 f43 by this
same code, then frozen for every shot); "ts4" = results/ts4-model.json at its true size.
Usage: pipeline.py   (writes results/PIPELINE.txt, results/pipeline.json, figures/pipeline-<model>-shot<N>.png)"""
import json
import math

import cv2
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import MultipleLocator
from scipy.optimize import least_squares, minimize_scalar

import ball_track_lib as btl
import carry_lib as cl
import diag_lib as dl
import geom
import rig
from common import AUDIT

OUT = []


def say(t=""):
    OUT.append(t)
    print(t)


TRACE = np.asarray(json.loads((AUDIT / "owner-trace" / "shot1-f43-trace.json").read_text())["polygon_native_px"])


def on_ray(point, depth):
    return point * (depth / point[2])


class Shot:
    def __init__(self, shot):
        self.shot = shot
        self.s = dl.setup(shot)
        s = self.s
        self.scene, self.prep, self.angles = s["scene"], s["prep"], s["angles"]
        self.times, self.t_app = self.prep["frame_time_ms"], self.prep["contact_ms"]
        self.fr, self.fc = s["d"]["bracket_frames"]
        self.shape = s["frames"][self.fr].shape
        c = cl.carry(shot)
        self.B, self.edge_r = c["B"], c["edge"]
        self.C = dl.observe_frame(s, self.fc)
        self.vz = float(s["v3"][2])
        self.track = btl.track(s, shot, verbose=say)
        toe, up, normal = geom.axes(*self.angles)
        self.toe, self.up, self.normal = toe, up, normal
        self.contact_point = self.scene.ball - geom.BALL_RADIUS_MM * normal

    def t(self, f):
        return self.times[f] - self.t_app

    def axis_residual(self, club, origin, obs, shift_px=0.0):
        _polys, axis, _lead = club.parts(self.angles, origin)
        a, b = obs["line"][0], obs["line"][1]
        fy = obs["ferrule"]["y"]
        rows = np.array([fy - 16.0, fy - 6.0, fy + 5.0])
        tt = (rows - axis[0, 1]) / (axis[1, 1] - axis[0, 1])
        xm = axis[0, 0] + tt * (axis[1, 0] - axis[0, 0])
        return (a + b * rows + shift_px - xm) / math.hypot(1.0, b)

    def place_edge(self, club, f, obs, edge, depth):
        base = on_ray(dl.origin_at(self.s, f), depth)
        weight = math.sqrt(max(len(edge), 1) / 3.0)
        ex, ey = np.array([1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0])

        def res(p):
            origin = base + p[0] * ex + p[1] * ey
            _polys, _axis, lead = club.parts(self.angles, origin)
            r_edge = np.array([e[1] - rig.top_at(lead, e[0]) for e in edge])
            return np.concatenate([self.axis_residual(club, origin, obs) * weight, np.where(np.isfinite(r_edge), r_edge, 5.0)])

        sol = least_squares(res, [0.0, 0.0], x_scale=5.0)
        r = res(sol.x)
        return base + sol.x[0] * ex + sol.x[1] * ey, float(np.sqrt(np.mean((r[:3] / weight) ** 2))), float(np.sqrt(np.mean(r[3:] ** 2)))

    def place_ferrule(self, club, f, obs, length, depth):
        base = on_ray(dl.origin_at(self.s, f), depth)
        ex, ey = np.array([1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0])

        def res(p):
            origin = base + p[0] * ex + p[1] * ey
            fer = self.scene.project(club.shaft_point(self.angles, origin, length))[0]
            return np.concatenate([self.axis_residual(club, origin, obs), [fer[1] - obs["ferrule"]["y"]]])

        sol = least_squares(res, [0.0, 0.0], x_scale=5.0)
        return base + sol.x[0] * ex + sol.x[1] * ey

    def ferrule_length(self, club, origin, obs):
        ts = np.linspace(20.0, 140.0, 4801)
        pts = np.array([club.shaft_point(self.angles, origin, 0.0)] * len(ts)) + ts[:, None] * (club.shaft_point(self.angles, origin, 1.0) - club.shaft_point(self.angles, origin, 0.0))
        ys = self.scene.project(pts)[:, 1]
        return float(ts[int(np.argmin(np.abs(ys - obs["ferrule"]["y"])))])

    def edge_window(self, frame, pred_lead, reach=8):
        img = cv2.GaussianBlur(frame.astype(np.float32), (0, 0), 0.7)
        grad = img[1:, :] - img[:-1, :]
        toe = pred_lead[:, 0].max()
        inside = rig.polygon_mask(pred_lead, frame.shape)[rig.SS // 2::rig.SS, rig.SS // 2::rig.SS]
        if inside.sum() < 20:
            return np.zeros((0, 3))
        dark = float(np.percentile(img[inside], 5)) + 15.0
        out = []
        for x in range(int(toe) - 26, int(toe) - 3):
            y = rig.top_at(pred_lead, float(x))
            if not np.isfinite(y):
                continue
            lo, hi = int(round(y)) - reach, int(round(y)) + reach
            if lo < 1 or hi + 2 >= img.shape[0] or x < 0 or x >= img.shape[1]:
                continue
            g = grad[lo:hi, x].copy()
            below = np.minimum(img[lo + 1:hi + 1, x], img[lo + 2:hi + 2, x])
            g[below > dark] = 0.0
            i = int(np.argmin(g))
            if g[i] < -6.0:
                out.append((x, lo + i + 0.5, float(g[i])))
        return np.asarray(out, dtype=float).reshape(-1, 3)

    def read(self, origin, spec):
        hit = self.contact_point - origin
        u, v = float(hit @ self.toe), float(hit @ self.up)
        return {"toe_of_centre": u, "up_of_centre": v, "from_toe": spec.toe_u - u, "above_le": v - spec.le_v}

    def solve(self, spec, t_mid, f44_mode="edge", slowed=True, trace_anchor=None, hosel_length=None):
        """Placements at consistent depth and the contact reading. Returns a dict."""
        club = rig.Club(self.scene, spec, self.shape)
        t1, t2 = t_mid - self.t(self.fr), self.t(self.fc) - t_mid
        frac = t1 / (t1 + (0.83 if slowed else 1.0) * t2)
        z_r, z_c = float(dl.origin_at(self.s, self.fr)[2]), float(dl.origin_at(self.s, self.fc)[2])
        info = {}
        for _ in range(2):
            if trace_anchor is not None:
                o_r = on_ray(trace_anchor, z_r)
                info["rest"] = (0.0, 0.0)
            elif hosel_length is not None:
                # the ferrule variant: both frames from the shaft line and the ferrule at this club's hosel length; no head pixel
                o_r = self.place_ferrule(club, self.fr, self.B, hosel_length, z_r)
                info["rest"] = (0.0, 0.0)
            else:
                o_r, ra, re = self.place_edge(club, self.fr, self.B, self.edge_r, z_r)
                info["rest"] = (ra, re)
            _p, _a, lead_r = club.parts(self.angles, o_r)
            d_fer = np.array([self.C["ferrule"]["x"] - self.B["ferrule"]["x"], self.C["ferrule"]["y"] - self.B["ferrule"]["y"]])
            edge_c = self.edge_window(self.s["frames"][self.fc], lead_r + d_fer)
            if f44_mode == "edge" and len(edge_c) >= 4:
                o_c, ra, re = self.place_edge(club, self.fc, self.C, edge_c, z_c)
                info["after"] = (ra, re, len(edge_c))
            else:
                o_c = self.place_ferrule(club, self.fc, self.C, hosel_length if hosel_length is not None else self.ferrule_length(club, o_r, self.B), z_c)
                info["after"] = (0.0, 0.0, 0)
            c0 = o_r + (o_c - o_r) * frac
            ray = c0 / np.linalg.norm(c0)
            c = float(self.contact_point @ self.normal) / float(ray @ self.normal) * ray
            z_r, z_c = c[2] - self.vz * t1, c[2] + 0.83 * self.vz * t2
        return {"rest": on_ray(o_r, z_r), "contact": c, "after": on_ray(o_c, z_c), "reading": self.read(c, spec), "info": info, "edge_after": edge_c, "frac": frac, "club": club}


def fit_trace(sh, spec, free_size):
    """Translation (and one size number) of a model onto the owner's trace on shot 1 f43, at the frame's working depth."""
    target = rig.polygon_mask(TRACE, sh.shape)
    base = dl.origin_at(sh.s, 43)

    def build(p):
        sp = spec.scaled(p[2]) if free_size else spec
        club = rig.Club(sh.scene, sp, sh.shape)
        m, _a, _l = club.mask(sh.angles, base + np.array([p[0], p[1], 0.0]))
        return rig.compare(m, target, 82.0), sp

    from scipy.optimize import minimize
    x0 = [3.0, 7.0, 0.93 if free_size else 1.0]
    n = 3 if free_size else 2
    res = minimize(lambda x: build(list(x) + [1.0] * (3 - len(x)))[0]["rms"], x0[:n], method="Nelder-Mead",
                   options={"xatol": 0.01, "fatol": 0.002, "maxiter": 600, "initial_simplex": np.vstack([x0[:n]] + [np.array(x0[:n]) + np.eye(n)[i] * (3.0 if i < 2 else 0.04) for i in range(n)])})
    p = list(res.x) + [1.0] * (3 - n)
    score, sp = build(p)
    return base + np.array([p[0], p[1], 0.0]), sp, score, p[2]


def figure(sh, name, sol, spec):
    fig, axs = plt.subplots(1, 3, figsize=(30, 8.4))
    b = sh.prep["ball"]
    for a, (title, f, origin) in zip(axs, (("frame %d, last before contact" % sh.fr, sh.fr, sol["rest"]), ("frame %d, first after contact" % sh.fc, sh.fc, sol["after"]), ("mid-contact, drawn over frame %d" % sh.fr, sh.fr, sol["contact"]))):
        m, _axis, _lead = sol["club"].mask(sh.angles, origin)
        bd = rig.boundary(m)
        x0, x1 = int(bd[:, 0].min()) - 8, int(max(bd[:, 0].max(), b["x"] + b["diameter_px"] / 2)) + 10
        vis = sh.B["bottom_y"] if f == sh.fr else sh.C["bottom_y"]
        y1 = int(min(bd[:, 1].max(), vis + 20)) + 6
        y0 = y1 - int((x1 - x0) * 0.62)
        a.imshow(sh.s["frames"][f][max(y0, 0):y1, x0:x1], cmap="gray", vmin=0, vmax=160, interpolation="nearest", extent=(x0 - .5, x1 - .5, y1 - .5, max(y0, 0) - .5))
        bd = np.vstack([bd, bd[:1]])
        a.plot(bd[:, 0], np.where(bd[:, 1] <= vis, bd[:, 1], np.nan), color="#39ff14", linewidth=1.5)
        a.plot(bd[:, 0], np.where(bd[:, 1] >= vis - .5, bd[:, 1], np.nan), color="#39ff14", linewidth=0.9, linestyle=(0, (2, 2)))
        a.add_patch(plt.Circle((b["x"], b["y"]), b["diameter_px"] / 2, fill=False, color="w", linewidth=0.7, linestyle=":"))
        if sh.shot == 1 and f == 43 and "contact" not in title:
            a.plot(*np.vstack([TRACE, TRACE[:1]]).T, color="#ff2020", linewidth=1.0)
        if "after" in title and len(sol["edge_after"]):
            a.scatter(sol["edge_after"][:, 0], sol["edge_after"][:, 1], s=14, c="#ff2bd6", marker="_", linewidths=1.2)
        if "contact" in title:
            cp = sh.scene.project(sh.contact_point)[0]
            a.scatter([cp[0]], [cp[1]], s=70, marker="x", c="#ff2020", linewidths=1.6)
        a.axhline(vis, color="#ffe600", linewidth=0.5, linestyle=":")
        a.xaxis.set_major_locator(MultipleLocator(5))
        a.yaxis.set_major_locator(MultipleLocator(5))
        a.tick_params(labelsize=6)
        a.set_xlim(x0 - .5, x1 - .5)
        a.set_ylim(y1 - .5, max(y0, 0) - .5)
        a.set_title("shot %d, %s model: %s" % (sh.shot, name, title), fontsize=10)
    fig.tight_layout()
    fig.savefig(AUDIT / "figures" / ("pipeline-%s-shot%d.png" % (name, sh.shot)), dpi=56)
    plt.close(fig)


shots = {}
for n in (1, 3, 4):
    say("SHOT %d" % n)
    shots[n] = Shot(n)
    tr = shots[n].track
    if tr:
        for k, fz in tr["fits"].items():
            say("   ball track, %s: mid-contact %+.2f ms from the application's (+-%.2f); %.1f m/s; launch %.1f deg; worst pixel miss %.2f" % (k, fz["t_mid"], fz["t_mid_sigma"], fz["speed"], fz["launch_deg"], fz["worst_px"]))

# ---- the generic model's one size number, from the owner's trace on shot 1 (and the TS4 model's own check on it)
sh1 = shots[1]
gen0, ts4 = rig.generic(sole_width=sh1.s["sole"][0], bounce=sh1.s["sole"][1]), rig.ts4()
say()
say("OWNER'S TRACE, shot 1 f43 (boundary rms px, overlap) at the frame's working depth")
anchor_g, gen, score_g, size_g = fit_trace(sh1, gen0, True)
say("   generic, moved + one size number: %.2f px, %.2f; size %.3f" % (score_g["rms"], score_g["iou"], size_g))
_a0, _s0, score_g1, _ = fit_trace(sh1, gen0, False)
say("   generic at size 1.000, moved only: %.2f px, %.2f" % (score_g1["rms"], score_g1["iou"]))
anchor_t, _sp, score_t, _ = fit_trace(sh1, ts4, False)
say("   TS4 at its true size, moved only:  %.2f px, %.2f" % (score_t["rms"], score_t["iou"]))
_a2, _s2, score_t2, size_t = fit_trace(sh1, ts4, True)
say("   TS4, moved + one size number:      %.2f px, %.2f; size %.3f (1.000 would mean the spec scale and the camera depth agree)" % (score_t2["rms"], score_t2["iou"], size_t))

# where the TS4's shaft axis meets the ground is the one spec number whose definition is uncertain: scan it on the trace
say("   TS4 at true size, shaft axis meeting the ground at x mm from the toe (spec blade length 80.1 if it runs to the hosel centreline, 72.5 if to the hosel's outer edge):")
from dataclasses import replace as _replace
scan = {}
for xg in (70.0, 72.5, 75.0, 77.5, 80.1, 82.5):
    _a, _s, sc, _ = fit_trace(sh1, _replace(ts4, axis_ground_from_toe=xg), False)
    scan[xg] = sc["rms"]
    say("      x = %.1f: %.2f px, %.2f" % (xg, sc["rms"], sc["iou"]))
best_xg = min(scan, key=scan.get)
ts4b = _replace(ts4, name="TS4 7-iron (research), axis at %.1f" % best_xg, axis_ground_from_toe=best_xg)
anchor_tb, _sp, score_tb, _ = fit_trace(sh1, ts4b, False)
_a3, _s3, score_tb2, size_tb = fit_trace(sh1, ts4b, True)
say("   -> taken: %.1f mm (lowest). With it, a free size comes out %.3f (%.2f px)" % (best_xg, size_tb, score_tb2["rms"]))

results = {}
for name, spec, anchor in (("generic", gen, anchor_g), ("ts4", ts4b, anchor_tb)):
    say()
    say("MODEL %s" % spec.name)
    hosel = sh1.ferrule_length(rig.Club(sh1.scene, spec, sh1.shape), anchor, sh1.B)
    say("   this club's hosel length on this model (ground point to the ferrule, on shot 1's traced frame): %.1f mm" % hosel)
    for n, sh in shots.items():
        if not sh.track:
            say("   shot %d: no ball track; skipped" % n)
            continue
        t_track = sh.track["fits"]["pixels + radar"]["t_mid"]
        rows = []
        for inst, t_mid in (("track", t_track), ("app", 0.0)):
            for mode in ("edge", "ferrule"):
                for slowed in (True, False):
                    for use_trace in ((False, True) if n == 1 else (False,)):
                        sol = sh.solve(spec, t_mid, mode, slowed, anchor if use_trace else None)
                        rows.append((inst, mode, slowed, use_trace, sol))
        tr_rows = [r for r in rows if r[0] == "track"]
        ft = np.array([r[4]["reading"]["from_toe"] for r in tr_rows])
        al = np.array([r[4]["reading"]["above_le"] for r in tr_rows])
        ap = [r for r in rows if r[0] == "app"]
        main = [r for r in tr_rows if r[1] == "edge" and r[2] and not r[3]][0][4]
        say("   shot %d: frame %d is %.2f ms before mid-contact, frame %d %.2f ms after | before-frame fit: shaft %.2f px, top edge %.2f px | after-frame: %d edge columns, shaft %.2f, edge %.2f px" % (
            n, sh.fr, t_track - sh.t(sh.fr), sh.fc, sh.t(sh.fc) - t_track, *main["info"]["rest"], main["info"]["after"][2], main["info"]["after"][0], main["info"]["after"][1]))
        say("      impact (ball-track instant, %d variants): %.1f .. %.1f mm from the toe (median %.1f); %.1f .. %.1f mm above the leading edge (median %.1f)" % (
            len(tr_rows), ft.min(), ft.max(), np.median(ft), al.min(), al.max(), np.median(al)))
        say("      with the application's instant: median %.1f mm from the toe, %.1f mm above the leading edge" % (
            np.median([r[4]["reading"]["from_toe"] for r in ap]), np.median([r[4]["reading"]["above_le"] for r in ap])))
        fer_rows = [sh.solve(spec, t_track, "ferrule", slowed, None, hosel) for slowed in (True, False)]
        fft = np.array([r["reading"]["from_toe"] for r in fer_rows])
        fal = np.array([r["reading"]["above_le"] for r in fer_rows])
        say("      FERRULE variant (shaft line + ferrule on both frames, hosel length from shot 1): %.1f mm from the toe, %.1f mm above the leading edge" % (fft.mean(), fal.mean()))
        sole_below = (main["contact"] + spec.le_v * sh.up)[1] - (sh.scene.ball[1] + geom.BALL_RADIUS_MM)
        say("      leading edge at mid-contact: %.1f mm below the ball's bottom" % sole_below)
        results["%s|%d" % (name, n)] = {"from_toe": [float(ft.min()), float(np.median(ft)), float(ft.max())], "above_le": [float(al.min()), float(np.median(al)), float(al.max())],
                                        "app_instant": [float(np.median([r[4]["reading"]["from_toe"] for r in ap])), float(np.median([r[4]["reading"]["above_le"] for r in ap]))],
                                        "t_mid": t_track, "leading_edge_below_ball_bottom": float(sole_below), "ferrule": [float(fft.mean()), float(fal.mean())],
                                        "ferrule_poses": {k: fer_rows[0][k].tolist() for k in ("rest", "contact", "after")},
                                        "main": {k: main[k].tolist() for k in ("rest", "contact", "after")}, "toe_u": spec.toe_u, "le_v": spec.le_v}
        figure(sh, name, main, spec)
(AUDIT / "results" / "PIPELINE.txt").write_text("\n".join(OUT) + "\n")
(AUDIT / "results" / "pipeline.json").write_text(json.dumps({"results": results, "generic_size": size_g, "ts4_size_check": size_t,
                                                             "tracks": {str(n): sh.track for n, sh in shots.items()}}, indent=1, default=float))

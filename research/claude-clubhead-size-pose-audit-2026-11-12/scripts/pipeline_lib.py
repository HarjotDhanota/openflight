"""class Shot and fit_trace of pipeline.py as a library, so later scripts can use them without rerunning pipeline.py
(which would rewrite its results). The bodies are copied unchanged; two optional constructor arguments are added
(PROTOCOL addendum 9): `angles` replaces the application's supplied (face, loft, roll) and `track_kwargs` is passed to
ball_track_lib.track. With both left at None this is pipeline.py's Shot."""
import json
import math

import cv2
import numpy as np
from scipy.optimize import least_squares, minimize

import ball_track_lib as btl
import carry_lib as cl
import diag_lib as dl
import geom
import rig
from common import AUDIT

TRACE = np.asarray(json.loads((AUDIT / "owner-trace" / "shot1-f43-trace.json").read_text())["polygon_native_px"])


def on_ray(point, depth):
    return point * (depth / point[2])


class Shot:
    def __init__(self, shot, angles=None, track_kwargs=None, say=lambda *_a: None, shared=None):
        self.shot = shot
        if shared is not None:  # reuse another Shot's loaded pixels and observations (same shot, other angles/track)
            self.s, self.B, self.edge_r, self.C = shared.s, shared.B, shared.edge_r, shared.C
        else:
            self.s = dl.setup(shot)
            c = cl.carry(shot)
            self.B, self.edge_r = c["B"], c["edge"]
            self.C = dl.observe_frame(self.s, self.s["d"]["bracket_frames"][1])
        s = self.s
        self.scene, self.prep = s["scene"], s["prep"]
        self.angles = tuple(s["angles"]) if angles is None else tuple(angles)
        self.times, self.t_app = self.prep["frame_time_ms"], self.prep["contact_ms"]
        self.fr, self.fc = s["d"]["bracket_frames"]
        self.shape = s["frames"][self.fr].shape
        self.vz = float(s["v3"][2])
        self.track = btl.track(s, shot, verbose=say, **(track_kwargs or {}))
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

    x0 = [3.0, 7.0, 0.93 if free_size else 1.0]
    n = 3 if free_size else 2
    res = minimize(lambda x: build(list(x) + [1.0] * (3 - len(x)))[0]["rms"], x0[:n], method="Nelder-Mead",
                   options={"xatol": 0.01, "fatol": 0.002, "maxiter": 600, "initial_simplex": np.vstack([x0[:n]] + [np.array(x0[:n]) + np.eye(n)[i] * (3.0 if i < 2 else 0.04) for i in range(n)])})
    p = list(res.x) + [1.0] * (3 - n)
    score, sp = build(p)
    return base + np.array([p[0], p[1], 0.0]), sp, score, p[2]

"""Computation behind carry.py and readout.py. PROTOCOL addendum 1 (M3b, M6): shaft-anchored rigid carry.

The club is one rigid body. Its shaft and ferrule stay sharp and clear of the turf on every frame. So:
  1. on the IN-AIR frame take the club's OWN observed head silhouette (no head model at all),
  2. move it to the NEAR frame with the 2-D similarity fixed by the ferrule (origin), the shaft axis (rotation) and
     the supplied depth ratio (scale),
  3. compare where it lands with (a) the sharp dark cavity's upper edge on the NEAR frame, (b) the lowest visible
     row, (c) the application's fitted model.
The generic model registered on the IN-AIR frame (translation only, M4) is carried by the same transform: that is
the proposed overlay. Nothing is fitted on the NEAR frame except the toe-ward stretch w (one number: observed toe
against carried toe, measured from the shaft axis), reported separately.

Writes results/CARRY.txt, results/carry.json, figures/carry-shot<N>.png. Usage: carry.py"""
import json
import math

import cv2
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import MultipleLocator

import diag_lib as dl
import geom
import sil
from common import AUDIT

def contour(mask):
    cs, _h = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    c = max(cs, key=cv2.contourArea)
    return c[:, 0, :].astype(float)


def similarity(obs_a, obs_b, k):
    """p_b = F_b + k R (p_a - F_a), R the rotation taking frame A's shaft direction to frame B's."""
    fa, fb = np.array([obs_a["ferrule"]["x"], obs_a["ferrule"]["y"]]), np.array([obs_b["ferrule"]["x"], obs_b["ferrule"]["y"]])
    ta, tb = math.atan2(1.0, obs_a["line"][1]), math.atan2(1.0, obs_b["line"][1])  # direction (b, 1): down the shaft
    r = tb - ta
    rot = np.array([[math.cos(r), math.sin(r)], [-math.sin(r), math.cos(r)]])  # rotates (b,1) of A onto (b,1) of B in (x,y)
    da, db = np.array([obs_a["line"][1], 1.0]), np.array([obs_b["line"][1], 1.0])
    da, db = da / np.linalg.norm(da), db / np.linalg.norm(db)
    if np.linalg.norm(rot @ da - db) > 1e-6:
        rot = rot.T
    assert np.linalg.norm(rot @ da - db) < 1e-6
    return lambda p: fb + k * (np.atleast_2d(p) - fa) @ rot.T, math.degrees(r), fb, db


def stretch(points, origin, axis, w):
    """Scale the component perpendicular to the shaft axis by w (rotation of the head about the shaft seen from behind)."""
    rel = points - origin
    along = rel @ axis
    perp = rel - np.outer(along, axis)
    return origin + np.outer(along, axis) + w * perp


def cavity_top(frame, columns, row_lo, row_hi):
    """Per column: the row of the strongest downward darkening above the brightest thin bar (the cavity's upper edge)."""
    img = cv2.GaussianBlur(frame.astype(np.float32), (0, 0), 0.7)
    grad = img[1:, :] - img[:-1, :]  # value at (y+0.5)
    out = []
    for x in columns:
        col = img[row_lo:row_hi + 1, x]
        bar = row_lo + int(np.argmax(col))
        g = grad[row_lo:max(bar - 2, row_lo + 1), x]
        if len(g) < 3:
            continue
        i = int(np.argmin(g))
        if g[i] < -6.0:
            out.append((x, row_lo + i + 0.5, float(g[i])))
    return np.asarray(out, dtype=float).reshape(-1, 3)


def top_at(poly, x):
    """Highest y of a closed polygon at column x (scan-line crossing)."""
    a, b = poly, np.roll(poly, -1, axis=0)
    hit = ((a[:, 0] <= x) & (b[:, 0] > x)) | ((b[:, 0] <= x) & (a[:, 0] > x))
    if not hit.any():
        return math.nan
    t = (x - a[hit, 0]) / (b[hit, 0] - a[hit, 0])
    return float((a[hit, 1] + t * (b[hit, 1] - a[hit, 1])).min())


DIAG = json.loads((AUDIT / "results" / "diag.json").read_text())


def carry(shot):
    diag = DIAG
    s = dl.setup(shot)
    fa, fb = dl.FRAMES[shot]
    A, B = dl.observe_frame(s, fa), dl.observe_frame(s, fb)
    za, zb = float(dl.origin_at(s, fa)[2]), float(dl.origin_at(s, fb)[2])
    k = za / zb
    move, turn, fb_pt, axis_b = similarity(A, B, k)
    own_a = contour(A["head"])
    own_b = move(own_a)
    mm = zb / s["scene"].focal
    prep = s["prep"]
    ball_bottom = prep["ball"]["y"] + prep["ball"]["diameter_px"] / 2.0
    # the generic model registered on the IN-AIR frame by translation only (M4, full window)
    reg = diag["m4"][str(shot)]["IN-AIR f%d floor 8" % fa]["table"]["supplied"]["full"]
    face, loft, lie = s["angles"]
    ax3 = geom.axes(face, loft, lie)
    gen_a, _ = geom.model_segments(s["scene"], ax3, dl.origin_at(s, fa), *s["sole"])
    gen_a = gen_a + np.array([reg["dx"], reg["dy"]])
    gen_b = move(gen_a)
    app_b = s["arrays"]["outline_%d" % fb]
    # toe-ward stretch: observed sharp toe on the NEAR frame against the carried own silhouette's toe, from the shaft axis
    perp = np.array([axis_b[1], -axis_b[0]])
    if perp[0] < 0:
        perp = -perp
    # sharp toe on the NEAR frame: right-most pixel of the dark cavity or the bright bar below the carried top (not the haze)
    diff_b = B["diff"]
    carried_top = float(own_b[:, 1].min())
    rows = np.arange(int(round(carried_top)) + 2, int(round(B["bottom_y"])))
    strong = (np.abs(diff_b) > 25.0)
    strong[: rows[0], :] = False
    strong[rows[-1] + 1:, :] = False
    strong[:, : int(B["x_cut"])] = False
    lab_n, lab, st, _c = cv2.connectedComponentsWithStats(strong.astype(np.uint8), connectivity=8)
    keep = np.zeros_like(strong)
    for i in range(1, lab_n):
        if st[i, cv2.CC_STAT_AREA] >= 12 and st[i, cv2.CC_STAT_LEFT] < own_b[:, 0].max():
            keep |= lab == i
    # exclude the ball in flight / brightened ball: bright pixels inside the resting ball's disc
    yy, xx = np.mgrid[0:strong.shape[0], 0:strong.shape[1]]
    in_ball = (xx - prep["ball"]["x"]) ** 2 + (yy - prep["ball"]["y"]) ** 2 <= (prep["ball"]["diameter_px"] / 2.0 + 1.0) ** 2
    keep &= ~(in_ball & (diff_b > 0) & (yy < prep["ball"]["y"] + 0.35 * prep["ball"]["diameter_px"]))
    sharp_toe_x = float(np.nonzero(keep.any(axis=0))[0].max()) + 0.5
    carried_toe = own_b[np.argmax(own_b[:, 0])]
    obs_toe = np.array([sharp_toe_x, carried_toe[1]])
    w = float(((obs_toe - fb_pt) @ perp) / ((carried_toe - fb_pt) @ perp))
    own_bw, gen_bw = stretch(own_b, fb_pt, axis_b, w), stretch(gen_b, fb_pt, axis_b, w)
    # verification on the NEAR frame: the dark cavity's upper edge against the carried top boundary
    cols = np.arange(int(sharp_toe_x) - 26, int(sharp_toe_x) - 3)
    edge = cavity_top(s["frames"][fb], cols, int(B["ferrule"]["y"]) - 14, int(round(B["bottom_y"])))
    res_own = np.array([e[1] - top_at(own_bw, e[0]) for e in edge])
    res_gen = np.array([e[1] - top_at(gen_bw, e[0]) for e in edge])
    res_app = np.array([e[1] - top_at(app_b, e[0]) for e in edge])
    res_own, res_gen, res_app = (r[np.isfinite(r)] for r in (res_own, res_gen, res_app))
    # rim offset: the same edge finder on the IN-AIR frame against the un-carried own silhouette
    cols_a = np.arange(int(A["toe_x"]) - 26, int(A["toe_x"]) - 3)
    edge_a = cavity_top(s["frames"][fa], cols_a, int(A["ferrule"]["y"]) - 14, int(round(A["bottom_y"])))
    rim = np.array([e[1] - top_at(own_a, e[0]) for e in edge_a])
    rim = rim[np.isfinite(rim)]
    return {"s": s, "A": A, "B": B, "frames": (fa, fb), "k": k, "turn": turn, "w": w, "mm": mm, "own_a": own_a, "gen_a": gen_a, "own_bw": own_bw, "gen_bw": gen_bw,
            "app_b": app_b, "edge": edge, "res_own": res_own, "res_gen": res_gen, "res_app": res_app, "sharp_toe_x": sharp_toe_x, "ball_bottom": ball_bottom,
            "rim_px": float(np.median(rim)) if len(rim) else float("nan"), "rim_n": int(len(rim))}

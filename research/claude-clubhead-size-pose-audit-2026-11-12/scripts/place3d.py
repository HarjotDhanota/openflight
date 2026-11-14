"""Place the rigid 3-D club on every frame from two things that stay sharp near impact: the SHAFT LINE (fixes the
position across the shaft) and the head's TOP EDGE (fixes it along the shaft). The sole is not used. Angles are the
application's supplied ones, unchanged. Size is ONE number taken from the owner's trace on shot 1 frame 43
(results/fit-trace.json, "translation+size") and frozen for every other frame and shot; size 1.00 is run beside it.
Independent checks, never fitted: the sharp toe (does the width agree?), the owner's trace on shot 1 f43, the lowest
visible row. In-air frames are placed on the whole clean silhouette instead (no thrown material there).
Writes results/PLACE3D.txt, figures/place3d-shot<N>.png. Usage: place3d.py"""
import json
import math

import cv2
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import MultipleLocator
from scipy.optimize import least_squares

import carry_lib as cl
import club3d
import diag_lib as dl
from common import AUDIT

OUT = []


def say(t=""):
    OUT.append(t)
    print(t)


fit = json.loads((AUDIT / "results" / "fit-trace.json").read_text())
SIZE = fit["translation+size, 3-D hosel"]["params"]["size"]
trace = np.asarray(json.loads((AUDIT / "owner-trace" / "shot1-f43-trace.json").read_text())["polygon_native_px"])
say("size frozen from the owner's trace on shot 1 f43: %.3f" % SIZE)


def filled(comp):
    m = comp.astype(np.uint8).copy()
    flood = m.copy()
    mask = np.zeros((m.shape[0] + 2, m.shape[1] + 2), np.uint8)
    cv2.floodFill(flood, mask, (0, 0), 2)
    return np.kron((flood != 2).astype(np.uint8), np.ones((club3d.SS, club3d.SS), np.uint8)).astype(bool)


def place_near(club, s, obs, edge, size):
    face, loft, roll = s["angles"]
    a, b = obs["line"][0], obs["line"][1]
    fy = obs["ferrule"]["y"]
    rows = np.array([fy - 16.0, fy - 6.0, fy + 5.0])
    weight = math.sqrt(len(edge) / 3.0)

    def res(p):
        _polys, axis, lead = club.parts(face, loft, roll, p[0], p[1], size)
        t = (rows - axis[0, 1]) / (axis[1, 1] - axis[0, 1])
        xm = axis[0, 0] + t * (axis[1, 0] - axis[0, 0])
        r_axis = (a + b * rows - xm) / math.hypot(1.0, b)
        r_edge = np.array([e[1] - cl.top_at(lead, e[0]) for e in edge])
        r_edge = np.where(np.isfinite(r_edge), r_edge, 5.0)
        return np.concatenate([r_axis * weight, r_edge])

    sol = least_squares(res, [0.0, 0.0], x_scale=5.0)
    r = res(sol.x)
    return sol.x, float(np.sqrt(np.mean((r[:3] / weight) ** 2))), float(np.sqrt(np.mean(r[3:] ** 2)))


def base(a, frame, x0, x1, y0, y1):
    a.imshow(frame[y0:y1, x0:x1], cmap="gray", vmin=0, vmax=160, interpolation="nearest", extent=(x0 - .5, x1 - .5, y1 - .5, y0 - .5))
    a.xaxis.set_major_locator(MultipleLocator(5))
    a.yaxis.set_major_locator(MultipleLocator(5))
    a.tick_params(labelsize=6)
    a.set_xlim(x0 - .5, x1 - .5)
    a.set_ylim(y1 - .5, y0 - .5)


def draw(a, m, vis, colour="#39ff14", label="rigid 3-D club (dashed: below the lowest visible row)"):
    b = club3d.boundary(m)
    b = np.vstack([b, b[:1]])
    a.plot(b[:, 0], np.where(b[:, 1] <= vis, b[:, 1], np.nan), color=colour, linewidth=1.6, label=label)
    a.plot(b[:, 0], np.where(b[:, 1] >= vis - .5, b[:, 1], np.nan), color=colour, linewidth=0.9, linestyle=(0, (2, 2)))


for shot in (1, 3, 4):
    c = cl.carry(shot)
    s, A, B = c["s"], c["A"], c["B"]
    fa, fb = c["frames"]
    face, loft, roll = s["angles"]
    shape = s["frames"][fb].shape
    prep = s["prep"]
    ball_bottom = prep["ball"]["y"] + prep["ball"]["diameter_px"] / 2.0
    panels = []
    for kind, f, obs in (("IN-AIR", fa, A), ("NEAR", fb, B)):
        origin = dl.origin_at(s, f)
        club = club3d.Club(s["scene"], origin, *s["sole"], shape)
        mm = float(origin[2]) / s["scene"].focal
        row_lo = obs["ferrule"]["y"] + 3.0
        target = filled(obs["comp"])
        for size in (SIZE, 1.0):
            start = {"face": face, "loft": loft, "roll": roll, "dx": 0.0, "dy": 0.0, "size": size}
            if kind == "IN-AIR":
                r, m, _axis = club3d.fit(club, target, start, "translation", row_lo)
                p = r["params"]
                how = "whole clean silhouette"
                extra = "boundary rms %.2f px, IoU %.2f" % (r["rms"], r["iou"])
            else:
                edge = c["edge"]
                (dx, dy), r_axis, r_edge = place_near(club, s, obs, edge, size)
                p = dict(start, dx=float(dx), dy=float(dy))
                how = "shaft line + top edge (%d columns)" % len(edge)
                m, _axis, _lead = club.mask(face, loft, roll, p["dx"], p["dy"], size)
                extra = "shaft residual %.2f px, top-edge residual %.2f px" % (r_axis, r_edge)
            polys, _axis, lead = club.parts(face, loft, roll, p["dx"], p["dy"], size)
            toe_obs = c["sharp_toe_x"] if kind == "NEAR" else obs["toe_x"]
            toe_gap = float(lead[:, 0].max() - toe_obs)
            bottom = float(max(q[:, 1].max() for q in polys[:-2]))
            line = "shot %d %-6s f%d size %.3f | placed by %s: shift %+5.1f, %+5.1f mm; %s | model toe - sharp toe %+.1f px = %+.1f mm | model bottom row %.1f; lowest visible %.1f (model %+.1f mm below it); ball bottom row %.1f (model %+.1f mm below it)" % (
                shot, kind, f, size, how, p["dx"], p["dy"], extra, toe_gap, toe_gap * mm, bottom, obs["bottom_y"], (bottom - obs["bottom_y"]) * mm, ball_bottom, (bottom - ball_bottom) * mm)
            if shot == 1 and kind == "NEAR":
                rt = club3d.compare(m, club3d.polygon_mask(trace, shape), 82.0)
                line += " | AGAINST THE OWNER'S TRACE (not used for this placement): boundary rms %.2f px, 95th %.2f, IoU %.2f" % (rt["rms"], rt["p95"], rt["iou"])
            say(line)
            if size == SIZE:
                panels.append((kind, f, m, obs))
    (_k0, f0, m0, o0), (_k1, f1, m1, o1) = panels
    fig, axs = plt.subplots(1, 3, figsize=(30, 8.4))
    b1 = club3d.boundary(m1)
    x0 = int(b1[:, 0].min()) - 6
    x1 = int(max(b1[:, 0].max(), prep["ball"]["x"] + prep["ball"]["diameter_px"] / 2)) + 8
    y1 = int(min(b1[:, 1].max(), o1["bottom_y"] + 18)) + 8
    y0 = y1 - int((x1 - x0) * 0.62)
    base(axs[1], s["frames"][f1], x0, x1, y0, y1)
    base(axs[2], s["frames"][f1], x0, x1, y0, y1)
    b0 = club3d.boundary(m0)
    cx, cyb = int(b0[:, 0].mean()), int(o0["bottom_y"]) + 12
    base(axs[0], s["frames"][f0], max(cx - (x1 - x0) // 2, 0), cx + (x1 - x0) // 2, max(cyb - (y1 - y0), 0), cyb)
    draw(axs[0], m0, o0["bottom_y"] + 50)
    draw(axs[2], m1, o1["bottom_y"])
    if shot == 1:
        axs[2].plot(*np.vstack([trace, trace[:1]]).T, color="#ff2020", linewidth=1.2, label="owner's trace (check only, not used)")
    e = c["edge"]
    axs[2].scatter(e[:, 0], e[:, 1], s=16, c="#ff2bd6", marker="_", linewidths=1.2, label="top edge used")
    yy = np.array([float(y0), o1["ferrule"]["y"] + 6.0])
    axs[2].plot(o1["line"][0] + o1["line"][1] * yy, yy, color="#ffe600", linewidth=0.7, linestyle=":", label="shaft line used")
    axs[2].axhline(o1["bottom_y"], color="#ffe600", linewidth=0.5, linestyle=":")
    t = prep["frame_time_ms"]
    axs[0].set_title("shot %d frame %d (%+.2f ms): clear air, model placed on the whole silhouette, size %.2f" % (shot, f0, t[f0] - prep["contact_ms"], SIZE), fontsize=10)
    axs[1].set_title("shot %d frame %d (%+.2f ms): bare pixels" % (shot, f1, t[f1] - prep["contact_ms"]), fontsize=10)
    axs[2].set_title("same frame: placed by shaft line + top edge only; angles as supplied; size %.2f" % SIZE, fontsize=10)
    axs[0].legend(fontsize=7, loc="lower left")
    axs[2].legend(fontsize=7, loc="lower left")
    fig.tight_layout()
    fig.savefig(AUDIT / "figures" / ("place3d-shot%d.png" % shot), dpi=58)
    plt.close(fig)
(AUDIT / "results" / "PLACE3D.txt").write_text("\n".join(OUT) + "\n")

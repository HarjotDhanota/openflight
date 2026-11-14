"""PROTOCOL addendum 13: the owner's placement. The model's hosel on the hosel in the image (shaft line, no length),
the head's top edge, and the toe. TS4 model, four shots through setup_free. Development run: every mark is known.
Writes results/OUTLINE-FIT.txt, results/outline-fit.json, figures/outline-fit-shot<N>.png. Usage: outline_fit.py"""
import json
import math
from dataclasses import replace
from types import SimpleNamespace

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.optimize import least_squares

import carry_lib as cl
import diag_lib as dl
import pipeline_lib as pl
import rig
import setup_free as sf
from common import AUDIT

OUT = []
RADIAL = {"speed_datum": "radial", "los_after_ms": 0.0}
SPEC = replace(rig.ts4(), name="TS4 7-iron (research), axis at 75.0", axis_ground_from_toe=75.0)
STRONG = 25.0
import os  # noqa: E402
import cv2  # noqa: E402
TOP_MODE = os.environ.get("TOP_MODE", "cavity")   # "cavity" = the first run; "rim" = PROTOCOL note 13a
TAG = {"cavity": "", "rim": "-rim", "rim2": "-rim2"}[TOP_MODE]   # "rim2" = PROTOCOL note 13b


def rim_top(frame, edge, reject_limit=False, thickness_px=None):
    """Per cavity-edge column, the rim's own upper edge: strongest brightening going down within 6 rows above it."""
    img = cv2.GaussianBlur(frame.astype(np.float32), (0, 0), 0.7)
    grad = img[1:, :] - img[:-1, :]          # value at (y + 0.5)
    tops, thick = [], []
    for x, yc, _g in edge:
        x = int(x)
        r1 = int(math.floor(yc - 0.5))        # gradient index of the cavity edge
        lo = max(r1 - 6, 1)
        seg = grad[lo:r1, x]
        if len(seg) < 3:
            tops.append(None)
            continue
        i = int(np.argmax(seg))
        if seg[i] < 3.0 or (reject_limit and i == 0):
            tops.append(None)
            continue
        sub = 0.0
        if 0 < i < len(seg) - 1:
            d = seg[i - 1] - 2 * seg[i] + seg[i + 1]
            sub = 0.5 * (seg[i - 1] - seg[i + 1]) / d if d < -1e-6 else 0.0
        y = lo + i + sub + 0.5
        tops.append(y)
        thick.append(yc - y)
    if thickness_px is not None:                     # a thickness supplied from the swing's previous frame
        return np.asarray([(e[0], e[1] - thickness_px, e[2]) for e in edge], dtype=float), float(thickness_px), len(thick)
    if not thick:
        return np.zeros((0, 3)), math.nan, 0
    med = float(np.median(thick))
    out = [(e[0], t if t is not None else e[1] - med, e[2]) for e, t in zip(edge, tops)]
    return np.asarray(out, dtype=float), med, len(thick)


def say(t=""):
    OUT.append(t)
    print(t)


def right_at(poly, y):
    """Right-most x of a closed polygon on row y (scan-line crossing)."""
    a, b = poly, np.roll(poly, -1, axis=0)
    hit = ((a[:, 1] <= y) & (b[:, 1] > y)) | ((b[:, 1] <= y) & (a[:, 1] > y))
    if not hit.any():
        return math.nan
    t = (y - a[hit, 1]) / (b[hit, 1] - a[hit, 1])
    return float((a[hit, 0] + t * (b[hit, 0] - a[hit, 0])).max())


def toe_points(s, f, obs):
    """Per row the right-most strongly changed pixel of the club (sub-pixel), the toe arc only, off the ball."""
    mag = np.abs(s["frames"][f].astype(np.float32) - s["ref"])
    ball = s["prep"]["ball"]
    pts = []
    for y in range(int(obs["ferrule"]["y"]), int(round(obs["bottom_y"]))):
        row = mag[y]
        xs = np.nonzero(row > STRONG)[0]
        xs = xs[xs > obs["x_cut"]]
        ok = [x for x in xs if np.count_nonzero(row[max(x - 6, 0):x] > STRONG) >= 3]
        if not ok:
            continue
        x = int(max(ok))
        if x + 1 >= len(row):
            continue
        sub = x + (row[x] - STRONG) / (row[x] - row[x + 1]) if row[x] > row[x + 1] else x + 0.5
        if math.hypot(sub - ball["x"], y - ball["y"]) <= ball["diameter_px"] / 2.0 + 2.0:
            continue
        pts.append((float(sub), float(y)))
    pts = np.asarray(pts).reshape(-1, 2)
    if len(pts) < 4:
        return pts, math.nan
    far = float(np.max(pts[:, 0]))
    return pts[pts[:, 0] >= far - 12.0], far


def fit(sh, club, f, obs, depth, edge, toe, angles0, use_toe, free_face):
    base = pl.on_ray(dl.origin_at(sh.s, f), depth)
    a, b = obs["line"][0], obs["line"][1]
    fy = obs["ferrule"]["y"]
    rows = np.array([fy - 16.0, fy - 6.0, fy + 5.0])
    w = math.sqrt(max(len(edge), 1) / 3.0)

    def parts(p):
        ang = (angles0[0] + (p[2] if free_face else 0.0), angles0[1], angles0[2])
        origin = base + np.array([p[0], p[1], 0.0])
        return ang, origin, club.parts(ang, origin)

    def res(p):
        _ang, _o, (_polys, axis, lead) = parts(p)
        tt = (rows - axis[0, 1]) / (axis[1, 1] - axis[0, 1])
        xm = axis[0, 0] + tt * (axis[1, 0] - axis[0, 0])
        r = [(a + b * rows - xm) / math.hypot(1.0, b) * w]
        re = np.array([e[1] - rig.top_at(lead, e[0]) for e in edge])
        r.append(np.where(np.isfinite(re), re, 5.0))
        if use_toe:
            rt = np.array([t[0] - right_at(lead, t[1]) for t in toe])
            r.append(np.where(np.isfinite(rt), rt, 5.0))
        return np.concatenate(r)

    best = None
    for seed in ((0.0, 10.0, 20.0, -10.0) if free_face else (0.0,)):
        x0 = [0.0, 0.0, seed] if free_face else [0.0, 0.0]
        sol = least_squares(res, x0, x_scale=[5.0, 5.0, 8.0][:len(x0)])
        if best is None or sol.cost < best.cost:
            best = sol
    ang, origin, _p = parts(best.x)
    r = res(best.x)
    n_e = len(edge)
    return origin, ang, {"shaft_px": float(np.sqrt(np.mean((r[:3] / w) ** 2))), "top_px": float(np.sqrt(np.mean(r[3:3 + n_e] ** 2))) if n_e else math.nan,
                         "toe_px": float(np.sqrt(np.mean(r[3 + n_e:] ** 2))) if use_toe and len(toe) else math.nan}


marks = json.loads((AUDIT / "results" / "photo-marks-1234.json").read_text())
record = {}
for n in (1, 2, 3, 4):
    s = sf.build(n)
    B, C = sf.observations(s)
    base_shot = pl.Shot(n, track_kwargs=RADIAL, shared=SimpleNamespace(s=s, B=B, edge_r=np.zeros((0, 3)), C=C))
    t_mid = base_shot.track["fits"]["pixels + radar"]["t_mid"]
    m = marks[str(n)]
    mark = (0.5 * (m["lines"][0] + m["outline"][0]), 0.5 * (m["lines"][1] + m["outline"][1]))
    f = base_shot.fr
    frame = s["frames"][f]
    toe, far = toe_points(s, f, B)
    edge = np.zeros((0, 3))
    if np.isfinite(far):
        cols = np.arange(int(far) - 26, int(far) - 3)
        edge = cl.cavity_top(frame, cols, int(B["ferrule"]["y"]) - 14, int(round(B["bottom_y"])))
    say("SHOT %d   mark %.1f / %.1f | rest frame %d: toe points %d (furthest x %.1f), top-edge points %d, lowest visible row %.1f" % (n, *mark, f, len(toe), far, len(edge), B["bottom_y"]))
    mm_per_px = float(dl.origin_at(s, f)[2]) / s["scene"].focal
    if TOP_MODE == "rim" and len(edge):
        edge, rim_px, rim_n = rim_top(frame, edge)
        say("   rim: its own upper edge found on %d of the columns; median thickness %.2f px = %.1f mm at this depth" % (rim_n, rim_px, rim_px * mm_per_px))
    if TOP_MODE == "rim2" and len(edge):
        cavity = edge
        edge, rim_px, rim_n = rim_top(frame, cavity, reject_limit=True)
        say("   rim: its own upper edge found on %d of %d columns; median thickness %s" % (rim_n, len(cavity), "%.2f px = %.1f mm" % (rim_px, rim_px * mm_per_px) if rim_n else "-"))
        if rim_n < 6:
            edge = np.zeros((0, 3))
            try:
                P = dl.observe_frame(s, f - 1)
                toe_p, far_p = toe_points(s, f - 1, P)
                cav_p = cl.cavity_top(s["frames"][f - 1], np.arange(int(far_p) - 26, int(far_p) - 3), int(P["ferrule"]["y"]) - 14, int(round(P["bottom_y"])))
                _e, rim_p, n_p = rim_top(s["frames"][f - 1], cav_p, reject_limit=True)
                mm_prev = float(dl.origin_at(s, f - 1)[2]) / s["scene"].focal
                say("        fewer than 6: previous frame %d gives %d columns, %.2f px = %.1f mm" % (f - 1, n_p, rim_p, rim_p * mm_prev))
                if n_p >= 6:
                    edge, rim_px, _n = rim_top(frame, cavity, reject_limit=True, thickness_px=rim_p * mm_prev / mm_per_px)
            except Exception as exc:
                say("        fewer than 6, and the previous frame could not be measured (%s)" % type(exc).__name__)
    record[str(n)] = {"mark": list(mark), "variants": {}}
    if len(edge) < 4 or len(toe) < 4:
        say("   too few edge points: not placed")
        continue
    angles0 = base_shot.angles
    club = rig.Club(s["scene"], SPEC, base_shot.shape)
    fig, axs = plt.subplots(1, 3, figsize=(27, 8))
    VARIANTS = (("V1 hosel + top", False, False), ("V2 hosel + top + toe", True, False), ("V3 hosel + top + toe, turn free", True, True))
    for (label, use_toe, free_face), ax in zip(VARIANTS[:2] if TOP_MODE == "rim2" else VARIANTS, axs):
        z = float(dl.origin_at(s, f)[2])
        sol = None
        for _ in range(3):
            origin, ang, q = fit(base_shot, club, f, B, z, edge, toe, angles0, use_toe, free_face)
            sh = pl.Shot(n, angles=ang, track_kwargs=RADIAL, shared=SimpleNamespace(s=s, B=B, edge_r=np.zeros((0, 3)), C=C))
            sol = sh.solve(SPEC, t_mid, "ferrule" if TOP_MODE == "rim2" else "edge", True, trace_anchor=origin)
            z = float(sol["rest"][2])
        rd = sol["reading"]
        key = label.split()[0] + ("a" if free_face else "")
        say("   %-34s face %+5.1f (supplied %+.1f) | fit rms px: shaft %.2f, top %.2f, toe %s | reading %.1f / %.1f (miss %+.1f / %+.1f)" % (
            label, ang[0], angles0[0], q["shaft_px"], q["top_px"], "%.2f" % q["toe_px"] if np.isfinite(q["toe_px"]) else " -  ", rd["from_toe"], rd["above_le"], rd["from_toe"] - mark[0], rd["above_le"] - mark[1]))
        record[str(n)]["variants"][key] = {"face": ang[0], "reading": [rd["from_toe"], rd["above_le"]], "fit": q}
        if free_face:
            # V3b: the supplied normal's contact point, read where it appears on the placed (turned) model
            toe0, up0, n0 = pl.geom.axes(*angles0)
            P = s["scene"].ball - pl.geom.BALL_RADIUS_MM * n0
            d = P / np.linalg.norm(P)
            c = sol["contact"]
            X = d * float(c @ sh.normal) / float(d @ sh.normal)
            u, v = float((X - c) @ sh.toe), float((X - c) @ sh.up)
            ft, al = SPEC.toe_u - u, v - SPEC.le_v
            say("   %-34s contact point by the supplied face normal, read on that placement    | reading %.1f / %.1f (miss %+.1f / %+.1f)" % ("V3b", ft, al, ft - mark[0], al - mark[1]))
            record[str(n)]["variants"]["V3b"] = {"reading": [ft, al]}
        # picture: the rest frame with the observations and this variant's outline
        mk, _axis, _lead = sol["club"].mask(sh.angles, sol["rest"])
        bd = rig.boundary(mk)
        bx = s["prep"]["ball"]
        x0, x1 = int(bd[:, 0].min()) - 10, int(max(bd[:, 0].max(), bx["x"] + bx["diameter_px"] / 2)) + 12
        y1 = int(min(bd[:, 1].max(), base_shot.shape[0] - 2)) + 6
        y0 = max(y1 - int((x1 - x0) * 0.66), 0)
        ax.imshow(frame[y0:y1, x0:x1], cmap="gray", vmin=0, vmax=160, interpolation="nearest", extent=(x0 - .5, x1 - .5, y1 - .5, y0 - .5))
        bd = np.vstack([bd, bd[:1]])
        ax.plot(bd[:, 0], bd[:, 1], color="#39ff14", linewidth=1.2)
        ax.scatter(edge[:, 0], edge[:, 1], s=22, c="#ff2bd6", marker="_", linewidths=1.6, label="top-edge points")
        ax.scatter(toe[:, 0], toe[:, 1], s=22, c="#00e5ff", marker="|", linewidths=1.6, label="toe points")
        yy = np.array([B["ferrule"]["y"] - 30, B["ferrule"]["y"] + 8])
        ax.plot(B["line"][0] + B["line"][1] * yy, yy, color="#ffe600", linewidth=0.9, linestyle="--", label="observed shaft line")
        ax.add_patch(plt.Circle((bx["x"], bx["y"]), bx["diameter_px"] / 2, fill=False, color="w", linewidth=0.7, linestyle=":"))
        ax.set_xlim(x0 - .5, x1 - .5)
        ax.set_ylim(y1 - .5, y0 - .5)
        ax.set_title("shot %d f%d: %s  (miss %+.1f / %+.1f mm)" % (n, f, label, rd["from_toe"] - mark[0], rd["above_le"] - mark[1]), fontsize=10)
        ax.legend(fontsize=8, loc="upper right")
        ax.tick_params(labelsize=6)
    fig.tight_layout()
    fig.savefig(AUDIT / "figures" / ("outline-fit%s-shot%d.png" % (TAG, n)), dpi=56)
    plt.close(fig)
    say()

say("RULE (written before running): one variant, the same for all four, within 4 mm heel-toe and 4 mm in height on every shot.")
for key in (("V1", "V2") if TOP_MODE == "rim2" else ("V1", "V2", "V3a", "V3b")):
    rows = []
    for n in (1, 2, 3, 4):
        v = record[str(n)]["variants"].get(key)
        if v is None:
            rows.append(None)
            continue
        mk = record[str(n)]["mark"]
        rows.append((v["reading"][0] - mk[0], v["reading"][1] - mk[1]))
    ok = all(r is not None and abs(r[0]) <= 4.0 and abs(r[1]) <= 4.0 for r in rows)
    say("   %-4s misses (heel-toe / height): %s -> %s" % (key, "   ".join("not placed" if r is None else "%+.1f / %+.1f" % r for r in rows), "PASS" if ok else "FAIL"))
(AUDIT / "results" / ("OUTLINE-FIT%s.txt" % TAG)).write_text("\n".join(OUT) + "\n")
(AUDIT / "results" / ("outline-fit%s.json" % TAG)).write_text(json.dumps(record, indent=1, default=float))

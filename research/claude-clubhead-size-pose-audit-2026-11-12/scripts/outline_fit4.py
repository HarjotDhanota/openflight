"""PROTOCOL note 13e (sixth pass: the cavity edge on every top column, one rim thickness per swing from the rest
frame and the frame before it). Built on the fifth pass, PROTOCOL note 13d (outline_fit3.py, kept): toe rows by the cavity's own right-hand rise,
horizontal toe residuals, bounded width. Otherwise as note 13c:
Hosel line (no length) + top edge + toe; the head's outline is the cavity's boundary moved out by the rim thickness
measured on the same frame. TS4 model, four shots through setup_free. Fourth pass over four known marks.
Writes results/OUTLINE-FIT4.txt, results/outline-fit4.json, figures/outline-fit4-shot<N>.png. Usage: outline_fit4.py"""
import json
import math
from dataclasses import replace
from types import SimpleNamespace

import cv2
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.path import Path
from scipy.optimize import least_squares

import carry_lib as cl
import diag_lib as dl
import pipeline_lib as pl
import rig
import setup_free as sf
import sil
from common import AUDIT

OUT = []
RADIAL = {"speed_datum": "radial", "los_after_ms": 0.0}
SPEC = replace(rig.ts4(), name="TS4 7-iron (research), axis at 75.0", axis_ground_from_toe=75.0)
STRONG = 25.0
import os  # noqa: E402
import re  # noqa: E402
ANGLES_MODE = os.environ.get("ANGLES_MODE", "application")   # "measured" = PROTOCOL note 13f
TAG = "" if ANGLES_MODE == "application" else "-measured"


def measured_angles(n, roll):
    """Addendum 9's row C for shots 1, 3, 4; the blind prediction's secondary orientation for shot 2."""
    if n == 2:
        text = (AUDIT / "results" / "HELDOUT-SHOT2-PREDICTION.txt").read_text()
        m = re.search(r"SECONDARY.*face ([+-][\d.]+), loft ([\d.]+)", text)
        return (float(m.group(1)), float(m.group(2)), roll)
    rows = json.loads((AUDIT / "results" / "dplane-rerun.json").read_text())[str(n)]["rows"]
    return tuple(rows["C"]["angles"])


SLOPE_FACTOR = 0.94      # vertical rim thickness to thickness across the rim, for a top line rising about 20 deg


def say(t=""):
    OUT.append(t)
    print(t)


def crude_far(s, f, obs):
    """The right-most strongly changed pixel of the club off the ball: only a seed for where the toe end is."""
    mag = np.abs(s["frames"][f].astype(np.float32) - s["ref"])
    ball = s["prep"]["ball"]
    best = math.nan
    for y in range(int(obs["ferrule"]["y"]), int(round(obs["bottom_y"]))):
        row = mag[y]
        xs = np.nonzero(row > STRONG)[0]
        xs = xs[xs > obs["x_cut"]]
        ok = [x for x in xs if np.count_nonzero(row[max(x - 6, 0):x] > STRONG) >= 3 and math.hypot(x - ball["x"], y - ball["y"]) > ball["diameter_px"] / 2.0 + 2.0]
        if ok and not (max(ok) <= best):
            best = float(max(ok))
    return best


def observe_outline(s, f, obs):
    """Top columns (rim top where seen, else cavity edge), toe rows (cavity's right edge), and the rim thickness."""
    frame = s["frames"][f]
    img = cv2.GaussianBlur(frame.astype(np.float32), (0, 0), 0.7)
    far = crude_far(s, f, obs)
    if not np.isfinite(far):
        return None, "no toe seed"
    cav = cl.cavity_top(frame, np.arange(int(far) - 26, int(far) - 3), int(obs["ferrule"]["y"]) - 14, int(round(obs["bottom_y"])))
    if len(cav) < 4:
        return None, "fewer than 4 top-edge columns"
    gy = img[1:, :] - img[:-1, :]
    gx = img[:, 1:] - img[:, :-1]
    # rim's own upper edge above each cavity-edge column
    tops, t_vals = [], []
    for x, yc, _g in cav:
        x = int(x)
        r1 = int(math.floor(yc - 0.5))
        lo = max(r1 - 6, 1)
        seg = gy[lo:r1, x]
        top = None
        if len(seg) >= 3:
            i = int(np.argmax(seg))
            if seg[i] >= 3.0 and i > 0:
                sub = 0.0
                if i < len(seg) - 1:
                    d = seg[i - 1] - 2 * seg[i] + seg[i + 1]
                    sub = 0.5 * (seg[i - 1] - seg[i + 1]) / d if d < -1e-6 else 0.0
                top = lo + i + sub + 0.5
                t_vals.append((yc - top) * SLOPE_FACTOR)
        tops.append(top)
    n_vert = len(t_vals)
    # the cavity: dark pixels connected to the points just under its upper edge
    seeds = [(int(x), int(round(yc)) + 2) for x, yc, _g in cav]
    level = float(np.median([img[y, x] for x, y in seeds])) + 12.0
    x_lo, x_hi = int(obs["x_cut"]), min(int(far) + 12, img.shape[1] - 2)
    y_lo, y_hi = max(int(cav[:, 1].min()) - 3, 1), int(round(obs["bottom_y"]))
    dark = np.zeros(img.shape, dtype=np.uint8)
    dark[y_lo:y_hi + 1, x_lo:x_hi + 1] = (img[y_lo:y_hi + 1, x_lo:x_hi + 1] < level)
    _n, lab = cv2.connectedComponents(dark, connectivity=8)
    ids = {int(lab[y, x]) for x, y in seeds if lab[y, x] > 0}
    if not ids:
        return None, "no dark cavity under the top-edge points"
    comp = np.isin(lab, list(ids))
    ys, xs = np.nonzero(comp)
    if xs.max() - xs.min() > 70:
        return None, "the dark region is %d px wide: not the cavity alone" % (xs.max() - xs.min())
    rights = {}
    x_start = int(cav[:, 0].max()) - 2                      # inside the cavity, under the last top columns
    for y in np.unique(ys):
        if not comp[y, max(x_start - 3, 0):x_start + 4].any():
            continue                                        # this row of the cavity does not reach the toe end
        seg = gx[y, x_start:min(x_start + 27, gx.shape[1])]
        rise = np.nonzero(seg >= 4.0)[0]
        if len(rise) == 0:
            continue                                        # no rim on this row: it runs out into a dark background
        i = int(rise[0])
        while i + 1 < len(seg) and seg[i + 1] > seg[i]:
            i += 1                                          # the steepest point of that first rise
        sub = 0.0
        if 0 < i < len(seg) - 1:
            d = seg[i - 1] - 2 * seg[i] + seg[i + 1]
            sub = 0.5 * (seg[i - 1] - seg[i + 1]) / d if d < -1e-6 else 0.0
        rights[int(y)] = float(x_start + i + sub + 0.5)
    if rights:
        med = float(np.median(list(rights.values())))
        rights = {y: x for y, x in rights.items() if abs(x - med) <= 4.0}
    furthest = max(rights.values()) if rights else math.nan
    toe_rows = sorted(rights)
    toe = []
    n_horiz = 0
    for y in toe_rows:
        xc = rights[y]
        x0 = int(math.ceil(xc))
        seg = gx[y, x0:x0 + 6]
        if len(seg) >= 3:
            i = int(np.argmin(seg))
            if seg[i] <= -3.0 and i < len(seg) - 1:
                t_vals.append((x0 + i + 0.5) - xc)
                n_horiz += 1
        toe.append((xc, float(y)))
    t = float(np.median(t_vals)) if len(t_vals) >= 6 else math.nan
    return {"t_vals": list(t_vals), "cav": cav, "tops": tops, "toe": np.asarray(toe).reshape(-1, 2), "t": t, "n_vert": n_vert, "n_horiz": n_horiz, "level": level, "far": far, "furthest": furthest, "comp": comp}, None


def obs_light(s, f):
    """Shaft line, ferrule and the head's rows without the head silhouette (used when dl.observe_frame fails)."""
    frame = s["frames"][f]
    diff, mask = sil.changed(frame, s["ref"], s["sigma"], 8.0)
    comp = sil.head_component(mask)
    ax = sil.shaft_axis(diff, comp)
    line = ax["line"]
    fer = sil.ferrule(frame, line, ax["ridge"][:, 1].min(), ax["head_bottom"] - 4)
    y_mid = 0.5 * (ax["head_top"] + ax["head_bottom"])
    return {"line": line, "ferrule": fer, "x_cut": line[0] + line[1] * y_mid + 6.0, "bottom_y": ax["head_bottom"] + 0.5}


def inside_distance(poly, pts):
    a, b = poly, np.roll(poly, -1, axis=0)
    ab = b - a
    l2 = np.maximum((ab ** 2).sum(axis=1), 1e-12)
    d = pts[:, None, :] - a[None, :, :]
    tt = np.clip((d * ab[None]).sum(axis=2) / l2, 0.0, 1.0)
    proj = a[None] + tt[..., None] * ab[None]
    dist = np.linalg.norm(pts[:, None, :] - proj, axis=2).min(axis=1)
    return np.where(Path(poly).contains_points(pts), dist, -dist)


def scaled(k):
    u_g = SPEC.toe_u - SPEC.axis_ground_from_toe
    blade = SPEC.blade.copy()
    blade[:, 0] = u_g + k * (blade[:, 0] - u_g)
    return replace(SPEC, blade=blade, toe_u=u_g + k * (SPEC.toe_u - u_g), axis_ground_from_toe=k * SPEC.axis_ground_from_toe)


def top_at(poly, x):
    return rig.top_at(poly, x)


def right_at(poly, y):
    a, b = poly, np.roll(poly, -1, axis=0)
    hit = ((a[:, 1] <= y) & (b[:, 1] > y)) | ((b[:, 1] <= y) & (a[:, 1] > y))
    if not hit.any():
        return math.nan
    tt = (y - a[hit, 1]) / (b[hit, 1] - a[hit, 1])
    return float((a[hit, 0] + tt * (b[hit, 0] - a[hit, 0])).max())


def fit(sh, f, obs, depth, pts, inset, free_k, n_top=None):
    base = pl.on_ray(dl.origin_at(sh.s, f), depth)
    a, b = obs["line"][0], obs["line"][1]
    fy = obs["ferrule"]["y"]
    rows = np.array([fy - 16.0, fy - 6.0, fy + 5.0])
    w = math.sqrt(max(len(pts), 1) / 3.0)

    def res(p):
        spec = scaled(p[2]) if free_k else SPEC
        club = rig.Club(sh.scene, spec, sh.shape)
        _polys, axis, lead = club.parts(sh.angles, base + np.array([p[0], p[1], 0.0]))
        tt = (rows - axis[0, 1]) / (axis[1, 1] - axis[0, 1])
        xm = axis[0, 0] + tt * (axis[1, 0] - axis[0, 0])
        r_top = np.array([(p[1] - i) - top_at(lead, p[0]) for p, i in zip(pts[:n_top], inset[:n_top])])
        r_toe = np.array([(p[0] + i) - right_at(lead, p[1]) for p, i in zip(pts[n_top:], inset[n_top:])])
        return np.concatenate([(a + b * rows - xm) / math.hypot(1.0, b) * w, np.where(np.isfinite(r_top), r_top, 5.0), np.where(np.isfinite(r_toe), r_toe, 5.0)])

    if free_k:
        sol = least_squares(res, [0.0, 0.0, 1.0], x_scale=[5.0, 5.0, 0.05], bounds=([-60.0, -60.0, 0.85], [60.0, 60.0, 1.15]))
    else:
        sol = least_squares(res, [0.0, 0.0], x_scale=[5.0, 5.0])
    r = res(sol.x)
    k = float(sol.x[2]) if free_k else 1.0
    return base + np.array([sol.x[0], sol.x[1], 0.0]), k, float(np.sqrt(np.mean((r[:3] / w) ** 2))), r[3:]


marks = json.loads((AUDIT / "results" / "photo-marks-1234.json").read_text())
record = {}
for n in (1, 2, 3, 4):
    s = sf.build(n)
    B, C = sf.observations(s)
    ang = measured_angles(n, s["angles"][2]) if ANGLES_MODE == "measured" else None
    sh = pl.Shot(n, angles=ang, track_kwargs=RADIAL, shared=SimpleNamespace(s=s, B=B, edge_r=np.zeros((0, 3)), C=C))
    t_mid = sh.track["fits"]["pixels + radar"]["t_mid"]
    if ang is not None:
        say("shot %d angles: face %+.2f, loft %.2f, roll %+.2f (application's: %+.2f, %.2f, %+.2f)" % (n, *sh.angles, *s["angles"]))
    m = marks[str(n)]
    mark = (0.5 * (m["lines"][0] + m["outline"][0]), 0.5 * (m["lines"][1] + m["outline"][1]))
    f = sh.fr
    record[str(n)] = {"mark": list(mark), "variants": {}}
    o, why = observe_outline(s, f, B)
    if o is None:
        say("SHOT %d   mark %.1f / %.1f | rest frame %d: NOT PLACED: %s" % (n, *mark, f, why))
        say()
        continue
    mm_per_px = float(dl.origin_at(s, f)[2]) / s["scene"].focal
    say("SHOT %d   mark %.1f / %.1f | rest frame %d: %d top columns (rim's own edge on %d), %d toe rows (rim's outer edge on %d); cavity level %.0f DN, its toe end x %.1f" % (
        n, *mark, f, len(o["cav"]), o["n_vert"], len(o["toe"]), o["n_horiz"], o["level"], o["furthest"]))
    vals_mm = [v * mm_per_px for v in o["t_vals"]]
    say("      rest frame %d rim values, mm across the rim: %s" % (f, " ".join("%.1f" % v for v in vals_mm) or "none"))
    prev_note = "not observed"
    try:
        try:
            P = dl.observe_frame(s, f - 1)
            how = "dl.observe_frame"
        except Exception:
            P = obs_light(s, f - 1)
            how = "shaft line + ferrule"
        op, why_p = observe_outline(s, f - 1, P)
        if op is None:
            prev_note = "no outline (%s)" % why_p
        else:
            mm_prev = float(dl.origin_at(s, f - 1)[2]) / s["scene"].focal
            prev = [v * mm_prev for v in op["t_vals"]]
            vals_mm += prev
            prev_note = "%s; %d values: %s" % (how, len(prev), " ".join("%.1f" % v for v in prev))
    except Exception as exc:
        prev_note = "failed (%s)" % type(exc).__name__
    say("      frame %d before it: %s" % (f - 1, prev_note))
    if len(vals_mm) < 6:
        say("   rim thickness: only %d values on the two frames (6 needed) -> NOT PLACED" % len(vals_mm))
        say()
        continue
    t = float(np.median(vals_mm)) / mm_per_px
    say("   rim thickness for this swing: %.1f mm across the rim (median of %d values) = %.2f px on the rest frame" % (t * mm_per_px, len(vals_mm), t))
    use_toe = len(o["toe"]) >= 4
    if not use_toe:
        say("   fewer than 4 toe rows with a rim: the toe is not used on this shot (hosel + top only)")
    top_pts = np.array([(c[0], c[1]) for c in o["cav"]])              # the cavity's upper edge on every column
    top_inset = np.full(len(top_pts), t / SLOPE_FACTOR)
    toe_pts = o["toe"] if use_toe else np.zeros((0, 2))
    pts = np.vstack([top_pts, toe_pts])
    inset = np.concatenate([top_inset, np.full(len(toe_pts), t)])
    fig, axs = plt.subplots(1, 3, figsize=(30, 8.4))
    all_pts, all_inset = pts, inset
    for (label, free_k, with_toe), ax in zip((("W0 hosel + top", False, False), ("W1 hosel + top + toe", False, True), ("W2 hosel + top + toe, width free", True, True)), axs):
        with_toe = with_toe and use_toe
        pts, inset = (all_pts, all_inset) if with_toe else (top_pts, top_inset)
        z = float(dl.origin_at(s, f)[2])
        for _ in range(3):
            origin, k, shaft_rms, r_pts = fit(sh, f, B, z, pts, inset, free_k and with_toe, n_top=len(top_pts))
            spec = scaled(k)
            sol = sh.solve(spec, t_mid, "ferrule", True, trace_anchor=origin)
            z = float(sol["rest"][2])
        rd = sol["reading"]
        ft, al = rd["from_toe"] / k, rd["above_le"]
        implied = sh.ferrule_length(sol["club"], sol["rest"], B)
        n_top = len(top_pts)
        say("   %-34s width %.3f | fit rms px: shaft %.2f, top %.2f, toe %.2f | ferrule distance this placement implies %.1f mm | reading %.1f / %.1f (miss %+.1f / %+.1f)" % (
            label, k, shaft_rms, float(np.sqrt(np.mean(r_pts[:n_top] ** 2))), float(np.sqrt(np.mean(r_pts[n_top:] ** 2))) if with_toe else float("nan"), implied, ft, al, ft - mark[0], al - mark[1]))
        record[str(n)]["variants"][label.split()[0]] = {"k": k, "reading": [ft, al], "implied_ferrule_mm": implied, "rim_px": t}
        mk, _axis, _lead = sol["club"].mask(sh.angles, sol["rest"])
        bd = rig.boundary(mk)
        bx = s["prep"]["ball"]
        x0, x1 = int(bd[:, 0].min()) - 8, int(max(bd[:, 0].max(), bx["x"] + bx["diameter_px"] / 2)) + 10
        y1 = int(min(bd[:, 1].max(), sh.shape[0] - 2)) + 6
        y0 = max(y1 - int((x1 - x0) * 0.72), 0)
        ax.imshow(s["frames"][f][y0:y1, x0:x1], cmap="gray", vmin=0, vmax=160, interpolation="nearest", extent=(x0 - .5, x1 - .5, y1 - .5, y0 - .5))
        bd = np.vstack([bd, bd[:1]])
        ax.plot(bd[:, 0], bd[:, 1], color="#39ff14", linewidth=1.3, label="model")
        ax.scatter(top_pts[:, 0], top_pts[:, 1], s=30, c="#ff8c00", marker="_", linewidths=1.8, label="cavity's upper edge")
        ax.scatter(top_pts[:, 0], top_pts[:, 1] - top_inset, s=30, c="#ff2bd6", marker="_", linewidths=1.8, label="head's top = that edge + rim thickness")
        if len(o["toe"]):
            ax.scatter(o["toe"][:, 0], o["toe"][:, 1], s=30, c="#00e5ff", marker="|", linewidths=1.8, label="cavity's toe edge" + ("" if use_toe else " (too few: not used)"))
        yy = np.array([B["ferrule"]["y"] - 30, B["ferrule"]["y"] + 8])
        ax.plot(B["line"][0] + B["line"][1] * yy, yy, color="#ffe600", linewidth=1.0, linestyle="--", label="hosel line")
        ax.add_patch(plt.Circle((bx["x"], bx["y"]), bx["diameter_px"] / 2, fill=False, color="w", linewidth=0.7, linestyle=":"))
        ax.set_xlim(x0 - .5, x1 - .5)
        ax.set_ylim(y1 - .5, y0 - .5)
        ax.set_title("shot %d, frame before impact: %s  (miss %+.1f / %+.1f mm)" % (n, label, ft - mark[0], al - mark[1]), fontsize=11)
        ax.legend(fontsize=8, loc="upper right")
        ax.tick_params(labelsize=6)
    fig.tight_layout()
    fig.savefig(AUDIT / "figures" / ("outline-fit4%s-shot%d.png" % (TAG, n)), dpi=60)
    plt.close(fig)
    say()

say("RULE (written before running): W0, W1 or W2, the same for all four, within 4 mm heel-toe and 4 mm in height on every shot.")
for key in ("W0", "W1", "W2"):
    rows = []
    for n in (1, 2, 3, 4):
        v = record[str(n)]["variants"].get(key)
        mk = record[str(n)]["mark"]
        rows.append(None if v is None else (v["reading"][0] - mk[0], v["reading"][1] - mk[1]))
    ok = all(r is not None and abs(r[0]) <= 4.0 and abs(r[1]) <= 4.0 for r in rows)
    imp = [record[str(n)]["variants"][key]["implied_ferrule_mm"] for n in (1, 2, 3, 4) if key in record[str(n)]["variants"]]
    say("   %-3s misses (heel-toe / height): %s -> %s | implied ferrule distance: %s mm" % (key, "   ".join("not placed" if r is None else "%+.1f / %+.1f" % r for r in rows), "PASS" if ok else "FAIL", " ".join("%.1f" % v for v in imp)))
(AUDIT / "results" / ("OUTLINE-FIT4%s.txt" % TAG)).write_text("\n".join(OUT) + "\n")
(AUDIT / "results" / ("outline-fit4%s.json" % TAG)).write_text(json.dumps(record, indent=1, default=float))

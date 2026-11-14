"""The functions of outline_fit4.py (PROTOCOL addendum 13, pass 6) as a library: the toe and top-edge finder, the
rim thickness, the fit. Copied unchanged by slicing that script above its main loop, so that system_run.py can use
them without running the four-shot loop. Nothing here reads a face photo or a mark."""
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



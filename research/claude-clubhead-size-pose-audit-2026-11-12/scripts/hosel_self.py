"""PROTOCOL addendum 11: the hosel length (shaft-axis ground point to ferrule) measured from each swing's own
clear-air frames, along the shaft axis only, and the readings that follow. All four shots through setup_free.
Stability test only: every mark is known. Writes results/HOSEL-SELF.txt and results/hosel-self.json.
Usage: PHOTO-free; hosel_self.py"""
import json
import re
from dataclasses import replace
from types import SimpleNamespace

import numpy as np

import diag_lib as dl
import pipeline_lib as pl
import rig
import setup_free as sf
import sil
from common import AUDIT

OUT = []
H0 = 61.0
RADIAL = {"speed_datum": "radial", "los_after_ms": 0.0}
SPEC = replace(rig.ts4(), name="TS4 7-iron (research), axis at 75.0", axis_ground_from_toe=75.0)
BAND = (-2, 6)          # px toe-ward of the shaft axis
WINDOW_MS = (-13.0, -3.0)


def say(t=""):
    OUT.append(t)
    print(t)


def ferrule_subpixel(frame, line, y_from, y_to):
    a, b = line[0], line[1]
    ys = np.arange(int(y_from), int(y_to) + 1)
    if len(ys) < 12:
        return None
    prof = []
    for y in ys:
        x = a + b * y
        xs = np.clip(np.round(np.array([x - 1.5, x - 0.5, x + 0.5, x + 1.5])).astype(int), 0, frame.shape[1] - 1)
        prof.append(float(frame[y, xs].max()))
    prof = np.asarray(prof)
    level = 0.5 * float(np.median(prof[: max(6, len(prof) // 2)]))
    dark = prof < level
    start = None
    for i in range(4, len(prof)):
        if dark[i] and start is None:
            start = i
        elif start is not None and not dark[i]:
            top = ys[start - 1] + (prof[start - 1] - level) / (prof[start - 1] - prof[start])
            bot = ys[i - 1] + (level - prof[i - 1]) / (prof[i] - prof[i - 1])
            y = 0.5 * (top + bot)
            return {"x": float(a + b * y), "y": float(y)}
    return None


def observe(s, f):
    """Shaft line, sub-pixel ferrule, and the club's lowest changed point in the band beside the axis."""
    frame = s["frames"][f]
    diff, mask = sil.changed(frame, s["ref"], s["sigma"], 8.0)
    comp = sil.head_component(mask)
    if comp is None:
        return None
    ax = sil.shaft_axis(diff, comp)
    if ax is None or ax["line"] is None or len(ax["ridge"]) < 6:
        return None
    line = ax["line"]
    fer = ferrule_subpixel(frame, line, ax["ridge"][:, 1].min(), ax["head_bottom"] - 4)
    if fer is None:
        return None
    mag = np.abs(diff)
    thr = np.maximum(4.0 * s["sigma"], 8.0)
    rows = np.arange(int(fer["y"]) + 6, min(int(fer["y"]) + 90, frame.shape[0] - 1))
    prof = []
    for y in rows:
        xc = line[0] + line[1] * y
        xs = np.clip(np.arange(int(round(xc)) + BAND[0], int(round(xc)) + BAND[1] + 1), 0, frame.shape[1] - 1)
        prof.append(float(np.max(mag[y, xs] - thr[y, xs])))       # > 0 where the club is
    prof = np.asarray(prof)
    on = prof > 0
    if not on[:8].any():
        return None
    # walk down from the hosel; the exit is the last "on" row before 4 rows of "off"
    last = None
    for i in range(len(rows)):
        if on[i]:
            last = i
        elif last is not None and i - last >= 4:
            break
    if last is None or last + 1 >= len(rows):
        return None
    p0, p1 = prof[last], prof[last + 1]
    exit_y = rows[last] + (p0 / (p0 - p1) if p0 > p1 else 0.5)
    return {"line": line, "ferrule": fer, "exit_y": float(exit_y)}


def model_exit(club, angles, origin, axis_px):
    m, _a, _l = club.mask(angles, origin)
    ss = rig.SS
    a0, a1 = axis_px
    slope = (a1[0] - a0[0]) / (a1[1] - a0[1])
    lowest = None
    for r in range(m.shape[0]):
        y = (r + 0.5) / ss - 0.5
        xc = a0[0] + slope * (y - a0[1])
        c0, c1 = int(round((xc + BAND[0] + 0.5) * ss)), int(round((xc + BAND[1] + 1.0 + 0.5) * ss))
        if c1 <= 0 or c0 >= m.shape[1]:
            continue
        if m[r, max(c0, 0):min(c1, m.shape[1])].any():
            lowest = y
    return None if lowest is None else lowest + 0.5 / ss


marks = json.loads((AUDIT / "results" / "photo-marks-1234.json").read_text())
record = {}
for n in (1, 2, 3, 4):
    s = sf.build(n)
    B, C = sf.observations(s)
    sh = pl.Shot(n, track_kwargs=RADIAL, shared=SimpleNamespace(s=s, B=B, edge_r=np.zeros((0, 3)), C=C))
    t_mid = sh.track["fits"]["pixels + radar"]["t_mid"]
    m = marks[str(n)]
    mark = (0.5 * (m["lines"][0] + m["outline"][0]), 0.5 * (m["lines"][1] + m["outline"][1]))

    def reading(h):
        sols = [sh.solve(SPEC, t_mid, "ferrule", slowed, None, h) for slowed in (True, False)]
        return float(np.mean([r["reading"]["from_toe"] for r in sols])), float(np.mean([r["reading"]["above_le"] for r in sols])), sols[0]

    ft0, al0, sol0 = reading(H0)
    club = sol0["club"]
    say("SHOT %d   mark %.1f / %.1f; with the fixed %.1f mm: %.1f / %.1f (miss %+.1f / %+.1f)" % (n, *mark, H0, ft0, al0, ft0 - mark[0], al0 - mark[1]))
    hs = []
    for f in range(sh.fr - 4, sh.fr + 1):
        t = sh.t(f) - t_mid
        if not WINDOW_MS[0] <= t <= WINDOW_MS[1]:
            continue
        try:
            o = observe(s, f)
        except Exception as exc:
            o = None
            say("   f%d (%.1f ms): not observable (%s)" % (f, t, type(exc).__name__))
        if o is None:
            say("   f%d (%.1f ms): shaft, ferrule or the club's lower end not found" % (f, t))
            continue
        z = float(sol0["contact"][2] + sh.vz * t)
        origin = sh.place_ferrule(club, f, o, H0, z)
        _polys, axis_px, _lead = club.parts(sh.angles, origin)
        fer_model = s["scene"].project(club.shaft_point(sh.angles, origin, H0))[0]
        k = float(s["scene"].project(club.shaft_point(sh.angles, origin, H0 + 1.0))[0][1] - fer_model[1])   # image rows per mm up the shaft (negative)
        ex_model = model_exit(club, sh.angles, origin, axis_px)
        if ex_model is None or abs(k) < 1e-6:
            say("   f%d (%.1f ms): model's lower end not found" % (f, t))
            continue
        d_obs, d_mod = o["exit_y"] - o["ferrule"]["y"], ex_model - fer_model[1]
        h = H0 + (d_obs - d_mod) / abs(k)
        hs.append(h)
        say("   f%d (%.1f ms before mid-contact): ferrule to the club's lower end %.1f rows observed, %.1f on the model at %.1f mm (%.3f rows per mm) -> H = %.1f mm" % (f, -t, d_obs, d_mod, H0, abs(k), h))
    if not hs:
        say("   no clear-air frame measured")
        record[str(n)] = {"H": None}
        continue
    H = float(np.median(hs))
    ft, al, _s = reading(H)
    say("   this swing's H: %.1f mm (frames: %s; max - min %.1f) -> reading %.1f / %.1f (miss %+.1f / %+.1f)" % (H, " ".join("%.1f" % v for v in hs), max(hs) - min(hs), ft, al, ft - mark[0], al - mark[1]))
    record[str(n)] = {"H": H, "frames": hs, "fixed": [ft0, al0], "own": [ft, al], "mark": list(mark)}
    say()

vals = [record[str(n)]["H"] for n in (1, 2, 3, 4) if record[str(n)].get("H") is not None]
within = max((max(record[str(n)]["frames"]) - min(record[str(n)]["frames"]) for n in (1, 2, 3, 4) if record[str(n)].get("H") is not None), default=float("nan"))
worse = []
for n in (1, 2, 3, 4):
    r = record[str(n)]
    if r.get("H") is None:
        continue
    d_fixed = float(np.hypot(r["fixed"][0] - r["mark"][0], r["fixed"][1] - r["mark"][1]))
    d_own = float(np.hypot(r["own"][0] - r["mark"][0], r["own"][1] - r["mark"][1]))
    worse.append(d_own - d_fixed)
say("RULE (written before running): (1) frames of one swing within 2 mm: worst %.1f mm -> %s; (2) the four shots within 4 mm: %s, spread %.1f -> %s; (3) no reading more than 1 mm farther from its mark: changes %s -> %s" % (
    within, "pass" if within <= 2.0 else "FAIL", " ".join("%.1f" % v for v in vals), max(vals) - min(vals) if vals else float("nan"), "pass" if vals and len(vals) == 4 and max(vals) - min(vals) <= 4.0 else "FAIL",
    " ".join("%+.1f" % v for v in worse), "pass" if worse and max(worse) <= 1.0 else "FAIL"))
(AUDIT / "results" / "HOSEL-SELF.txt").write_text("\n".join(OUT) + "\n")
(AUDIT / "results" / "hosel-self.json").write_text(json.dumps(record, indent=1, default=float))

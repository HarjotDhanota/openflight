"""PROTOCOL addendum 12: the visible hosel (its top under the ferrule to where its bright run ends) measured on the
last rest frame and the first frame after, converted to mm and interpolated to mid-contact. Four shots, setup_free.
Feasibility probe. Writes results/HOSEL-VISIBLE.txt, results/hosel-visible.json, figures/hosel-visible.png.
Usage: hosel_visible.py"""
import json
from dataclasses import replace
from types import SimpleNamespace

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

import pipeline_lib as pl
import rig
import setup_free as sf
from common import AUDIT

OUT = []
H0 = 61.0
RADIAL = {"speed_datum": "radial", "los_after_ms": 0.0}
SPEC = replace(rig.ts4(), name="TS4 7-iron (research), axis at 75.0", axis_ground_from_toe=75.0)


def say(t=""):
    OUT.append(t)
    print(t)


def profile(frame, line, y0, y1):
    a, b = line[0], line[1]
    ys = np.arange(int(y0), int(y1) + 1)
    prof = []
    for y in ys:
        x = a + b * y
        xs = np.clip(np.round(np.array([x - 1.5, x - 0.5, x + 0.5, x + 1.5])).astype(int), 0, frame.shape[1] - 1)
        prof.append(float(frame[y, xs].max()))
    return ys, np.asarray(prof)


def hosel(frame, obs):
    """(top row, bottom row, plateau, shaft level, ys, prof) of the bright hosel run on the shaft axis, or a reason."""
    line = obs["line"]
    fy = obs["ferrule"]["y"]
    ys, prof = profile(frame, line, fy - 30, min(fy + 75, frame.shape[0] - 2))
    shaft = float(np.median(prof[ys < fy - 8]))
    level = 0.5 * shaft
    i = int(np.argmin(np.abs(ys - fy)))
    if prof[i] >= level:                       # the gap's centre must be dark
        near = [j for j in range(max(i - 4, 0), min(i + 5, len(ys))) if prof[j] < level]
        if not near:
            return "the ferrule gap is not dark at the observed ferrule"
        i = near[len(near) // 2]
    j = i
    while j + 1 < len(ys) and prof[j + 1] < level:
        j += 1
    if j + 9 >= len(ys):
        return "no hosel below the ferrule gap"
    top = ys[j] + (level - prof[j]) / (prof[j + 1] - prof[j])
    plateau = float(np.median(prof[j + 2:j + 9]))
    half = 0.5 * plateau
    k = j + 2
    while k + 1 < len(ys) and prof[k + 1] >= half:
        k += 1
    if k + 1 >= len(ys):
        return "the bright run does not end inside the search (%d rows)" % (ys[-1] - ys[j])
    bottom = ys[k] + (prof[k] - half) / (prof[k] - prof[k + 1])
    return top, bottom, plateau, shaft, ys, prof


record = {}
fig, axs = plt.subplots(2, 4, figsize=(26, 9))
for col, n in enumerate((1, 2, 3, 4)):
    s = sf.build(n)
    B, C = sf.observations(s)
    sh = pl.Shot(n, track_kwargs=RADIAL, shared=SimpleNamespace(s=s, B=B, edge_r=np.zeros((0, 3)), C=C))
    t_mid = sh.track["fits"]["pixels + radar"]["t_mid"]
    sol = sh.solve(SPEC, t_mid, "ferrule", True, None, H0)
    club = sol["club"]
    say("SHOT %d   frames %d / %d, time fraction to contact %.2f" % (n, sh.fr, sh.fc, sol["frac"]))
    vals = {}
    for row, (name, f, obs, origin) in enumerate((("before", sh.fr, B, sol["rest"]), ("after", sh.fc, C, sol["after"]))):
        p0 = s["scene"].project(club.shaft_point(sh.angles, origin, H0))[0]
        p1 = s["scene"].project(club.shaft_point(sh.angles, origin, H0 + 1.0))[0]
        k = abs(float(p1[1] - p0[1]))                 # image rows per mm along the shaft
        r = hosel(s["frames"][f], obs)
        ax = axs[row, col]
        if isinstance(r, str):
            say("   %-6s f%d: FAILED: %s" % (name, f, r))
            ys, prof = profile(s["frames"][f], obs["line"], obs["ferrule"]["y"] - 30, min(obs["ferrule"]["y"] + 75, s["frames"][f].shape[0] - 2))
            ax.plot(ys, prof, color="k", linewidth=1.0)
            ax.set_title("shot %d %s f%d: failed" % (n, name, f), fontsize=9)
            continue
        top, bottom, plateau, shaft, ys, prof = r
        L = (bottom - top) / k
        vals[name] = L
        say("   %-6s f%d: hosel top row %.2f, bright run ends row %.2f = %.1f rows; %.3f rows per mm -> L = %.1f mm (shaft %.0f DN, hosel plateau %.0f DN)" % (name, f, top, bottom, bottom - top, k, L, shaft, plateau))
        ax.plot(ys, prof, color="k", linewidth=1.0)
        ax.axvline(top, color="#d62728", linewidth=1.0)
        ax.axvline(bottom, color="#1f77b4", linewidth=1.0)
        ax.axhline(0.5 * plateau, color="#1f77b4", linewidth=0.6, linestyle=":")
        ax.set_title("shot %d %s f%d: brightness down the shaft axis; L = %.1f mm" % (n, name, f, L), fontsize=9)
        ax.set_xlabel("image row", fontsize=8)
        ax.tick_params(labelsize=7)
    if len(vals) == 2:
        Lc = vals["before"] + (vals["after"] - vals["before"]) * sol["frac"]
        say("   at contact (interpolated): %.1f mm; the two frames differ by %.1f mm" % (Lc, abs(vals["after"] - vals["before"])))
        record[str(n)] = {"before": vals["before"], "after": vals["after"], "contact": Lc}
    else:
        record[str(n)] = {**vals, "contact": None}
    say()
fig.tight_layout()
fig.savefig(AUDIT / "figures" / "hosel-visible.png", dpi=60)
ok = [record[str(n)]["contact"] for n in (1, 2, 3, 4) if record[str(n)]["contact"] is not None]
pair = [abs(record[str(n)]["after"] - record[str(n)]["before"]) for n in (1, 2, 3, 4) if record[str(n)]["contact"] is not None]
if len(ok) == 4:
    say("RULE (written before running): L at contact across the four shots %s: max - min %.1f mm (limit 3) -> %s; largest before/after difference %.1f mm (limit 4) -> %s" % (
        " ".join("%.1f" % v for v in ok), max(ok) - min(ok), "pass" if max(ok) - min(ok) <= 3.0 else "FAIL", max(pair), "pass" if max(pair) <= 4.0 else "FAIL"))
else:
    say("RULE (written before running): FAIL - the hosel was measured on both frames of only %d of 4 shots (%s)" % (len(ok), " ".join("%.1f" % v for v in ok)))
(AUDIT / "results" / "HOSEL-VISIBLE.txt").write_text("\n".join(OUT) + "\n")
(AUDIT / "results" / "hosel-visible.json").write_text(json.dumps(record, indent=1, default=float))

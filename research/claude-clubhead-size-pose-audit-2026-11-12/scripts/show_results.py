"""One picture per shot: the face photo with the photographed mark and every impact reading drawn on it.

Readings are stored as mm from the toe extreme along the score lines and mm above the leading edge; they are put back
on each photo through that photo's own score-line rectification (the inverse of photo_marks.py's mapping).
Nothing is computed here; it only draws results/pipeline.json, pipeline-turn-spec.json and the application's replay.
Writes figures/impact-on-photo.png. Usage: show_results.py"""
import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

import photo_marks as pm          # runs the mark measurement again and exposes its photos and constants
from common import AUDIT

pipe = json.loads((AUDIT / "results" / "pipeline.json").read_text())["results"]
turn = json.loads((AUDIT / "results" / "pipeline-turn-spec.json").read_text())
APP = {1: (11.49, 13.81), 3: (44.02, None), 4: (63.34, None)}      # mm from the toe, mm above the leading edge (None = height refused)
NAMES = {1: "shot 1 (slightly heavy, toe)", 3: "shot 3 (fat, centre)", 4: "shot 4 (fat, heel)"}
VIEW = {1: (95, 400, 95, 305), 3: (125, 470, 70, 290), 4: (150, 540, 130, 375)}


def to_pixel(ph, from_toe, above_le):
    u_t, v_b, _n = pm.line_frame(ph)
    u = u_t + pm.C_TOE - from_toe
    v = v_b + above_le - pm.C_BOT
    X = (-u / ph.scale) * ph.d_u + (v / ph.scale) * ph.e_v + float(ph.normal @ ph.r0) * ph.normal
    return np.array([pm.PP[0] + pm.F * X[0] / X[2], pm.PP[1] + pm.F * X[1] / X[2]])


fig, axs = plt.subplots(1, 3, figsize=(30, 11.5))
for a, n in zip(axs, (1, 3, 4)):
    ph = pm.photos[n]
    m, c_px, _thr = ph.mark()
    x0, x1, y0, y1 = VIEW[n]
    crop = ph.img[y0:y1, x0:x1]
    lo, hi = np.percentile(crop, [1, 99.5])
    a.imshow(np.clip((crop - lo) / (hi - lo), 0, 1), cmap="gray", interpolation="nearest", extent=(x0 - .5, x1 - .5, y1 - .5, y0 - .5))
    a.contour(np.arange(ph.img.shape[1]), np.arange(ph.img.shape[0]), m.astype(float), levels=[0.5], colors=["#ff2bd6"], linewidths=1.3)
    mark = pm.record[str(n)]
    m_ft, m_al = 0.5 * (mark["lines"][0] + mark["outline"][0]), 0.5 * (mark["lines"][1] + mark["outline"][1])
    a.scatter([c_px[0]], [c_px[1]], s=260, marker="+", c="#ff2bd6", linewidths=2.6, zorder=5, label="actual mark: %.0f mm from toe, %.0f mm up" % (m_ft, m_al))
    rows = [("TS4, placed by top edge", pipe["ts4|%d" % n]["from_toe"][1], pipe["ts4|%d" % n]["above_le"][1], "#39ff14", "x"),
            ("TS4, turn solved (spec dimensions)", turn[str(n)]["readings"]["rate"]["from_toe"], turn[str(n)]["readings"]["rate"]["above_le"], "#00e5ff", "D"),
            ("generic model, placed by top edge", pipe["generic|%d" % n]["from_toe"][1], pipe["generic|%d" % n]["above_le"][1], "#ffe600", "^")]
    for label, ft, al, colour, marker in rows:
        p = to_pixel(ph, ft, al)
        miss = np.hypot(ft - m_ft, al - m_al)
        a.scatter([p[0]], [p[1]], s=150, marker=marker, c=colour, edgecolors="k", linewidths=1.0 if marker != "x" else 2.4, zorder=6,
                  label="%s: %.0f, %.0f  (%.0f mm from the mark)" % (label, ft, al, miss))
    ft, al = APP[n]
    if al is not None:
        p = to_pixel(ph, ft, al)
        a.scatter([p[0]], [p[1]], s=150, marker="s", c="#ff8c00", edgecolors="k", linewidths=1.0, zorder=6, label="app: %.0f, %.0f  (%.0f mm from the mark)" % (ft, al, np.hypot(ft - m_ft, al - m_al)))
    else:
        p0, p1 = to_pixel(ph, ft, 3.0), to_pixel(ph, ft, 50.0)
        a.plot([p0[0], p1[0]], [p0[1], p1[1]], color="#ff8c00", linewidth=1.6, linestyle="--", zorder=4, label="app: %.0f mm from toe, height refused" % ft)
    # 10 mm scale bar along the score lines, bottom left
    s0 = to_pixel(ph, 30.0, 2.5)
    s1 = to_pixel(ph, 40.0, 2.5)
    a.plot([s0[0], s1[0]], [s0[1], s1[1]], color="w", linewidth=3.0, zorder=6)
    a.text((s0[0] + s1[0]) / 2, min(s0[1], s1[1]) - 6, "10 mm", color="w", fontsize=11, ha="center", va="bottom")
    a.set_xlim(x0 - .5, x1 - .5)
    a.set_ylim(y1 - .5, y0 - .5)
    a.set_xticks([])
    a.set_yticks([])
    a.legend(fontsize=12, loc="upper center", bbox_to_anchor=(0.5, -0.02), framealpha=1.0)
    a.set_title(NAMES[n], fontsize=14)
fig.suptitle("Impact readings drawn on the face photos (numbers: mm from the toe tip, mm above the leading edge). Research readings on three already-studied shots.", fontsize=13)
fig.tight_layout(rect=(0, 0.02, 1, 0.95))
fig.savefig(AUDIT / "figures" / "impact-on-photo.png", dpi=62)
print("wrote impact-on-photo.png")

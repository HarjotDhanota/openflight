"""All four shots side by side: each face photo with the mark (frozen detector), the research reading (TS4 model,
shaft+ferrule placement, the same setup_free code path on every shot) and the application's own reading.
Draws only: readings come from results/HELDOUT-VALIDATION.txt and heldout-shot2-prediction.json.
Usage: PHOTO_SHOTS=1,2,3,4 show_all4.py   (writes figures/all4-on-photo.png)"""
import json
import os
import re

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

import photo_marks as pm
from common import AUDIT

reading = {}
shot = None
for line in (AUDIT / "results" / "HELDOUT-VALIDATION.txt").read_text().splitlines():
    m = re.match(r"SHOT (\d)", line)
    if m:
        shot = int(m.group(1))
    m = re.search(r"setup_free:\s+rest frame.*reading ([\d.]+) / ([\d.]+)", line)
    if m:
        reading[shot] = (float(m.group(1)), float(m.group(2)))
reading[2] = tuple(json.loads((AUDIT / "results" / "heldout-shot2-prediction.json").read_text())["primary"])
import os  # noqa: E402
SOURCE = os.environ.get("READINGS", "ferrule")   # "outline" = the owner's placement, results/outline-fit4-measured.json (W1)
if SOURCE == "outline":
    fit4 = json.loads((AUDIT / "results" / "outline-fit4-measured.json").read_text())
    reading = {n: tuple(fit4[str(n)]["variants"]["W1"]["reading"]) for n in (1, 2, 3, 4)}
if SOURCE == "system":   # the single frozen runner of PROTOCOL addendum 14
    run = json.loads((AUDIT / "results" / "system-run.json").read_text())
    reading = {n: (run[str(n)]["impact"]["from_toe"], run[str(n)]["impact"]["above_le"]) for n in (1, 2, 3, 4) if run[str(n)]["status"] == "read"}
METHOD = {"ferrule": "TS4 model placed by shaft and ferrule.", "outline": "TS4 model placed by hosel line, top edge and toe; no club length given.",
          "system": "Single frozen runner (hosel line, top edge, toe; angles from the camera's ball track and club descent)."}[SOURCE]
TAIL = "" if SOURCE == "ferrule" else ", method developed on these four shots (not a blind test)"
APP = {1: (11.49, 13.81), 2: None, 3: (44.02, None), 4: (63.34, None)}   # None = refused (whole reading, or height only)
NAMES = {1: "Shot 1: slightly heavy, toe", 2: "Shot 2: clean, centre (blind test)" if os.environ.get("READINGS", "ferrule") == "ferrule" else "Shot 2: clean, centre", 3: "Shot 3: fat, centre", 4: "Shot 4: fat, heel"}
VIEW = {1: (95, 400, 95, 305), 2: (170, 520, 45, 265), 3: (125, 470, 70, 290), 4: (150, 540, 130, 375)}


def to_pixel(ph, from_toe, above_le):
    u_t, v_b, _n = pm.line_frame(ph)
    u = u_t + pm.C_TOE - from_toe
    v = v_b + above_le - pm.C_BOT
    X = (-u / ph.scale) * ph.d_u + (v / ph.scale) * ph.e_v + float(ph.normal @ ph.r0) * ph.normal
    return np.array([pm.PP[0] + pm.F * X[0] / X[2], pm.PP[1] + pm.F * X[1] / X[2]])


fig, axs = plt.subplots(2, 2, figsize=(24, 17.5))
for a, n in zip(axs.ravel(), (1, 2, 3, 4)):
    ph = pm.photos[n]
    m, c_px, _thr = ph.mark()
    rec = pm.record[str(n)]
    mark = (0.5 * (rec["lines"][0] + rec["outline"][0]), 0.5 * (rec["lines"][1] + rec["outline"][1]))
    shift = c_px - to_pixel(ph, *mark)      # so the drawn separation is exactly the reported miss
    x0, x1, y0, y1 = VIEW[n]
    crop = ph.img[y0:y1, x0:x1]
    lo, hi = np.percentile(crop, [1, 99.5])
    a.imshow(np.clip((crop - lo) / (hi - lo), 0, 1), cmap="gray", interpolation="nearest", extent=(x0 - .5, x1 - .5, y1 - .5, y0 - .5))
    a.contour(np.arange(ph.img.shape[1]), np.arange(ph.img.shape[0]), m.astype(float), levels=[0.5], colors=["#ff2bd6"], linewidths=1.3)
    a.scatter([c_px[0]], [c_px[1]], s=330, marker="+", c="#ff2bd6", linewidths=2.8, zorder=5, label="actual mark: %.1f mm from toe, %.1f mm up" % mark)
    r = reading[n]
    p = to_pixel(ph, *r) + shift
    a.scatter([p[0]], [p[1]], s=270, marker="x", c="#39ff14", linewidths=3.0, zorder=6,
              label="research reading: %.1f, %.1f   (miss %+.1f across, %+.1f up; %.1f mm apart)" % (r[0], r[1], r[0] - mark[0], r[1] - mark[1], float(np.hypot(r[0] - mark[0], r[1] - mark[1]))))
    app = APP[n]
    if app is None:
        a.plot([], [], color="#ff8c00", linewidth=0, label="app: shot refused, no reading")
    elif app[1] is None:
        p0, p1 = to_pixel(ph, app[0], 3.0) + shift, to_pixel(ph, app[0], 50.0) + shift
        a.plot([p0[0], p1[0]], [p0[1], p1[1]], color="#ff8c00", linewidth=1.8, linestyle="--", zorder=4, label="app: %.1f mm from toe, height refused" % app[0])
    else:
        q = to_pixel(ph, *app) + shift
        a.scatter([q[0]], [q[1]], s=170, marker="s", c="#ff8c00", edgecolors="k", linewidths=1.0, zorder=6,
                  label="app: %.1f, %.1f   (%.1f mm apart)" % (app[0], app[1], float(np.hypot(app[0] - mark[0], app[1] - mark[1]))))
    s0, s1 = to_pixel(ph, 30.0, 2.5) + shift, to_pixel(ph, 40.0, 2.5) + shift
    a.plot([s0[0], s1[0]], [s0[1], s1[1]], color="w", linewidth=3.0, zorder=6)
    a.text((s0[0] + s1[0]) / 2, min(s0[1], s1[1]) - 6, "10 mm", color="w", fontsize=11, ha="center", va="bottom")
    a.set_xlim(x0 - .5, x1 - .5)
    a.set_ylim(y1 - .5, y0 - .5)
    a.set_xticks([])
    a.set_yticks([])
    a.legend(fontsize=12, loc="upper center", bbox_to_anchor=(0.5, -0.01), framealpha=1.0)
    a.set_title(NAMES[n], fontsize=15)
fig.suptitle("Impact location on the face photos, all four shots (mm from the toe tip, mm above the leading edge). " + METHOD + "\n"
             "Research readings, one club, one session" + TAIL + ". The mark's own position is uncertain by about 1 mm across and 1-3 mm in height.", fontsize=14)
fig.subplots_adjust(left=0.02, right=0.98, top=0.91, bottom=0.09, wspace=0.04, hspace=0.34)
fig.savefig(AUDIT / "figures" / {"ferrule": "all4-on-photo.png", "outline": "all4-on-photo-outline.png", "system": "all4-on-photo-system.png"}[SOURCE], dpi=62)
print("wrote the %s figure" % SOURCE)
mk = {n: (0.5 * (pm.record[str(n)]["lines"][0] + pm.record[str(n)]["outline"][0]), 0.5 * (pm.record[str(n)]["lines"][1] + pm.record[str(n)]["outline"][1])) for n in (1, 2, 3, 4)}
for n in (1, 2, 3, 4):
    if n in reading:
        print("shot %d: reading %.1f / %.1f | mark %.1f / %.1f | miss %+.1f / %+.1f mm, %.1f mm apart" % (n, *reading[n], *mk[n], reading[n][0] - mk[n][0], reading[n][1] - mk[n][1],
              float(np.hypot(reading[n][0] - mk[n][0], reading[n][1] - mk[n][1]))))

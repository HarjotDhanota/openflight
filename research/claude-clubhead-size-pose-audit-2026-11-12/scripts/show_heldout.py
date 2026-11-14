"""Picture of the shot 2 blind test: the face photo with the mark as the frozen detector outlined it, the mark's
centre by the three outlines of photo_mark_variants.py, and the blind prediction. Draws only; computes nothing new.
Usage: PHOTO_SHOTS=1,2,3,4 show_heldout.py   (writes figures/heldout-shot2-on-photo.png)"""
import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

import photo_marks as pm
from common import AUDIT

pred = json.loads((AUDIT / "results" / "heldout-shot2-prediction.json").read_text())["primary"]
ph = pm.photos[2]
m, c_px, _thr = ph.mark()
rec = pm.record["2"]
mark = (0.5 * (rec["lines"][0] + rec["outline"][0]), 0.5 * (rec["lines"][1] + rec["outline"][1]))


def to_pixel(from_toe, above_le):
    u_t, v_b, _n = pm.line_frame(ph)
    u = u_t + pm.C_TOE - from_toe
    v = v_b + above_le - pm.C_BOT
    X = (-u / ph.scale) * ph.d_u + (v / ph.scale) * ph.e_v + float(ph.normal @ ph.r0) * ph.normal
    return np.array([pm.PP[0] + pm.F * X[0] / X[2], pm.PP[1] + pm.F * X[1] / X[2]])


shift = c_px - to_pixel(*mark)          # so that the drawn separation is exactly the reported miss
p = to_pixel(*pred) + shift
alts = {"mark centre, overlap outline (43.5, 11.9)": (43.5, 11.9), "mark centre, shading-flattened outline (42.3, 9.3)": (42.3, 9.3), "mark centre, box by eye (42.8, 15.8)": (42.8, 15.8)}
fig, ax = plt.subplots(figsize=(15, 9.6))
x0, x1, y0, y1 = 170, 520, 45, 265
crop = ph.img[y0:y1, x0:x1]
lo, hi = np.percentile(crop, [1, 99.5])
ax.imshow(np.clip((crop - lo) / (hi - lo), 0, 1), cmap="gray", interpolation="nearest", extent=(x0 - .5, x1 - .5, y1 - .5, y0 - .5))
ax.contour(np.arange(ph.img.shape[1]), np.arange(ph.img.shape[0]), m.astype(float), levels=[0.5], colors=["#ff2bd6"], linewidths=1.3)
ax.scatter([c_px[0]], [c_px[1]], s=320, marker="+", c="#ff2bd6", linewidths=2.8, zorder=5, label="actual mark, frozen detector: %.1f mm from toe, %.1f mm up" % mark)
for (label, v), colour in zip(alts.items(), ("#ffb000", "#ff7a00", "#ffe600")):
    q = to_pixel(*v) + shift
    ax.scatter([q[0]], [q[1]], s=70, marker="o", facecolors="none", edgecolors=colour, linewidths=1.6, zorder=5, label=label)
ax.scatter([p[0]], [p[1]], s=260, marker="x", c="#39ff14", linewidths=3.0, zorder=6,
           label="blind prediction: %.1f, %.1f  (%.1f mm from the frozen-detector mark)" % (pred[0], pred[1], float(np.hypot(pred[0] - mark[0], pred[1] - mark[1]))))
s0, s1 = to_pixel(30.0, 2.5) + shift, to_pixel(40.0, 2.5) + shift
ax.plot([s0[0], s1[0]], [s0[1], s1[1]], color="w", linewidth=3.0)
ax.text((s0[0] + s1[0]) / 2, min(s0[1], s1[1]) - 5, "10 mm", color="w", fontsize=11, ha="center", va="bottom")
ax.set_xlim(x0 - .5, x1 - .5)
ax.set_ylim(y1 - .5, y0 - .5)
ax.set_xticks([])
ax.set_yticks([])
ax.legend(fontsize=11, loc="upper center", bbox_to_anchor=(0.5, -0.01), framealpha=1.0)
ax.set_title("Shot 2 (never analysed before): impact predicted from the swing frames before this photo was opened.\nResearch reading; one shot; numbers are mm from the toe tip, mm above the leading edge.", fontsize=12)
fig.tight_layout()
fig.savefig(AUDIT / "figures" / "heldout-shot2-on-photo.png", dpi=75)
print("wrote heldout-shot2-on-photo.png")

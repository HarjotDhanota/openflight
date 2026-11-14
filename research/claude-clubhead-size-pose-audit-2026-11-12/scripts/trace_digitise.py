"""Digitise the owner's red trace (drawn on a screenshot of figures/carry-shot1.png, right panel) into native prepared
pixels of shot 1 frame 43. Registration: the cyan curve in the screenshot is the carried generic outline whose native
coordinates are known exactly (carry_lib), so its bounding box fixes scale and offset on each axis.
Writes owner-trace/shot1-f43-trace.json and a check figure. Usage: trace_digitise.py"""
import json
import cv2
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import carry_lib as cl
from common import AUDIT

img = cv2.imread(str(AUDIT / "owner-trace" / "owner-red-trace-on-carry-shot1.png"))
b, g, r = (img[:, :, i].astype(int) for i in range(3))
cyan = (b > 170) & (g > 150) & (r < 110)
red = (r > 170) & (g < 90) & (b < 90)
print("screenshot", img.shape, "cyan px", int(cyan.sum()), "red px", int(red.sum()))
c = cl.carry(1)
gen = c["gen_bw"]
from scipy.optimize import minimize
from scipy.spatial import cKDTree
cyan[:150, :] = False  # the legend
ys, xs = np.nonzero(cyan)
pts = np.column_stack([xs, ys]).astype(float)
dense_native = np.vstack([np.linspace(gen[i], gen[(i + 1) % len(gen)], 12) for i in range(len(gen))])


def cost(p):
    s, ox, oy = p
    tree = cKDTree(dense_native * s + np.array([ox, oy]))
    d = tree.query(pts)[0]
    return float(np.mean(np.minimum(d, 6.0) ** 2))


best = None
for s0 in (8.6, 9.0, 9.4):
    for dx0 in (-6, 0, 6):
        for dy0 in (-6, 0, 6):
            res = minimize(cost, [s0, 68 - 115 * s0 + dx0, 227 - 76 * s0 + dy0], method="Nelder-Mead", options={"xatol": 1e-3, "fatol": 1e-5, "maxiter": 4000})
            if best is None or res.fun < best.fun:
                best = res
sx = sy = float(best.x[0])
ox, oy = float(best.x[1]), float(best.x[2])
d = cKDTree(dense_native * sx + np.array([ox, oy])).query(pts)[0]
dist = d
print("isotropic scale %.3f screenshot px per native px; offsets %.1f %.1f" % (sx, ox, oy))
print("cyan pixels to the mapped known curve: median %.2f, 95th %.2f screenshot px (= %.2f, %.2f native px)" % (np.median(d), np.percentile(d, 95), np.median(d) / sx, np.percentile(d, 95) / sx))
# red trace: close, fill, take the outer contour
closed = cv2.morphologyEx(red.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
flood = closed.copy()
mask = np.zeros((flood.shape[0] + 2, flood.shape[1] + 2), np.uint8)
cv2.floodFill(flood, mask, (0, 0), 2)
inside = (flood != 2).astype(np.uint8)
cs, _ = cv2.findContours(inside, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
cont = max(cs, key=cv2.contourArea)[:, 0, :].astype(float)
# the trace's centre line is about half a stroke inside the filled region's edge
stroke = 2.0 * red.sum() / max(cv2.arcLength(max(cs, key=cv2.contourArea), True), 1.0) / 2.0
print("filled area %.0f screenshot px^2, contour points %d, stroke half-width about %.1f screenshot px = %.2f native px" % (cv2.contourArea(max(cs, key=cv2.contourArea)), len(cont), stroke, stroke / sx))
native = np.column_stack([(cont[:, 0] - ox) / sx, (cont[:, 1] - oy) / sy])[::4]
out = {"shot": 1, "frame": 43, "polygon_native_px": native.tolist(), "registration": {"sx": sx, "sy": sy, "ox": ox, "oy": oy,
       "cyan_to_known_curve_native_px_median": float(np.median(dist) / sx)}, "stroke_half_width_native_px": float(stroke / sx),
       "note": "outer edge of the owner's red stroke; the stroke centre is about one half-width inside"}
(AUDIT / "owner-trace" / "shot1-f43-trace.json").write_text(json.dumps(out))
s = c["s"]
fig, ax = plt.subplots(figsize=(15, 8))
x0, x1, y0, y1 = 112, 202, 66, 118
ax.imshow(s["frames"][43][y0:y1, x0:x1], cmap="gray", vmin=0, vmax=160, interpolation="nearest", extent=(x0 - .5, x1 - .5, y1 - .5, y0 - .5))
ax.plot(*np.vstack([native, native[:1]]).T, color="#ff2020", linewidth=1.4)
ax.set_title("owner's trace, digitised into native pixels, on the bare frame (shot 1 f43): x %.1f..%.1f, y %.1f..%.1f" % (native[:, 0].min(), native[:, 0].max(), native[:, 1].min(), native[:, 1].max()))
fig.tight_layout(); fig.savefig(AUDIT / "owner-trace" / "check-trace-on-raw.png", dpi=60)
print("x %.1f..%.1f y %.1f..%.1f" % (native[:, 0].min(), native[:, 0].max(), native[:, 1].min(), native[:, 1].max()))

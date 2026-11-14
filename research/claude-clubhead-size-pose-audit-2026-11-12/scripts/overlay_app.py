"""The application's own fitted outline (its outline_px, untouched) on raw pixels, last-rest frame, beside the same raw
crop with nothing drawn. Identical native scale in both panels. Usage: overlay_app.py <shot> [<frame>]"""
import sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import MultipleLocator
import geom
from common import AUDIT, prepared

shot = int(sys.argv[1])
state, arrays = prepared(shot)
frames = arrays["prepared_frames"]
d, prep = state["placement"]["diagnostics"], state["prepared"]
f = int(sys.argv[2]) if len(sys.argv) > 2 else d["bracket_frames"][0]
o = state["orientation"]
face, loft, lie = (o[k]["value"] for k in ("face_angle", "dynamic_loft", "lie"))
scene = geom.Scene(prep["camera"]["focal_px"], prep["camera"]["principal_point_px"], (prep["ball"]["x"], prep["ball"]["y"]), prep["ball_range_mm"])
ax3 = geom.axes(face, loft, lie)
fr = state["frames"][str(f)]
origin = geom.pose_origin(scene, ax3, fr["toe_mm"], fr["up_mm"], fr["normal_mm"])
outline = arrays["outline_%d" % f]
line = geom.shaft_line(scene, ax3, origin, loft, d["shaft_constraint"]["hosel_axis_uv_mm"])
bx, by, bd = prep["ball"]["x"], prep["ball"]["y"], prep["ball"]["diameter_px"]
x0, x1 = int(outline[:, 0].min() - 22), int(max(outline[:, 0].max(), bx + bd / 2) + 8)
y0, y1 = int(outline[:, 1].min() - 30), int(outline[:, 1].max() + 12)
fig, axs = plt.subplots(1, 2, figsize=(22, 11.0 * (y1 - y0) / (x1 - x0) * 1.0 + 0.8))
ext = (x0 - 0.5, x1 - 0.5, y1 - 0.5, y0 - 0.5)
crop = frames[f][y0:y1, x0:x1].astype(float)
for k, a in enumerate(axs):
    a.imshow(crop, cmap="gray", vmin=0, vmax=max(120.0, float(np.percentile(crop, 99.7))), interpolation="nearest", extent=ext)
    a.xaxis.set_major_locator(MultipleLocator(5)); a.yaxis.set_major_locator(MultipleLocator(5)); a.tick_params(labelsize=7)
    a.set_xlim(x0 - 0.5, x1 - 0.5); a.set_ylim(y1 - 0.5, y0 - 0.5)
closed = np.vstack([outline, outline[:1]])
axs[1].plot(closed[:, 0], closed[:, 1], color="#ff8c00", linewidth=1.6, label="application's fitted generic outline (its own pixels)")
axs[1].plot(line[:, 0], line[:, 1], color="#ff8c00", linewidth=1.2, linestyle="--", label="application's model shaft axis")
axs[1].add_patch(plt.Circle((bx, by), bd / 2, fill=False, color="w", linewidth=0.8, linestyle=":", label="resting ball"))
axs[1].legend(fontsize=8, loc="upper right")
t = prep["frame_time_ms"][f] - prep["contact_ms"]
axs[0].set_title("shot %d frame %d, raw prepared pixels, nothing drawn (%+.2f ms from the application's contact)" % (shot, f, t), fontsize=10)
axs[1].set_title("same pixels with the application's model: face %.1f, loft %.1f, lie roll %.1f deg, depth %.0f mm" % (face, loft, lie, origin[2]), fontsize=10)
fig.tight_layout()
fig.savefig(AUDIT / "figures" / ("app-overlay-shot%d-f%d.png" % (shot, f)), dpi=62)
print("wrote app-overlay-shot%d-f%d.png" % (shot, f), "ball", round(bx, 2), round(by, 2), round(bd, 2), "pp", prep["camera"]["principal_point_px"], "roi", prep["roi_origin"])

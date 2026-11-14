"""Fit the rigid 3-D club to the OWNER'S red trace on shot 1 frame 43. The trace is the target; nothing else is.
Depth is the application's (not fitted). Free-parameter sets are fixed in club3d.FREE and run in that order.
Writes results/FIT-TRACE.txt, results/fit-trace.json, figures/fit-trace-shot1.png. Usage: fit_trace.py"""
import json
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import MultipleLocator
import club3d
import diag_lib as dl
from common import AUDIT

OUT = []
def say(t=""):
    OUT.append(t); print(t)

s = dl.setup(1)
f = 43
trace = np.asarray(json.loads((AUDIT / "owner-trace" / "shot1-f43-trace.json").read_text())["polygon_native_px"])
shape = s["frames"][f].shape
target = club3d.polygon_mask(trace, shape)
club = club3d.Club(s["scene"], s["origin_rest"], *s["sole"], shape)
face, loft, roll = s["angles"]
start = {"face": face, "loft": loft, "roll": roll, "dx": 0.0, "dy": 0.0, "size": 1.0}
row_lo = 82.0   # below the rounded top of the traced hosel
mm = float(s["origin_rest"][2]) / s["scene"].focal
say("shot 1 f43: owner's trace x %.1f..%.1f, y %.1f..%.1f (native px); compared below row %.0f; depth %.0f mm (%.2f mm/px), not fitted" % (trace[:, 0].min(), trace[:, 0].max(), trace[:, 1].min(), trace[:, 1].max(), row_lo, s["origin_rest"][2], mm))
say("head outline without the stub: %d of %d points; shaft in (toe, up, normal): %s; axis ground point from the face centre: %s mm" % (len(club3d.HEAD), len(club3d.geom.OUTLINE), np.round(club3d.SHAFT_TUN, 3).tolist(), np.round(club3d.GROUND_TUN, 1).tolist()))
results, masks = {}, {}

def line(name, r):
    p = r.get("params", start)
    say("  %-46s boundary rms %.2f px (mean %.2f, 95th %.2f, max %.2f) | IoU %.2f, %2.0f %% of the model off the trace, %2.0f %% of the trace uncovered | face %+6.1f loft %5.1f roll %+5.1f  shift %+5.1f, %+5.1f mm  size %.3f" % (
        name, r["rms"], r["mean"], r["p95"], r["max"], r["iou"], 100 * r["off_target"], 100 * r["uncovered"], p["face"], p["loft"], p["roll"], p["dx"], p["dy"], p["size"]))

m, axis, _l = club.mask(face, loft, roll, inplane_stub=True)
r = club3d.compare(m, target, row_lo); results["application's pose, application's flat model"] = r; masks["app"] = (m, axis)
line("application's pose, its flat model (stub in plane)", r)
m, axis, _l = club.mask(face, loft, roll)
r = club3d.compare(m, target, row_lo); results["application's pose, 3-D hosel"] = r
line("application's pose, 3-D hosel", r)
r, m, axis = club3d.fit(club, target, start, "translation", row_lo, inplane_stub=True)
results["translation only, flat model"] = r; line("translation only, flat model", r)
seeds = [{"face": face + 15}, {"face": face + 30}, {"face": face - 15}, {"loft": loft + 12}, {"loft": loft - 10}, {"face": face + 25, "loft": loft + 10}]
for free in club3d.FREE:
    r, m, axis = club3d.fit(club, target, start, free, row_lo, starts=seeds)
    results[free + ", 3-D hosel"] = r; masks[free] = (m, axis)
    line(free + ", 3-D hosel", r)
(AUDIT / "results" / "FIT-TRACE.txt").write_text("\n".join(OUT) + "\n")
(AUDIT / "results" / "fit-trace.json").write_text(json.dumps(results, indent=1, default=float))

x0, x1, y0, y1 = 108, 202, 62, 120
ext = (x0 - .5, x1 - .5, y1 - .5, y0 - .5)
show = [("app", "application's pose and flat model", "#ff8c00"), ("translation", "3-D hosel, moved only (no rotation)", "#00e5ff"), ("translation+face+loft+roll", "3-D hosel, moved and ROTATED (face, loft, roll free), size 1", "#39ff14")]
fig, axs = plt.subplots(1, 4, figsize=(34, 34 / 4 * (y1 - y0) / (x1 - x0) + 1.0))
for a in axs:
    a.imshow(s["frames"][f][y0:y1, x0:x1], cmap="gray", vmin=0, vmax=160, interpolation="nearest", extent=ext)
    a.xaxis.set_major_locator(MultipleLocator(5)); a.yaxis.set_major_locator(MultipleLocator(5)); a.tick_params(labelsize=6)
    a.set_xlim(x0 - .5, x1 - .5); a.set_ylim(y1 - .5, y0 - .5)
axs[0].set_title("shot 1 frame 43, bare pixels", fontsize=10)
for a, (key, label, colour) in zip(axs[1:], show):
    m, axis = masks[key]
    b = club3d.boundary(m)
    a.plot(*np.vstack([trace, trace[:1]]).T, color="#ff2020", linewidth=1.3, label="owner's trace")
    a.plot(*np.vstack([b, b[:1]]).T, color=colour, linewidth=1.5, label=label)
    a.plot(axis[:, 0], axis[:, 1], color=colour, linewidth=0.8, linestyle="--")
    name = {"app": "application's pose, application's flat model"}.get(key, key + ", 3-D hosel")
    r = results[name]
    a.set_title("%s\nboundary rms %.1f px, IoU %.2f" % (label, r["rms"], r["iou"]), fontsize=9)
    a.legend(fontsize=7, loc="lower left")
fig.tight_layout(); fig.savefig(AUDIT / "figures" / "fit-trace-shot1.png", dpi=56)

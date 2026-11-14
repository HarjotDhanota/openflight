"""Signed difference from the pre-swing median with the observed-silhouette contour, for checking the extraction.
Usage: diff_view.py <shot> <frame> [<frame> ...]"""
import sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import MultipleLocator
import diag_lib as dl
from common import AUDIT

shot = int(sys.argv[1])
s = dl.setup(shot)
fs = [int(v) for v in sys.argv[2:]]
fig, axs = plt.subplots(len(fs), 2, figsize=(22, 6.6 * len(fs)), squeeze=False)
b = s["prep"]["ball"]
for row, f in zip(axs, fs):
    obs = dl.observe_frame(s, f)
    x0, x1, y0, y1 = int(obs["toe_x"] - 75), int(obs["toe_x"] + 22), int(obs["bottom_y"] - 50), int(obs["bottom_y"] + 10)
    ext = (x0 - 0.5, x1 - 0.5, y1 - 0.5, y0 - 0.5)
    raw = s["frames"][f][y0:y1, x0:x1].astype(float)
    row[0].imshow(raw, cmap="gray", vmin=0, vmax=160, interpolation="nearest", extent=ext)
    row[1].imshow(obs["diff"][y0:y1, x0:x1], cmap="RdBu_r", vmin=-60, vmax=60, interpolation="nearest", extent=ext)
    for a in row:
        a.contour(np.arange(x0, x1), np.arange(y0, y1), obs["head"][y0:y1, x0:x1].astype(float), levels=[0.5], colors=["#00c800"], linewidths=1.0)
        a.add_patch(plt.Circle((b["x"], b["y"]), b["diameter_px"] / 2, fill=False, color="k" if a is row[1] else "w", linewidth=0.6, linestyle=":"))
        a.axhline(obs["top_y"], color="#ffb000", linewidth=0.5); a.axhline(obs["bottom_y"], color="#ffb000", linewidth=0.5)
        a.xaxis.set_major_locator(MultipleLocator(5)); a.yaxis.set_major_locator(MultipleLocator(5)); a.tick_params(labelsize=6)
        a.set_xlim(x0 - 0.5, x1 - 0.5); a.set_ylim(y1 - 0.5, y0 - 0.5)
    row[0].set_title("shot %d frame %d raw (fixed 0..160 DN); green: observed head silhouette" % (shot, f), fontsize=9)
    row[1].set_title("frame minus pre-swing median, +-60 DN (red brighter)", fontsize=9)
fig.tight_layout()
name = "check-diff-shot%d-%s.png" % (shot, "-".join(str(f) for f in fs))
fig.savefig(AUDIT / "figures" / name, dpi=58)
print("wrote", name)

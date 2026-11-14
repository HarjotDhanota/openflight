"""Enlarged views of the head on the last rest frame of each shot (raw pixels, and the change from the reference image),
to decide what a toe finder can follow. Looks only; measures nothing. Writes figures/toe-look.png. Usage: toe_look.py"""
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import MultipleLocator

import setup_free as sf
from common import AUDIT

fig, axs = plt.subplots(2, 4, figsize=(34, 15))
for col, n in enumerate((1, 2, 3, 4)):
    s = sf.build(n)
    B, _C = sf.observations(s)
    f = s["d"]["bracket_frames"][0]
    frame = s["frames"][f].astype(np.float32)
    diff = frame - s["ref"]
    ball = s["prep"]["ball"]
    x0, x1 = int(B["ferrule"]["x"]) - 12, int(ball["x"] + ball["diameter_px"] / 2) + 14
    y0, y1 = int(B["ferrule"]["y"]) - 10, int(B["bottom_y"]) + 8
    for row, (img, kw, name) in enumerate(((frame, {"cmap": "gray", "vmin": 0, "vmax": 170}, "raw"), (diff, {"cmap": "RdBu_r", "vmin": -90, "vmax": 90}, "frame minus reference"))):
        ax = axs[row, col]
        ax.imshow(img[y0:y1, x0:x1], interpolation="nearest", extent=(x0 - .5, x1 - .5, y1 - .5, y0 - .5), **kw)
        ax.add_patch(plt.Circle((ball["x"], ball["y"]), ball["diameter_px"] / 2, fill=False, color="#ffe600", linewidth=0.8, linestyle=":"))
        ax.xaxis.set_major_locator(MultipleLocator(5))
        ax.yaxis.set_major_locator(MultipleLocator(5))
        ax.grid(color="#39ff14", linewidth=0.2, alpha=0.5)
        ax.tick_params(labelsize=7)
        ax.set_title("shot %d f%d: %s" % (n, f, name), fontsize=11)
fig.tight_layout()
fig.savefig(AUDIT / "figures" / "toe-look.png", dpi=58)
print("wrote toe-look.png")

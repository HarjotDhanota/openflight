"""Pre-swing median (frames 0-30) around the resting ball of each shot, raw prepared pixels, nothing drawn but ticks.
To see what the ball rests on. Usage: rest_view.py"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import MultipleLocator
from common import AUDIT, prepared

fig, axs = plt.subplots(1, 3, figsize=(24, 9))
for a, shot in zip(axs, (1, 3, 4)):
    state, arrays = prepared(shot)
    frames = arrays["prepared_frames"]
    ref = np.median(frames[:31].astype(np.float32), axis=0)
    b = state["prepared"]["ball"]
    x0, x1, y0, y1 = int(b["x"] - 32), int(b["x"] + 32), int(b["y"] - 22), int(b["y"] + 34)
    y1 = min(y1, ref.shape[0]); x1 = min(x1, ref.shape[1])
    a.imshow(ref[y0:y1, x0:x1], cmap="gray", vmin=0, vmax=160, interpolation="nearest", extent=(x0 - 0.5, x1 - 0.5, y1 - 0.5, y0 - 0.5))
    a.xaxis.set_major_locator(MultipleLocator(5)); a.yaxis.set_major_locator(MultipleLocator(5)); a.tick_params(labelsize=7)
    a.set_title("shot %d pre-swing median; ball centre (%.1f, %.1f), diameter %.1f px, bottom row %.1f" % (shot, b["x"], b["y"], b["diameter_px"], b["y"] + b["diameter_px"] / 2), fontsize=9)
    col = int(round(b["x"]))
    print("shot", shot, "column", col, "rows", y0, "..", y1 - 1, ":", " ".join("%d" % v for v in ref[y0:y1, col]))
fig.tight_layout(); fig.savefig(AUDIT / "figures" / "rest-ball.png", dpi=60)

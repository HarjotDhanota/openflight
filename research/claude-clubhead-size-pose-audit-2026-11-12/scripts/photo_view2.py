"""A gridded view of one face photo, for choosing by eye the scored patch, the mark's box and the outline landmarks.
Usage: photo_view2.py <shot> [x0 x1 y0 y1]   (writes figures/photo-grid-shot<N>[-zoom].png)"""
import sys

import cv2
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import MultipleLocator

from common import AUDIT, ROOT

shot = int(sys.argv[1])
img = cv2.imread(str(ROOT / "face-photos" / ("shot-%04d.pgm" % shot)), cv2.IMREAD_GRAYSCALE).astype(np.float32)
zoom = len(sys.argv) >= 6
x0, x1, y0, y1 = (int(v) for v in sys.argv[2:6]) if zoom else (0, img.shape[1], 0, img.shape[0])
fig, ax = plt.subplots(figsize=(22, 22 * (y1 - y0) / (x1 - x0) + 0.6))
lo, hi = np.percentile(img[y0:y1, x0:x1], [1, 99.5])
ax.imshow(np.clip((img[y0:y1, x0:x1] - lo) / (hi - lo), 0, 1), cmap="gray", interpolation="nearest", extent=(x0 - .5, x1 - .5, y1 - .5, y0 - .5))
step = 10 if zoom else 20
ax.xaxis.set_major_locator(MultipleLocator(step))
ax.yaxis.set_major_locator(MultipleLocator(step))
ax.grid(color="#ffe600", linewidth=0.25, alpha=0.5)
ax.tick_params(labelsize=7, labeltop=True, labelright=True)
ax.set_title("shot %d face photo %s" % (shot, img.shape), fontsize=10)
fig.tight_layout()
out = AUDIT / "figures" / ("photo-grid-shot%d%s.png" % (shot, "-zoom" if zoom else ""))
fig.savefig(out, dpi=62)
print("wrote", out)

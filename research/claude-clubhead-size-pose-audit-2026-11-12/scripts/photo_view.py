"""Enlarged views of a face photo (original pixels, read-only) with a coordinate grid, for landmarking by eye.
Usage: photo_view.py <shot> x0 x1 y0 y1 [gamma]"""
import sys

import cv2
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import MultipleLocator

from common import AUDIT, ROOT

shot = int(sys.argv[1])
x0, x1, y0, y1 = (int(v) for v in sys.argv[2:6])
gamma = float(sys.argv[6]) if len(sys.argv) > 6 else 1.0
img = cv2.imread(str(ROOT / "face-photos" / ("shot-%04d.pgm" % shot)), cv2.IMREAD_GRAYSCALE)
crop = img[y0:y1, x0:x1].astype(float)
lo, hi = np.percentile(crop, [1, 99.5])
show = np.clip((crop - lo) / (hi - lo), 0, 1) ** gamma
fig, ax = plt.subplots(figsize=(20, 20 * (y1 - y0) / (x1 - x0) + 0.4))
ax.imshow(show, cmap="gray", interpolation="nearest", extent=(x0 - .5, x1 - .5, y1 - .5, y0 - .5))
step = 5 if (x1 - x0) <= 160 else 10
ax.xaxis.set_major_locator(MultipleLocator(step))
ax.yaxis.set_major_locator(MultipleLocator(step))
ax.tick_params(labelsize=7, top=True, labeltop=True, right=True, labelright=True)
ax.grid(color="#ffe600", alpha=0.25, linewidth=0.4)
ax.set_title("shot %d face photo, original pixels %s, x %d..%d y %d..%d" % (shot, img.shape, x0, x1, y0, y1), fontsize=9)
fig.tight_layout()
name = "photo-shot%d-%d-%d-%d-%d.png" % (shot, x0, x1, y0, y1)
fig.savefig(AUDIT / "figures" / name, dpi=62)
print("wrote", name, "image", img.shape)

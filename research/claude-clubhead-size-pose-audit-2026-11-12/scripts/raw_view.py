"""Raw prepared pixels, enlarged with nearest-neighbour, native coordinates on the axes. No model, no overlay.
Usage: raw_view.py <shot> <frame> [<frame> ...]   (reads the prepared frames saved by the unmodified app replay)"""
import sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import MultipleLocator
from common import AUDIT, prepared

shot = int(sys.argv[1]); wanted = [int(v) for v in sys.argv[2:]]
state, arrays = prepared(shot)
frames = arrays["prepared_frames"]
bx, by = state["prepared"]["ball"]["x"], state["prepared"]["ball"]["y"]
for f in wanted:
    x0, x1, y0, y1 = int(bx - 100), int(bx + 30), int(by - 50), int(by + 25)
    fig, ax = plt.subplots(figsize=(16, 9.6))
    crop = frames[f][y0:y1, x0:x1].astype(float)
    ax.imshow(crop, cmap="gray", vmin=0, vmax=max(120.0, float(np.percentile(crop, 99.8))), interpolation="nearest", extent=(x0 - 0.5, x1 - 0.5, y1 - 0.5, y0 - 0.5))
    ax.xaxis.set_major_locator(MultipleLocator(5)); ax.yaxis.set_major_locator(MultipleLocator(5))
    ax.tick_params(labelsize=7)
    ax.set_title("shot %d frame %d raw prepared pixels (%.2f ms from the app's contact instant)" % (shot, f, state["prepared"]["frame_time_ms"][f] - state["prepared"]["contact_ms"]), fontsize=10)
    fig.tight_layout()
    fig.savefig(AUDIT / "figures" / ("raw-shot%d-f%d.png" % (shot, f)), dpi=70)
    plt.close(fig)
    print("wrote raw-shot%d-f%d.png" % (shot, f))

"""The earlier session's three unopened shots: what the replay gives the runner, and a strip of the SWING frames
round impact (2x2 binned to the 640x400 scale the method is defined at). No face photo is read.
Writes figures/t1-frames-shot<N>.png. Usage: t1_look.py"""
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from common import AUDIT

T1 = Path("F:/Coding/OpenFlight/pi-handoff/Harjot-outdoor-test-1/arm5/paired/run-01")
CAPS = {2: "camera_20261024_172137_777_002", 3: "camera_20261024_172322_381_003", 33: "camera_20261024_174053_920_039"}


def binned(frames):
    f = frames.astype(np.float32)
    return (f[:, 0::2, 0::2] + f[:, 1::2, 0::2] + f[:, 0::2, 1::2] + f[:, 1::2, 1::2]) / 4.0


for n, cap in CAPS.items():
    rp = json.loads((AUDIT / "results" / ("t1-shot%d" % n) / "replay.json").read_text())
    fs = rp["stages"]["full_shot"]
    ctx, m = fs.get("camera_fusion_context") or {}, fs["metrics"]
    box = ctx.get("box_resting_ball")
    det = (ctx.get("ball_range") or {}).get("details") or {}
    print("T1 SHOT %d  status %s | box ball %s | lens distance %s m | aim offset %s" % (n, fs.get("status"), None if box is None else (round(box["x_px"], 1), round(box["y_px"], 1), round(box["diameter_px"], 2), box.get("diameter_support")),
          det.get("lens_distance_m"), (ctx.get("geometry") or {}).get("parameters", {}).get("ball_horizontal_output_offset_deg")))
    print("   metrics:", {k: (None if m[k]["value"] is None else round(m[k]["value"], 2)) for k in ("ball_speed", "club_speed", "club_path", "attack_angle", "dynamic_lie", "launch_vertical", "launch_horizontal", "face_angle", "dynamic_loft")})
    print("   impact:", (fs.get("impact") or {}).get("status"), "|", (fs.get("impact") or {}).get("reason"))
    z = np.load(T1 / "arm5/camera" / cap / "frames.npz")
    ts = (z["sensor_timestamp_ns"] - z["sensor_timestamp_ns"][0]) / 1e6
    print("   frames %s, pre-trigger count %s, spacing %.2f ms, span %.0f ms" % (z["frames"].shape, int(z["pre_trigger_count"]), float(np.median(np.diff(ts))), ts[-1]))
    fr = binned(z["frames"])
    if box is None:
        continue
    bx, by, br = box["x_px"] / 2.0, box["y_px"] / 2.0, box["diameter_px"] / 4.0
    x0, x1, y0, y1 = int(bx) - 170, int(bx) + 120, int(by) - 150, int(by) + 60
    x0, y0 = max(x0, 0), max(y0, 0)
    first = max(int(z["pre_trigger_count"]) - 5, 0)
    idx = list(range(first, min(first + 10, len(fr))))
    fig, axs = plt.subplots(2, 5, figsize=(30, 17))
    for a, f in zip(axs.ravel(), idx):
        a.imshow(fr[f][y0:y1, x0:x1], cmap="gray", vmin=0, vmax=170, interpolation="nearest", extent=(x0 - .5, x1 - .5, y1 - .5, y0 - .5))
        a.add_patch(plt.Circle((bx, by), br, fill=False, color="#ff2020", linewidth=0.6, linestyle=":"))
        a.set_title("t1 shot %d f%d  (%.1f ms)" % (n, f, ts[f]), fontsize=12)
        a.set_xticks([])
        a.set_yticks([])
    fig.tight_layout()
    fig.savefig(AUDIT / "figures" / ("t1-frames-shot%d.png" % n), dpi=50)
    plt.close(fig)
    print("   wrote t1-frames-shot%d.png (frames %d-%d)" % (n, idx[0], idx[-1]))

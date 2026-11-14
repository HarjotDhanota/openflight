"""PROTOCOL addendum 1 (M3b, M6): shaft-anchored rigid carry, text and figures (computation in carry_lib.py).

Writes results/CARRY.txt, results/carry.json, figures/carry-shot<N>.png. Usage: carry.py"""
import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import MultipleLocator

import carry_lib as cl
from common import AUDIT

OUT = []


def say(text=""):
    OUT.append(text)
    print(text)


def closed(p):
    return np.vstack([p, p[:1]])


def draw_clipped(a, p, colour, label, vis):
    c = closed(p)
    a.plot(c[:, 0], np.where(c[:, 1] <= vis, c[:, 1], np.nan), color=colour, linewidth=1.8, label=label)
    a.plot(c[:, 0], np.where(c[:, 1] >= vis - 0.5, c[:, 1], np.nan), color=colour, linewidth=1.0, linestyle=(0, (2, 2)))


result = {}
for shot in (1, 3, 4):
    c = cl.carry(shot)
    s, A, B = c["s"], c["A"], c["B"]
    fa, fb = c["frames"]
    mm, prep = c["mm"], s["prep"]
    own, gen, app = c["own_bw"], c["gen_bw"], c["app_b"]
    stats = {name: [float(np.mean(r)), float(np.sqrt(np.mean(r ** 2)))] for name, r in (("own", c["res_own"]), ("generic", c["res_gen"]), ("app", c["res_app"]))}
    rim = c["rim_px"]
    rec = {"frames": [fa, fb], "depth_ratio": c["k"], "shaft_turn_deg": c["turn"], "stretch_w": c["w"], "mm_per_px": mm, "rim_px": rim,
           "own": [float(own[:, 1].min()), float(own[:, 1].max()), float(own[:, 0].max())], "generic": [float(gen[:, 1].min()), float(gen[:, 1].max()), float(gen[:, 0].max())],
           "app": [float(app[:, 1].min()), float(app[:, 1].max()), float(app[:, 0].max())], "visible_bottom_y": B["bottom_y"], "sharp_toe_x": c["sharp_toe_x"],
           "ball_bottom_y": c["ball_bottom"], "edge_columns": int(len(c["edge"])), "edge_minus": stats}
    result[shot] = rec
    say("shot %d: IN-AIR f%d -> NEAR f%d (%+.2f ms) | depth ratio %.3f, shaft turned %+.2f deg in the image, toe-ward stretch w %.3f, %.2f mm/px" % (
        shot, fa, fb, prep["frame_time_ms"][fb] - prep["contact_ms"], c["k"], c["turn"], c["w"], mm))
    say("   rim: on f%d the cavity's upper edge is %.1f px below the silhouette's top (%d columns) -> subtract it from the edge residuals below" % (fa, rim, c["rim_n"]))
    say("   cavity's upper edge on f%d (%d columns), net of the rim: %+.1f px below the club's OWN carried silhouette; %+.1f px below the carried generic model; %+.1f px = %.0f mm below the APPLICATION's model" % (
        fb, len(c["edge"]), stats["own"][0] - rim, stats["generic"][0] - rim, stats["app"][0] - rim, (stats["app"][0] - rim) * mm))
    say("   club's OWN carried silhouette: rows %.1f .. %.1f, toe x %.1f | lowest visible row %.1f -> %.1f px = %.1f mm of the head out of sight | resting ball's bottom row %.1f" % (
        rec["own"][0], rec["own"][1], rec["own"][2], B["bottom_y"], rec["own"][1] - B["bottom_y"], (rec["own"][1] - B["bottom_y"]) * mm, c["ball_bottom"]))
    say("   carried generic model: rows %.1f .. %.1f, toe x %.1f (bottom %.1f mm below the visible line, %.1f mm below the ball's bottom row) | application's model: rows %.1f .. %.1f, toe x %.1f | sharp toe x %.1f" % (
        rec["generic"][0], rec["generic"][1], rec["generic"][2], (rec["generic"][1] - B["bottom_y"]) * mm, (rec["generic"][1] - c["ball_bottom"]) * mm, rec["app"][0], rec["app"][1], rec["app"][2], c["sharp_toe_x"]))

    x0 = int(min(app[:, 0].min(), gen[:, 0].min()) - 14)
    x1 = int(max(app[:, 0].max(), prep["ball"]["x"] + prep["ball"]["diameter_px"] / 2) + 8)
    y0, y1 = int(min(app[:, 1].min(), own[:, 1].min()) - 14), int(max(gen[:, 1].max(), own[:, 1].max()) + 8)
    ext = (x0 - 0.5, x1 - 0.5, y1 - 0.5, y0 - 0.5)
    crop = s["frames"][fb][y0:y1, x0:x1].astype(float)
    fig, axs = plt.subplots(1, 3, figsize=(27, 27 / 3 * (y1 - y0) / (x1 - x0) + 1.1))
    for a in axs:
        a.imshow(crop, cmap="gray", vmin=0, vmax=160, interpolation="nearest", extent=ext)
        a.xaxis.set_major_locator(MultipleLocator(5)); a.yaxis.set_major_locator(MultipleLocator(5)); a.tick_params(labelsize=6)
        a.set_xlim(x0 - 0.5, x1 - 0.5); a.set_ylim(y1 - 0.5, y0 - 0.5)
    t = prep["frame_time_ms"][fb] - prep["contact_ms"]
    axs[0].set_title("shot %d frame %d, raw prepared pixels, nothing drawn (%+.2f ms from the application's contact)" % (shot, fb, t), fontsize=10)
    axs[1].plot(*closed(app).T, color="#ff8c00", linewidth=1.8, label="application's fitted model (its own outline pixels)")
    axs[1].set_title("application 0fa9870b", fontsize=10)
    draw_clipped(axs[2], own, "#00d000", "this club's OWN silhouette from frame %d, carried rigidly by the shaft" % fa, B["bottom_y"])
    draw_clipped(axs[2], gen, "#00e5ff", "generic model placed on frame %d, carried the same way (dashed: out of sight)" % fa, B["bottom_y"])
    axs[2].scatter(c["edge"][:, 0], c["edge"][:, 1], s=14, c="#ff2bd6", marker="_", linewidths=1.2, label="dark cavity's upper edge on this frame (check)")
    axs[2].scatter([B["ferrule"]["x"]], [B["ferrule"]["y"]], s=60, marker="+", c="#ffe600", label="ferrule (anchor)")
    axs[2].axhline(B["bottom_y"], color="#ffe600", linewidth=0.6, linestyle=":")
    axs[2].set_title("shaft-anchored carry from frame %d (research); yellow dotted: lowest visible row" % fa, fontsize=10)
    for a in axs[1:]:
        a.add_patch(plt.Circle((prep["ball"]["x"], prep["ball"]["y"]), prep["ball"]["diameter_px"] / 2, fill=False, color="w", linewidth=0.7, linestyle=":"))
        a.legend(fontsize=7, loc="upper left", framealpha=0.7)
    fig.tight_layout()
    fig.savefig(AUDIT / "figures" / ("carry-shot%d.png" % shot), dpi=58)
    plt.close(fig)

(AUDIT / "results" / "CARRY.txt").write_text("\n".join(OUT) + "\n")
(AUDIT / "results" / "carry.json").write_text(json.dumps(result, indent=1, default=float))

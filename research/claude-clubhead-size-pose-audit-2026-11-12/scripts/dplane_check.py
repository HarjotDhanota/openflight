"""What the application's D-plane step had for each placed shot, beside two things measured here from pixels:
the ball's launch from its own track (ball_track_lib) and the club's descent from the ferrule's drop between the two
frames before contact. Read-only. Writes results/DPLANE-CHECK.txt. Usage: dplane_check.py"""
import json
import math

import numpy as np

import carry_lib as cl
import diag_lib as dl
from common import AUDIT

OUT = []


def say(t=""):
    OUT.append(t)
    print(t)


tracks = json.loads((AUDIT / "results" / "pipeline.json").read_text())["tracks"]
for n in (1, 3, 4):
    state = json.loads((AUDIT / "results" / ("shot%d" % n) / "placement.json").read_text())
    cm = state["collision_model"]
    say("SHOT %d  (D-plane method: %s, model %s)" % (n, cm.get("face_method"), cm.get("model_version")))
    for key, e in cm["inputs"].items():
        if isinstance(e, dict):
            say("   input  %-18s %8s  %-10s source %s%s" % (key, "%.2f" % e["value"] if isinstance(e.get("value"), (int, float)) else str(e.get("value")), e.get("label"), e.get("source"),
                                                          "  [experimental]" if e.get("experimental") else ""))
    for key in ("face_angle", "dynamic_loft", "spin_axis", "face_to_path"):
        e = cm["metrics"].get(key)
        if isinstance(e, dict):
            say("   output %-18s %8s  %-10s %s" % (key, "%.2f" % e["value"] if isinstance(e.get("value"), (int, float)) else str(e.get("value")), e.get("status"), (e.get("note") or "")[:90]))
    o = state["orientation"]
    say("   used by the placement: face %+.2f (sigma %.0f), loft %.2f (sigma %.0f), roll %+.2f (sigma %.0f)" % (
        o["face_angle"]["value"], o["face_angle"]["sigma"], o["dynamic_loft"]["value"], o["dynamic_loft"]["sigma"], o["lie"]["value"], o["lie"]["sigma"]))
    fit = tracks[str(n)]["fits"]["pixels + radar"]
    say("   MEASURED HERE, ball track: launch %.1f deg above the camera axis, %.1f deg right of it, %.1f m/s (radar %.1f)" % (fit["launch_deg"], fit["azimuth_deg"], fit["speed"], tracks[str(n)]["radar_speed"]))
    s = dl.setup(n)
    c = cl.carry(n)
    A, B = c["A"], c["B"]
    fa, fb = c["frames"]
    mm = 0.5 * (float(dl.origin_at(s, fa)[2]) + float(dl.origin_at(s, fb)[2])) / s["scene"].focal
    drop = (B["ferrule"]["y"] - A["ferrule"]["y"]) * mm
    travel = float(np.linalg.norm(s["v3"])) * (s["times"][fb] - s["times"][fa])
    say("   MEASURED HERE, club descent: the ferrule dropped %.1f mm over %.0f mm of travel between frames %d and %d -> about %.1f deg downward (the D-plane step was given %s)" % (
        drop, travel, fa, fb, -math.degrees(math.asin(max(min(drop / travel, 1.0), -1.0))), "%.2f" % cm["inputs"]["attack_angle"]["value"] if isinstance(cm["inputs"].get("attack_angle", {}).get("value"), (int, float)) else "nothing"))
    say()
(AUDIT / "results" / "DPLANE-CHECK.txt").write_text("\n".join(OUT) + "\n")

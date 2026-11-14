"""Shot 1 impact point again, with the contact instant taken from the ball's own track (results/ball-track.json)
instead of the application's. Club placements are those of impact1.py, unchanged (results/impact-shot1.json).
Writes results/IMPACT-SHOT1-BALLTRACK.txt and results/impact-balltrack.json. No photo is read. Usage: impact_balltrack.py"""
import json

import numpy as np

import diag_lib as dl
import geom
from common import AUDIT

OUT = []


def say(t=""):
    OUT.append(t)
    print(t)


s = dl.setup(1)
SCENE = s["scene"]
imp = json.loads((AUDIT / "results" / "impact-shot1.json").read_text())
track = json.loads((AUDIT / "results" / "ball-track.json").read_text())
angles = tuple(imp["angles"])
t43, t44 = imp["times_ms"]["f43"], imp["times_ms"]["f44"]


def reading(origin):
    toe, up, normal = geom.axes(*angles)
    contact = SCENE.ball - geom.BALL_RADIUS_MM * normal
    ray = contact / np.linalg.norm(contact)
    hit = float(origin @ normal) / float(ray @ normal) * ray - origin
    return float(hit @ toe), float(hit @ up)


instants = {"application's contact instant": 0.0,
            "ball track, pixels + radar speed": track["fits"]["PIXELS + RADAR SPEED"]["t_dep_ms"],
            "ball track, pixels only": track["fits"]["PIXELS ONLY"]["t_dep_ms"]}
say("SHOT 1 IMPACT POINT BY CONTACT INSTANT (research reading; development shot). Club placements unchanged from impact1.py.")
say("frame 43 at %.3f ms and frame 44 at %+.3f ms on the application's clock" % (t43, t44))
out = {}
for label, t_mid in instants.items():
    t1, t2 = t_mid - t43, t44 - t_mid
    rows = []
    for anchor in ("AUTO", "TRACE"):
        o43 = np.array(imp["poses"][anchor]["f43"])
        for f44_name, key in (("head edge", "f44_head_edge"), ("shaft only", "f44_shaft_only")):
            o44 = np.array(imp["poses"][anchor][key])
            for interp, frac in (("linear", t1 / (t1 + t2)), ("slowed 0.83", t1 / (t1 + 0.83 * t2))):
                origin = o43 + (o44 - o43) * frac
                u, v = reading(origin)
                rows.append((anchor, f44_name, interp, u, v, origin))
    us, vs = np.array([r[3] for r in rows]), np.array([r[4] for r in rows])
    say()
    say("%s: mid-contact %+.2f ms; frame 43 is %.2f ms before it, frame 44 %.2f ms after" % (label, t_mid, t1, t2))
    say("   all eight: toe-ward %+.1f .. %+.1f mm (median %+.1f); up %+.1f .. %+.1f mm (median %+.1f)" % (us.min(), us.max(), np.median(us), vs.min(), vs.max(), np.median(vs)))
    pick = [r for r in rows if r[0] == "TRACE" and r[1] == "head edge" and r[2] == "slowed 0.83"][0]
    say("   trace anchor, f44 by head edge, slowed: %+.1f toe-ward, %+.1f up" % (pick[3], pick[4]))
    out[label] = {"t_mid_ms": t_mid, "toe_range": [float(us.min()), float(us.max())], "up_range": [float(vs.min()), float(vs.max())], "toe_median": float(np.median(us)),
                  "up_median": float(np.median(vs)), "pick": {"toe_mm": pick[3], "up_mm": pick[4], "origin": pick[5].tolist()}}
say()
say("The photographed mark (results/PHOTO-SHOT1.txt, measured earlier, not used here): +26.9 .. +29.3 toe-ward (median +27.7), -0.9 .. +0.9 up (median 0.0).")
(AUDIT / "results" / "IMPACT-SHOT1-BALLTRACK.txt").write_text("\n".join(OUT) + "\n")
(AUDIT / "results" / "impact-balltrack.json").write_text(json.dumps(out, indent=1))

"""Make the club's DEPTH consistent with the contact instant. Shot 1.

The camera fixes where the club is across its view; it does not measure the club's distance. Until now that distance
came from the application's motion model on the application's contact instant, so moving the instant left the face
short of the ball. Rule, stated before running:
   contact   the face centre stays on its camera ray; its distance is set so the face plane passes through the ball's
             contact point (resting ball centre minus one radius along the face normal; no compression modelled)
   before    f43's face centre stays on its camera ray; distance = contact distance minus club speed (supplied path
             and attack) times the time to mid-contact
   after     f44 likewise, with the club slowed to 0.83 after contact (the application's assumption)
   size      rescaled by f43's distance ratio, so the model still covers exactly the same pixels on f43 (the owner's
             trace fit is untouched)
Done for both contact instants. Writes results/FINAL-POSES.txt and results/final-poses.json. Usage: final_poses.py"""
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
toe, up, normal = geom.axes(*angles)
t43, t44 = imp["times_ms"]["f43"], imp["times_ms"]["f44"]
o43 = np.array(imp["poses"]["TRACE"]["f43"])
o44 = np.array(imp["poses"]["TRACE"]["f44_head_edge"])
size0 = imp["size"]
vz = float(s["v3"][2])                      # mm per ms toward the target, from the supplied club speed, path and attack
contact_point = SCENE.ball - geom.BALL_RADIUS_MM * normal
say("club speed toward the target %.2f mm/ms; ball contact point (%.1f, %.1f, %.1f) mm; size before %.3f" % (vz, *contact_point, size0))
out = {}
for name, t_mid in (("track", track["fits"]["PIXELS + RADAR SPEED"]["t_dep_ms"]), ("app", 0.0)):
    t1, t2 = t_mid - t43, t44 - t_mid
    frac = t1 / (t1 + 0.83 * t2)
    c0 = o43 + (o44 - o43) * frac                      # where the club is across the view at mid-contact
    ray = c0 / np.linalg.norm(c0)
    c = float(contact_point @ normal) / float(ray @ normal) * ray
    gap_before = float((contact_point - c0) @ normal)
    before = o43 * ((c[2] - vz * t1) / o43[2])
    after = o44 * ((c[2] + 0.83 * vz * t2) / o44[2])
    size = size0 * before[2] / o43[2]
    u, v = float((contact_point - c) @ toe), float((contact_point - c) @ up)
    # what the reading was with the old depth (camera ray through the contact pixel onto the old plane)
    r = contact_point / np.linalg.norm(contact_point)
    hit = float(c0 @ normal) / float(r @ normal) * r - c0
    out[name] = {"t_mid_ms": t_mid, "before": before.tolist(), "contact": c.tolist(), "after": after.tolist(), "size": size,
                 "reading": {"toe_mm": u, "up_mm": v}, "gap_removed_mm": gap_before}
    say()
    say("%s instant (mid-contact %+.2f ms): f43 %.2f ms before, f44 %.2f ms after" % (name, t_mid, t1, t2))
    say("   contact: face was %.1f mm short of the ball along the face normal; face centre moved %.1f mm along its camera ray to touch (depth %.1f -> %.1f mm)" % (gap_before, float(np.linalg.norm(c - c0)), c0[2], c[2]))
    say("   f43 depth %.1f -> %.1f mm; f44 depth %.1f -> %.1f mm; size %.3f -> %.3f (same pixels on f43)" % (o43[2], before[2], o44[2], after[2], size0, size))
    say("   impact point: %+.1f mm toe-ward, %+.1f mm up (with the old depth: %+.1f, %+.1f)" % (u, v, float(hit @ toe), float(hit @ up)))
    ball44 = np.array(track["fits"]["PIXELS + RADAR SPEED"]["f44"]["position_mm"])
    sep = float((ball44 - after) @ normal) - geom.BALL_RADIUS_MM
    say("   on f44 the ball's surface is %.1f mm clear of the face plane (it should be positive: the ball has left)" % sep)
(AUDIT / "results" / "FINAL-POSES.txt").write_text("\n".join(OUT) + "\n")
(AUDIT / "results" / "final-poses.json").write_text(json.dumps(out, indent=1))

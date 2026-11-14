"""Is the head on shot 1's traced frame TURNED (face open to the camera) rather than short between toe and shaft?

The TS4 model at its true size, with the shaft axis where the spec sheet puts it (80.1 mm from the toe), is too wide
for the owner's trace; moving the axis to 75 mm fits. A head turned about the vertical would look narrower in exactly
the same way. This script lets the face angle go free instead of moving the axis, and then lets loft and roll go too.
Stated before running: if a free face angle with the axis at 80.1 fits the trace as well as the axis at 75 does
(within 0.15 px rms), the two explanations cannot be told apart on this frame, and the turn it asks for is reported.
Writes results/YAW-TEST.txt. Usage: yaw_test.py"""
from dataclasses import replace

import numpy as np
from scipy.optimize import minimize

import diag_lib as dl
import rig
from common import AUDIT
import json

OUT = []


def say(t=""):
    OUT.append(t)
    print(t)


s = dl.setup(1)
trace = np.asarray(json.loads((AUDIT / "owner-trace" / "shot1-f43-trace.json").read_text())["polygon_native_px"])
shape = s["frames"][43].shape
target = rig.polygon_mask(trace, shape)
base = dl.origin_at(s, 43)
face0, loft0, roll0 = s["angles"]
ts4 = rig.ts4()


def run(spec, free):
    club = rig.Club(s["scene"], spec, shape)
    names = ["dx", "dy"] + list(free)
    scale = {"dx": 3.0, "dy": 3.0, "face": 8.0, "loft": 6.0, "roll": 3.0}
    start = {"dx": 3.0, "dy": 7.0, "face": face0, "loft": loft0, "roll": roll0}

    def pose(x):
        p = dict(start)
        for n, v in zip(names, x):
            p[n] = start[n] + v * scale[n]
        return p

    def cost(x):
        p = pose(x)
        m, _a, _l = club.mask((p["face"], p["loft"], p["roll"]), base + np.array([p["dx"], p["dy"], 0.0]))
        return rig.compare(m, target, 82.0)["rms"]

    best = None
    seeds = [np.zeros(len(names))]
    if "face" in free:
        for f in (10.0, 20.0, 30.0, -15.0):
            x = np.zeros(len(names))
            x[names.index("face")] = (f - face0) / scale["face"]
            seeds.append(x)
    for seed in seeds:
        res = minimize(cost, seed, method="Nelder-Mead", options={"xatol": 0.01, "fatol": 0.002, "maxiter": 900, "initial_simplex": np.vstack([seed, seed + np.eye(len(names))])})
        if best is None or res.fun < best.fun:
            best = res
    p = pose(best.x)
    m, _a, _l = club.mask((p["face"], p["loft"], p["roll"]), base + np.array([p["dx"], p["dy"], 0.0]))
    return rig.compare(m, target, 82.0), p


say("shot 1 f43, owner's trace; supplied face %+.1f, loft %.1f, roll %+.1f; TS4 model at true size" % (face0, loft0, roll0))
for label, xg, free in (("axis 80.1 (spec), supplied angles", 80.1, ()), ("axis 75.0, supplied angles", 75.0, ()), ("axis 80.1, FACE ANGLE free", 80.1, ("face",)),
                        ("axis 80.1, face + loft + roll free", 80.1, ("face", "loft", "roll")), ("axis 75.0, face + loft + roll free", 75.0, ("face", "loft", "roll"))):
    sc, p = run(replace(ts4, axis_ground_from_toe=xg), free)
    say("   %-38s boundary rms %.2f px, overlap %.2f | face %+5.1f, loft %4.1f, roll %+4.1f" % (label, sc["rms"], sc["iou"], p["face"], p["loft"], p["roll"]))
(AUDIT / "results" / "YAW-TEST.txt").write_text("\n".join(OUT) + "\n")

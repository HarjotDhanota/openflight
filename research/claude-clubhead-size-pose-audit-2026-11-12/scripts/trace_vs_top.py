"""Shot 1, frame 43: the owner's hand trace of the head against what outline_fit4.py takes as the head's top and toe.
Per top column: the trace's top, the cavity's upper edge, and that edge raised by the swing's rim thickness.
Per toe row: the trace's right-most point, the cavity's toe edge, and that edge moved out by the rim thickness.
Diagnostic only; fits nothing. Writes results/TRACE-VS-TOP.txt. Usage: trace_vs_top.py"""
import json
import math

import numpy as np

import carry_lib as cl
import outline_fit4 as of4      # runs the sixth pass again (same outputs) and exposes its finder
import rig
import setup_free as sf
from common import AUDIT, ROI_ORIGIN

OUT = []


def say(t=""):
    OUT.append(t)
    print(t)


s = sf.build(1)
B, _C = sf.observations(s)
f = s["d"]["bracket_frames"][0]
x0, y0 = s["prep"]["roi_origin"]
trace = np.asarray(json.loads((AUDIT / "owner-trace" / "shot1-f43-trace.json").read_text())["polygon_native_px"], dtype=float)
trace = trace + np.array([ROI_ORIGIN[0] - x0, ROI_ORIGIN[1] - y0])      # application's crop -> this crop
o, _why = of4.observe_outline(s, f, B)
t = of4.record["1"]["variants"]["W1"]["rim_px"]
mm = float(of4.dl.origin_at(s, f)[2]) / s["scene"].focal
say("shot 1 f%d; rim thickness used %.2f px (%.2f px vertical); %.2f mm per px" % (f, t, t / of4.SLOPE_FACTOR, mm))
say("TOP   column | trace top | cavity edge | head top taken (cavity - rim) | trace minus head top (px; + = trace lower)")
d_top = []
for x, yc, _g in o["cav"]:
    yt = rig.top_at(trace, float(x))
    head = yc - t / of4.SLOPE_FACTOR
    if math.isfinite(yt):
        d_top.append(yt - head)
        say("      %5.0f  | %7.2f   | %7.2f     | %7.2f                       | %+.2f   (trace minus cavity edge %+.2f)" % (x, yt, yc, head, yt - head, yt - yc))
say("   median: trace is %+.2f px = %+.1f mm from the head top taken (+ = trace lower)" % (float(np.median(d_top)), float(np.median(d_top)) * mm))
say("TOE   row | trace right-most | cavity toe edge | head toe taken (cavity + rim) | trace minus head toe (px; + = trace further right)")
d_toe = []
for xc, y in o["toe"]:
    xt = of4.right_at(trace, float(y))
    if math.isfinite(xt):
        d_toe.append(xt - (xc + t))
        say("      %4.0f | %7.2f          | %7.2f         | %7.2f                       | %+.2f" % (y, xt, xc, xc + t, xt - (xc + t)))
if d_toe:
    say("   median: trace is %+.2f px = %+.1f mm from the head toe taken (+ = trace further right)" % (float(np.median(d_toe)), float(np.median(d_toe)) * mm))
(AUDIT / "results" / "TRACE-VS-TOP.txt").write_text("\n".join(OUT) + "\n")

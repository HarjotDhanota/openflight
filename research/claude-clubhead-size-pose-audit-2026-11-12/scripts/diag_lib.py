"""PROTOCOL M1-M5. Writes results/DIAG.txt and results/diag.json. No face photo, no mark, no fitted threshold.
Usage: diag.py"""
import json
import math

import numpy as np

import geom
import observe
import sil
from common import AUDIT, prepared

OUT = []
BALL_MM = 42.67
FRAMES = {1: (42, 43), 3: (42, 43), 4: (43, 44)}  # (IN-AIR, NEAR = last rest)
EXTRA = {1: (41, 44), 3: (41, 44), 4: (42, 45)}   # also measured in M3 (earlier frame, first after-contact frame)


def say(text=""):
    OUT.append(text)
    print(text)


def setup(shot):
    state, arrays = prepared(shot)
    d, prep = state["placement"]["diagnostics"], state["prepared"]
    o = state["orientation"]
    angles = tuple(o[k]["value"] for k in ("face_angle", "dynamic_loft", "lie"))
    scene = geom.Scene(prep["camera"]["focal_px"], prep["camera"]["principal_point_px"], (prep["ball"]["x"], prep["ball"]["y"]), prep["ball_range_mm"])
    rest = d["bracket_frames"][0]
    times = prep["frame_time_ms"]
    p, a = math.radians(d["club_path"]["value"]), math.radians(d["attack_angle"]["value"])
    v3 = d["club_speed_mps"] * np.array([math.sin(p) * math.cos(a), -math.sin(a), math.cos(p) * math.cos(a)])
    fr = state["frames"][str(rest)]
    origin_rest = geom.pose_origin(scene, geom.axes(*angles), fr["toe_mm"], fr["up_mm"], fr["normal_mm"])
    frames = arrays["prepared_frames"]
    ref, sigma = observe.reference(frames)
    return {"state": state, "arrays": arrays, "d": d, "prep": prep, "angles": angles, "scene": scene, "rest": rest, "times": times, "v3": v3,
            "origin_rest": origin_rest, "frames": frames, "ref": ref, "sigma": sigma,
            "sole": (d["club_model"]["sole_width"]["value"], d["club_model"]["effective_bounce"]["value"]),
            "foot": d["shaft_constraint"]["hosel_axis_uv_mm"]}


def origin_at(s, f):
    """Application's last-rest origin carried to frame f by the SUPPLIED club motion (for depth and a starting place only)."""
    return s["origin_rest"] - s["v3"] * (s["times"][s["rest"]] - s["times"][f])


def observe_frame(s, f, floor=8.0):
    diff, mask = sil.changed(s["frames"][f], s["ref"], s["sigma"], floor)
    comp = sil.head_component(mask)
    if comp is None:
        return None
    ax = sil.shaft_axis(diff, comp)
    if ax is None or ax["line"] is None:
        return None
    a, b = ax["line"][0], ax["line"][1]
    # the hosel: follow the axis down to the head's top row; cut the head 6 px toe-ward of the axis there
    fer = sil.ferrule(s["frames"][f], ax["line"], ax["ridge"][:, 1].min(), ax["head_bottom"] - 4)
    ys, xs = np.nonzero(comp)
    y_mid = 0.5 * (ax["head_top"] + ax["head_bottom"])
    x_cut = a + b * y_mid + 6.0
    head = sil.head_silhouette(comp, x_cut)
    hy, hx = np.nonzero(head)
    toe_x = int(hx.max())
    top_cols = [x for x in np.unique(hx) if x >= toe_x - 26]
    top_y = min(int(hy[hx == x].min()) for x in top_cols)
    bottom_y = int(hy.max())
    return {"diff": diff, "comp": comp, "head": head, "line": ax["line"], "ferrule": fer, "x_cut": x_cut, "toe_x": toe_x + 0.5,
            "toe_y": float(hy[hx == toe_x].mean()), "top_y": top_y - 0.5, "bottom_y": bottom_y + 0.5, "head_top": ax["head_top"]}



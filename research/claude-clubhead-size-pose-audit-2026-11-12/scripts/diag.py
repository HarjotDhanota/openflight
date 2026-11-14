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


# ------------------------------------------------------------------------------------------------ M1
say("M1  GENERIC OUTLINE LEDGER (recomputed from club_outline_reference.json, category iron)")
uv = geom.OUTLINE
ref_json = json.loads((geom.APP / "src/openflight/camera/club_outline_reference.json").read_text())["categories"]["iron"]
centre = json.loads((geom.APP / "src/openflight/camera/face_centre_reference.json").read_text())["categories"]["iron"]
toe_u, sole_v = uv[:, 0].max(), uv[:, 1].min()
x = toe_u - uv[:, 0]          # mm heel-ward of the toe extreme (the kiosk drawing's x)
y = uv[:, 1] - sole_v         # mm above the lowest point, along the face (the kiosk drawing's -y)
say("  points %d; u %.2f .. %.2f, v %.2f .. %.2f -> box %.2f x %.2f mm INCLUDING the hosel stub" % (len(uv), uv[:, 0].min(), uv[:, 0].max(), uv[:, 1].min(), uv[:, 1].max(), np.ptp(uv[:, 0]), np.ptp(uv[:, 1])))
say("  face centre: %.2f mm from the toe extreme, %.2f mm above the lowest point (JSON: %.2f, %.2f)" % (toe_u, -sole_v, centre["centre_from_toe_mm"], centre["centre_above_leading_edge_mm"]))
peak = int(np.argmax(y))
say("  toe peak: %.1f mm above the sole, %.1f mm heel-ward of the toe extreme" % (y[peak], x[peak]))
for at in (60.0, 70.0, 74.0):
    top = max(y[i] for i in range(len(x)) if abs(x[i] - at) < 1.5)
    say("  top line %.0f mm from the toe: %.1f mm above the sole" % (at, top))
top_pts = np.array([(x[i], y[i]) for i in range(len(x)) if 30.5 <= x[i] <= 73.5 and y[i] > 25.0])
slope = np.polyfit(top_pts[:, 0], top_pts[:, 1], 1)[0]
say("  top line slope between 30 and 74 mm from the toe: %.1f deg falling toward the heel (drawing: (30,-56.07) to (74,-35.65) = %.1f deg)" % (math.degrees(math.atan(-slope)), math.degrees(math.atan(20.42 / 44.0))))
foot = ref_json.get("hosel_foot_uv_mm") or None
say("  heel end of the head proper (drawing node 0): 84.87 mm from the toe; blade length quoted by the source table: 79.4 mm")
say("  hosel: axis meets the ground line 78.2 mm from the toe; drawn at 58.4 deg = atan(tan 62.3 x cos 31.4), i.e. the 62.3 deg lie seen ALONG THE FACE NORMAL of a 31.4 deg loft")
say("  outline's heel-most point %.1f mm from the toe at %.1f mm above the sole: the hosel tube's outer edge, 40 mm up the axis" % (x.max(), y[int(np.argmax(x))]))
say("  score lines (not in the outline): longest 53.6 mm, toe margin 17.44 mm")
say("  lineage: ui/src/components/panel/faceImpact.ts IRON.head (svg mm, +x heel, +y down), spline-flattened 12 points per curve -> JSON (commit bf545398, later fillet edits 359eaab6, 466c2e09); source table research/club-models/README.md section 6 (45 7-irons, 10 brands)")
say("  units: one number scale for both axes (mm); no separate x/y factor exists anywhere between the drawing and the projector")
say()

# ------------------------------------------------------------------------------------------------ M2
say("M2  IS THE SUPPLIED ORIENTATION APPLIED TO THE DRAWN MODEL?")
S = {shot: setup(shot) for shot in (1, 3, 4)}
for shot, s in S.items():
    face, loft, lie = s["angles"]
    origin = s["origin_rest"]
    base, _ = geom.model_segments(s["scene"], geom.axes(face, loft, lie), origin, *s["sole"])
    app = s["arrays"]["outline_%d" % s["rest"]]
    say("  shot %d: supplied face %+.2f, loft %.2f, roll %+.2f deg. My projection against the application's own outline pixels: max %.5f px" % (shot, face, loft, lie, np.abs(base - app).max()))
    for label, angles in (("loft set to 0", (face, 0.0, lie)), ("face set to 0", (0.0, loft, lie)), ("roll set to 0", (face, loft, 0.0)), ("all three 0", (0.0, 0.0, 0.0))):
        other, _ = geom.model_segments(s["scene"], geom.axes(*angles), origin, *s["sole"])
        say("     %-14s outline moves by up to %5.2f px (rms %.2f); box %.1f x %.1f px against %.1f x %.1f supplied" % (
            label, np.abs(other - base).max(), math.sqrt(np.mean((other - base) ** 2)), np.ptp(other[:, 0]), np.ptp(other[:, 1]), np.ptp(base[:, 0]), np.ptp(base[:, 1])))
say("  -> the angles ARE applied. Tilting a flat face outline by the loft only squeezes its height by cos(loft): about 10 to 16 %; the picture barely changes.")
say()

# ------------------------------------------------------------------------------------------------ M3
say("M3  RIGID-DESCENT CHECK (model-free; mm at the supplied depth, and in ball diameters)")
record = {}
for shot, s in S.items():
    prep = s["prep"]
    ball_px = prep["ball"]["diameter_px"]
    ball_bottom = prep["ball"]["y"] + ball_px / 2.0
    rows = []
    for f in sorted(set(FRAMES[shot]) | set(EXTRA[shot])):
        obs = observe_frame(s, f)
        if obs is None or obs["ferrule"] is None:
            say("  shot %d frame %d: no shaft/ferrule found" % (shot, f))
            continue
        z = float(origin_at(s, f)[2])
        k = z / s["scene"].focal
        fer = obs["ferrule"]
        rec = {"frame": f, "t_ms": s["times"][f] - prep["contact_ms"], "depth_mm": z, "ferrule": [fer["x"], fer["y"]], "top_y": obs["top_y"], "bottom_y": obs["bottom_y"],
               "toe_x": obs["toe_x"], "ferrule_to_top_mm": (obs["top_y"] - fer["y"]) * k, "ferrule_to_bottom_mm": (obs["bottom_y"] - fer["y"]) * k,
               "height_mm": (obs["bottom_y"] - obs["top_y"]) * k, "height_ball": (obs["bottom_y"] - obs["top_y"]) / ball_px,
               "ferrule_to_bottom_ball": (obs["bottom_y"] - fer["y"]) / ball_px, "ferrule_to_top_ball": (obs["top_y"] - fer["y"]) / ball_px,
               "top_above_ball_bottom_mm": (ball_bottom - obs["top_y"]) * k, "bottom_below_ball_bottom_mm": (obs["bottom_y"] - ball_bottom) * k}
        rows.append(rec)
        say("  shot %d f%d %+6.2f ms  depth %4.0f mm | ferrule row %6.1f | toe-side top row %5.1f | lowest visible row %5.1f | ferrule->top %5.1f mm (%.2f ball) | ferrule->bottom %5.1f mm (%.2f ball) | visible height %4.1f mm (%.2f ball) | top is %5.1f mm above, bottom %4.1f mm below the resting ball's bottom row %.1f" % (
            shot, f, rec["t_ms"], z, fer["y"], obs["top_y"], obs["bottom_y"], rec["ferrule_to_top_mm"], rec["ferrule_to_top_ball"], rec["ferrule_to_bottom_mm"], rec["ferrule_to_bottom_ball"],
            rec["height_mm"], rec["height_ball"], rec["top_above_ball_bottom_mm"], rec["bottom_below_ball_bottom_mm"], ball_bottom))
    record[shot] = rows
    for a, b in zip(rows, rows[1:]):
        d_top, d_bot = b["ferrule_to_top_mm"] - a["ferrule_to_top_mm"], b["ferrule_to_bottom_mm"] - a["ferrule_to_bottom_mm"]
        k = 0.5 * (a["depth_mm"] + b["depth_mm"]) / s["scene"].focal
        drop = (b["ferrule"][1] - a["ferrule"][1]) * k
        verdict = "BOTTOM OCCLUDED on f%d (rule met)" % b["frame"] if abs(d_top) < 4.0 and d_bot < -8.0 else "rule not met"
        say("     f%d -> f%d: ferrule moved down %5.1f mm; ferrule->top changed %+5.1f mm; ferrule->bottom changed %+5.1f mm : %s" % (a["frame"], b["frame"], drop, d_top, d_bot, verdict))
say()

# ------------------------------------------------------------------------------------------------ M4
say("M4  SILHOUETTE REGISTRATION (XOR px^2 right of the hosel; translation re-optimised for every variant)")
VARIANTS = [("supplied", {})]
VARIANTS += [("size %.2f" % v, {"size": v}) for v in (0.85, 0.90, 0.95, 1.05, 1.10, 1.15)]
VARIANTS += [("face %+d" % v, {"face": v}) for v in (-10, 10, 20, 30)]
VARIANTS += [("loft %+d" % v, {"loft": v}) for v in (-10, 10, 20)]
VARIANTS += [("roll %+d" % v, {"roll": v}) for v in (-6, -3, 3, 6)]
reg = {}
for shot, s in S.items():
    reg[shot] = {}
    for kind, f in zip(("IN-AIR", "NEAR"), FRAMES[shot]):
        for floor in (8.0, 5.0, 12.0):
            obs = observe_frame(s, f, floor)
            origin = origin_at(s, f)
            shape = s["frames"][f].shape
            obs_up = sil.upsample(obs["head"])
            window = np.zeros(shape, dtype=bool)
            window[:, int(math.ceil(obs["x_cut"])):] = True
            visible = window.copy()
            visible[int(round(obs["bottom_y"] + 0.5)):, :] = False
            w_full, w_vis = sil.upsample(window), sil.upsample(visible)
            face, loft, lie = s["angles"]
            table = {}
            for name, change in (VARIANTS if floor == 8.0 else VARIANTS[:1]):
                ax3 = geom.axes(face + change.get("face", 0.0), loft + change.get("loft", 0.0), lie + change.get("roll", 0.0))
                size = change.get("size", 1.0)
                # keep the face centre where the supplied pose has it; scale and angles act about it
                mask, outline = sil.model_mask(s["scene"], ax3, origin, shape, *s["sole"], size=size)
                row = {"app_pose": sil.score(mask, obs_up, w_full), "app_pose_visible": sil.score(mask, obs_up, w_vis)}
                for wname, w in (("full", w_full), ("visible", w_vis)):
                    cost, dx, dy = sil.register(mask, obs_up, w)
                    moved = sil.shift(mask, int(round(dx * sil.SS)), int(round(dy * sil.SS)))
                    sc = sil.score(moved, obs_up, w)
                    sc.update({"dx": dx, "dy": dy, "model_bottom_y": float(outline[:, 1].max() + dy), "model_top_y": float(outline[:, 1].min() + dy), "model_toe_x": float(outline[:, 0].max() + dx)})
                    row[wname] = sc
                table[name] = row
            reg[shot]["%s f%d floor %d" % (kind, f, floor)] = {"obs": {k: obs[k] for k in ("x_cut", "toe_x", "top_y", "bottom_y")}, "line": list(obs["line"]), "table": table,
                                                               "depth_mm": float(origin[2])}
            base = table["supplied"]
            k = float(origin[2]) / s["scene"].focal
            if floor == 8.0:
                say("  shot %d %s f%d (depth %.0f mm, %.2f mm/px), observed head %d px^2, rows %.1f .. %.1f, toe x %.1f" % (shot, kind, f, origin[2], k, base["full"]["observed"], obs["top_y"], obs["bottom_y"], obs["toe_x"]))
                if kind == "NEAR":
                    a = base["app_pose"]
                    say("     application's own pose:        XOR %4.0f  overhang %4.0f (%2.0f %% of the model is not on the head)  uncovered %4.0f  IoU %.2f" % (a["xor"], a["overhang"], 100 * a["overhang"] / max(a["model"], 1), a["uncovered"], a["iou"]))
                for wname in ("full", "visible"):
                    b = base[wname]
                    say("     translation only, %-7s window: XOR %4.0f  overhang %4.0f  uncovered %4.0f  IoU %.2f | shift %+5.2f, %+5.2f px | model top row %.1f (observed %.1f), model bottom row %.1f (lowest visible %.1f: %+.1f mm), toe x %.1f (observed %.1f)" % (
                        wname, b["xor"], b["overhang"], b["uncovered"], b["iou"], b["dx"], b["dy"], b["model_top_y"], obs["top_y"], b["model_bottom_y"], obs["bottom_y"], (b["model_bottom_y"] - obs["bottom_y"]) * k, b["model_toe_x"], obs["toe_x"]))
                wname = "full" if kind == "IN-AIR" else "visible"
                ranked = sorted(table.items(), key=lambda kv: kv[1][wname]["xor"])
                say("     variants, %s window, XOR (change against supplied): " % wname + "; ".join("%s %.0f (%+.0f %%)" % (n, r[wname]["xor"], 100 * (r[wname]["xor"] / base[wname]["xor"] - 1)) for n, r in ranked[:8]))
                say("     size sweep, %s window: " % wname + "; ".join("%s %.0f" % (n, table[n][wname]["xor"]) for n in ["size 0.85", "size 0.90", "size 0.95", "supplied", "size 1.05", "size 1.10", "size 1.15"]))
            else:
                b = base["full" if kind == "IN-AIR" else "visible"]
                say("     floor %2d DN: translation only XOR %4.0f IoU %.2f shift %+5.2f, %+5.2f px; observed rows %.1f .. %.1f" % (floor, b["xor"], b["iou"], b["dx"], b["dy"], obs["top_y"], obs["bottom_y"]))
say()

# ------------------------------------------------------------------------------------------------ M5
say("M5  SHAFT AXIS AFTER REGISTRATION (observed ridge line minus the model's axis, at the head's top row; + = observed toe-ward of the model)")
for shot, s in S.items():
    face, loft, lie = s["angles"]
    for kind, f in zip(("IN-AIR", "NEAR"), FRAMES[shot]):
        entry = reg[shot]["%s f%d floor 8" % (kind, f)]
        b = entry["table"]["supplied"]["full" if kind == "IN-AIR" else "visible"]
        origin = origin_at(s, f)
        ax3 = geom.axes(face, loft, lie)
        line = geom.shaft_line(s["scene"], ax3, origin, loft, s["foot"]) + np.array([b["dx"], b["dy"]])
        a_obs, b_obs = entry["line"][0], entry["line"][1]
        yq = entry["obs"]["top_y"]
        t = (yq - line[0, 1]) / (line[1, 1] - line[0, 1])
        x_model = line[0, 0] + t * (line[1, 0] - line[0, 0])
        x_obs = a_obs + b_obs * yq
        k = entry["depth_mm"] / s["scene"].focal
        ang_obs = math.degrees(math.atan2(1.0, -b_obs)) if b_obs else 90.0
        ang_model = math.degrees(math.atan2(-(line[1, 1] - line[0, 1]), -(line[1, 0] - line[0, 0])))
        say("  shot %d %s f%d: observed axis %.1f deg from horizontal, model %.1f deg; offset at row %.1f: %+.1f px = %+.1f mm (after the registration shift)" % (shot, kind, f, ang_obs, ang_model, yq, x_obs - x_model, (x_obs - x_model) * k))
say("  3-D note (deduction): the drawing places the shaft point of true height h at face height h*cos(31.4); the projector then lowers it again by cos(dynamic loft).")
for loft in (21.6, 26.7, 32.9):
    h = 31.5
    v = 35.2  # the hosel foot's face height above the sole, mm
    true_x = 78.2 + (v * math.cos(math.radians(loft))) / math.tan(math.radians(62.3))
    say("     dynamic loft %.1f: the model's hosel foot (99.9 mm from the toe) stands %.1f mm above the sole; the real axis is there %.1f mm from the toe -> the model's axis is %.1f mm too far heel-ward" % (
        loft, v * math.cos(math.radians(loft)), true_x, 99.9 - true_x))

(AUDIT / "results" / "DIAG.txt").write_text("\n".join(OUT) + "\n")
(AUDIT / "results" / "diag.json").write_text(json.dumps({"m3": record, "m4": reg}, indent=1, default=float))

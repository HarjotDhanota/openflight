"""PROTOCOL addendum 9, T1 and T2: the two measured inputs of the D-plane step, checked on shots 1, 3 and 4.

T1  ball launch from the ball's own track, the radar reading taken as the line-of-sight component (ball_track_lib,
    speed_datum="radial"), beside the old "full speed" treatment and the pixels-only fit.
T2  the club's descent on every frame before contact that carries it: the ferrule (sub-pixel, clear of the turf), the
    lowest visible row of the club on the shaft axis, and the application's own tracked point (_clubhead_anchor,
    imported read-only). Chords, and the quadratic tangent (the application's own slope_with_sigma) at the last frame
    and at mid-contact.
Angles are given against the camera axis (which the placement treats as level) and against the horizon, with the
camera pitch inferred from the resting ball the application's way.
Read-only on the application and the captures. Writes results/DPLANE-INPUTS.txt and results/dplane-inputs.json.
Usage: dplane_inputs.py"""
import json
import math
import sys
from types import SimpleNamespace

import numpy as np

import ball_track_lib as btl
import diag_lib as dl
import sil
from common import AUDIT

sys.path.insert(0, "F:/Coding/OpenFlight/openflight-integration/src")
from openflight.camera import club_delivery as cd  # noqa: E402  (read-only use of two functions)

OUT = []
LENS_MM, BALL_MM, MAT_MM = 95.0, 21.35, 25.4
ARC_TURN_DEG_PER_MS = 1.3


def say(t=""):
    OUT.append(t)
    print(t)


def ferrule_subpixel(frame, line, y_from, y_to):
    """sil.ferrule's dark gap on the shaft axis, its two ends interpolated at the half level (sub-pixel centre)."""
    a, b = line[0], line[1]
    ys = np.arange(int(y_from), int(y_to) + 1)
    if len(ys) < 12:
        return None
    prof = []
    for y in ys:
        x = a + b * y
        xs = np.clip(np.round(np.array([x - 1.5, x - 0.5, x + 0.5, x + 1.5])).astype(int), 0, frame.shape[1] - 1)
        prof.append(float(frame[y, xs].max()))
    prof = np.asarray(prof)
    level = 0.5 * float(np.median(prof[: max(6, len(prof) // 2)]))
    dark = prof < level
    start = None
    for i in range(4, len(prof)):
        if dark[i] and start is None:
            start = i
        elif start is not None and not dark[i]:
            top = ys[start - 1] + (prof[start - 1] - level) / (prof[start - 1] - prof[start])
            bot = ys[i - 1] + (level - prof[i - 1]) / (prof[i] - prof[i - 1])
            y = 0.5 * (top + bot)
            return {"x": float(a + b * y), "y": float(y), "length": float(bot - top)}
    return None


def observe(s, f, ball):
    frame = s["frames"][f]
    diff, mask = sil.changed(frame, s["ref"], s["sigma"], 8.0)
    comp = sil.head_component(mask)
    if comp is None:
        return None
    ax = sil.shaft_axis(diff, comp)
    if ax is None or ax["line"] is None or len(ax["ridge"]) < 6:
        return None
    line = ax["line"]
    fer = ferrule_subpixel(frame, line, ax["ridge"][:, 1].min(), ax["head_bottom"] - 4)
    if fer is None:
        return None
    # lowest changed row of the club on the shaft axis (within +-5 px of it), below the ferrule
    low = None
    for y in range(int(fer["y"]), comp.shape[0]):
        x = int(round(line[0] + line[1] * y))
        if comp[y, max(x - 5, 0):x + 6].any():
            low = y
        elif low is not None and y - low > 3:
            break
    out = {"ferrule": fer, "axis_low": None if low is None else {"x": float(line[0] + line[1] * (low + 0.5)), "y": low + 0.5}, "slope": float(line[1]), "app": None, "app_note": None}
    t_before = (s["prep"]["contact_ms"] - s["times"][f]) / 1000.0
    try:
        anchor, refusal = cd._clubhead_anchor(frame, np.clip(np.round(s["ref"]), 0, 255).astype(np.uint8), ball, reach_m=cd.approach_reach_m(t_before, s["d"]["club_speed_mps"]))
    except Exception as exc:  # the application's finder is run outside its own pipeline here; a failure is a result
        anchor, refusal = None, "%s: %s" % (type(exc).__name__, exc)
    if anchor is not None:
        out["app"] = {"x": float(anchor.point_px[0]), "y": float(anchor.point_px[1])}
        out["app_note"] = anchor.corner_status
    else:
        out["app_note"] = refusal
    return out


def to_mm(s, f, pt):
    """Level-camera coordinates (x right, y down, z forward) of an image point at the application's depth for frame f."""
    z = float(dl.origin_at(s, f)[2])
    sc = s["scene"]
    return np.array([(pt["x"] - sc.centre[0]) * z / sc.focal, (pt["y"] - sc.centre[1]) * z / sc.focal, z])


def angle(v):
    return math.degrees(math.atan2(-v[1], math.hypot(v[0], v[2])))


def tangent(times_ms, pts, at_ms, degree=2):
    xyz = np.column_stack([pts[:, 0], -pts[:, 1], pts[:, 2]]) / 1000.0
    return cd.slope_with_sigma(np.asarray(times_ms) / 1000.0, xyz, at_s=at_ms / 1000.0, degree=degree, position_sigma_m=0.001)


record = {}
for n in (1, 3, 4):
    s = dl.setup(n)
    prep = s["prep"]
    state = s["state"]
    cm = state["collision_model"]["inputs"]
    P0 = s["scene"].ball
    rng = float(np.linalg.norm(P0))
    beta = math.degrees(math.atan2(P0[1], math.hypot(P0[0], P0[2])))
    pitch = math.degrees(math.asin((LENS_MM - BALL_MM) / rng)) - beta
    pitch_mat = math.degrees(math.asin((LENS_MM - BALL_MM - MAT_MM) / rng)) - beta
    say("SHOT %d   resting ball %.0f mm from the lens, %.2f deg below the assumed axis" % (n, rng, beta))
    say("   camera pitch inferred from the resting ball: %.2f deg down (ball on the unit's own surface) or %.2f deg (ball 25.4 mm higher: the mat). Angles against the horizon = angles against the camera axis minus this." % (pitch, pitch_mat))
    # ---------------- T1
    say("   T1 BALL LAUNCH (application gave the D-plane step %.2f deg, source %s)" % (cm["launch_vertical"]["value"], cm["launch_vertical"]["source"]))
    fits = {}
    for label, kw in (("radar as FULL speed (old)", {"speed_datum": "total"}), ("radar as line-of-sight speed, ball at the tee", {"speed_datum": "radial", "los_after_ms": 0.0}),
                      ("radar as line-of-sight speed, ball 6 ms out", {"speed_datum": "radial", "los_after_ms": 6.0})):
        tr = btl.track(s, n, verbose=lambda *_a: None, **kw)
        fz = tr["fits"]["pixels + radar"]
        fits[label] = fz
        say("      %-46s launch %5.1f deg above the camera axis = %5.1f above the horizon (%.1f with the mat); %4.1f deg right; full speed %.1f m/s (radar %.1f); mid-contact %+.2f ms; worst pixel %.2f" % (
            label, fz["launch_deg"], fz["launch_deg"] - pitch, fz["launch_deg"] - pitch_mat, fz["azimuth_deg"], fz["speed"], tr["radar_speed"], fz["t_mid"], fz["worst_px"]))
    fz = tr["fits"]["pixels only"]
    say("      %-46s launch %5.1f deg above the camera axis; full speed %.1f m/s; mid-contact %+.2f ms" % ("pixels only (no radar)", fz["launch_deg"], fz["speed"], fz["t_mid"]))
    main = fits["radar as line-of-sight speed, ball at the tee"]
    t_mid = prep["contact_ms"] + main["t_mid"]
    # ---------------- T2
    ball = SimpleNamespace(x=prep["ball"]["x"], y=prep["ball"]["y"], diameter_px=prep["ball"]["diameter_px"])
    rest = s["rest"]
    obs = {}
    for f in range(rest - 5, rest + 1):
        try:
            o = observe(s, f, ball)
        except Exception as exc:
            o = None
            say("      f%d: not observable (%s)" % (f, type(exc).__name__))
        if o is not None:
            obs[f] = o
    say("   T2 CLUB DESCENT (application gave the D-plane step %+.2f deg; it reads a quadratic tangent at its last tracked frame)" % cm["attack_angle"]["value"])
    say("      frame   ms to mid-contact | ferrule x, y px | lowest row on the axis | application's tracked point (status)")
    for f, o in obs.items():
        say("      f%d   %7.2f           | %6.2f %6.2f   | %s                 | %s" % (f, s["times"][f] - t_mid, o["ferrule"]["x"], o["ferrule"]["y"],
            "  -   " if o["axis_low"] is None else "%6.1f" % o["axis_low"]["y"], "refused: %s" % o["app_note"] if o["app"] is None else "%6.2f %6.2f (%s)" % (o["app"]["x"], o["app"]["y"], o["app_note"])))
    fs = sorted(obs)
    series = {}
    for key in ("ferrule", "axis_low", "app"):
        ok = [f for f in fs if obs[f][key] is not None]
        # keep the run of consecutive frames ending at the last frame before contact
        run = []
        for f in reversed(ok):
            if not run or run[-1] - f == 1:
                run.append(f)
            else:
                break
        run = sorted(run)
        if len(run) >= 2:
            series[key] = (run, np.array([to_mm(s, f, obs[f][key]) for f in run]))
    names = {"ferrule": "ferrule", "axis_low": "lowest visible row", "app": "application's point"}
    summary = {}
    for key, (run, pts) in series.items():
        t = np.array([s["times"][f] for f in run])
        drops = np.diff(pts[:, 1])
        chords = [angle(pts[i + 1] - pts[i]) for i in range(len(run) - 1)]
        line = "      %-20s frames %d-%d | drop per interval mm: %s | chord deg: %s" % (names[key], run[0], run[-1], " ".join("%5.1f" % d for d in drops), " ".join("%+5.1f" % c for c in chords))
        entry = {"frames": run, "drop_mm": drops.tolist(), "chord_deg": chords}
        if len(run) >= 3:
            last = tangent(t, pts, t[-1])
            mid = tangent(t, pts, t_mid)
            first = tangent(t, pts, t[0])
            turn = (last[0] - first[0]) / (t[-1] - t[0])
            lin = tangent(t, pts, t[-1], degree=1)
            line += " | quadratic tangent at last frame %+.1f, at mid-contact %+.1f (+-%.1f); turn %+.2f deg/ms; straight line %+.1f" % (last[0], mid[0], mid[1], turn, lin[0])
            entry.update({"tangent_last": last[0], "tangent_mid": mid[0], "tangent_mid_sigma": mid[1], "turn_deg_per_ms": turn, "line_deg": lin[0]})
        say(line)
        summary[key] = entry
    # the rule on the application's tracked point
    verdict = "not evaluable (a series is missing)"
    if "ferrule" in series and "app" in series:
        fr_run, fr_pts = series["ferrule"]
        ap_run, ap_pts = series["app"]
        common_f = [f for f in fr_run if f in ap_run]
        if len(common_f) >= 2:
            d_f = np.diff([fr_pts[fr_run.index(f)][1] for f in common_f])
            d_a = np.diff([ap_pts[ap_run.index(f)][1] for f in common_f])
            short = d_f - d_a
            say("      application's point drops LESS than the ferrule by (mm per interval, frames %d-%d): %s" % (common_f[0], common_f[-1], " ".join("%+5.1f" % v for v in short)))
            earlier_ok = len(short) >= 2 and bool(np.all(np.abs(short[:-1]) <= 2.0))
            verdict = "turf-line stall on the last interval: %s (last %.1f mm, rule >= 4; earlier intervals within 2 mm: %s)" % ("YES" if short[-1] >= 4.0 and earlier_ok else "NO", short[-1], earlier_ok if len(short) >= 2 else "no earlier interval")
            summary["stall_mm"] = short.tolist()
    say("      RULE: " + verdict)
    # adopted attack
    fe = summary.get("ferrule", {})
    if len(fe.get("frames", [])) >= 4 and 0.0 <= fe["turn_deg_per_ms"] <= 3.0:  # levelling = the angle rising with time
        attack_cam, how = fe["tangent_mid"], "ferrule quadratic tangent at mid-contact (%d frames, turn %+.2f deg/ms)" % (len(fe["frames"]), fe["turn_deg_per_ms"])
    elif fe.get("chord_deg"):
        run = fe["frames"]
        t_chord = 0.5 * (s["times"][run[-1]] + s["times"][run[-2]])
        attack_cam = fe["chord_deg"][-1] + ARC_TURN_DEG_PER_MS * (t_mid - t_chord)
        how = "ferrule last chord %+.1f moved %.2f ms to contact at %.1f deg/ms (fallback: %s)" % (fe["chord_deg"][-1], t_mid - t_chord, ARC_TURN_DEG_PER_MS,
            "fewer than 4 frames" if len(run) < 4 else "fitted turn %+.2f deg/ms outside 0..3" % fe["turn_deg_per_ms"])
    else:
        attack_cam, how = float("nan"), "no ferrule series"
    say("      ADOPTED attack: %+.1f deg against the camera axis = %+.1f against the horizon (%+.1f with the mat) -- %s" % (attack_cam, attack_cam - pitch, attack_cam - pitch_mat, how))
    say()
    record[str(n)] = {"pitch_deg": pitch, "pitch_mat_deg": pitch_mat, "launch_cam_deg": main["launch_deg"], "azimuth_cam_deg": main["azimuth_deg"], "t_mid_ms": main["t_mid"], "speed": main["speed"],
                      "launch_old_cam_deg": fits["radar as FULL speed (old)"]["launch_deg"], "t_mid_old_ms": fits["radar as FULL speed (old)"]["t_mid"],
                      "launch_6ms_cam_deg": fits["radar as line-of-sight speed, ball 6 ms out"]["launch_deg"],
                      "attack_cam_deg": attack_cam, "attack_how": how, "series": summary,
                      "app": {k: cm[k]["value"] for k in ("club_path", "attack_angle", "launch_horizontal", "launch_vertical")}, "app_launch_source": cm["launch_vertical"]["source"]}
(AUDIT / "results" / "DPLANE-INPUTS.txt").write_text("\n".join(OUT) + "\n")
(AUDIT / "results" / "dplane-inputs.json").write_text(json.dumps(record, indent=1, default=float))

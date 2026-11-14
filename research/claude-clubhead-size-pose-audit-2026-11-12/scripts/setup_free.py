"""PROTOCOL addendum 10: the inputs of the research pipeline WITHOUT the application's placement stage, so a shot the
application never placed can be run. Same dict as diag_lib.setup(), built from the capture's frames and the upstream
part of the unmodified replay (results/shotN/replay.json):
   resting ball and lens distance   replay full_shot.camera_fusion_context (setup box)
   frame times                      capture sensor timestamps
   crop                             ball -170..+205 px across, -100..+145 px down (clipped to the frame)
   last rest frame                  the last frame on which the ball still measures at rest (see the note in build())
   club speed, path, attack         replay metrics (used for depth progression only)
   face, loft, roll                 replay metrics: face - aim offset, dynamic loft, roll = 62.30 - dynamic lie
Read-only."""
import json
import math

import numpy as np

import ball_flight_lib as bf
import common
import diag_lib as dl
import geom
import observe
from common import AUDIT

CROP = (-170, 205, -100, 145)
FOCAL = 1000.0001
RISE_PX = 1.5


def build(shot, say=lambda *_a: None, use_track_bracket=True):
    replay = json.loads((AUDIT / "results" / ("shot%d" % shot) / "replay.json").read_text())
    fs = replay["stages"]["full_shot"]
    ctx, m = fs["camera_fusion_context"], fs["metrics"]
    box = ctx["box_resting_ball"]
    lens_mm = 1000.0 * ctx["ball_range"]["details"]["lens_distance_m"]
    yaw = ctx["geometry"]["parameters"]["ball_horizontal_output_offset_deg"]
    full, ts = common.frames(shot)
    h, w = full.shape[1:]
    x0, x1 = max(0, int(round(box["x_px"])) + CROP[0]), min(w, int(round(box["x_px"])) + CROP[1])
    y0, y1 = max(0, int(round(box["y_px"])) + CROP[2]), min(h, int(round(box["y_px"])) + CROP[3])
    frames = full[:, y0:y1, x0:x1]
    times = ((ts - ts[0]) / 1e6).tolist()
    ref, sigma = observe.reference(frames)
    ball = {"x": box["x_px"] - x0, "y": box["y_px"] - y0, "diameter_px": box["diameter_px"]}
    ball0, r0 = np.array([ball["x"], ball["y"]]), ball["diameter_px"] / 2.0
    # last rest frame: the ball's top edge against the reference image, same routine and arc
    # Rule as first written (first frame that is unmeasurable or risen) failed on shots 1, 3, 4: the club covers the
    # ball on a frame or two BEFORE contact, and the (15,165) arc does not fit shot 1's resting ball. Fixed on those
    # shots, before shot 2 was run: the last frame on which the ball still measures at rest (arc -10..190, top edge
    # within 1.5 px and centre within 3 px across of the reference image's) is the last rest frame.
    arc = (-10.0, 190.0)
    rest_fit = bf.measure(ref, ball0, r0, arc)
    if rest_fit is None:
        raise RuntimeError("resting ball not measurable on the reference image")
    top0 = rest_fit["centre"][1] - rest_fit["radius"]
    rest, log = None, []
    for f in range(31, len(frames)):
        mm = bf.measure(frames[f], ball0, r0, arc)
        rise = None if mm is None else float(top0 - (mm["centre"][1] - mm["radius"]))
        dx = None if mm is None else float(mm["centre"][0] - rest_fit["centre"][0])
        log.append((f, rise, dx))
        if mm is not None and abs(rise) <= RISE_PX and abs(dx) <= 3.0:
            rest = f
    if rest is None or rest + 1 >= len(frames):
        raise RuntimeError("no last rest frame found")
    say("   setup_free: ball (top-edge rise, sideways px) by frame: %s -> last frame measured at rest %d" % (
        " ".join("f%d %s" % (f, "n/a" if r is None else "%+.1f,%+.1f" % (r, x)) for f, r, x in log if rest - 3 <= f <= rest + 2), rest))
    centre = [w / 2.0 - 0.5 - x0, h / 2.0 - 0.5 - y0]
    scene = geom.Scene(FOCAL, centre, (ball["x"], ball["y"]), lens_mm)
    # PROTOCOL note 10b (amendment): the head can cover the resting ball on its last frames. The first moved frame is
    # the earliest fc for which the ball's own track puts mid-contact between frame fc-1 and frame fc.
    import ball_track_lib as btl
    if not use_track_bracket:
        first_moved = rest + 1
    else:
        first_moved = None
        for fc in range(rest + 1, min(rest + 5, len(frames) - 2)):
            ref_ms = 0.5 * (times[fc - 1] + times[fc])
            trial = {"prep": {"ball": ball, "frame_time_ms": times, "contact_ms": ref_ms}, "scene": scene, "d": {"bracket_frames": [fc - 1, fc]}, "frames": frames, "ref": ref}
            tr = btl.track(trial, shot, verbose=lambda *_a: None, speed_datum="radial", los_after_ms=0.0)
            if tr is None:
                say("   setup_free: first moved frame %d? no ball track" % fc)
                continue
            fz = tr["fits"]["pixels + radar"]
            t_abs = ref_ms + fz["t_mid"]
            # 1.5 px, not the 1 px first written in note 10b: shot 1's own track through the setup box's ball is 1.05 px
            ok = fz["worst_px"] < 1.5 and times[fc - 1] - 0.2 <= t_abs <= times[fc] + 0.2
            say("   setup_free: first moved frame %d? ball seen on %s; mid-contact %.2f ms after frame %d (must be 0..%.2f); worst pixel %.2f -> %s" % (
                fc, tr["frames"], t_abs - times[fc - 1], fc - 1, times[fc] - times[fc - 1], fz["worst_px"], "yes" if ok else "no"))
            if ok:
                first_moved = fc
                break
        if first_moved is None:
            raise RuntimeError("no first moved frame whose ball track puts mid-contact inside its own bracket")
        rest = first_moved - 1
    speed = m["club_speed"]["value"] * 0.44704
    path, attack = m["club_path"]["value"] - yaw, m["attack_angle"]["value"]
    angles = (m["face_angle"]["value"] - yaw, m["dynamic_loft"]["value"], geom.STATIC_LIE - m["dynamic_lie"]["value"])
    p, a = math.radians(path), math.radians(attack)
    v3 = speed * np.array([math.sin(p) * math.cos(a), -math.sin(a), math.cos(p) * math.cos(a)])
    contact_ms = 0.5 * (times[rest] + times[first_moved])  # a reference instant only; the ball track finds mid-contact
    toe, up, normal = geom.axes(*angles)
    origin_rest = scene.ball - geom.BALL_RADIUS_MM * normal - v3 * (contact_ms - times[rest])
    prep = {"camera": {"focal_px": FOCAL, "principal_point_px": centre}, "ball": ball, "ball_range_mm": lens_mm, "frame_time_ms": times, "contact_ms": contact_ms,
            "roi_origin": [x0, y0]}
    d = {"bracket_frames": [rest, first_moved], "club_speed_mps": speed, "club_path": {"value": path}, "attack_angle": {"value": attack}}
    s = {"state": {"replay_metrics": {k: m[k]["value"] for k in ("club_path", "attack_angle", "launch_horizontal", "launch_vertical", "face_angle", "dynamic_loft", "dynamic_lie")}, "yaw": yaw},
         "arrays": None, "d": d, "prep": prep, "angles": angles, "scene": scene, "rest": rest, "times": times, "v3": v3, "origin_rest": origin_rest,
         "frames": frames, "ref": ref, "sigma": sigma, "sole": (19.9, 3.0), "foot": None}
    return s


def observations(s):
    """(B, C): dl.observe_frame on the last rest frame and the first frame after; None where it fails."""
    out = []
    for f in s["d"]["bracket_frames"]:
        try:
            out.append(dl.observe_frame(s, f))
        except Exception as exc:  # reported by the caller; never silently replaced
            out.append(None)
            print("   dl.observe_frame failed on f%d: %s: %s" % (f, type(exc).__name__, exc))
    return out

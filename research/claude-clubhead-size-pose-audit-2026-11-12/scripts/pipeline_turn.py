"""TS4 model at its published dimensions, with the head's TURN solved from the pixels. Research reading, development shots.

Model: results/ts4-model.json as built: photo outline at true size, shaft axis 80.1 mm from the toe (spec blade length),
lie 62.5, static loft 33, offset 0.100 in. Nothing is taken from shot 1 or from a trace.
Per frame, three unknowns, three sharp observations, none of them under the turf or the thrown-up material:
   across the shaft   the shaft line
   along the shaft    the TOP OF THE HOSEL (lower end of the dark ferrule gap). Its distance up the axis is MEASURED on
                      each shot's own clear-air frame, where the whole head is visible and placed by its height centre.
                      (First run used the spec's 54.8 mm: the model then sat 4 to 5 px high in clear air. Kept in
                      the conversation, superseded here.)
   turn (face angle)  the toe tip: the model's toe extreme must sit on the observed sharp toe
Loft and roll stay as supplied. The open branch (face open to the camera) is taken; a closed head narrows the view
the same way and is not excluded by this.
Frames: the clear-air frame, the last frame before contact (both give a turn, so a closing rate), and the first frame
after (shaft and hosel only; its turn is set to the contact value). Contact instant from the ball's track. Depth as
before: the face touches the ball at mid-contact; other frames by club speed x time.
Two readings: turn carried to contact at the measured closing rate, and turn held at the last frame's value.
Writes results/PIPELINE-TURN.txt, results/pipeline-turn.json, figures/turn-shot<N>.png. Usage: pipeline_turn.py"""
import json
import math
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import MultipleLocator
from scipy.optimize import least_squares

import ball_track_lib as btl
import carry_lib as cl
import diag_lib as dl
import geom
import rig
from common import AUDIT

OUT = []


def say(t=""):
    OUT.append(t)
    print(t)


SPEC = rig.ts4()
# which along-shaft datum: "spec" = the published hosel length on every frame; "measured" = read on each clear-air frame
ANCHOR = os.environ.get("HOSEL_ANCHOR", "spec")
HOSEL_TOP = json.loads((AUDIT / "results" / "ts4-model.json").read_text())["hosel_length"]
MARKS = json.loads((AUDIT / "results" / "photo-marks.json").read_text())
TRACE = np.asarray(json.loads((AUDIT / "owner-trace" / "shot1-f43-trace.json").read_text())["polygon_native_px"])
say("ALONG-SHAFT DATUM: %s" % ("the spec hosel length on every frame" if ANCHOR == "spec" else "measured on each shot's clear-air frame"))
say("TS4 model: shaft axis %.1f mm from the toe, hosel top %.1f mm up the axis, lie %.1f, static loft %.1f; toe extreme %.1f mm from the face centre, leading edge %.1f mm below it" % (
    SPEC.axis_ground_from_toe, HOSEL_TOP, SPEC.static_lie, SPEC.static_loft, SPEC.toe_u, -SPEC.le_v))


def on_ray(point, depth):
    return point * (depth / point[2])


def col_extent(polys, x):
    """(top, bottom) rows of a set of polygons at column x."""
    ys = []
    for poly in polys:
        a, b = poly, np.roll(poly, -1, axis=0)
        hit = ((a[:, 0] <= x) & (b[:, 0] > x)) | ((b[:, 0] <= x) & (a[:, 0] > x))
        if hit.any():
            tt = (x - a[hit, 0]) / (b[hit, 0] - a[hit, 0])
            ys.extend((a[hit, 1] + tt * (b[hit, 1] - a[hit, 1])).tolist())
    return (min(ys), max(ys)) if ys else (math.nan, math.nan)


def solve_frame(s, club, f, obs, toe_x, depth, angles, face_fixed=None, hosel_top=None):
    """(origin, face angle, residuals) from the shaft line, an along-shaft datum and (unless face_fixed) the toe tip.

    Along the shaft: the hosel top at `hosel_top` mm up the axis; or, when hosel_top is None (clear-air frame, whole
    head visible), the middle of the head's height over the toe-side columns, which a blurred fringe does not bias."""
    scene = s["scene"]
    base = on_ray(dl.origin_at(s, f), depth)
    a, b = obs["line"][0], obs["line"][1]
    fy = obs["ferrule"]["y"]
    y_top = obs["ferrule"]["rows"][1] + 0.5
    rows = np.array([fy - 16.0, fy - 6.0, fy + 5.0])
    ex, ey = np.array([1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0])
    cols, obs_mid = [], {}
    if hosel_top is None:
        head = obs["head"]
        for x in range(int(toe_x) - 24, int(toe_x) - 5):
            ys = np.nonzero(head[:, x])[0]
            if len(ys):
                cols.append(x)
                obs_mid[x] = 0.5 * (ys.min() - 0.5 + ys.max() + 0.5)

    def res(p):
        face = face_fixed if face_fixed is not None else p[2]
        ang = (face, angles[1], angles[2])
        origin = base + p[0] * ex + p[1] * ey
        _polys, axis, lead = club.parts(ang, origin)
        tt = (rows - axis[0, 1]) / (axis[1, 1] - axis[0, 1])
        r_axis = (a + b * rows - (axis[0, 0] + tt * (axis[1, 0] - axis[0, 0]))) / math.hypot(1.0, b)
        if hosel_top is not None:
            r_top = scene.project(club.shaft_point(ang, origin, hosel_top))[0][1] - y_top
        else:
            polys = _polys[:-2]
            mids = []
            for x in cols:
                top_m, bot_m = col_extent(polys, float(x))
                if np.isfinite(top_m):
                    mids.append(0.5 * (top_m + bot_m) - obs_mid[x])
            r_top = float(np.mean(mids)) if mids else 5.0
        out = list(r_axis) + [r_top]
        if face_fixed is None:
            out.append(float(lead[:, 0].max()) - toe_x)
        return np.asarray(out)

    if face_fixed is None:
        sol = least_squares(res, [0.0, 0.0, max(angles[0], 0.0) + 12.0], bounds=([-150.0, -150.0, 0.0], [150.0, 150.0, 55.0]), x_scale=[5.0, 5.0, 5.0])
        face = float(sol.x[2])
    else:
        sol = least_squares(res, [0.0, 0.0], x_scale=5.0)
        face = face_fixed
    r = res(sol.x)
    return base + sol.x[0] * ex + sol.x[1] * ey, face, r


record = {}
for n in (1, 3, 4):
    s = dl.setup(n)
    scene, prep, angles = s["scene"], s["prep"], s["angles"]
    times, t_app = prep["frame_time_ms"], prep["contact_ms"]
    fr, fc = s["d"]["bracket_frames"]
    shape = s["frames"][fr].shape
    c = cl.carry(n)
    A, B = c["A"], c["B"]
    fa = c["frames"][0]
    C = dl.observe_frame(s, fc)
    vz = float(s["v3"][2])
    club = rig.Club(scene, SPEC, shape)
    say()
    say("SHOT %d  (supplied face %+.1f, loft %.1f, roll %+.1f)" % (n, *angles))
    track = btl.track(s, n, verbose=lambda *_a: None)
    t_mid = track["fits"]["pixels + radar"]["t_mid"]
    ta, tr, tc = (times[f] - t_app - t_mid for f in (fa, fr, fc))        # ms relative to mid-contact
    say("   mid-contact %+.2f ms from the application's instant; frames %d, %d, %d at %+.2f, %+.2f, %+.2f ms" % (t_mid, fa, fr, fc, ta, tr, tc))
    za, zr, zc = (float(dl.origin_at(s, f)[2]) for f in (fa, fr, fc))
    out = {}
    for mode in ("rate", "held"):
        for _ in range(2):
            o_a, face_a, res_a = solve_frame(s, club, fa, A, A["toe_x"], za, angles, hosel_top=HOSEL_TOP if ANCHOR == "spec" else None)
            # this club's hosel-top distance up the axis, read on the clear-air frame where the whole head is visible
            ang_a = (face_a, angles[1], angles[2])
            ts_ = np.linspace(30.0, 90.0, 2401)
            p0_, p1_ = club.shaft_point(ang_a, o_a, 0.0), club.shaft_point(ang_a, o_a, 1.0)
            rows_ = scene.project(p0_ + ts_[:, None] * (p1_ - p0_))[:, 1]
            hosel = float(ts_[int(np.argmin(np.abs(rows_ - (A["ferrule"]["rows"][1] + 0.5))))])
            if ANCHOR == "spec":
                hosel = HOSEL_TOP
            o_r, face_r, res_r = solve_frame(s, club, fr, B, c["sharp_toe_x"], zr, angles, hosel_top=hosel)
            rate = (face_r - face_a) / (tr - ta)                      # deg per ms (negative = closing)
            face_c = face_r if mode == "held" else float(np.clip(face_r + rate * (0.0 - tr), -10.0, face_r))
            ang_c = (face_c, angles[1], angles[2])
            o_c2, _f, res_c = solve_frame(s, club, fc, C, None, zc, angles, face_fixed=face_c, hosel_top=hosel)
            # carry the shaft's ground point to mid-contact, then hang the head on it at the contact orientation
            g_r = club.shaft_point((face_r, angles[1], angles[2]), o_r, 0.0)
            g_c2 = club.shaft_point(ang_c, o_c2, 0.0)
            frac = (0.0 - tr) / ((0.0 - tr) + 0.83 * tc)
            g_mid = g_r + (g_c2 - g_r) * frac
            toe, up, normal = geom.axes(*ang_c)
            origin0 = g_mid - (club.shaft_point(ang_c, np.zeros(3), 0.0))
            contact_point = scene.ball - geom.BALL_RADIUS_MM * normal
            ray = origin0 / np.linalg.norm(origin0)
            origin = float(contact_point @ normal) / float(ray @ normal) * ray
            za, zr, zc = origin[2] + vz * ta, origin[2] + vz * tr, origin[2] + 0.83 * vz * tc
        hit = contact_point - origin
        u, v = float(hit @ toe), float(hit @ up)
        out[mode] = {"from_toe": SPEC.toe_u - u, "above_le": v - SPEC.le_v, "face_contact": face_c, "origin": origin.tolist()}
        if mode == "rate":
            say("   clear-air frame %d: head turned %+.1f deg (shaft %.2f px, head height centre %+.2f px, toe %+.2f px); hosel top measured %.1f mm up the axis (spec hosel length %.1f)" % (fa, face_a, float(np.sqrt(np.mean(res_a[:3] ** 2))), res_a[3], res_a[4], hosel, HOSEL_TOP))
            say("   last frame %d:      head turned %+.1f deg (shaft %.2f px, hosel top %+.2f px, toe %+.2f px) -> closing at %.0f deg per second" % (fr, face_r, float(np.sqrt(np.mean(res_r[:3] ** 2))), res_r[3], res_r[4], -rate * 1000.0))
            say("   after frame %d:     shaft %.2f px, hosel top %+.2f px (turn set to the contact value)" % (fc, float(np.sqrt(np.mean(res_c[:3] ** 2))), res_c[3]))
            keep = {"o_r": o_r, "face_r": face_r, "o_a": o_a, "face_a": face_a, "origin": origin, "face_c": face_c}
            if n == 1:
                m, _ax, _l = club.mask((face_r, angles[1], angles[2]), o_r)
                sc = rig.compare(m, rig.polygon_mask(TRACE, shape), 82.0)
                say("   against the owner's trace on frame 43 (not used): boundary rms %.2f px, overlap %.2f" % (sc["rms"], sc["iou"]))
    mark = MARKS[str(n)]
    m_ft, m_al = 0.5 * (mark["lines"][0] + mark["outline"][0]), 0.5 * (mark["lines"][1] + mark["outline"][1])
    for mode, label in (("rate", "turn carried to contact at that rate"), ("held", "turn held at the last frame's value")):
        o = out[mode]
        say("   IMPACT, %-38s (face %+5.1f at contact): %.1f mm from the toe, %.1f mm above the leading edge | mark %.1f, %.1f | miss %+.1f, %+.1f mm" % (
            label, o["face_contact"], o["from_toe"], o["above_le"], m_ft, m_al, o["from_toe"] - m_ft, o["above_le"] - m_al))
    record[str(n)] = {"t_mid": t_mid, "hosel_top_mm": hosel, "face_air": keep["face_a"], "face_last": keep["face_r"], "rate_deg_per_s": float(-rate * 1000.0), "readings": out, "mark": [m_ft, m_al]}

    fig, axs = plt.subplots(1, 3, figsize=(30, 8.4))
    b = prep["ball"]
    panels = (("frame %d, clear air, turned %+.1f deg" % (fa, keep["face_a"]), fa, keep["o_a"], keep["face_a"], A),
              ("frame %d, last before contact, turned %+.1f deg" % (fr, keep["face_r"]), fr, keep["o_r"], keep["face_r"], B),
              ("mid-contact (turned %+.1f deg), drawn over frame %d" % (keep["face_c"], fr), fr, keep["origin"], keep["face_c"], B))
    for a, (title, f, origin_f, face_f, obs) in zip(axs, panels):
        m, _axis, _lead = club.mask((face_f, angles[1], angles[2]), origin_f)
        bd = rig.boundary(m)
        x0, x1 = int(bd[:, 0].min()) - 8, int(max(bd[:, 0].max(), b["x"] + b["diameter_px"] / 2)) + 10
        vis = obs["bottom_y"]
        y1 = int(min(bd[:, 1].max(), vis + 20)) + 6
        y0 = max(y1 - int((x1 - x0) * 0.62), 0)
        a.imshow(s["frames"][f][y0:y1, x0:x1], cmap="gray", vmin=0, vmax=160, interpolation="nearest", extent=(x0 - .5, x1 - .5, y1 - .5, y0 - .5))
        bd = np.vstack([bd, bd[:1]])
        a.plot(bd[:, 0], np.where(bd[:, 1] <= vis, bd[:, 1], np.nan), color="#39ff14", linewidth=1.5)
        a.plot(bd[:, 0], np.where(bd[:, 1] >= vis - .5, bd[:, 1], np.nan), color="#39ff14", linewidth=0.9, linestyle=(0, (2, 2)))
        a.add_patch(plt.Circle((b["x"], b["y"]), b["diameter_px"] / 2, fill=False, color="w", linewidth=0.7, linestyle=":"))
        if n == 1 and f == 43 and "contact" not in title:
            a.plot(*np.vstack([TRACE, TRACE[:1]]).T, color="#ff2020", linewidth=1.0)
        if "contact" in title:
            cp = scene.project(contact_point)[0]
            a.scatter([cp[0]], [cp[1]], s=70, marker="x", c="#ff2020", linewidths=1.6)
        a.axhline(vis, color="#ffe600", linewidth=0.5, linestyle=":")
        a.xaxis.set_major_locator(MultipleLocator(5))
        a.yaxis.set_major_locator(MultipleLocator(5))
        a.tick_params(labelsize=6)
        a.set_xlim(x0 - .5, x1 - .5)
        a.set_ylim(y1 - .5, y0 - .5)
        a.set_title("shot %d, TS4 at spec dimensions: %s" % (n, title), fontsize=10)
    fig.tight_layout()
    fig.savefig(AUDIT / "figures" / ("turn-%s-shot%d.png" % (ANCHOR, n)), dpi=56)
    plt.close(fig)
(AUDIT / "results" / ("PIPELINE-TURN-%s.txt" % ANCHOR)).write_text("\n".join(OUT) + "\n")
(AUDIT / "results" / ("pipeline-turn-%s.json" % ANCHOR)).write_text(json.dumps(record, indent=1, default=float))

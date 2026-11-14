"""PROTOCOL addendum 14: ONE runner, the same for every shot. From the capture's frames and the upstream part of the
unmodified replay to the fused angles and the impact position. Reads no face photo and no mark.

   1 frames   last rest frame / first moved frame (setup_free)
   2 ball     track, radar as line-of-sight speed -> mid-contact, launch, direction; camera pitch from the resting ball
   3 club     descent from the sub-pixel ferrule on the frames before contact (addendum 9's rule)
   4 fusion   the application's inversion of 2 and 3 with its club path -> face, dynamic loft; roll from dynamic lie
   5 place    hosel line + top edge + toe on the last rest frame; carried to the first moved frame by the ferrule
   6 read     the ball's contact point on the face: mm from the toe, mm above the leading edge
Usage: system_run.py [shots...]            the real run; writes results/SYSTEM-RUN.txt, system-run.json and
                                           figures/system-run.png (what would have been shown)
       PORT_CHECK=1 system_run.py          steps 5-6 with the seventh pass's angles forced in, against
                                           results/outline-fit4-measured.json; writes nothing"""
import json
import math
import os
import sys
from types import SimpleNamespace

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

import diag_lib as dl
import outline_lib as ol
import pipeline_lib as pl
import setup_free as sf
import sil
from common import AUDIT

sys.path.insert(0, "F:/Coding/OpenFlight/openflight-integration/src")
from openflight import collision_forward as cf  # noqa: E402  (read-only: the application's own inversion)
from openflight.camera import club_delivery as cd  # noqa: E402  (read-only: its slope_with_sigma)
from openflight.clubs import ClubType  # noqa: E402

OUT = []
LENS_MM, BALL_MM = 95.0, 21.35
ARC_TURN_DEG_PER_MS = 1.3
PORT_CHECK = os.environ.get("PORT_CHECK") == "1"
EMPTY = np.zeros((0, 3))


def say(t=""):
    OUT.append(t)
    print(t)


# ------------------------------------------------------------------ step 3: the club's descent from the ferrule
def ferrule_frame(s, f):
    """Sub-pixel ferrule on the shaft axis of frame f (dplane_inputs.py's observer), or None."""
    frame = s["frames"][f]
    diff, mask = sil.changed(frame, s["ref"], s["sigma"], 8.0)
    comp = sil.head_component(mask)
    if comp is None:
        return None
    ax = sil.shaft_axis(diff, comp)
    if ax is None or ax["line"] is None or len(ax["ridge"]) < 6:
        return None
    a, b = ax["line"][0], ax["line"][1]
    ys = np.arange(int(ax["ridge"][:, 1].min()), int(ax["head_bottom"] - 4) + 1)
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
            return {"x": float(a + b * y), "y": float(y)}
    return None


def descent(s, rest, t_mid_abs):
    """(attack against the camera axis in deg, how it was obtained, frames used) from the ferrule's 3-D positions."""
    pts = {}
    for f in range(rest - 5, rest + 1):
        try:
            p = ferrule_frame(s, f)
        except Exception:
            p = None
        if p is not None:
            z = float(dl.origin_at(s, f)[2])
            sc = s["scene"]
            pts[f] = np.array([(p["x"] - sc.centre[0]) * z / sc.focal, (p["y"] - sc.centre[1]) * z / sc.focal, z])
    run = []
    for f in sorted(pts, reverse=True):
        if (not run and f == rest) or (run and run[-1] - f == 1):
            run.append(f)
        elif run:
            break
    run = sorted(run)
    if len(run) < 2:
        return None, "fewer than 2 consecutive frames with a ferrule ending at the last rest frame", run
    t = np.array([s["times"][f] for f in run])
    xyz = np.array([pts[f] for f in run])

    def ang(v):
        return math.degrees(math.atan2(-v[1], math.hypot(v[0], v[2])))

    chords = [ang(xyz[i + 1] - xyz[i]) for i in range(len(run) - 1)]
    if len(run) >= 4:
        luf = np.column_stack([xyz[:, 0], -xyz[:, 1], xyz[:, 2]]) / 1000.0
        first = cd.slope_with_sigma(t / 1000.0, luf, at_s=t[0] / 1000.0, degree=2, position_sigma_m=0.001)
        last = cd.slope_with_sigma(t / 1000.0, luf, at_s=t[-1] / 1000.0, degree=2, position_sigma_m=0.001)
        mid = cd.slope_with_sigma(t / 1000.0, luf, at_s=t_mid_abs / 1000.0, degree=2, position_sigma_m=0.001)
        if first is not None and last is not None and mid is not None:
            turn = (last[0] - first[0]) / (t[-1] - t[0])
            if 0.0 <= turn <= 3.0:
                return mid[0], "quadratic tangent at mid-contact, %d frames, turn %+.2f deg/ms" % (len(run), turn), run
    t_chord = 0.5 * (t[-1] + t[-2])
    return chords[-1] + ARC_TURN_DEG_PER_MS * (t_mid_abs - t_chord), "last chord %+.1f moved %.2f ms to contact at %.1f deg/ms (%d frames)" % (chords[-1], t_mid_abs - t_chord, ARC_TURN_DEG_PER_MS, len(run)), run


# ------------------------------------------------------------------ steps 5 and 6
def place_and_read(n, s, B, C, angles):
    """Addendum 13 pass 6, variant W1 (W0 without 4 toe rows). Returns (result dict, None) or (None, reason)."""
    sh = pl.Shot(n, angles=angles, track_kwargs=ol.RADIAL, shared=SimpleNamespace(s=s, B=B, edge_r=EMPTY, C=C))
    t_mid = sh.track["fits"]["pixels + radar"]["t_mid"]
    f = sh.fr
    o, why = ol.observe_outline(s, f, B)
    if o is None:
        return None, "no outline on the last rest frame (%s)" % why
    mm_per_px = float(dl.origin_at(s, f)[2]) / s["scene"].focal
    vals_mm = [v * mm_per_px for v in o["t_vals"]]
    try:
        try:
            P = dl.observe_frame(s, f - 1)
        except Exception:
            P = ol.obs_light(s, f - 1)
        op, _why_p = ol.observe_outline(s, f - 1, P)
        if op is not None:
            mm_prev = float(dl.origin_at(s, f - 1)[2]) / s["scene"].focal
            vals_mm += [v * mm_prev for v in op["t_vals"]]
    except Exception:
        pass
    if len(vals_mm) < 6:
        return None, "rim thickness: only %d values on the two frames (6 needed)" % len(vals_mm)
    t = float(np.median(vals_mm)) / mm_per_px
    use_toe = len(o["toe"]) >= 4
    top_pts = np.array([(c[0], c[1]) for c in o["cav"]])
    top_inset = np.full(len(top_pts), t / ol.SLOPE_FACTOR)
    toe_pts = o["toe"] if use_toe else np.zeros((0, 2))
    pts = np.vstack([top_pts, toe_pts])
    inset = np.concatenate([top_inset, np.full(len(toe_pts), t)])
    z = float(dl.origin_at(s, f)[2])
    sol = None
    for _ in range(3):
        origin, k, shaft_rms, r_pts = ol.fit(sh, f, B, z, pts, inset, False, n_top=len(top_pts))
        sol = sh.solve(ol.scaled(k), t_mid, "ferrule", True, trace_anchor=origin)
        z = float(sol["rest"][2])
    rd = sol["reading"]
    n_top = len(top_pts)
    return {"from_toe": float(rd["from_toe"]), "above_le": float(rd["above_le"]), "variant": "hosel + top + toe" if use_toe else "hosel + top (fewer than 4 toe rows)",
            "rim_mm": t * mm_per_px, "rim_values": len(vals_mm), "top_columns": int(n_top), "toe_rows": int(len(o["toe"])),
            "fit_rms_px": {"shaft": shaft_rms, "top": float(np.sqrt(np.mean(r_pts[:n_top] ** 2))), "toe": float(np.sqrt(np.mean(r_pts[n_top:] ** 2))) if use_toe else None},
            "implied_ferrule_mm": float(sh.ferrule_length(sol["club"], sol["rest"], B)), "t_mid": float(t_mid), "frames": [int(sh.fr), int(sh.fc)],
            "after_contact_ms": float(sh.t_app + t_mid - sh.times[sh.fr])}, None


def run_shot(n):
    rec = {"shot": n, "status": "refused"}
    try:
        s = sf.build(n)
    except Exception as exc:
        rec["reason"] = "frames: %s" % exc
        return rec
    B, C = sf.observations(s)
    if B is None or C is None:
        rec["reason"] = "the shaft and ferrule were not found on a frame next to contact"
        return rec
    replay = json.loads((AUDIT / "results" / ("shot%d" % n) / "replay.json").read_text())
    m = replay["stages"]["full_shot"]["metrics"]
    yaw = s["state"]["yaw"]
    rec["sensors"] = {"ball_speed_mph_radar": m["ball_speed"]["value"], "club_speed_mph_radar": m["club_speed"]["value"], "club_path_deg": m["club_path"]["value"], "dynamic_lie_deg": m["dynamic_lie"]["value"]}
    if PORT_CHECK:
        angles = ol.measured_angles(n, s["angles"][2])
    else:
        sh0 = pl.Shot(n, track_kwargs=ol.RADIAL, shared=SimpleNamespace(s=s, B=B, edge_r=EMPTY, C=C))
        if sh0.track is None:
            rec["reason"] = "the ball could not be tracked after contact"
            return rec
        fz = sh0.track["fits"]["pixels + radar"]
        P0 = s["scene"].ball
        rng = float(np.linalg.norm(P0))
        pitch = math.degrees(math.asin((LENS_MM - BALL_MM) / rng)) - math.degrees(math.atan2(P0[1], math.hypot(P0[0], P0[2])))
        launch_v, launch_h = fz["launch_deg"] - pitch, fz["azimuth_deg"] + yaw
        t_mid_abs = sh0.t_app + fz["t_mid"]
        attack_cam, how, used = descent(s, sh0.fr, t_mid_abs)
        flagged = attack_cam is None
        attack = m["attack_angle"]["value"] if flagged else attack_cam - pitch
        if flagged:
            how = "application's attack used: " + how
        inv = cf.face_and_loft_from_launch(club=ClubType.IRON_7, launch_vertical_deg=launch_v, launch_horizontal_deg=launch_h, club_path_deg=m["club_path"]["value"], attack_angle_deg=attack)
        rec["fusion"] = {"camera_pitch_deg": pitch, "ball_speed_mps": fz["speed"], "launch_vertical_deg": launch_v, "launch_horizontal_deg": launch_h, "attack_deg": attack, "attack_how": how,
                         "attack_frames": used, "club_path_deg": m["club_path"]["value"], "mid_contact_ms_after_rest_frame": float(t_mid_abs - sh0.times[sh0.fr]), "ball_frames": sh0.track["frames"]}
        if inv is None:
            rec["reason"] = "the launch and club direction are outside the application's inversion"
            return rec
        angles = (inv[0] - yaw, inv[1], s["angles"][2])
        rec["fusion"].update({"face_deg": inv[0], "dynamic_loft_deg": inv[1], "roll_deg": s["angles"][2]})
    res, why = place_and_read(n, s, B, C, angles)
    if res is None:
        rec["reason"] = why
        return rec
    rec.update({"status": "read", "impact": res})
    return rec


if PORT_CHECK:
    ref = json.loads((AUDIT / "results" / "outline-fit4-measured.json").read_text())
    worst = 0.0
    for n in (1, 2, 3, 4):
        r = run_shot(n)
        want = ref[str(n)]["variants"]["W1"]["reading"]
        got = [r["impact"]["from_toe"], r["impact"]["above_le"]] if r["status"] == "read" else [float("nan")] * 2
        worst = max(worst, abs(got[0] - want[0]), abs(got[1] - want[1]))
        print("port check shot %d: runner %.4f / %.4f, seventh pass %.4f / %.4f" % (n, *got, *want))
    print("PORT CHECK %s (largest difference %.2e mm)" % ("OK" if worst < 1e-6 else "FAILED", worst))
    sys.exit(0)

shots = [int(v) for v in sys.argv[1:]] or [1, 2, 3, 4]
records = {}
say("SYSTEM RUN (PROTOCOL addendum 14). One procedure for every shot; no face photo and no mark is read.")
say()
for n in shots:
    r = run_shot(n)
    records[str(n)] = r
    sn = r.get("sensors", {})
    say("SHOT %d   radar: ball %.1f mph, club %.1f mph (line of sight)" % (n, sn.get("ball_speed_mph_radar", float("nan")), sn.get("club_speed_mph_radar", float("nan"))))
    fu = r.get("fusion")
    if fu:
        say("   ball     seen on frames %s; full speed %.1f m/s (%.1f mph); launch %.1f deg up, %.1f deg right of the target line; camera pitch %.2f deg" % (
            fu["ball_frames"], fu["ball_speed_mps"], fu["ball_speed_mps"] / 0.44704, fu["launch_vertical_deg"], fu["launch_horizontal_deg"], fu["camera_pitch_deg"]))
        say("   club     attack %+.1f deg (%s); path %.1f deg (application)" % (fu["attack_deg"], fu["attack_how"], fu["club_path_deg"]))
        if "face_deg" in fu:
            say("   fusion   face %+.1f deg, dynamic loft %.1f deg, face to path %+.1f deg; mid-contact %.2f ms after the last rest frame" % (fu["face_deg"], fu["dynamic_loft_deg"], fu["face_deg"] - fu["club_path_deg"], fu["mid_contact_ms_after_rest_frame"]))
    if r["status"] == "read":
        im = r["impact"]
        say("   IMPACT   %.1f mm from the toe, %.1f mm above the leading edge   [%s; frames %d/%d; rim %.1f mm from %d values; %d top columns, %d toe rows; fit rms px shaft %.2f, top %.2f%s]" % (
            im["from_toe"], im["above_le"], im["variant"], *im["frames"], im["rim_mm"], im["rim_values"], im["top_columns"], im["toe_rows"], im["fit_rms_px"]["shaft"], im["fit_rms_px"]["top"],
            "" if im["fit_rms_px"]["toe"] is None else ", toe %.2f" % im["fit_rms_px"]["toe"]))
    else:
        say("   IMPACT   NO READING: %s" % r["reason"])
    say()
(AUDIT / "results" / "SYSTEM-RUN.txt").write_text("\n".join(OUT) + "\n")
(AUDIT / "results" / "system-run.json").write_text(json.dumps(records, indent=1, default=float))

# ---- what would have been shown: the face, toe on the left as in a face-on view, with the impact point
spec = ol.SPEC
fig, axs = plt.subplots(1, len(shots), figsize=(7.2 * len(shots), 7.6))
for ax, n in zip(np.atleast_1d(axs), shots):
    r = records[str(n)]
    blade = spec.blade
    x = spec.toe_u - blade[:, 0]            # mm from the toe
    y = blade[:, 1] - spec.le_v             # mm above the leading edge
    ax.fill(np.append(x, x[0]), np.append(y, y[0]), color="#d9d9d9", edgecolor="#404040", linewidth=1.5)
    for yy in np.arange(8.0, 46.0, 4.0):
        ax.plot([20.0, 62.0], [yy, yy], color="#b0b0b0", linewidth=0.8)
    if r["status"] == "read":
        im = r["impact"]
        ax.add_patch(plt.Circle((im["from_toe"], im["above_le"]), 6.0, color="#d62728", alpha=0.35))
        ax.scatter([im["from_toe"]], [im["above_le"]], s=140, marker="x", c="#d62728", linewidths=3)
        head = "%.0f mm from toe, %.0f mm up" % (im["from_toe"], im["above_le"])
    else:
        head = "no impact reading"
    fu = r.get("fusion", {})
    lines = [head]
    if "face_deg" in fu:
        lines += ["ball %.0f mph, launch %.1f up / %.1f right" % (fu["ball_speed_mps"] / 0.44704, fu["launch_vertical_deg"], fu["launch_horizontal_deg"]),
                  "attack %+.1f, path %.1f, face %+.1f, loft %.1f" % (fu["attack_deg"], fu["club_path_deg"], fu["face_deg"], fu["dynamic_loft_deg"])]
    ax.set_title("Shot %d\n%s" % (n, "\n".join(lines)), fontsize=11)
    ax.set_xlim(-6, 92)
    ax.set_ylim(-8, 64)
    ax.set_aspect("equal")
    ax.set_xlabel("mm from the toe (toe on the left, as when looking at the face)", fontsize=9)
    ax.set_ylabel("mm above the leading edge", fontsize=9)
    ax.tick_params(labelsize=8)
fig.suptitle("What the system would have shown: impact on the face and the fused numbers. Research runner; reads no face photo.", fontsize=12)
fig.tight_layout(rect=(0, 0, 1, 0.95))
fig.savefig(AUDIT / "figures" / "system-run.png", dpi=70)

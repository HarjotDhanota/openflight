"""PROTOCOL addendum 10. The frozen shaft+ferrule reading through setup_free (no application placement stage).

  heldout.py validate   shots 1, 3, 4: setup_free beside the application's own setup; writes results/HELDOUT-VALIDATION.txt
  heldout.py predict    shot 2: the blind prediction; writes results/HELDOUT-SHOT2-PREDICTION.txt and .json, and a
                        figure of the placement. Reads no face photo and no mark.
Frozen: TS4, shaft axis 75.0 mm, hosel length 61.0 mm, line-of-sight ball track, mean of slowed/unslowed."""
import json
import sys
from dataclasses import replace
from types import SimpleNamespace

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

import pipeline_lib as pl
import rig
import setup_free as sf
from common import AUDIT

sys.path.insert(0, "F:/Coding/OpenFlight/openflight-integration/src")
from openflight import collision_forward as cf  # noqa: E402
from openflight.clubs import ClubType  # noqa: E402

OUT = []
HOSEL_MM = 61.0
RADIAL = {"speed_datum": "radial", "los_after_ms": 0.0}
SPEC = replace(rig.ts4(), name="TS4 7-iron (research), axis at 75.0", axis_ground_from_toe=75.0)
MODE = sys.argv[1] if len(sys.argv) > 1 else "validate"


def say(t=""):
    OUT.append(t)
    print(t)


def reading(sh):
    t_mid = sh.track["fits"]["pixels + radar"]["t_mid"]
    sols = [sh.solve(SPEC, t_mid, "ferrule", slowed, None, HOSEL_MM) for slowed in (True, False)]
    ft = float(np.mean([r["reading"]["from_toe"] for r in sols]))
    al = float(np.mean([r["reading"]["above_le"] for r in sols]))
    return ft, al, t_mid, sols[0]


def free_shot(n, angles=None):
    s = sf.build(n, say)
    B, C = sf.observations(s)
    if B is None or C is None:
        return s, None
    shared = SimpleNamespace(s=s, B=B, edge_r=np.zeros((0, 3)), C=C)
    return s, pl.Shot(n, angles=angles, track_kwargs=RADIAL, shared=shared)


def figure(sh, sol, path, title):
    fig, axs = plt.subplots(1, 3, figsize=(27, 8))
    b = sh.prep["ball"]
    for a, (name, f, origin) in zip(axs, (("frame %d, last rest" % sh.fr, sh.fr, sol["rest"]), ("frame %d, first after" % sh.fc, sh.fc, sol["after"]), ("mid-contact, over frame %d" % sh.fr, sh.fr, sol["contact"]))):
        m, _axis, _lead = sol["club"].mask(sh.angles, origin)
        bd = rig.boundary(m)
        x0, x1 = int(bd[:, 0].min()) - 10, int(max(bd[:, 0].max(), b["x"] + b["diameter_px"] / 2)) + 12
        y1 = int(min(bd[:, 1].max(), sh.shape[0] - 2)) + 6
        y0 = max(y1 - int((x1 - x0) * 0.66), 0)
        a.imshow(sh.s["frames"][f][y0:y1, x0:x1], cmap="gray", vmin=0, vmax=160, interpolation="nearest", extent=(x0 - .5, x1 - .5, y1 - .5, y0 - .5))
        bd = np.vstack([bd, bd[:1]])
        a.plot(bd[:, 0], bd[:, 1], color="#39ff14", linewidth=1.2)
        a.add_patch(plt.Circle((b["x"], b["y"]), b["diameter_px"] / 2, fill=False, color="w", linewidth=0.7, linestyle=":"))
        if "contact" in name:
            cp = sh.scene.project(sh.contact_point)[0]
            a.scatter([cp[0]], [cp[1]], s=70, marker="x", c="#ff2020", linewidths=1.6)
        a.set_xlim(x0 - .5, x1 - .5)
        a.set_ylim(y1 - .5, y0 - .5)
        a.set_title("%s: %s" % (title, name), fontsize=10)
        a.tick_params(labelsize=6)
    fig.tight_layout()
    fig.savefig(path, dpi=56)
    plt.close(fig)


if MODE == "validate":
    marks = json.loads((AUDIT / "results" / "photo-marks.json").read_text())
    say("VALIDATION of setup_free on the development shots (TS4, shaft+ferrule, line-of-sight ball track). mm from the toe / above the leading edge.")
    for n in (1, 3, 4):
        m = marks[str(n)]
        mark = (0.5 * (m["lines"][0] + m["outline"][0]), 0.5 * (m["lines"][1] + m["outline"][1]))
        old = pl.Shot(n, track_kwargs=RADIAL)
        o_ft, o_al, o_t, _ = reading(old)
        say("SHOT %d   mark %.1f / %.1f" % (n, *mark))
        say("   application's setup (addendum 9 row B): rest frame %d; angles %+.2f %.2f %+.2f; reading %.1f / %.1f (miss %+.1f / %+.1f); mid-contact %.2f ms after frame %d" % (
            old.fr, *old.angles, o_ft, o_al, o_ft - mark[0], o_al - mark[1], old.t_app + o_t - old.times[old.fr], old.fr))
        s, new = free_shot(n)
        if new is None:
            say("   setup_free: OBSERVATION FAILED")
            continue
        n_ft, n_al, n_t, sol = reading(new)
        say("   setup_free:                             rest frame %d (%s); angles %+.2f %.2f %+.2f; reading %.1f / %.1f (miss %+.1f / %+.1f); mid-contact %.2f ms after frame %d" % (
            new.fr, "same" if new.fr == old.fr else "DIFFERENT", *new.angles, n_ft, n_al, n_ft - mark[0], n_al - mark[1], new.t_app + n_t - new.times[new.fr], new.fr))
        say("   ball px (crop-free): application %.2f, %.2f d %.2f range %.1f | setup box %.2f, %.2f d %.2f range %.1f" % (
            old.prep["ball"]["x"] + old.prep["roi_origin"][0], old.prep["ball"]["y"] + old.prep["roi_origin"][1], old.prep["ball"]["diameter_px"], old.prep["ball_range_mm"],
            new.prep["ball"]["x"] + new.prep["roi_origin"][0], new.prep["ball"]["y"] + new.prep["roi_origin"][1], new.prep["ball"]["diameter_px"], new.prep["ball_range_mm"]))
        say("   ferrule px (crop-free) rest frame: application's crop %.2f, %.2f | setup_free crop %.2f, %.2f" % (
            old.B["ferrule"]["x"] + old.prep["roi_origin"][0], old.B["ferrule"]["y"] + old.prep["roi_origin"][1], new.B["ferrule"]["x"] + new.prep["roi_origin"][0], new.B["ferrule"]["y"] + new.prep["roi_origin"][1]))
        figure(new, sol, AUDIT / "figures" / ("heldout-validation-shot%d.png" % n), "shot %d setup_free" % n)
    (AUDIT / "results" / "HELDOUT-VALIDATION.txt").write_text("\n".join(OUT) + "\n")
else:
    n = 2
    say("BLIND PREDICTION, shot %d (PROTOCOL addendum 10). No face photo or mark has been read. TS4, shaft axis 75.0 mm, hosel 61.0 mm." % n)
    s, sh = free_shot(n)
    record = {"shot": n}
    if sh is None:
        say("FAILED: the shaft line / ferrule could not be observed by dl.observe_frame on a bracket frame.")
    elif sh.track is None:
        say("FAILED: the ball track has fewer than two measurable frames.")
    else:
        mt = s["state"]["replay_metrics"]
        for k, fz in sh.track["fits"].items():
            say("   ball track, %s: mid-contact %+.2f ms from the reference (+-%.2f); %.1f m/s; launch %.1f deg above the camera axis, %.1f right; worst pixel %.2f" % (
                k, fz["t_mid"], fz["t_mid_sigma"], fz["speed"], fz["launch_deg"], fz["azimuth_deg"], fz["worst_px"]))
        ft, al, t_mid, sol = reading(sh)
        say("   rest frame %d, first moved %d; mid-contact %.2f ms after frame %d and %.2f ms before frame %d" % (sh.fr, sh.fc, sh.t_app + t_mid - sh.times[sh.fr], sh.fr, sh.times[sh.fc] - sh.t_app - t_mid, sh.fc))
        say("   ferrule px in the crop: rest frame %.1f, %.1f; after %.1f, %.1f; shaft slope %.3f / %.3f" % (sh.B["ferrule"]["x"], sh.B["ferrule"]["y"], sh.C["ferrule"]["x"], sh.C["ferrule"]["y"], sh.B["line"][1], sh.C["line"][1]))
        say("   orientation (application's upstream metrics): face %+.2f, loft %.2f, roll %+.2f" % sh.angles)
        say("PRIMARY PREDICTION: %.1f mm from the toe, %.1f mm above the leading edge" % (ft, al))
        record.update({"primary": [ft, al], "angles": list(sh.angles), "t_mid": t_mid, "frames": [sh.fr, sh.fc]})
        figure(sh, sol, AUDIT / "figures" / "heldout-shot2-prediction.png", "shot 2 blind")
        # secondary: measured launch through the application's inversion; attack as the application had it is kept
        # unless the ball track's launch is available (it is): launch against the horizon = camera-axis launch - pitch
        fz = sh.track["fits"]["pixels + radar"]
        P0 = sh.scene.ball
        rng = float(np.linalg.norm(P0))
        pitch = np.degrees(np.arcsin((95.0 - 21.35) / rng)) - np.degrees(np.arctan2(P0[1], np.hypot(P0[0], P0[2])))
        inv = cf.face_and_loft_from_launch(club=ClubType.IRON_7, launch_vertical_deg=fz["launch_deg"] - pitch, launch_horizontal_deg=fz["azimuth_deg"] + s["state"]["yaw"],
                                           club_path_deg=mt["club_path"], attack_angle_deg=mt["attack_angle"])
        if inv is not None:
            angles2 = (inv[0] - s["state"]["yaw"], inv[1], sh.angles[2])
            sh2 = pl.Shot(n, angles=angles2, track_kwargs=RADIAL, shared=SimpleNamespace(s=s, B=sh.B, edge_r=np.zeros((0, 3)), C=sh.C))
            ft2, al2, _t, _s = reading(sh2)
            say("SECONDARY (launch from the ball track, %.1f deg against the horizon at pitch %.2f; application's attack %+.2f): face %+.2f, loft %.2f -> %.1f mm from the toe, %.1f mm above the leading edge" % (
                fz["launch_deg"] - pitch, pitch, mt["attack_angle"], angles2[0], angles2[1], ft2, al2))
            record["secondary"] = [ft2, al2]
        say("   application's metrics for reference: launch %.2f, attack %+.2f, path %.2f, face %.2f, loft %.2f, dynamic lie %.2f" % (mt["launch_vertical"], mt["attack_angle"], mt["club_path"], mt["face_angle"], mt["dynamic_loft"], mt["dynamic_lie"]))
    (AUDIT / "results" / "HELDOUT-SHOT2-PREDICTION.txt").write_text("\n".join(OUT) + "\n")
    (AUDIT / "results" / "heldout-shot2-prediction.json").write_text(json.dumps(record, indent=1, default=float))

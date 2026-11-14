"""PROTOCOL addendum 9, T3: the application's own D-plane inversion fed the measured inputs of dplane_inputs.py, and
the TS4 research pipeline rerun with the face and loft that come out.

Rows per shot (TS4 model, shaft axis at 75.0 mm, hosel length from shot 1's trace at the application's angles):
  A  application's angles, ball track with the radar as full speed   (the earlier result, reproduced here)
  B  application's angles, ball track with the radar as line-of-sight speed   (isolates the timing change)
  C  measured attack + measured launch through the application's inversion; camera level, as the application places
  D  C with the loft raised by the camera pitch inferred from the resting ball   (sensitivity)
  E  C with the horizontal launch from the ball track as well   (extra, not in the protocol; differs on shot 1 only)
Each row: top-edge placement and shaft+ferrule placement, in mm from the toe / above the leading edge, beside the mark.
Read-only on the application. Writes results/DPLANE-RERUN.txt and results/dplane-rerun.json. Usage: dplane_rerun.py"""
import json
import sys
from dataclasses import replace

import numpy as np

import pipeline_lib as pl
import rig
from common import AUDIT

sys.path.insert(0, "F:/Coding/OpenFlight/openflight-integration/src")
from openflight import collision_forward as cf  # noqa: E402  (read-only use of one function)
from openflight.clubs import ClubType  # noqa: E402

OUT = []


def say(t=""):
    OUT.append(t)
    print(t)


inputs = json.loads((AUDIT / "results" / "dplane-inputs.json").read_text())
marks = json.loads((AUDIT / "results" / "photo-marks.json").read_text())
RADIAL = {"speed_datum": "radial", "los_after_ms": 0.0}
spec = replace(rig.ts4(), name="TS4 7-iron (research), axis at 75.0", axis_ground_from_toe=75.0)


def invert(launch_v, launch_h, path, attack):
    return cf.face_and_loft_from_launch(club=ClubType.IRON_7, launch_vertical_deg=launch_v, launch_horizontal_deg=launch_h, club_path_deg=path, attack_angle_deg=attack)


base = {n: pl.Shot(n) for n in (1, 3, 4)}
anchor, _sp, score, _ = pl.fit_trace(base[1], spec, False)
hosel = base[1].ferrule_length(rig.Club(base[1].scene, spec, base[1].shape), anchor, base[1].B)
say("TS4 hosel length on shot 1's trace at the application's angles: %.1f mm (pipeline.py: 61.0); trace fit %.2f px" % (hosel, score["rms"]))
say()

record = {}
for n in (1, 3, 4):
    sh0 = base[n]
    inp = inputs[str(n)]
    app = inp["app"]
    state = sh0.s["state"]
    face_app, loft_app, roll = sh0.s["angles"]
    yaw = state["collision_model"]["metrics"]["face_angle"]["value"] - face_app  # target line minus camera axis
    check = invert(app["launch_vertical"], app["launch_horizontal"], app["club_path"], app["attack_angle"])
    pitch = inp["pitch_deg"]
    attack_h, launch_h = inp["attack_cam_deg"] - pitch, inp["launch_cam_deg"] - pitch
    new = invert(launch_h, app["launch_horizontal"], app["club_path"], attack_h)
    cam_horizontal = inp["azimuth_cam_deg"] + yaw
    new_e = invert(launch_h, cam_horizontal, app["club_path"], attack_h)
    m = marks[str(n)]
    mark = (0.5 * (m["lines"][0] + m["outline"][0]), 0.5 * (m["lines"][1] + m["outline"][1]))
    say("SHOT %d   mark %.1f mm from the toe, %.1f mm above the leading edge" % (n, *mark))
    say("   self-check: the inversion on the application's own inputs gives face %.2f, loft %.2f (application recorded %.2f, %.2f)" % (
        check[0], check[1], state["collision_model"]["metrics"]["face_angle"]["value"], state["collision_model"]["metrics"]["dynamic_loft"]["value"]))
    say("   inputs against the horizon: attack %+.1f (application %+.2f), launch %.1f (application %.2f, %s); camera pitch %.2f deg; target line is %.2f deg from the camera axis" % (
        attack_h, app["attack_angle"], launch_h, app["launch_vertical"], inp["app_launch_source"], pitch, yaw))
    say("   D-plane out: face %+.2f, loft %.2f (application: %+.2f, %.2f); with the ball track's horizontal launch %.1f instead of %.2f: face %+.2f, loft %.2f" % (
        new[0], new[1], check[0], check[1], cam_horizontal, app["launch_horizontal"], new_e[0], new_e[1]))
    rows = (("A application angles, old ball track", (face_app, loft_app, roll), None),
            ("B application angles, line-of-sight track", (face_app, loft_app, roll), RADIAL),
            ("C measured attack + launch", (new[0] - yaw, new[1], roll), RADIAL),
            ("D C + loft raised by the pitch", (new[0] - yaw, new[1] + pitch, roll), RADIAL),
            ("E C + ball-track horizontal launch", (new_e[0] - yaw, new_e[1], roll), RADIAL))
    record[str(n)] = {"mark": mark, "dplane": {"face": new[0], "loft": new[1], "face_e": new_e[0], "loft_e": new_e[1], "attack": attack_h, "launch": launch_h}, "rows": {}}
    for label, angles, kw in rows:
        sh = pl.Shot(n, angles=angles, track_kwargs=kw, shared=sh0)
        t_mid = sh.track["fits"]["pixels + radar"]["t_mid"]
        edge = sh.solve(spec, t_mid, "edge", True)["reading"]
        fer = [sh.solve(spec, t_mid, "ferrule", slowed, None, hosel)["reading"] for slowed in (True, False)]
        fer_ft, fer_al = float(np.mean([r["from_toe"] for r in fer])), float(np.mean([r["above_le"] for r in fer]))
        say("   %-42s face %+5.1f loft %4.1f | mid-contact %+.2f ms | top edge %5.1f / %4.1f (miss %+5.1f / %+5.1f) | shaft+ferrule %5.1f / %4.1f (miss %+5.1f / %+5.1f)" % (
            label, angles[0], angles[1], t_mid, edge["from_toe"], edge["above_le"], edge["from_toe"] - mark[0], edge["above_le"] - mark[1], fer_ft, fer_al, fer_ft - mark[0], fer_al - mark[1]))
        record[str(n)]["rows"][label[0]] = {"angles": list(angles), "t_mid": t_mid, "edge": [edge["from_toe"], edge["above_le"]], "ferrule": [fer_ft, fer_al]}
    say()

say("RULE (written before running): orientation explains the fat-shot height miss only if the top-edge height miss on shots 3 AND 4 is under 3 mm in row C with shot 1 no worse than row A by more than 1 mm.")
h = {n: {k: record[str(n)]["rows"][k]["edge"][1] - record[str(n)]["mark"][1] for k in "ABCDE"} for n in (1, 3, 4)}
ok = abs(h[3]["C"]) < 3.0 and abs(h[4]["C"]) < 3.0 and abs(h[1]["C"]) <= abs(h[1]["A"]) + 1.0
say("   top-edge height miss, row A -> row C: shot 1 %+.1f -> %+.1f, shot 3 %+.1f -> %+.1f, shot 4 %+.1f -> %+.1f mm: %s" % (
    h[1]["A"], h[1]["C"], h[3]["A"], h[3]["C"], h[4]["A"], h[4]["C"], "SUPPORTED" if ok else "NOT SUPPORTED"))
(AUDIT / "results" / "DPLANE-RERUN.txt").write_text("\n".join(OUT) + "\n")
(AUDIT / "results" / "dplane-rerun.json").write_text(json.dumps(record, indent=1, default=float))

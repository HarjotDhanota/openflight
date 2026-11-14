"""Export shot 1's placed club as one JSON scene for the 3-D viewer (viewer3d/club-pose-3d.html).
Reads results/impact-shot1.json (written by impact1.py), the replay's prepared frames and the generic outline.
Camera frame: x right, y down, z forward, mm; the camera is at the origin. Usage: export_scene.py"""
import base64
import json

import cv2
import numpy as np

import club3d
import diag_lib as dl
import geom
from common import AUDIT

s = dl.setup(1)
prep = s["prep"]
imp = json.loads((AUDIT / "results" / "impact-shot1.json").read_text())
trace = json.loads((AUDIT / "owner-trace" / "shot1-f43-trace.json").read_text())["polygon_native_px"]


def png(frame):
    img = np.clip(frame.astype(np.float32) / 170.0, 0, 1) ** 0.85 * 255.0
    ok, buf = cv2.imencode(".png", img.astype(np.uint8))
    return "data:image/png;base64," + base64.b64encode(buf.tobytes()).decode("ascii")


key = "TRACE|f44 by head edge, slowed 0.83"
scene = {
    "shot": 1,
    "camera": {"width": int(s["frames"][43].shape[1]), "height": int(s["frames"][43].shape[0]), "focal_px": float(s["scene"].focal),
               "cx": float(prep["camera"]["principal_point_px"][0]), "cy": float(prep["camera"]["principal_point_px"][1])},
    "frames": {"f43": png(s["frames"][43]), "f44": png(s["frames"][44])},
    "outline_uv": geom.OUTLINE.round(3).tolist(),
    "blade_count": int(len(club3d.HEAD)),
    "size": imp["size"],
    "sole_width_mm": s["sole"][0], "bounce_deg": s["sole"][1],
    "shaft_tun": club3d.SHAFT_TUN.tolist(), "ground_tun": club3d.GROUND_TUN.tolist(),
    "hosel_radius_mm": club3d.HOSEL_RADIUS, "shaft_radius_mm": club3d.SHAFT_RADIUS,
    "hosel_length_mm": imp["poses"]["TRACE"]["hosel_length_mm"],
    "angles": {"face": imp["angles"][0], "loft": imp["angles"][1], "roll": imp["angles"][2]},
    "ball_mm": imp["ball_mm"], "ball_radius_mm": imp["ball_radius_mm"],
    "poses": {
        "before": {"label": "2.44 ms before contact", "frame": "f43", "origin": imp["poses"]["TRACE"]["f43"]},
        "contact": {"label": "at contact", "frame": "f43", "origin": imp["contact"][key]["origin"]},
        "after": {"label": "1.18 ms after contact", "frame": "f44", "origin": imp["poses"]["TRACE"]["f44_head_edge"]},
    },
    "reading": {"toe_mm": imp["contact"][key]["toe_mm"], "up_mm": imp["contact"][key]["up_mm"],
                "toe_range": [22.5, 25.6], "up_range": [0.3, 5.4]},
    "photo_mark": {"toe_mm": 27.7, "up_mm": 0.0, "radius_mm": 9.4},
    "app": {"toe_mm": 32.7, "up_mm": -10.8},
    "trace_px": [[round(p[0], 2), round(p[1], 2)] for p in trace[::2]],
    "score_lines": {"count": 13, "pitch_mm": 3.46, "lowest_above_leading_edge_mm": 6.71, "length_mm": 53.6, "toe_margin_mm": 17.44},
}
track = json.loads((AUDIT / "results" / "ball-track.json").read_text())
bt = json.loads((AUDIT / "results" / "impact-balltrack.json").read_text())
fit_ = track["fits"]["PIXELS + RADAR SPEED"]
t_track = fit_["t_dep_ms"]
p44 = np.array(fit_["f44"]["position_mm"])
ray44 = p44 / np.linalg.norm(p44)
scene["contact_origins"] = {"track": bt["ball track, pixels + radar speed"]["pick"]["origin"], "app": imp["contact"][key]["origin"]}
scene["timing"] = {"track": {"before_ms": t_track - imp["times_ms"]["f43"], "after_ms": imp["times_ms"]["f44"] - t_track},
                   "app": {"before_ms": -imp["times_ms"]["f43"], "after_ms": imp["times_ms"]["f44"]}, "track_minus_app_ms": t_track}
scene["ball_flight"] = {
    "f44": fit_["f44"]["position_mm"], "f45": fit_["f45"]["position_mm"], "f46": fit_["f46"]["position_mm"],
    "f44_band": [(p44 - 6.0 * ray44).tolist(), (p44 + 6.0 * ray44).tolist()],
    "f44_pixels_only": track["fits"]["PIXELS ONLY"]["f44"]["position_mm"],
    "speed_m_s": fit_["speed"], "launch_deg": fit_["launch_deg"], "azimuth_deg": fit_["azimuth_deg"],
}
final = json.loads((AUDIT / "results" / "final-poses.json").read_text())
scene["poses_by_instant"] = {k: {"before": v["before"], "contact": v["contact"], "after": v["after"], "size": v["size"]} for k, v in final.items()}
out = AUDIT / "viewer3d"
out.mkdir(exist_ok=True)
(out / "scene-shot1.json").write_text(json.dumps(scene))
print("wrote", out / "scene-shot1.json", "%.0f kB" % ((out / "scene-shot1.json").stat().st_size / 1e3))
print("poses", {k: [round(v, 1) for v in p["origin"]] for k, p in scene["poses"].items()}, "ball", [round(v, 1) for v in scene["ball_mm"]])

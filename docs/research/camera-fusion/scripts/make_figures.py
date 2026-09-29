"""Figures for the camera-fusion research log, from real captures only.

    uv run python docs/research/camera-fusion/scripts/make_figures.py \
        --frame <resting-ball frame .png> --bundle <session bundle .zip>
"""

import argparse
import io
import json
import zipfile
from pathlib import Path

import cv2
import numpy as np

from openflight.camera import tester_server as ts
from openflight.camera.reference_ball_range import estimate_reference_ball_range

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--frame", required=True, help="1280x800 resting-ball frame (PNG)")
parser.add_argument("--bundle", required=True, help="tester session bundle zip with a swing clip")
parser.add_argument("--pitch-deg", type=float, default=1.75, help="LIS3DH pitch for the frame")
parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1] / "figures")
ARGS = parser.parse_args()
OUT = ARGS.out
OUT.mkdir(exist_ok=True)
RIG = Path("config/enclosure_v3_rig_geometry.json")
FRAME = ARGS.frame
BUNDLE = ARGS.bundle

GREEN, RED, AMBER, WHITE = (80, 220, 120), (70, 70, 235), (40, 180, 250), (255, 255, 255)
facts = {}


def label(image, text, xy, color=WHITE, scale=0.7):
    cv2.putText(image, text, xy, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 4, cv2.LINE_AA)
    cv2.putText(image, text, xy, cv2.FONT_HERSHEY_SIMPLEX, scale, color, 2, cv2.LINE_AA)


def save(name, image, quality=82):
    cv2.imwrite(str(OUT / name), image, [cv2.IMWRITE_JPEG_QUALITY, quality])


# ---------------------------------------------------------------- field frame
gray = cv2.imread(FRAME, cv2.IMREAD_GRAYSCALE)
frames = np.repeat(gray[None], 3, axis=0)
camera = ts._reference_ball_camera(
    ts.ARMS["arm5"], RIG, {"camera_pitch_deg": ARGS.pitch_deg, "roll_deg": 0.0}, None, None
)
result = estimate_reference_ball_range(frames, camera, ball_center_height_m=0.021335)

# the camera's level line at the LIS3DH pitch with nominal intrinsics; only a reference line
horizon = next(
    r for r in range(0, 800) if camera.ray_model.rays(np.array([640.0, float(r)]))[2] < 0
)
overlay = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
cv2.line(overlay, (0, horizon), (1279, horizon), AMBER, 1)
label(
    overlay,
    f"camera level line at LIS3DH pitch {ARGS.pitch_deg:+.2f} deg (nominal intrinsics)",
    (20, horizon - 12),
    AMBER,
    0.6,
)
for candidate in result.candidates:
    center = (int(round(candidate.x_px)), int(round(candidate.y_px)))
    radius = int(round(candidate.diameter_px / 2))
    color = GREEN if result.selected is not None and candidate is result.selected else RED
    cv2.circle(overlay, center, radius + 4, color, 3)
sel = result.selected
label(
    overlay,
    f"selected; fitted d = {sel.diameter_px:.0f} px (at the fit's size limit)",
    (int(sel.x_px) + 30, int(sel.y_px) + 50),
    GREEN,
    0.65,
)
other = [c for c in result.candidates if c is not sel]
if other:
    o = other[0]
    label(
        overlay,
        f"refused: score {o.score:.1f} > 2.5",
        (int(o.x_px) + 25, int(o.y_px) + 50),
        RED,
        0.65,
    )
save("field-detection.jpg", overlay)
facts["field"] = {
    "selected": [round(sel.x_px, 1), round(sel.y_px, 1), round(sel.diameter_px, 1)],
    "selected_score": round(sel.score, 2),
    "other_scores": [round(c.score, 2) for c in other if c.score is not None],
    "level_line_row": horizon,
    "candidates": len(result.candidates),
}

# close-up of the resting ball
x, y = int(sel.x_px), int(sel.y_px)
crop = cv2.cvtColor(gray[y - 70 : y + 70, x - 110 : x + 110], cv2.COLOR_GRAY2BGR)
crop = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_NEAREST)
cv2.circle(crop, (110 * 3, 70 * 3), int(sel.diameter_px / 2 * 3) + 6, GREEN, 3)
save("ball-closeup.jpg", crop)

# ---------------------------------------------------------------- swing clip
zf = zipfile.ZipFile(BUNDLE)
name = [n for n in zf.namelist() if n.endswith("frames.npz")][0]
clip = np.load(io.BytesIO(zf.read(name)))
video = clip["frames"]
pre = int(clip["pre_trigger_count"])
times = (clip["sensor_timestamp_ns"] - clip["sensor_timestamp_ns"][0]) / 1e6
tiles = []
for index in range(max(0, pre - 3), min(len(video), pre + 3)):
    tile = cv2.cvtColor(video[index][300:620, 420:900], cv2.COLOR_GRAY2BGR)
    tile = cv2.convertScaleAbs(tile, alpha=3.0, beta=0)  # brightened 3x for print
    label(tile, f"f{index}  {times[index]:.1f} ms", (10, 28), WHITE, 0.7)
    tiles.append(tile)
row1, row2 = np.hstack(tiles[:3]), np.hstack(tiles[3:6])
save("swing-contact-strip.jpg", np.vstack([row1, row2]))
facts["swing"] = {
    "clip": name.split("/")[-2],
    "fps": round(1000.0 / float(np.median(np.diff(times))), 1),
    "exposure_us": int(clip["exposure_us"][0]),
    "gain": float(clip["analogue_gain"][0]),
    "pre_trigger": pre,
    "frames": int(len(video)),
    "mean_dn": round(float(video.mean()), 1),
}
(OUT / "facts.json").write_text(json.dumps(facts, indent=2))
print(json.dumps(facts, indent=2))

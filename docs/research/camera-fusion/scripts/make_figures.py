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

# horizon: first image row whose ray points down, per column band
horizon = next(
    r for r in range(0, 800) if camera.ray_model.rays(np.array([640.0, float(r)]))[2] < 0
)
overlay = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
shade = overlay.copy()
shade[:horizon] = (shade[:horizon] * 0.45).astype(np.uint8)
overlay = cv2.addWeighted(shade, 1.0, overlay, 0.0, 0)
cv2.line(overlay, (0, horizon), (1279, horizon), AMBER, 2)
label(
    overlay,
    "horizon at the solved tilt: a resting ball cannot be above this line",
    (20, horizon - 12),
    AMBER,
    0.65,
)
for candidate in result.candidates:
    center = (int(round(candidate.x_px)), int(round(candidate.y_px)))
    radius = int(round(candidate.diameter_px / 2))
    color = GREEN if result.selected is not None and candidate is result.selected else RED
    cv2.circle(overlay, center, radius + 4, color, 3)
sel = result.selected
label(
    overlay,
    f"ball: {sel.diameter_px:.0f} px, selected (score {sel.score:.2f})",
    (int(sel.x_px) + 30, int(sel.y_px) + 50),
    GREEN,
)
other = [c for c in result.candidates if c is not sel]
if other:
    o = other[0]
    label(
        overlay,
        f"baseboard spot: score {o.score:.1f} (> 2.5, refused)",
        (int(o.x_px) + 25, int(o.y_px) + 50),
        RED,
    )
label(
    overlay,
    "1280x800, 3 ms x 6, LIS3DH pitch +1.75 deg; tape 1.25 m, static IWR 1.246 m",
    (20, 780),
    WHITE,
    0.6,
)
save("field-detection.jpg", overlay)
facts["field"] = {
    "selected": [round(sel.x_px, 1), round(sel.y_px, 1), round(sel.diameter_px, 1)],
    "horizon_row": horizon,
    "candidates": len(result.candidates),
}

# close-up: the white door behind the ball
x, y = int(sel.x_px), int(sel.y_px)
crop = cv2.cvtColor(gray[y - 70 : y + 70, x - 110 : x + 110], cv2.COLOR_GRAY2BGR)
crop = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_NEAREST)
cv2.circle(crop, (110 * 3, 70 * 3), int(sel.diameter_px / 2 * 3) + 6, GREEN, 3)
label(crop, "top half against the white door: contrast 12 DN (gate 12)", (12, 30), WHITE, 0.8)
label(crop, "base hidden in carpet pile", (12, 405), WHITE, 0.8)
save("ball-closeup.jpg", crop)

# roll check: the gap under the door, a level line in the room
cols = list(range(440, 830, 10))
rows = [445 + int(np.argmin(gray[445:480, c - 2 : c + 3].mean(axis=1))) for c in cols]
slope, intercept = np.polyfit(cols, rows, 1)
rollfig = cv2.cvtColor(gray[330:600, 330:930], cv2.COLOR_GRAY2BGR)
for c, r in zip(cols, rows):
    cv2.circle(rollfig, (c - 330, r - 330), 3, AMBER, -1)
cv2.line(
    rollfig,
    (440 - 330, int(slope * 440 + intercept) - 330),
    (830 - 330, int(slope * 830 + intercept) - 330),
    GREEN,
    2,
)
label(
    rollfig,
    f"door gap tilts {np.degrees(np.arctan(slope)):+.2f} deg in the image",
    (12, 28),
    GREEN,
    0.7,
)
label(rollfig, "LIS3DH roll read -2.92 deg", (12, 58), RED, 0.7)
save("roll-check.jpg", rollfig)
facts["roll_image_deg"] = round(float(np.degrees(np.arctan(slope))), 2)

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
    tile = cv2.convertScaleAbs(tile, alpha=3.0, beta=0)  # the clip is dark: 198 us exposure
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

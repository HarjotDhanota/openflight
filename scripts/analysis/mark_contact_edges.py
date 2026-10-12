"""Hand-mark heel, toe and topline on the two frames bracketing contact, for every shot.

Usage (from the repo root, with the session export in ~/Downloads or OPENFLIGHT_SESSION):

    uv run python scripts/analysis/mark_contact_edges.py --out contact_marks.jsonl
    uv run python scripts/analysis/mark_contact_edges.py --annotator harjot --pass-index 2

For each shot and each of the two frames (floor/ceil of the acoustic contact frame) a window
shows the frame at 12x with a 4-px grid and the teed ball circled. Click, in order:

    1. HEEL   - where the hosel meets the topline (left end of the head outline)
    2. TOE    - the far right end of the head outline, on the topline row
    3. TOPLINE - any point on the topline directly above the ball centre

Keys: `u` undo last click, `s` skip this frame, `q` quit (progress is saved after every frame).
Appends one JSON record per frame; never overwrites, so a second pass is a repeatability test.

Frames are un-mirrored before display (stored frames are horizontally mirrored). Coordinates
are recorded in un-mirrored 320x200 pixel units, sub-pixel from the 12x click position.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from openflight.camera.club_motion import detect_reference_ball  # noqa: E402

TEE_M = 1.575
SPEED_OF_SOUND = 343.0
SCALE = 12
CROP_W, CROP_H = 64, 40
LABELS = ("heel", "toe", "topline")


def find_session(explicit: str | None) -> Path:
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    if os.environ.get("OPENFLIGHT_SESSION"):
        candidates.append(Path(os.environ["OPENFLIGHT_SESSION"]))
    name = "openflight_session_20260825_181734_filtered"
    for root in (Path.home() / "Downloads", Path.home()):
        candidates += [root / name, root / name / name]
    for c in candidates:
        if (c / "shots.csv").is_file() and (c / "shots").is_dir():
            return c
    raise SystemExit("session export not found; pass --session or set OPENFLIGHT_SESSION")


def contact_frame(meta: dict) -> float:
    return float(meta["pre_trigger_frames"]) - TEE_M / SPEED_OF_SOUND * float(meta["delivered_fps"])


def render(frame: np.ndarray, ball, x0: int, y0: int, clicks: list, title: str) -> np.ndarray:
    g = frame[y0 : y0 + CROP_H, x0 : x0 + CROP_W]
    img = cv2.resize(
        cv2.cvtColor(g, cv2.COLOR_GRAY2BGR),
        None,
        fx=SCALE,
        fy=SCALE,
        interpolation=cv2.INTER_NEAREST,
    )
    for i in range(0, CROP_W, 4):
        cv2.line(img, (i * SCALE, 0), (i * SCALE, CROP_H * SCALE), (70, 70, 70), 1)
        cv2.putText(
            img,
            str(x0 + i),
            (i * SCALE + 2, CROP_H * SCALE - 4),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.32,
            (0, 200, 255),
            1,
            cv2.LINE_AA,
        )
    for j in range(0, CROP_H, 4):
        cv2.line(img, (0, j * SCALE), (CROP_W * SCALE, j * SCALE), (70, 70, 70), 1)
        cv2.putText(
            img,
            str(y0 + j),
            (2, j * SCALE + 12),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.32,
            (0, 200, 255),
            1,
            cv2.LINE_AA,
        )
    cv2.circle(
        img,
        (int((ball.x - x0 + 0.5) * SCALE), int((ball.y - y0 + 0.5) * SCALE)),
        int(ball.diameter_px / 2 * SCALE),
        (200, 190, 40),
        1,
        cv2.LINE_AA,
    )
    colours = ((0, 220, 0), (0, 0, 255), (40, 140, 255))
    for (name, px, py), col in zip(clicks, colours):
        p = (int((px - x0 + 0.5) * SCALE), int((py - y0 + 0.5) * SCALE))
        cv2.drawMarker(img, p, col, cv2.MARKER_CROSS, 18, 2, cv2.LINE_AA)
        cv2.putText(
            img, name, (p[0] + 8, p[1] - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 1, cv2.LINE_AA
        )
    nxt = LABELS[len(clicks)] if len(clicks) < 3 else "done - any key"
    cv2.putText(
        img,
        f"{title}   click: {nxt}   (u undo, s skip, q quit)",
        (6, 20),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        (0, 0, 0),
        3,
        cv2.LINE_AA,
    )
    cv2.putText(
        img,
        f"{title}   click: {nxt}   (u undo, s skip, q quit)",
        (6, 20),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        (255, 255, 255),
        1,
        cv2.LINE_AA,
    )
    return img


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--session")
    ap.add_argument("--out", default="contact_marks.jsonl")
    ap.add_argument("--annotator", default=os.environ.get("USERNAME", "unknown"))
    ap.add_argument("--pass-index", type=int, default=1)
    ap.add_argument("--exclude", default="shot_001_7-iron")
    args = ap.parse_args()
    session = find_session(args.session)
    out = Path(args.out)
    done = set()
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                if r.get("annotator") == args.annotator and r.get("pass_index") == args.pass_index:
                    done.add((r["shot"], r["frame"]))
    shots = sorted(
        p
        for p in (session / "shots").iterdir()
        if p.is_dir() and p.name not in args.exclude.split(",")
    )
    win = "contact edges"
    cv2.namedWindow(win, cv2.WINDOW_AUTOSIZE)
    state = {"clicks": [], "x0": 0, "y0": 0}

    def on_mouse(event, x, y, _flags, _param):
        if event == cv2.EVENT_LBUTTONDOWN and len(state["clicks"]) < 3:
            px = state["x0"] + x / SCALE - 0.5
            py = state["y0"] + y / SCALE - 0.5
            state["clicks"].append((LABELS[len(state["clicks"])], round(px, 2), round(py, 2)))

    cv2.setMouseCallback(win, on_mouse)
    for shot in shots:
        frames = np.load(shot / "frames.npz")["frames"][:, :, ::-1]
        meta = json.loads((shot / "camera_metadata.json").read_text())
        fc = contact_frame(meta)
        try:
            ball = detect_reference_ball(frames)
        except ValueError:
            print(f"{shot.name}: no ball, skipped")
            continue
        x0 = int(np.clip(round(ball.x - 34), 0, frames.shape[2] - CROP_W))
        y0 = int(np.clip(round(ball.y - 22), 0, frames.shape[1] - CROP_H))
        for f in (int(math.floor(fc)), int(math.ceil(fc))):
            if (shot.name, f) in done:
                continue
            state.update(clicks=[], x0=x0, y0=y0)
            title = f"{shot.name} f{f} ({(f - fc) * 1000 / meta['delivered_fps']:+.1f} ms)"
            skipped = False
            while True:
                cv2.imshow(win, render(frames[f], ball, x0, y0, state["clicks"], title))
                k = cv2.waitKey(30) & 0xFF
                if k == ord("q"):
                    cv2.destroyAllWindows()
                    return 0
                if k == ord("u") and state["clicks"]:
                    state["clicks"].pop()
                if k == ord("s"):
                    skipped = True
                    break
                if len(state["clicks"]) == 3 and k != 255:
                    break
            rec = {
                "shot": shot.name,
                "frame": f,
                "contact_frame": round(fc, 3),
                "annotator": args.annotator,
                "pass_index": args.pass_index,
                "ball": [round(ball.x, 2), round(ball.y, 2), round(ball.diameter_px, 2)],
                "mirror_corrected": True,
                "skipped": skipped,
                "marks": {name: [px, py] for name, px, py in state["clicks"]}
                if not skipped
                else {},
            }
            with out.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec) + "\n")
    cv2.destroyAllWindows()
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

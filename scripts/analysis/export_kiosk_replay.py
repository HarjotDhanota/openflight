"""Export a recorded tester session's kiosk shots and replay clips for the kiosk UI replay.

Usage: export_kiosk_replay.py <tester>/arm5/paired/run-NN <out dir>

Writes <out>/shots.json (the kiosk's own shot dicts in shot order, the setup record and
the target line) and <out>/clips/<camera_replay id>.webm (VP8, the raw frames at the
kiosk's 60 fps replay rate). Serve it with `REPLAY_DIR=<out> npm run dev:replay` in ui/.
"""

import glob
import json
import os
import sys

import cv2
import numpy as np

PLAYBACK_FPS = 60


def main(run: str, out: str) -> None:
    log = glob.glob(os.path.join(run, "session_*_arm5.jsonl"))[0]
    with open(log, encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle]
    start = next(row for row in rows if row["type"] == "session_start")
    final = {}
    for row in rows:
        if row["type"] in ("shot_detected", "shot_updated"):
            final[row["shot_number"]] = row  # shot_updated follows shot_detected: last wins
    clips = {}
    for path in glob.glob(os.path.join(run, "arm5", "camera", "*", "metadata.json")):
        with open(path, encoding="utf-8") as handle:
            clips[json.load(handle)["trigger_timestamp"]] = os.path.dirname(path)
    captures = {row["shot_number"]: row for row in rows if row["type"] == "camera_capture"}
    os.makedirs(os.path.join(out, "clips"), exist_ok=True)
    shots = []
    for number in sorted(final):
        shot = {key: value for key, value in final[number].items() if key not in ("ts", "type")}
        replay, capture = shot.get("camera_replay"), captures.get(number)
        if replay and capture:
            trigger = capture["trigger_timestamp"]
            nearest = min(clips, key=lambda stamp: abs(stamp - trigger))
            if abs(nearest - trigger) >= 0.01:
                raise ValueError(f"shot {number}: no capture folder at its trigger time")
            write_clip(clips[nearest], os.path.join(out, "clips", replay["id"] + ".webm"))
        shots.append(shot)
    tester_dir = os.path.dirname(os.path.dirname(os.path.dirname(run)))
    setup_path = os.path.join(tester_dir, "setup.json")
    setup = None
    if os.path.exists(setup_path):
        with open(setup_path, encoding="utf-8") as handle:
            setup = json.load(handle)
    recording = {
        "tester_id": os.path.basename(tester_dir),
        "session_uuid": start.get("session_uuid"),
        "club": shots[0].get("club", "driver") if shots else "driver",
        "setup": setup,
        "target_line": start["config"].get("target_line"),
        "shots": shots,
    }
    with open(os.path.join(out, "shots.json"), "w", encoding="utf-8") as handle:
        json.dump(recording, handle)
    print(f"{len(shots)} shots; {len(os.listdir(os.path.join(out, 'clips')))} clips")


def write_clip(capture_dir: str, path: str) -> None:
    if os.path.exists(path):
        return
    frames = np.load(os.path.join(capture_dir, "frames.npz"))["frames"]
    height, width = frames.shape[1:]
    writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"VP80"), PLAYBACK_FPS, (width, height))
    for frame in frames:
        writer.write(cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR))
    writer.release()


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])

"""Build the resting-ball detector fixtures from the Harjot-outdoor-test-1 session.

Outdoors at dusk on 24 Oct 2026, OV9281 with the 6 mm lens at 1280x800, 7-iron:

* ``empty-mat-edge``: the setup's ball-range capture (setup-20261025-87a0393da1ec,
  arm5-000002, 3000 us x 1). Its five frames' median, CROPPED to the rows of the
  mat's lit back edge. The whole-patch search found the mat edge, empty of any ball,
  more ball-like than the ball and withheld the range as ambiguous.
* ``shot-14``: all 24 frames of shot 14's clip (145 us x 12), CROPPED around the
  resting ball. The scene detector measured the ball 40.6 px across; the impact
  detector only its lit cap, 23.2 px, and the shot was withheld.
* ``setup-ball``: the same setup capture's median, CROPPED around the ball. The box step
  measured it 58.98 px across, the lit sphere grown into its shaded underside and shadow.
* ``shot-10`` and ``shot-05``: the first ten frames of each clip, CROPPED around the
  resting ball (shot 10, its lit cap 34.5 px to the scene detector) and around the gap in
  the net the scene detector took for a 33 px ball (shot 5, no ball on the mat).

    uv run python scripts/analysis/ball_detector_fixtures.py \
        ../pi-handoff/Harjot-outdoor-test-1 tests/fixtures/ball_detector
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

SETUP = "calibration/tee-range/guided/epochs/setup-20261025-87a0393da1ec"
SETUP_CAPTURE = "camera-arm5-000002-diagnostic.npz"
SETUP_CROP = (290, 330, 990, 540)  # x0, y0, x1, y1: the box's width, the mat's back edge
RUN = "arm5/paired/run-01"
SHOT = 14
SHOT_CROP = (745, 365, 850, 465)
SETUP_BALL_CROP = (640, 340, 810, 500)
# shot: (x0, y0, x1, y1) around the resting ball, or what the scene detector took for one
SHOT_FRAME_CROPS = {10: (774, 359, 894, 479), 5: (571, 331, 691, 451)}
SHOT_FRAME_COUNT = 10


def _capture_dir(session: Path, shot: int) -> Path:
    """The clip whose metadata carries this shot's camera_capture trigger time."""
    log = next((session / RUN).glob("session_*.jsonl"))
    trigger = None
    for line in log.read_text(encoding="utf-8").splitlines():
        event = json.loads(line) if line.strip() else {}
        if event.get("type") == "camera_capture" and event.get("shot_number") == shot:
            trigger = event["trigger_timestamp"]
    for directory in sorted((session / RUN / "arm5" / "camera").glob("camera_*")):
        metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
        if metadata.get("trigger_timestamp") == trigger:
            return directory
    raise ValueError(f"no clip carries shot {shot}'s trigger time")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    with np.load(args.session / SETUP / SETUP_CAPTURE) as data:
        frames = data["frames"]
    x0, y0, x1, y1 = SETUP_CROP
    np.savez_compressed(
        args.output / "outdoor-test-1-87a0393da1ec-empty-mat-edge.npz",
        frame=np.median(frames, axis=0).astype(np.uint8)[y0:y1, x0:x1],
        origin_px=np.asarray((x0, y0)),
        box_px=np.asarray((301, 296, 975, 676)),
    )

    x0, y0, x1, y1 = SETUP_BALL_CROP
    np.savez_compressed(
        args.output / "outdoor-test-1-87a0393da1ec-setup-ball.npz",
        frame=np.round(np.median(frames, axis=0)).astype(np.uint8)[y0:y1, x0:x1],
        origin_px=np.asarray((x0, y0)),
    )
    for shot, (x0, y0, x1, y1) in SHOT_FRAME_CROPS.items():
        with np.load(_capture_dir(args.session, shot) / "frames.npz") as data:
            crop = data["frames"][:SHOT_FRAME_COUNT, y0:y1, x0:x1]
        np.savez_compressed(
            args.output / f"outdoor-test-1-shot-{shot:02d}-resting.npz",
            frames=crop,
            origin_px=np.asarray((x0, y0)),
        )

    clip = _capture_dir(args.session, SHOT)
    with np.load(clip / "frames.npz") as data:
        frames = data["frames"]
        exposure_us, gain = int(data["exposure_us"][0]), float(data["analogue_gain"][0])
    x0, y0, x1, y1 = SHOT_CROP
    np.savez_compressed(
        args.output / "outdoor-test-1-shot-14-dark.npz",
        frames=frames[:, y0:y1, x0:x1],
        origin_px=np.asarray((x0, y0)),
        exposure_us=np.asarray(exposure_us),
        analogue_gain=np.asarray(gain),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

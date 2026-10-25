#!/usr/bin/env python3
"""Build the camera-fallback field fixture from the Harjot-outdoor-test-1 session.

Outdoors at dusk on 24 Oct 2026, OV9281 with the 6 mm lens at 1280x800, 120 fps, 7-iron,
the IWR lost on 32 of 33 shots:

* ``shot-28-launch``: shot 28's frames from nine before the first post-trigger frame
  to the clip's end (45 us x 22), CROPPED to the column the ball rose through and its
  resting place, with each frame's sensor timestamp and exposure. Its ball is a clean
  six-point track; the OPS read it at 87.6 mph radial.

    uv run python scripts/analysis/camera_fallback_fixtures.py \\
        ../pi-handoff/Harjot-outdoor-test-1 tests/fixtures/camera_fallback
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

RUN = "arm5/paired/run-01/arm5/camera"
SHOT_28_CLIP = "camera_20261024_173609_021_033"
SHOT_28_CROP = (730, 60, 830, 450)  # x0, y0, x1, y1
BACKGROUND_LEAD_FRAMES = 9


def build(tester_dir: Path, out_dir: Path) -> Path:
    data = np.load(tester_dir / RUN / SHOT_28_CLIP / "frames.npz")
    first_post = int(data["pre_trigger_count_by_exposure"])
    start = first_post - BACKGROUND_LEAD_FRAMES
    x0, y0, x1, y1 = SHOT_28_CROP
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "outdoor-test-1-shot-28-launch.npz"
    np.savez_compressed(
        path,
        frames=data["frames"][start:, y0:y1, x0:x1],
        sensor_timestamp_ns=data["sensor_timestamp_ns"][start:],
        exposure_us=data["exposure_us"][start:],
        first_post_index=np.asarray(BACKGROUND_LEAD_FRAMES),
        origin_px=np.asarray([x0, y0]),
        image_size_px=np.asarray([data["frames"].shape[-1], data["frames"].shape[-2]]),
    )
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tester_dir", type=Path)
    parser.add_argument("out_dir", type=Path)
    args = parser.parse_args()
    print(build(args.tester_dir, args.out_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

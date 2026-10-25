#!/usr/bin/env python3
"""Build the camera head track's field fixture: small crops of rendered, checked swings.

Research only. Each swing keeps the frames from 6 before its last pre-contact frame to
that frame, cropped round the club's last approach and the ball, with its clock,
exposures, trigger and gate latency, so tests/test_camera_head_track_field.py can
replay the head track on real pixels.

    uv run python scripts/analysis/head_track_fixtures.py CAPTURE_DIR:SHOT:BALL_X:BALL_Y ... \\
        --out tests/fixtures/head_track/outdoor-test-1.npz
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from openflight.camera.head_track import acoustic_contact_window, gate_latency_ns

BEFORE, AFTER = 6, 0
MARGIN_LEFT_PX, MARGIN_RIGHT_PX, ABOVE_PX, BELOW_PX = 300, 100, 430, 90


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("swings", nargs="+", help="capture_dir:shot:ball_x:ball_y")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    payload: dict[str, np.ndarray] = {"swing_count": np.asarray(len(args.swings))}
    for number, spec in enumerate(args.swings, start=1):
        capture, shot, ball_x, ball_y = spec.rsplit(":", 3)
        with np.load(Path(capture) / "frames.npz") as archive:
            clip = {name: archive[name] for name in archive.files}
        metadata = json.loads((Path(capture) / "metadata.json").read_text())
        stamps = clip["sensor_timestamp_ns"].astype(np.int64)
        middle = stamps + clip["exposure_us"].astype(np.int64) * 500
        window = acoustic_contact_window(
            float(clip["trigger_boottime_ns"]), gate_latency_ns(metadata)
        )
        last_rest = int(np.searchsorted(middle, window[0])) - 1
        first, stop = last_rest - BEFORE, last_rest + AFTER + 1
        bx, by = float(ball_x), float(ball_y)
        x0, x1 = int(bx - MARGIN_LEFT_PX), int(bx + MARGIN_RIGHT_PX)
        y0, y1 = max(0, int(by - ABOVE_PX)), int(by + BELOW_PX)
        prefix = f"s{number}_"
        payload[prefix + "shot"] = np.asarray(int(shot))
        payload[prefix + "frames"] = clip["frames"][first:stop, y0:y1, x0:x1]
        payload[prefix + "sensor_timestamp_ns"] = stamps[first:stop]
        payload[prefix + "exposure_us"] = clip["exposure_us"][first:stop]
        payload[prefix + "trigger_boottime_ns"] = np.asarray(clip["trigger_boottime_ns"])
        payload[prefix + "gate_latency_ns"] = np.asarray(gate_latency_ns(metadata))
        payload[prefix + "crop_offset_xy"] = np.asarray([x0, y0])
        payload[prefix + "ball_xy"] = np.asarray([bx, by])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(args.out, **payload)
    print(args.out, args.out.stat().st_size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

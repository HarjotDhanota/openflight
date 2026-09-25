"""A small capture tree shaped like a Pi tester folder, with Pi paths in its session log."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

TESTER = "pilot-1"
PI_RUN = f"/home/pi/openflight_sessions/tester_pilot/{TESTER}/arm5/paired/run-01"


def ops_capture(shot: int) -> dict:
    return {
        "type": "rolling_buffer_capture",
        "shot_number": shot,
        "sample_time": 10.0,
        "trigger_time": 10.1,
        "processor_config": {"sample_rate_hz": 30_000, "club_type": "7-iron"},
        "i_samples": [
            2048 + int(500 * math.cos(2 * math.pi * 4000.0 * i / 30_000)) for i in range(4096)
        ],
        "q_samples": [
            2048 + int(500 * math.sin(2 * math.pi * 4000.0 * i / 30_000)) for i in range(4096)
        ],
    }


def pgm(path: Path) -> None:
    path.write_bytes(b"P5\n8 4\n255\n" + bytes(range(32)))


def capture_tree(root: Path, shots=(1, 2)) -> Path:
    run = root / TESTER / "arm5" / "paired" / "run-01"
    (run / "iwr6843").mkdir(parents=True)
    events = [
        {
            "type": "session_start",
            "session_uuid": "session-bundle",
            "config": {"camera_capture": {"output_dir": f"{PI_RUN}/arm5/camera"}},
        }
    ]
    for shot in shots:
        capture = run / "arm5" / "camera" / f"camera_00{shot}"
        capture.mkdir(parents=True)
        np.savez(
            capture / "frames.npz",
            frames=np.zeros((3, 4, 8), np.uint8),
            sensor_timestamp_ns=np.arange(3, dtype=np.int64),
            host_timestamp_ns=np.arange(3, dtype=np.int64),
            exposure_us=np.full(3, 300, np.int32),
            analogue_gain=np.full(3, 4.0, np.float32),
        )
        (capture / "metadata.json").write_text("{}", encoding="utf-8")
        for label in ("first", "trigger", "last"):
            pgm(capture / f"{label}.pgm")
        (run / "iwr6843" / f"iwr_00{shot}.l3dump").write_bytes(b"raw-dump")
        events += [
            ops_capture(shot),
            {"type": "shot_detected", "shot_number": shot, "ball_speed_mph": 70.0},
            {
                "type": "camera_capture",
                "shot_number": shot,
                "capture_path": f"{PI_RUN}/arm5/camera/camera_00{shot}",
            },
            {
                "type": "iwr6843_capture",
                "shot_number": shot,
                "capture_path": f"{PI_RUN}/iwr6843/iwr_00{shot}.l3dump",
            },
        ]
    (run / "session_20260924_185002_arm5.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )
    (root / TESTER / "arm5" / "arm.json").write_text('{"gain": 6.0}\n', encoding="utf-8")
    (root / TESTER / "impact").mkdir()
    pgm(root / TESTER / "impact" / "camera_001.pgm")
    (root / TESTER / "ladder.json").write_text(
        json.dumps(
            {"rungs": {}, "photos": {"camera_001": f"/home/pi/x/{TESTER}/impact/camera_001.pgm"}}
        ),
        encoding="utf-8",
    )
    (root / "tester-server.log").write_bytes(b"tester alive\n")
    (root / "tester-server.log.1").write_bytes(b"prior run\n")
    return root

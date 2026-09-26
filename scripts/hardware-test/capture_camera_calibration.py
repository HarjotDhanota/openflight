#!/usr/bin/env python3
"""Capture one OV9281 checkerboard view per sound-trigger edge."""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from openflight.camera.triggered_buffer import unpack_r8_frame  # noqa: E402
from openflight.gpio_factory import ensure_lgpio_pin_factory  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trigger-pin", type=int, default=17)
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    parser.add_argument("--fps", type=float, required=True)
    parser.add_argument("--exposure-us", type=int, required=True)
    parser.add_argument("--gain", type=float, required=True)
    parser.add_argument("--captures", type=int, default=12)
    parser.add_argument("--rotate-180", action="store_true")
    parser.add_argument("--outdir", type=Path, required=True)
    return parser.parse_args(argv)


def validate_args(args: argparse.Namespace) -> None:
    for name in ("width", "height", "fps", "exposure_us", "gain", "captures"):
        if getattr(args, name) <= 0:
            raise ValueError(f"--{name.replace('_', '-')} must be positive")
    frame_period_us = round(1_000_000 / args.fps)
    if args.exposure_us >= frame_period_us:
        raise ValueError(f"--exposure-us must be shorter than {frame_period_us} us")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        validate_args(args)
        import cv2  # pylint: disable=import-error,import-outside-toplevel
        from gpiozero import Button  # pylint: disable=import-error,import-outside-toplevel
        from picamera2 import Picamera2  # pylint: disable=import-error,import-outside-toplevel
    except (ImportError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    args.outdir.mkdir(parents=True, exist_ok=True)
    capture_set = datetime.now().strftime("%Y%m%d_%H%M%S")
    frame_duration_us = round(1_000_000 / args.fps)
    camera = Picamera2()
    config = camera.create_video_configuration(
        raw={"size": (args.width, args.height), "format": "R8"},
        controls={
            "AeEnable": False,
            "ExposureTime": args.exposure_us,
            "AnalogueGain": args.gain,
            "FrameDurationLimits": (frame_duration_us, frame_duration_us),
        },
        buffer_count=8,
        display=None,
        encode=None,
    )
    camera.configure(config)
    ensure_lgpio_pin_factory()
    trigger = threading.Event()
    button = Button(args.trigger_pin, pull_up=False, bounce_time=None)
    last_edge = 0.0

    def on_trigger() -> None:
        nonlocal last_edge
        now = time.monotonic()
        if now - last_edge >= 0.5:
            last_edge = now
            trigger.set()

    button.when_pressed = on_trigger
    records: list[dict[str, object]] = []
    camera_started = False
    try:
        camera.start()
        camera_started = True
        time.sleep(0.5)
        print(f"Calibration capture armed at {args.width}x{args.height}, {args.fps:g} FPS.")
        print(
            "Move the board to a new position, then clap. Include every corner and several tilts."
        )
        for number in range(1, args.captures + 1):
            print(f"Waiting for checkerboard view {number}/{args.captures}...", flush=True)
            trigger.wait()
            trigger.clear()
            request = camera.capture_request()
            try:
                metadata = request.get_metadata()
                image = unpack_r8_frame(
                    request.make_array("raw"),
                    args.width,
                    args.height,
                    args.rotate_180,
                )
            finally:
                request.release()
            path = args.outdir / f"{capture_set}_view_{number:02d}.png"
            if not cv2.imwrite(str(path), np.asarray(image, dtype=np.uint8)):
                raise RuntimeError(f"could not write {path}")
            record = {
                "view": number,
                "path": path.name,
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "sensor_timestamp_ns": int(metadata.get("SensorTimestamp", 0)),
                "exposure_us": int(metadata.get("ExposureTime", 0)),
                "analogue_gain": float(metadata.get("AnalogueGain", 0.0)),
            }
            records.append(record)
            print(f"Captured {path.name}. Reposition the board before the next clap.", flush=True)
    except KeyboardInterrupt:
        print("Calibration capture cancelled.")
        return 130
    finally:
        button.close()
        if camera_started:
            camera.stop()
        camera.close()

    (args.outdir / f"{capture_set}_metadata.json").write_text(
        json.dumps(records, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Captured {len(records)} checkerboard views in {args.outdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

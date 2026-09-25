#!/usr/bin/env python3
"""Capture one raw static IWR6843 setup ring and derive diagnostic evidence."""

from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
from pathlib import Path

from openflight.iwr6843.static_capture import StaticCaptureInputs, capture_static_range


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Save one raw-first IWR6843 empty-tee or ball-present setup capture. "
            "Stop the OpenFlight runtime before using this command."
        )
    )
    parser.add_argument("--capture-id", required=True, help="Safe unique output identifier")
    parser.add_argument(
        "--kind", required=True, choices=("empty", "ball_present"), help="Scene state"
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path, help="Exact runtime radar cfg")
    parser.add_argument(
        "--firmware", required=True, type=Path, help="Exact declared flashed firmware image"
    )
    parser.add_argument("--rig-geometry", required=True, type=Path)
    parser.add_argument("--calibration", required=True, type=Path)
    parser.add_argument("--port", help="IWR6843 serial device; auto-detect when omitted")
    parser.add_argument(
        "--settle-seconds",
        type=float,
        default=1.0,
        help="Post-config fill time before requesting the ring (minimum 0.25; default 1.0)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run one setup capture and return a shell-friendly status code."""
    args = _parser().parse_args(argv)
    cancel = threading.Event()

    def request_cancel(_signum, _frame):
        cancel.set()

    previous = {}
    for name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, name, None)
        if sig is not None:
            previous[sig] = signal.signal(sig, request_cancel)
    try:
        result = capture_static_range(
            StaticCaptureInputs(
                capture_id=args.capture_id,
                capture_kind=args.kind,
                output_dir=args.output_dir,
                config_path=args.config,
                firmware_path=args.firmware,
                rig_geometry_path=args.rig_geometry,
                calibration_path=args.calibration,
                port=args.port,
                settle_s=args.settle_seconds,
            ),
            cancel_event=cancel,
        )
    except (FileExistsError, FileNotFoundError, ValueError) as error:
        print(f"capture refused: {error}", file=sys.stderr)
        return 2
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["usable"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

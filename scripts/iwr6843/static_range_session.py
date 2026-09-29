#!/usr/bin/env python3
"""Hold the IWR6843 open for a range setup's empty and ball captures.

The tester service starts this once per setup and sends capture requests as
JSON lines on stdin; see ``openflight.iwr6843.static_session`` for the protocol.
Stop the OpenFlight runtime before using it.
"""

from __future__ import annotations

import argparse
import json
import queue
import signal
import sys
import threading
from pathlib import Path

from openflight.iwr6843.static_session import IDLE_TIMEOUT_S, SessionInputs, serve


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", required=True, type=Path, help="Exact runtime radar cfg")
    parser.add_argument(
        "--firmware", required=True, type=Path, help="Exact declared flashed firmware image"
    )
    parser.add_argument("--rig-geometry", required=True, type=Path)
    parser.add_argument("--calibration", required=True, type=Path)
    parser.add_argument("--port", help="IWR6843 serial device; auto-detect when omitted")
    parser.add_argument("--settle-seconds", type=float, default=1.0)
    parser.add_argument("--idle-timeout-s", type=float, default=IDLE_TIMEOUT_S)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Serve capture requests until closed, idle, signalled or stdin ends."""
    args = _parser().parse_args(argv)
    requests: queue.Queue = queue.Queue()
    cancel = threading.Event()

    def read_stdin():
        for line in sys.stdin:
            if line.strip():
                requests.put(line)
        requests.put(None)

    def request_cancel(_signum, _frame):
        cancel.set()
        requests.put(None)

    for name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, name, None)
        if sig is not None:
            signal.signal(sig, request_cancel)

    def emit(event: dict) -> None:
        try:
            print(json.dumps(event), flush=True)
        except (BrokenPipeError, OSError):
            # the tester went away; the result files still carry the outcome
            pass

    threading.Thread(target=read_stdin, daemon=True, name="session-stdin").start()
    return serve(
        requests,
        emit,
        SessionInputs(
            config_path=args.config,
            firmware_path=args.firmware,
            rig_geometry_path=args.rig_geometry,
            calibration_path=args.calibration,
            port=args.port,
            settle_s=args.settle_seconds,
        ),
        idle_timeout_s=args.idle_timeout_s,
        cancel_event=cancel,
    )


if __name__ == "__main__":
    raise SystemExit(main())

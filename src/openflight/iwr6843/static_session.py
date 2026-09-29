"""Hold the IWR6843 open across the empty and ball setup captures.

Closing the CP2105 port after a capture costs 5 s on the Pi: the kernel's
purge-on-close request times out (``cp210x ttyUSB0: failed set request 0x12
status: -110``, 2026-09-29), whichever way the port is closed. A setup takes an
empty capture, waits for the ball to be placed, then a ball capture, so one
process keeps the port open between them and closes it once.

The protocol is JSON lines. Requests arrive on stdin::

    {"op": "capture", "capture_id": ..., "kind": "empty"|"ball_present",
     "output_dir": ..., "log_path": ..., "close_after": false}
    {"op": "close"}

Events go to stdout: ``ready``, ``done`` (one per capture request, sent before
any slow close), ``error`` (a bad request) and ``closed`` (last). Each capture
still reserves its ID and writes the same result file as a one-shot capture, so
a restarted tester recovers it the same way.
"""

from __future__ import annotations

import json
import os
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from openflight.iwr6843.driver import IWR6843Radar
from openflight.iwr6843.static_capture import StaticCaptureInputs, capture_static_range

EVENTS = ("ready", "done", "error", "closed")
# Release the radar if no capture is requested for this long.
IDLE_TIMEOUT_S = 600.0


@dataclass(frozen=True)
class SessionInputs:
    """Files and port shared by every capture in one session."""

    config_path: Path
    firmware_path: Path
    rig_geometry_path: Path
    calibration_path: Path
    port: str | None = None
    settle_s: float = 1.0


def _close_radar(radar: Any) -> None:
    radar.close()


def serve(  # pylint: disable=too-many-arguments,too-many-locals,too-many-branches
    requests: "queue.Queue[str | None]",
    emit: Callable[[dict], None],
    inputs: SessionInputs,
    *,
    radar_factory: Callable[..., Any] = IWR6843Radar,
    capture: Callable[..., dict] = capture_static_range,
    close: Callable[[Any], None] = _close_radar,
    idle_timeout_s: float = IDLE_TIMEOUT_S,
    cancel_event: threading.Event | None = None,
) -> int:
    """Answer capture requests until closed, idle, cancelled or stdin ends."""
    cancel = cancel_event or threading.Event()
    held: dict[str, Any] = {"radar": None}

    def factory(**_kwargs):
        if held["radar"] is None:
            held["radar"] = radar_factory(port=inputs.port)
        return held["radar"]

    emit({"event": "ready", "pid": os.getpid()})
    reason = "stdin_closed"
    while True:
        try:
            line = requests.get(timeout=idle_timeout_s)
        except queue.Empty:
            reason = "idle"
            break
        if line is None:
            reason = "cancelled" if cancel.is_set() else "stdin_closed"
            break
        try:
            request = json.loads(line)
        except ValueError:
            emit({"event": "error", "message": "request is not JSON"})
            continue
        if not isinstance(request, dict) or request.get("op") not in {"capture", "close"}:
            emit({"event": "error", "message": "request op must be capture or close"})
            continue
        if request["op"] == "close":
            reason = "requested"
            break
        result: dict | None = None
        refused = None
        try:
            result = capture(
                StaticCaptureInputs(
                    capture_id=str(request["capture_id"]),
                    capture_kind=str(request["kind"]),
                    output_dir=Path(request["output_dir"]),
                    config_path=inputs.config_path,
                    firmware_path=inputs.firmware_path,
                    rig_geometry_path=inputs.rig_geometry_path,
                    calibration_path=inputs.calibration_path,
                    port=inputs.port,
                    settle_s=inputs.settle_s,
                ),
                radar_factory=factory,
                cancel_event=cancel,
                close_radar=False,
            )
        except (FileExistsError, FileNotFoundError, KeyError, ValueError) as error:
            refused = f"capture refused: {error}"
        if result is not None and not result.get("radar_left_open"):
            # the capture closed it (or should have): the next one reopens
            held["radar"] = None
        log_path = request.get("log_path")
        if log_path:
            try:
                with Path(log_path).open("a", encoding="utf-8") as handle:
                    handle.write(
                        (json.dumps(result, indent=2, sort_keys=True) if result else refused) + "\n"
                    )
            except OSError:
                pass
        emit(
            {
                "event": "done",
                "capture_id": request.get("capture_id"),
                "usable": bool(result and result.get("usable")),
                "status": result.get("status") if result else "refused",
                "radar_open": held["radar"] is not None,
                "refused": refused,
            }
        )
        if request.get("close_after"):
            reason = "close_after"
            break
        if cancel.is_set():
            reason = "cancelled"
            break
    close_s = None
    close_error = None
    if held["radar"] is not None:
        started = time.monotonic()
        try:
            close(held["radar"])
        except Exception as error:  # pylint: disable=broad-exception-caught
            close_error = f"{type(error).__name__}: {error}"
        close_s = round(time.monotonic() - started, 3)
        held["radar"] = None
    emit({"event": "closed", "reason": reason, "close_s": close_s, "close_error": close_error})
    return 0


__all__ = ["EVENTS", "IDLE_TIMEOUT_S", "SessionInputs", "serve"]

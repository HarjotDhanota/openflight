#!/usr/bin/env python3
"""Verify that the tester can reach the IWR6843 single-port firmware CLI."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

import serial

from openflight.iwr6843.driver import (
    _CP2105_STANDARD_INTERFACE,
    _PROBE_WINDOW_S,
    _RESET_ACTION,
    IWR6843Radar,
    _probe_help,
    port_identity,
)

EVIDENCE_SCHEMA = "openflight.iwr6843.cli_check.v1"
EVIDENCE_PREFIX = "IWR6843 CLI evidence: "


def no_help_reply_message(port: str) -> str:
    """Why a configured port gave no CLI help, naming the device and the operator action."""
    identity = port_identity(port)
    where = port if identity["resolved"] == port else f"{port} -> {identity['resolved']}"
    interface = identity["cp2105_interface"]
    if interface:
        where += f" ({interface})"
    if interface and identity["usb_interface"] == _CP2105_STANDARD_INTERFACE:
        action = (
            "this is the CP2105 Standard interface, which has no CLI: run this check with "
            "the same adapter's Enhanced if00 port (/dev/serial/by-id/...-if00-port0)"
        )
    else:
        action = f"{_RESET_ACTION}, then run this check again on the same port"
        if not interface:
            action = f"confirm it is the CP2105 Enhanced/UARTA interface (if00), then {action}"
    return f"IWR6843 CLI did not answer help within {_PROBE_WINDOW_S:g} s on {where}; {action}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main(argv: list[str] | None = None) -> int:
    """Probe the CLI once and return a shell status; a failure prints one reason line."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--port",
        help=(
            "IWR6843 Enhanced/UARTA serial device; auto-detect when omitted. "
            "Prefer a stable /dev/serial/by-id/...-if00-port0 path."
        ),
    )
    parser.add_argument(
        "--operator-reset",
        choices=("pressed", "not-pressed"),
        help=(
            "Whether the operator pressed the IWR6843 RESET button before this check; "
            "recorded as null when omitted."
        ),
    )
    args = parser.parse_args(argv)
    events: list[dict] = []
    evidence = {
        "schema": EVIDENCE_SCHEMA,
        "started_at_utc": _utc_now(),
        "completed_at_utc": None,
        "requested_port": args.port,
        "operator_reset": args.operator_reset,
        "port": None,
        "result": "failed",
        "error": None,
        "events": events,
    }
    try:
        radar = IWR6843Radar(port=args.port, events=events)
        evidence["port"] = radar.port
        try:
            if b"sensorStart" not in _probe_help(radar.ser, radar.port, events):
                raise RuntimeError(no_help_reply_message(radar.port))
        finally:
            radar.close()
    except (OSError, RuntimeError, serial.SerialException) as error:
        evidence["error"] = {"type": type(error).__name__, "message": str(error)}
        _print_evidence(evidence)
        # One line, last in the job log, so the tester page can show the reason.
        print(f"IWR6843 CLI check failed: {error}", file=sys.stderr)
        return 1
    evidence["result"] = "ready"
    _print_evidence(evidence)
    print(f"IWR6843 CLI ready on {radar.port}")
    return 0


def _print_evidence(evidence: dict) -> None:
    evidence["completed_at_utc"] = _utc_now()
    # Flushed so it precedes the reason line when stdout and stderr share a log.
    print(EVIDENCE_PREFIX + json.dumps(evidence, sort_keys=True), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())

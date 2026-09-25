#!/usr/bin/env python3
"""Verify that the tester can reach the IWR6843 single-port firmware CLI."""

from __future__ import annotations

import argparse

from openflight.iwr6843.driver import IWR6843Radar


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--port",
        help=(
            "IWR6843 Enhanced/UARTA serial device; auto-detect when omitted. "
            "Prefer a stable /dev/serial/by-id/...-if00-port0 path."
        ),
    )
    args = parser.parse_args(argv)
    radar = IWR6843Radar(port=args.port)
    try:
        response = radar.cmd("help")
        if "sensorStart" not in response:
            raise RuntimeError(
                "IWR6843 CLI did not answer on the selected port; use the CP2105 "
                "Enhanced/UARTA interface (if00), set functional mode, and press RESET"
            )
        print(f"IWR6843 CLI ready on {radar.port}")
    finally:
        radar.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

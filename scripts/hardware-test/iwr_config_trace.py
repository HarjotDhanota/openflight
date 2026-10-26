"""Research-only ADC setup trace; no RF-start, RAM-load or flash operation."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from openflight.iwr6843.clock_evidence import _hex  # noqa: E402
from openflight.iwr6843.config_trace import record_config_trace  # noqa: E402
from openflight.iwr6843.driver import IWR6843Radar  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--build", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--timeout-s", type=float, default=10.0)
    args = parser.parse_args(argv)
    try:
        _hex(args.build, 64)
        if not math.isfinite(args.timeout_s) or not 0 < args.timeout_s <= 15:
            raise ValueError("diagnostic timeout must be positive and at most 15 seconds")
        with args.output.open("x", encoding="utf-8") as handle:

            def emit(row):
                handle.write(json.dumps(row) + "\n")
                handle.flush()

            with IWR6843Radar(port=args.port) as radar:
                status = record_config_trace(
                    radar.ser, emit, build=args.build, timeout_ns=int(args.timeout_s * 10**9)
                )
        return 0 if status == "complete" else 1
    except (OSError, ValueError, RuntimeError) as error:
        print(f"IWR configuration trace failed: {type(error).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

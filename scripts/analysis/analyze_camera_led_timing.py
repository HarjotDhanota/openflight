#!/usr/bin/env python3
"""Analyze recorded LED transitions without accessing Pi hardware."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from openflight.camera.led_calibration import analyze_main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(analyze_main())

#!/usr/bin/env python3
"""Choose an LED ROI on a saved fit frame without a Pi or a running server."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from openflight.camera.led_preview import preview_main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(preview_main())

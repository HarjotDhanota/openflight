#!/usr/bin/env python3
"""Record GATE LED calibration clips with fixed exposure and original event evidence."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from openflight.camera.led_recording import record_main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(record_main())

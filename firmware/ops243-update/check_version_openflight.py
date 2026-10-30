#!/usr/bin/env python3
"""Run inside the existing OpenFlight checkout using its uv environment."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "src"))
from openflight.ops243 import OPS243Radar

radar = OPS243Radar(port="/dev/ttyAMA0")
try:
    radar.connect()
    print("OPS243 firmware:", radar.get_firmware_version())
finally:
    radar.disconnect()

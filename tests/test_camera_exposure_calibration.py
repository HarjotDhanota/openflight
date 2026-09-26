"""Tests for camera exposure calibration command options."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "hardware-test"
    / "calibrate_camera_exposure.py"
)
_SPEC = importlib.util.spec_from_file_location("calibrate_camera_exposure", _SCRIPT)
calibration = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = calibration
_SPEC.loader.exec_module(calibration)


def test_no_prompt_option_supports_browser_driven_runs() -> None:
    args = calibration.parse_args(["--no-prompt"])

    assert args.no_prompt is True

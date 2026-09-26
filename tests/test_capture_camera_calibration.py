"""Argument validation for the sound-triggered calibration capture helper."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "hardware-test"
    / "capture_camera_calibration.py"
)
_SPEC = importlib.util.spec_from_file_location("capture_camera_calibration", _SCRIPT)
capture = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = capture
_SPEC.loader.exec_module(capture)


def arguments(*extra: str):
    return capture.parse_args(
        [
            "--width",
            "320",
            "--height",
            "200",
            "--fps",
            "450",
            "--exposure-us",
            "150",
            "--gain",
            "4",
            "--outdir",
            "captures",
            *extra,
        ]
    )


def test_valid_capture_settings_are_accepted() -> None:
    args = arguments("--captures", "12", "--rotate-180")

    capture.validate_args(args)

    assert args.captures == 12
    assert args.rotate_180 is True


def test_nonpositive_values_are_rejected() -> None:
    with pytest.raises(ValueError, match="--gain must be positive"):
        capture.validate_args(arguments("--gain", "0"))


def test_exposure_must_fit_inside_frame_period() -> None:
    with pytest.raises(ValueError, match="shorter than 2222 us"):
        capture.validate_args(arguments("--exposure-us", "2222"))

"""The gain screen records the hitting zone as well as the whole frame."""

import importlib.util
from pathlib import Path

import numpy as np
import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "hardware-test"
    / "calibrate_camera_exposure.py"
)


def _module():
    spec = importlib.util.spec_from_file_location("calibrate_camera_exposure", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_clipped_sky_does_not_hide_a_well_exposed_hitting_zone():
    images = np.full((3, 800, 1280), 255, dtype=np.uint8)
    images[:, 360:, :] = 110  # the lower half: mat and ball

    summary = _module().summarize_images(images, 300, 1.0)

    assert summary["clipped_pct"] > 40.0
    assert summary["zone_median"] == pytest.approx(110.0)
    assert summary["zone_clipped_pct"] == pytest.approx(0.0)


def test_the_sensor_black_level_is_read_from_frame_metadata():
    module = _module()

    assert module.black_level_dn({"SensorBlackLevels": (4096, 4096, 4096, 4096)}) == pytest.approx(
        16.0
    )
    assert module.black_level_dn({}) is None

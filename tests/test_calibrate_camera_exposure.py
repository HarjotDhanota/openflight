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


def _sweep_args():
    from types import SimpleNamespace  # pylint: disable=import-outside-toplevel

    return SimpleNamespace(
        target_mean_low=80.0, target_mean_high=150.0, max_clipped_pct=1.0, max_dark_pct=5.0
    )


def _result(gain, mean, clipped, dark=0.0):
    return {"gain": gain, "mean": mean, "clipped_pct": clipped, "dark_pct": dark}


def test_a_dark_scene_is_not_told_to_reduce_its_brightness():
    """Outdoors-test-5 at dusk: gain 4 was the best, too dark, and every brighter gain clipped."""
    results = [
        _result(4, 48.6, 0.97),
        _result(1, 23.9, 0.0),
        _result(6, 60.6, 8.55),
        _result(15.9, 94.3, 14.14),
    ]

    reason = _module().miss_reason(results[0], results, _sweep_args())

    assert "reduce scene brightness" not in reason
    assert "too dark" in reason and "48.6" in reason
    assert "brighter settings clip" in reason


def test_a_dark_scene_with_headroom_is_told_to_add_light_or_sweep_higher():
    results = [_result(16, 50.0, 0.0), _result(8, 30.0, 0.0)]

    reason = _module().miss_reason(results[0], results, _sweep_args())

    assert "too dark" in reason
    assert "add light" in reason
    assert "reduce scene brightness" not in reason


def test_a_bright_scene_is_still_told_to_reduce_its_brightness():
    results = [_result(1, 190.0, 6.0), _result(2, 230.0, 20.0)]

    reason = _module().miss_reason(results[0], results, _sweep_args())

    assert "too bright" in reason and "6.00% clipped" in reason
    assert "reduce scene brightness" in reason

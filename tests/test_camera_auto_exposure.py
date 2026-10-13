"""Tests for the hitting zone's exposure rating (the kiosk's swing exposure lives in
swing_exposure and is tested in test_swing_exposure)."""

import numpy as np
import pytest

from openflight.camera import auto_exposure
from openflight.camera.auto_exposure import measure_exposure


@pytest.mark.parametrize(
    ("value", "expected"),
    [(18, "too_dark"), (110, "good"), (252, "too_bright")],
)
def test_measure_exposure_classifies_impact_zone(value, expected):
    image = np.full((200, 320), value, dtype=np.uint8)
    if expected == "good":
        image[100:175, 90:230] = np.tile(
            np.linspace(45, 185, 140, dtype=np.uint8),
            (75, 1),
        )

    result = measure_exposure(image)

    assert result.status == expected
    assert result.sample_available is True


def test_measure_exposure_flags_nearly_black_view():
    result = measure_exposure(np.zeros((200, 320), dtype=np.uint8))

    assert result.status == "too_dark"
    assert "lens cover" in result.message


@pytest.mark.parametrize(
    "retired",
    ["EXPOSURE_STEPS", "ExposureStep", "AutoExposurePolicy", "exposure_steps_for_fps"],
)
def test_the_zone_metered_startup_ladder_is_retired(retired):
    """13 Oct: 650-1250 us steps past the blur ceiling and gains of 18 and 20 the sensor
    clamps at 15.94, metered on the zone and locked at startup. The resting ball meters now."""
    assert not hasattr(auto_exposure, retired)

"""The LIS3DH placement guard: upright and near the expected pose, or why not."""

import pytest

from openflight.inclinometer.placement import PLACEMENT_TOLERANCE_DEG, placement_guard


def _reading(pitch=0.0, x_g=0.0, z_g=1.0, **extra):
    return {"calibrated_pitch_deg": pitch, "x_g": x_g, "y_g": 0.0, "z_g": z_g, **extra}


@pytest.mark.parametrize(
    ("reading", "ready", "warned"),
    [
        (_reading(2.0), True, False),
        (_reading(2.01), True, True),
        (_reading(-2.01), True, True),
        (_reading(x_g=1.0, z_g=0.01), True, True),
        (_reading(x_g=-1.0, z_g=0.01), True, True),
        (_reading(z_g=-1.0), False, False),
        (_reading(roll_deg=True), False, False),
        (_reading(float("nan")), False, False),
    ],
)
def test_placement_guard_flags_deviation_and_blocks_invalid_orientation(reading, ready, warned):
    result = placement_guard(reading)
    assert result["ready"] is ready
    assert result["warned"] is warned


def test_the_guard_reports_the_errors_against_the_expected_pose():
    result = placement_guard(
        _reading(7.5, roll_deg=-1.0), expected_pitch_deg=6.0, expected_roll_deg=-1.5
    )
    assert result["ready"] is True and result["warned"] is False
    assert result["pitch_error_deg"] == pytest.approx(1.5)
    assert result["roll_error_deg"] == pytest.approx(0.5)
    assert result["tolerance_deg"] == PLACEMENT_TOLERANCE_DEG
    assert result["accuracy_qualified"] is False

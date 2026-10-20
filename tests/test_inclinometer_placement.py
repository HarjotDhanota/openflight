"""The LIS3DH placement guard: upright and near the expected pose, or why not."""

import pytest

from openflight.inclinometer.placement import PLACEMENT_TOLERANCE_DEG, placement_guard


@pytest.mark.parametrize(
    ("reading", "ready", "warned"),
    [
        ({"calibrated_pitch_deg": 2.0, "x_g": 0.0, "y_g": 0.0, "z_g": 1.0}, True, False),
        ({"calibrated_pitch_deg": 2.01, "x_g": 0.0, "y_g": 0.0, "z_g": 1.0}, True, True),
        ({"calibrated_pitch_deg": -2.01, "x_g": 0.0, "y_g": 0.0, "z_g": 1.0}, True, True),
        ({"calibrated_pitch_deg": 0.0, "x_g": 1.0, "y_g": 0.0, "z_g": 0.01}, True, True),
        ({"calibrated_pitch_deg": 0.0, "x_g": -1.0, "y_g": 0.0, "z_g": 0.01}, True, True),
        ({"calibrated_pitch_deg": 0.0, "x_g": 0.0, "y_g": 0.0, "z_g": -1.0}, False, False),
        (
            {
                "calibrated_pitch_deg": 0.0,
                "roll_deg": True,
                "x_g": 0.0,
                "y_g": 0.0,
                "z_g": 1.0,
            },
            False,
            False,
        ),
        (
            {
                "calibrated_pitch_deg": float("nan"),
                "x_g": 0.0,
                "y_g": 0.0,
                "z_g": 1.0,
            },
            False,
            False,
        ),
    ],
)
def test_placement_guard_flags_deviation_and_blocks_invalid_orientation(reading, ready, warned):
    result = placement_guard(reading)
    assert result["ready"] is ready
    assert result["warned"] is warned


def test_the_guard_reports_the_errors_against_the_expected_pose():
    result = placement_guard(
        {"calibrated_pitch_deg": 7.5, "roll_deg": -1.0, "x_g": 0.0, "y_g": 0.0, "z_g": 1.0},
        expected_pitch_deg=6.0,
        expected_roll_deg=-1.5,
    )
    assert result["ready"] is True and result["warned"] is False
    assert result["pitch_error_deg"] == pytest.approx(1.5)
    assert result["roll_error_deg"] == pytest.approx(0.5)
    assert result["tolerance_deg"] == PLACEMENT_TOLERANCE_DEG
    assert result["accuracy_qualified"] is False

"""The placement gate: every named status, the boundary, and the absences."""

from __future__ import annotations

import math

import pytest

from openflight.placement import (
    PLACEMENT_STATUSES,
    PLACEMENT_TOLERANCE_DEG,
    assess_placement,
)


def _assess(pitch, roll, *, expected_pitch=10.0, expected_roll=0.0, tolerance=5.0):
    return assess_placement(
        pitch_deg=pitch,
        roll_deg=roll,
        expected_pitch_deg=expected_pitch,
        expected_roll_deg=expected_roll,
        tolerance_deg=tolerance,
    )


def test_the_designed_placement_is_ok_and_carries_signed_errors():
    assessment = _assess(11.0, -1.5)

    assert assessment.status == "ok"
    assert assessment.ok is True
    assert assessment.pitch_error_deg == pytest.approx(1.0)
    assert assessment.roll_error_deg == pytest.approx(-1.5)
    assert assessment.tolerance_deg == 5.0
    assert "placed as designed" in assessment.reason


def test_pitch_alone_outside_the_tolerance_is_tilted_pitch():
    assessment = _assess(17.0, 0.0)

    assert assessment.status == "tilted_pitch"
    assert assessment.ok is False
    assert assessment.pitch_error_deg == pytest.approx(7.0)
    assert assessment.roll_error_deg == pytest.approx(0.0)
    assert "pitch is outside" in assessment.reason


def test_roll_alone_outside_the_tolerance_is_tilted_roll():
    assessment = _assess(10.0, -8.0)

    assert assessment.status == "tilted_roll"
    assert assessment.roll_error_deg == pytest.approx(-8.0)
    assert "roll is outside" in assessment.reason


def test_both_axes_outside_the_tolerance_is_tilted_both():
    assessment = _assess(2.0, 9.0)

    assert assessment.status == "tilted_both"
    assert assessment.pitch_error_deg == pytest.approx(-8.0)
    assert assessment.roll_error_deg == pytest.approx(9.0)
    assert "pitch and roll are outside" in assessment.reason


def test_exactly_the_tolerance_still_passes_and_a_hair_more_does_not():
    assert _assess(15.0, 5.0).status == "ok"
    assert _assess(5.0, -5.0).status == "ok"
    assert _assess(15.001, 0.0).status == "tilted_pitch"
    assert _assess(10.0, 5.001).status == "tilted_roll"


def test_a_missing_or_nan_reading_is_no_reading_not_a_guess():
    for pitch, roll in ((None, 0.0), (10.0, None), (None, None), (math.nan, 0.0), (10.0, math.inf)):
        assessment = assess_placement(
            pitch_deg=pitch,
            roll_deg=roll,
            expected_pitch_deg=10.0,
            expected_roll_deg=0.0,
        )
        assert assessment.status == "no_reading"
        assert assessment.pitch_error_deg is None
        assert assessment.roll_error_deg is None
        assert "no usable inclinometer" in assessment.reason


def test_a_missing_expectation_is_no_expected_orientation():
    for expected_pitch, expected_roll in ((None, 0.0), (10.0, None), (None, None), (math.nan, 0.0)):
        assessment = assess_placement(
            pitch_deg=10.4,
            roll_deg=0.2,
            expected_pitch_deg=expected_pitch,
            expected_roll_deg=expected_roll,
        )
        assert assessment.status == "no_expected_orientation"
        assert assessment.pitch_error_deg is None
        assert "cannot be judged" in assessment.reason


def test_a_missing_reading_outranks_a_missing_expectation():
    assessment = assess_placement(
        pitch_deg=None,
        roll_deg=None,
        expected_pitch_deg=None,
        expected_roll_deg=None,
    )

    assert assessment.status == "no_reading"


def test_the_default_tolerance_is_the_documented_five_degrees():
    assert PLACEMENT_TOLERANCE_DEG == 5.0

    assessment = assess_placement(
        pitch_deg=15.0,
        roll_deg=0.0,
        expected_pitch_deg=10.0,
        expected_roll_deg=0.0,
    )

    assert assessment.tolerance_deg == 5.0
    assert assessment.status == "ok"


def test_a_tighter_tolerance_fails_a_placement_the_default_would_pass():
    assert _assess(13.0, 0.0, tolerance=2.0).status == "tilted_pitch"
    assert _assess(13.0, 0.0).status == "ok"


def test_a_nonsense_tolerance_is_refused_rather_than_silently_widened():
    for tolerance in (0.0, -1.0, math.nan):
        with pytest.raises(ValueError, match="tolerance_deg"):
            _assess(10.0, 0.0, tolerance=tolerance)


def test_every_documented_status_name_is_reachable():
    reached = {
        _assess(10.0, 0.0).status,
        _assess(20.0, 0.0).status,
        _assess(10.0, 20.0).status,
        _assess(20.0, 20.0).status,
        _assess(None, None).status,
        _assess(10.0, 0.0, expected_pitch=None).status,
    }

    assert reached == set(PLACEMENT_STATUSES)


def test_to_dict_rounds_and_names_everything_the_ui_needs():
    data = _assess(16.23456, 0.5).to_dict()

    assert data == {
        "status": "tilted_pitch",
        "pitch_error_deg": 6.235,
        "roll_error_deg": 0.5,
        "tolerance_deg": 5.0,
        "reason": data["reason"],
    }
    assert data["reason"]

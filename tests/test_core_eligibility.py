"""The pre-W13 qualified-core predicate (S3): eligibility is not lifecycle."""

import math
import subprocess
import sys

import pytest

from openflight import core_eligibility as ce, pi_trace

FULL = {
    "ball_speed_mph": 120.0,
    "launch_angle_vertical": 18.2,
    "launch_angle_vertical_source": "camera",
    "launch_angle_horizontal": -1.1,
    "launch_angle_horizontal_source": "camera",
    "launch_angle_horizontal_status": None,
}


def test_todays_payload_is_never_eligible_without_a_distance_field():
    result = ce.evaluate(dict(FULL))
    assert result["eligible"] is False and result["reasons"] == ["distance_not_in_payload"]
    assert result["contract"] == "pre_w13_v1"
    assert result["core"] == FULL


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"ball_speed_mph": None}, "ball_speed_missing"),
        ({"ball_speed_mph": math.nan}, "ball_speed_missing"),
        ({"ball_speed_mph": math.inf}, "ball_speed_missing"),
        ({"ball_speed_mph": True}, "ball_speed_missing"),
        ({"ball_speed_mph": "120"}, "ball_speed_missing"),
        ({"launch_angle_vertical_source": "estimated"}, "launch_vertical_not_measured"),
        ({"launch_angle_vertical_source": "mock"}, "launch_vertical_not_measured"),
        ({"launch_angle_vertical": None}, "launch_vertical_not_measured"),
        ({"launch_angle_vertical": math.nan}, "launch_vertical_not_measured"),
        ({"launch_angle_horizontal_source": None}, "launch_horizontal_not_measured"),
        (
            {"launch_angle_horizontal_source": "radar_low_coherence"},
            "launch_horizontal_not_measured",
        ),
        ({"launch_angle_horizontal": None}, "launch_horizontal_not_measured"),
        ({"launch_angle_horizontal_status": "rejected_span"}, "launch_horizontal_not_measured"),
        ({"launch_angle_horizontal_status": "withheld_low_snr"}, "launch_horizontal_not_measured"),
    ],
)
def test_each_missing_core_part_is_its_own_reason(change, reason):
    result = ce.evaluate({**FULL, **change})
    assert result["eligible"] is False
    assert result["reasons"] == [reason, "distance_not_in_payload"]


def test_a_non_string_status_is_not_measured_and_does_not_raise():
    result = ce.evaluate({**FULL, "launch_angle_horizontal_status": {"state": "rejected"}})
    assert "launch_horizontal_not_measured" in result["reasons"]


def test_a_status_that_is_not_a_rejection_keeps_the_axis_measured():
    result = ce.evaluate({**FULL, "launch_angle_horizontal_status": "accepted"})
    assert result["reasons"] == ["distance_not_in_payload"]


@pytest.mark.parametrize(
    "change,reason",
    [
        (
            {"launch_angle_horizontal_status": "azimuth_uncalibrated"},
            "launch_horizontal_not_measured",
        ),
        (
            {
                "launch_angle_horizontal_source": "radar",
                "launch_angle_horizontal_status": "azimuth_uncalibrated",
            },
            "launch_horizontal_not_measured",
        ),
        (
            {"launch_angle_vertical_status": "elevation_uncalibrated"},
            "launch_vertical_not_measured",
        ),
        ({"launch_angle_vertical_status": "UNCALIBRATED"}, "launch_vertical_not_measured"),
    ],
)
def test_an_uncalibrated_launch_axis_is_not_measured(change, reason):
    """Coordinator ruling: uncalibrated is not measured for the core, until W13/the owner say."""
    result = ce.evaluate({**FULL, **change})
    assert result["eligible"] is False
    assert result["reasons"] == [reason, "distance_not_in_payload"]


def test_an_uncalibrated_distance_is_not_measured(monkeypatch):
    monkeypatch.setattr(ce, "DISTANCE_KEY", "ball_distance_m")
    result = ce.evaluate(
        {**FULL, "ball_distance_m": 1.52, "ball_distance_m_status": "range_uncalibrated"}
    )
    assert result["eligible"] is False and result["reasons"] == ["distance_missing"]


def test_a_named_distance_field_makes_the_full_core_eligible(monkeypatch):
    monkeypatch.setattr(ce, "DISTANCE_KEY", "ball_distance_m")
    result = ce.evaluate({**FULL, "ball_distance_m": 1.52, "ball_distance_m_status": "measured"})
    assert result["eligible"] is True and result["reasons"] == []
    assert result["core"]["ball_distance_m"] == 1.52
    assert result["core"]["ball_distance_m_status"] == "measured"


@pytest.mark.parametrize(
    "distance",
    [
        {},
        {"ball_distance_m": 1.52},
        {"ball_distance_m": math.nan, "ball_distance_m_status": "measured"},
        {"ball_distance_m": None, "ball_distance_m_status": "measured"},
        {"ball_distance_m": 1.52, "ball_distance_m_status": "rejected_out_of_range"},
        {"ball_distance_m": 1.52, "ball_distance_m_status": {"state": "measured"}},
    ],
)
def test_a_named_distance_without_a_finite_value_and_status_is_missing(monkeypatch, distance):
    monkeypatch.setattr(ce, "DISTANCE_KEY", "ball_distance_m")
    result = ce.evaluate({**FULL, **distance})
    assert result["eligible"] is False and result["reasons"] == ["distance_missing"]


def test_speed_only_partial_is_ineligible():
    result = ce.evaluate({"ball_speed_mph": 120.0})
    assert result["eligible"] is False
    assert result["reasons"] == [
        "launch_vertical_not_measured",
        "launch_horizontal_not_measured",
        "distance_not_in_payload",
    ]
    assert result["core"]["launch_angle_vertical"] is None


def test_evaluate_does_not_change_the_payload():
    payload = dict(FULL)
    ce.evaluate(payload)
    assert payload == FULL


def test_constants_and_the_single_finite_helper():
    assert ce.CONTRACT == "pre_w13_v1"
    assert ce.MEASURED_LAUNCH_SOURCES == frozenset({"radar", "camera"})
    assert ce.DISTANCE_KEY is None
    assert ce._finite is pi_trace._finite  # pylint: disable=protected-access  (R-F14)


def test_importing_the_predicate_does_not_load_the_rolling_buffer_package():
    """Global constraint: new top-level modules stay clear of the shared-file import cycle."""

    code = (
        "import sys, openflight.core_eligibility; "
        "print(any(m.startswith('openflight.rolling_buffer') for m in sys.modules))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "False"

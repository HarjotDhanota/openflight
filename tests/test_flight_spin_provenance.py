"""A confidence number cannot turn explicitly modeled spin into a measurement."""

from datetime import datetime

import pytest

from openflight.flight import compute_flight
from openflight.flight_sim import resolve_launch
from openflight.launch_monitor import Shot


def _shot(source, confidence):
    return Shot(
        ball_speed_mph=140.0,
        ball_speed_contract="full",
        timestamp=datetime(2026, 10, 30),
        launch_angle_vertical=15.0,
        launch_angle_vertical_source="radar",
        launch_angle_horizontal=2.0,
        launch_angle_horizontal_source="radar",
        inclinometer={"applied_to": ["iwr6843"]},
        spin_rpm=3000.0,
        spin_confidence=confidence,
        spin_source=source,
        spin_axis_deg=3.0,
        spin_axis_source="calculated (D-plane tilt)",
    )


@pytest.mark.parametrize("source", ["calculated", "model_geometry", "estimated"])
@pytest.mark.parametrize("confidence", [None, 0.9])
def test_explicit_model_spin_label_wins_over_confidence(source, confidence):
    shot = _shot(source, confidence)

    assert resolve_launch(shot).spin_source == source
    result = compute_flight(shot)
    assert result.status == source
    for block in ("as_measured", "normalized"):
        assert all(
            metric["status"] == source for metric in result.payload[block]["metrics"].values()
        )


@pytest.mark.parametrize("source", [None, "measured"])
def test_accepted_legacy_and_measured_spin_remain_measured(source):
    assert resolve_launch(_shot(source, 0.9)).spin_source == "measured"

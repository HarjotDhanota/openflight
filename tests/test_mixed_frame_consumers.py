"""An explicitly mixed frame cannot define a qualified flight or simulator shot."""

from datetime import datetime

import pytest

from openflight.flight import compute_flight
from openflight.launch_monitor import Shot
from openflight.sim.resolver import resolve_shot
from openflight.sim.types import PlayerState, UnmeasuredShotError


def _shot(**overrides):
    values = dict(
        ball_speed_mph=140.0,
        ball_speed_contract="full",
        timestamp=datetime(2026, 10, 30),
        launch_angle_vertical=15.0,
        launch_angle_vertical_source="radar",
        launch_angle_horizontal=2.0,
        launch_angle_horizontal_source="radar",
        launch_angle_horizontal_frame="target_line",
        inclinometer={"applied_to": ["iwr6843"]},
        spin_rpm=3000.0,
        spin_confidence=0.9,
        spin_axis_deg=3.0,
        spin_axis_source="calculated (D-plane tilt)",
        spin_axis_frame="target_line",
    )
    values.update(overrides)
    return Shot(**values)


@pytest.mark.parametrize("field", ["spin_axis_frame", "launch_angle_horizontal_frame"])
def test_mixed_frame_refuses_both_flights_with_a_reason(field):
    result = compute_flight(_shot(**{field: "mixed"}))

    assert result.conditions is None
    assert result.as_measured is None and result.normalized is None
    for block in ("as_measured", "normalized"):
        for metric in result.payload[block]["metrics"].values():
            assert metric["value"] is None and metric["status"] == "not_measured"
            assert "mixed" in metric["reason"] and "frame" in metric["reason"]


@pytest.mark.parametrize("field", ["spin_axis_frame", "launch_angle_horizontal_frame"])
def test_mixed_frame_refuses_export_without_consuming_a_shot_number(field):
    state = PlayerState()

    with pytest.raises(UnmeasuredShotError, match="mixed.*frame"):
        resolve_shot(_shot(**{field: "mixed"}), state)

    assert state.shot_counter == 0


@pytest.mark.parametrize("frame", [None, "target_line", "unit_boresight"])
def test_known_or_legacy_frame_preserves_flight_and_export(frame):
    shot = _shot(launch_angle_horizontal_frame=frame, spin_axis_frame=frame)
    result = compute_flight(shot)
    state = PlayerState()

    assert result.conditions is not None and result.as_measured is not None
    exported = resolve_shot(shot, state)
    assert exported.hla == 2.0 and exported.spin_axis_deg == 3.0
    assert state.shot_counter == 1

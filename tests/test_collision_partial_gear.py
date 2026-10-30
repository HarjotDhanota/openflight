"""A partial strike location must expose its conditional gear calculation."""

import math

import pytest

from openflight import collision_model as cm
from openflight.clubs import ClubType


def evaluate(**location):
    return cm.evaluate(
        club=ClubType.DRIVER,
        club_path=cm.Input(-2.0, frame="target_line"),
        attack_angle=cm.Input(-4.0),
        launch_horizontal=cm.Input(1.0, frame="target_line"),
        launch_vertical=cm.Input(12.0),
        club_speed_mph=cm.Input(85.0),
        **location,
    )


@pytest.mark.parametrize(
    "known,missing",
    [
        ("impact_offset_mm", "impact_height_mm"),
        ("impact_height_mm", "impact_offset_mm"),
    ],
)
@pytest.mark.parametrize("unknown", [None, cm.Input(math.nan), cm.Input(4.0, "estimated")])
def test_partial_location_keeps_assumption_on_spin_axis_inputs_and_forward(known, missing, unknown):
    partial = evaluate(**{known: cm.Input(8.0), missing: unknown})
    explicit = evaluate(**{known: cm.Input(8.0), missing: cm.Input(0.0)})

    for name in ("spin_rate", "spin_axis"):
        metric = partial.metrics[name]
        assert metric.value == pytest.approx(explicit.metrics[name].value)
        assert missing in metric.note
        assert "assumed 0 mm" in metric.note
        assert explicit.metrics[name].note is None
    assert partial.inputs[known].value == 8.0
    assert partial.inputs[missing].value is None
    assert not partial.inputs[missing].present
    assert missing in partial.inputs[missing].reason
    assert partial.to_dict()["forward"]["impact_location_note"] == partial.metrics["spin_rate"].note


def test_no_location_and_complete_location_keep_existing_contracts():
    absent = evaluate()
    complete = evaluate(impact_offset_mm=cm.Input(8.0), impact_height_mm=cm.Input(3.0))
    for name in ("spin_rate", "spin_axis"):
        assert absent.metrics[name].note == cm.NOTE_NO_GEAR
        assert complete.metrics[name].note is None
    assert "impact_location_note" not in absent.to_dict()["forward"]
    assert "impact_location_note" not in complete.to_dict()["forward"]

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


@pytest.mark.parametrize("coordinate", ["impact_offset_mm", "impact_height_mm"])
def test_gear_spin_keeps_its_known_coordinate_even_without_a_d_plane(coordinate):
    result = cm.evaluate(
        club=ClubType.DRIVER,
        club_path=cm.Input(0.0, frame="target_line"),
        attack_angle=cm.Input(0.0),
        launch_horizontal=cm.Input(0.0, frame="target_line"),
        launch_vertical=cm.Input(0.0),
        club_speed_mph=cm.Input(85.0),
        **{coordinate: cm.Input(8.0)},
    )
    assert result.metrics["spin_rate"].present
    assert not result.metrics["d_plane_tilt"].present
    assert result.inputs[coordinate].value == 8.0


def test_unknown_partial_and_measured_centred_contact_stay_distinct():
    unknown = evaluate()
    partial = evaluate(impact_offset_mm=cm.Input(8.0))
    centred = evaluate(impact_offset_mm=cm.Input(0.0), impact_height_mm=cm.Input(0.0))

    for name in ("spin_rate", "spin_axis"):
        assert unknown.metrics[name].condition == cm.CONDITION_CENTRED_CONTACT
        assert "impact_height_mm" in partial.metrics[name].condition
        assert "assumed 0 mm" in partial.metrics[name].condition
        assert centred.metrics[name].condition is None
        assert centred.metrics[name].value == pytest.approx(unknown.metrics[name].value)
    assert unknown.to_dict()["metrics"]["spin_rate"]["condition"] == cm.CONDITION_CENTRED_CONTACT
    assert "condition" not in centred.to_dict()["metrics"]["spin_rate"]
    assert unknown.to_dict()["forward"]["condition"] == cm.CONDITION_CENTRED_CONTACT
    assert "condition" not in centred.to_dict()["forward"]


def test_metrics_without_a_strike_dependence_carry_no_condition():
    unknown = evaluate().to_dict()["metrics"]
    for name in ("face_angle", "dynamic_loft", "spin_loft", "face_to_path", "d_plane_tilt"):
        assert "condition" not in unknown[name]

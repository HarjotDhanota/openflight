"""The camera's experimental impact location feeds nothing but its own two records.

It is shown to testers before it is validated, so no model may use it: no gear
effect in the collision model, and no change to spin, spin axis, face, flight or
any other metric of the shot.
"""

from __future__ import annotations

import pytest

from openflight import collision_model, shot_models
from openflight.clubs import ClubType
from tests.every_metric import fusion_shot

READING = {
    "status": "ok",
    "reason": None,
    "impact_offset_mm": 18.0,
    "impact_offset_sigma_mm": 3.0,
    "height_status": "ok",
    "height_reason": None,
    "impact_height_mm": 9.0,
    "impact_height_sigma_mm": 4.0,
    "handedness": "right",
    "label": "experimental_unqualified",
}
IMPACT_METRICS = {"impact_offset", "impact_height"}


def test_the_collision_inputs_never_carry_the_impact_location():
    inputs = shot_models._collision_inputs(  # pylint: disable=protected-access
        fusion_shot(impact_location=READING)
    )
    assert not {"impact_offset_mm", "impact_height_mm"} & set(inputs)


def test_the_collision_model_is_never_given_an_impact_location(monkeypatch):
    calls = []
    evaluate = collision_model.evaluate

    def spy(**kwargs):
        calls.append(kwargs)
        return evaluate(**kwargs)

    monkeypatch.setattr(collision_model, "evaluate", spy)
    fusion_shot(impact_location=READING)
    assert calls
    for kwargs in calls:
        assert kwargs.get("impact_offset_mm") is None
        assert kwargs.get("impact_height_mm") is None


def test_an_experimental_impact_input_would_drive_no_gear_effect_either():
    kwargs = dict(shot_models._collision_inputs(fusion_shot()))  # pylint: disable=protected-access
    plain = collision_model.evaluate(club=ClubType.IRON_7, **kwargs)
    given = collision_model.evaluate(
        club=ClubType.IRON_7,
        impact_offset_mm=collision_model.Input(18.0, "experimental", "camera impact location"),
        impact_height_mm=collision_model.Input(9.0, "experimental", "camera impact location"),
        **kwargs,
    )
    assert "impact_offset_mm" not in given.inputs
    assert given.to_dict()["metrics"] == plain.to_dict()["metrics"]


@pytest.mark.parametrize("handed", ["RH", "LH"])
def test_every_other_metric_is_unchanged_by_the_reading(monkeypatch, handed):
    monkeypatch.setattr(shot_models.server, "golfer_handed", handed)
    without = fusion_shot().to_dict()
    with_reading = fusion_shot(impact_location=READING).to_dict()
    assert with_reading["metrics"]["impact_offset"]["status"] == "experimental"
    for metric_id, record in without["metrics"].items():
        if metric_id not in IMPACT_METRICS:
            assert with_reading["metrics"][metric_id] == record, metric_id
    for field in ("collision_model", "flight", "spin_rpm", "spin_axis_deg", "face_angle_deg"):
        assert with_reading[field] == without[field], field

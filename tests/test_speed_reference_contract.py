"""Valid ball launch references compose; full smash needs full impact club speed."""

import pytest

from openflight import full_speed, shot_models
from tests.test_collision_speed_contract import shot
from tests.test_full_speed_routes import _total


@pytest.mark.parametrize(
    "changes", [{}, {"tee_range_source": "camera_prior"}, {"vertical_source": "camera_fallback"}]
)
def test_existing_peak_projection_launch_reference_remains_a_valid_collision_input(changes):
    produced = _total(**changes)
    assert produced["referred_to"] == "launch"
    current = shot(ball_speed_full=produced, ball_speed_full_mph=produced["value_mph"])
    value = shot_models._full_ball_speed_input(current)
    assert value.present
    assert value.value == produced["value_mph"]
    assert value.label == (
        "estimated" if produced["status"] == "experimental" else produced["status"]
    )
    assert value.experimental is (produced["status"] == "experimental")


def ball(reference="separation"):
    return {"status": "calculated", "value_mph": 130.0, "referred_to": reference}


def club(reference="impact", point="clubhead", status="calculated"):
    return {"status": status, "value_mph": 90.0, "referred_to": reference, "reference_point": point}


@pytest.mark.parametrize(
    "ball_record,club_record,why",
    [
        (ball(), club("the reading's time"), "impact"),
        (ball(), club(None), "impact"),
        (ball(), club("unknown"), "impact"),
        (ball(), club(point="hosel"), "clubhead"),
        (ball(), club(point=None), "clubhead"),
        (ball("the reading's time"), club(), "launch"),
        (ball(None), club(), "launch"),
    ],
)
def test_full_smash_never_mixes_missing_or_incompatible_reference_contracts(
    ball_record, club_record, why
):
    value = full_speed.evaluate_full_smash(ball_record, club_record)
    assert value["value"] is None
    assert value["status"] == "not_measured"
    assert why in value["reason"]


@pytest.mark.parametrize("reference", ["launch", "separation"])
@pytest.mark.parametrize("label", ["calculated", "estimated", "model_geometry", "experimental"])
def test_bound_full_smash_keeps_ratio_and_weaker_label(reference, label):
    value = full_speed.evaluate_full_smash(ball(reference), club(status=label))
    assert value["value"] == pytest.approx(130.0 / 90.0)
    assert value["status"] == label

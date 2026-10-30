"""A stale scalar must not override the full ball speed's explicit quality record."""

import pytest

from openflight import collision_model as cm, shot_models
from tests.test_collision_speed_contract import shot


@pytest.mark.parametrize("status", ["not_measured", "unknown", None])
def test_an_explicit_unavailable_full_speed_record_is_not_upgraded(status):
    current = shot(
        ball_speed_full_mph=124.0,
        ball_speed_full={"status": status, "value_mph": None, "reason": "projection unavailable"},
    )
    value = shot_models._full_ball_speed_input(current)
    assert not value.present
    assert "projection unavailable" in value.reason


def test_full_ball_speed_keeps_a_model_geometry_label():
    current = shot(
        ball_speed_full_mph=124.0,
        ball_speed_full={
            "status": "model_geometry",
            "value_mph": 124.0,
            "note": "declared test geometry",
        },
    )
    value = shot_models._full_ball_speed_input(current)
    assert value.present
    assert value.label == cm.LABEL_MODEL_GEOMETRY


@pytest.mark.parametrize("reference", ["the reading's time", "peak_radial"])
def test_an_explicit_reading_time_speed_is_not_a_separation_speed(reference):
    current = shot(
        ball_speed_full_mph=124.0,
        ball_speed_full={"status": "calculated", "value_mph": 124.0, "referred_to": reference},
    )
    value = shot_models._full_ball_speed_input(current)
    assert not value.present
    assert "separation" in value.reason


@pytest.mark.parametrize(
    "status,label,experimental",
    [
        ("calculated", "calculated", False),
        ("estimated", "estimated", False),
        ("experimental", "estimated", True),
    ],
)
def test_a_supported_ball_speed_keeps_its_own_label(status, label, experimental):
    current = shot(
        ball_speed_full_mph=124.0,
        ball_speed_full={"status": status, "value_mph": 124.0, "referred_to": "separation"},
    )
    value = shot_models._full_ball_speed_input(current)
    assert value.value == 124.0
    assert value.label == label
    assert value.experimental is experimental


def test_legacy_full_speed_scalar_without_a_quality_record_keeps_its_contract():
    value = shot_models._full_ball_speed_input(shot(ball_speed_full_mph=124.0))
    assert value.value == 124.0
    assert value.label == "calculated"

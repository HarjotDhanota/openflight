"""The shared physical constants keep their values, and each user takes them from one place."""

import importlib

import pytest

from openflight import constants


def test_values():
    assert constants.MPH_TO_MPS == 0.44704
    assert constants.GRAVITY == 9.81
    assert constants.BALL_DIAMETER_MM == 42.67
    assert constants.BALL_DIAMETER_M == 0.04267
    assert constants.FLIGHT_BALL_RADIUS_M == 0.02135


@pytest.mark.parametrize(
    "module,name,shared",
    [
        ("openflight.ops_speed_track", "MPH_TO_MPS", "MPH_TO_MPS"),
        ("openflight.camera.impact_session", "MPH_TO_MPS", "MPH_TO_MPS"),
        ("openflight.camera.club_motion", "BALL_DIAMETER_MM", "BALL_DIAMETER_MM"),
        ("openflight.camera.impact_location", "BALL_DIAMETER_MM", "BALL_DIAMETER_MM"),
        ("openflight.rig_geometry", "BALL_DIAMETER_MM", "BALL_DIAMETER_MM"),
        ("openflight.server", "BALL_DIAMETER_MM", "BALL_DIAMETER_MM"),
        ("openflight.camera.club_delivery", "GOLF_BALL_DIAMETER_M", "BALL_DIAMETER_M"),
        ("openflight.camera.ground_patch", "GOLF_BALL_DIAMETER_M", "BALL_DIAMETER_M"),
        ("openflight.camera.reference_ball_range", "GOLF_BALL_DIAMETER_M", "BALL_DIAMETER_M"),
    ],
)
def test_users_import_the_shared_constant(module, name, shared):
    assert getattr(importlib.import_module(module), name) is getattr(constants, shared)

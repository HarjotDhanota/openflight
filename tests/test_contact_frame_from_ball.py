"""The ball-defined contact frame, next to the acoustic one it replaces."""

from __future__ import annotations

import math

import pytest

from openflight.camera.clubpose.impact_zone import (
    contact_frame_from_ball,
    contact_frame_from_trigger,
)

TRIGGER_INDEX = 71  # the last pre-trigger frame, as the archive reports it
FPS = 467.6


def test_the_ball_clock_places_contact_before_the_trigger_frame():
    # Contact 1.5 ms before the trigger at 467.6 fps is 0.70 frames before it.
    assert contact_frame_from_ball(TRIGGER_INDEX, FPS, -0.0015) == pytest.approx(
        (TRIGGER_INDEX + 1) - 0.0015 * FPS
    )


def test_it_agrees_with_the_acoustic_frame_when_the_ball_agrees_with_the_microphone():
    sound_flight_s = 1.524 / 343.0

    assert contact_frame_from_ball(TRIGGER_INDEX, FPS, -sound_flight_s) == pytest.approx(
        contact_frame_from_trigger(TRIGGER_INDEX, FPS, ball_to_unit_m=1.524)
    )


def test_a_trigger_that_fired_early_moves_contact_later_than_the_acoustic_frame():
    """The August session: contact ~2.6 ms before the trigger, not 4.4."""
    acoustic = contact_frame_from_trigger(TRIGGER_INDEX, FPS, ball_to_unit_m=1.524)
    ball = contact_frame_from_ball(TRIGGER_INDEX, FPS, -0.0026)

    assert ball > acoustic
    assert ball - acoustic == pytest.approx((1.524 / 343.0 - 0.0026) * FPS)


def test_bad_inputs_are_refused_by_name():
    with pytest.raises(ValueError, match="frame rate"):
        contact_frame_from_ball(TRIGGER_INDEX, 0.0, -0.002)
    with pytest.raises(ValueError, match="contact time"):
        contact_frame_from_ball(TRIGGER_INDEX, FPS, math.nan)

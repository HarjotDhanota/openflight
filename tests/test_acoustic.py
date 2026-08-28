"""Acoustic trigger timing must follow the installation, not a constant.

The sound trigger fires when the impact reaches the microphone, so the camera
frame of contact sits earlier than the trigger by the sound's travel time. That
time is set by how far the unit sits from the ball, which varies per build, and
by air temperature. A hard-coded frame offset is only ever right for the rig it
was measured on.
"""

from __future__ import annotations

import math

import pytest

from openflight.acoustic import (
    ISA_SEA_LEVEL_TEMP_C,
    impact_frame_from_trigger,
    impact_time_from_trigger,
    speed_of_sound_ms,
    trigger_lag_s,
)


class TestSpeedOfSound:
    def test_matches_the_textbook_value_at_zero_celsius(self):
        assert speed_of_sound_ms(0.0) == pytest.approx(331.3, abs=0.5)

    def test_matches_the_textbook_value_at_twenty_celsius(self):
        assert speed_of_sound_ms(20.0) == pytest.approx(343.2, abs=0.6)

    def test_rises_with_temperature(self):
        assert speed_of_sound_ms(35.0) > speed_of_sound_ms(5.0)

    def test_default_is_the_isa_reference_used_by_ballistics(self):
        assert speed_of_sound_ms() == pytest.approx(speed_of_sound_ms(ISA_SEA_LEVEL_TEMP_C))

    def test_rejects_temperatures_below_absolute_zero(self):
        with pytest.raises(ValueError):
            speed_of_sound_ms(-300.0)


class TestTriggerLag:
    def test_lag_matches_the_measured_rig(self):
        """The 2026-08-25 rig sits 1.575 m from the ball and the ball tracker
        put impact 2.11 frames (4.5 ms) before the trigger at 468 fps."""
        assert trigger_lag_s(1.575, 20.0) == pytest.approx(0.0046, abs=0.0004)

    def test_lag_scales_with_distance(self):
        """The whole point: a unit at twice the distance waits twice as long."""
        assert trigger_lag_s(3.15) == pytest.approx(2.0 * trigger_lag_s(1.575), rel=1e-9)

    def test_distance_dominates_temperature(self):
        """Doubling the distance changes the lag far more than any plausible
        temperature swing, so distance is the term that must be configurable."""
        by_distance = trigger_lag_s(3.15, 20.0) - trigger_lag_s(1.575, 20.0)
        by_temperature = abs(trigger_lag_s(1.575, 0.0) - trigger_lag_s(1.575, 40.0))
        assert by_distance > 10.0 * by_temperature

    @pytest.mark.parametrize("bad", [0.0, -1.0, float("nan"), float("inf")])
    def test_rejects_impossible_distances(self, bad):
        with pytest.raises(ValueError):
            trigger_lag_s(bad)


class TestImpactTime:
    def test_impact_precedes_the_trigger(self):
        trigger = 1000.0
        assert impact_time_from_trigger(trigger, 1.575) < trigger

    def test_impact_time_is_the_trigger_minus_the_lag(self):
        trigger, distance = 1000.0, 2.0
        assert impact_time_from_trigger(trigger, distance) == pytest.approx(
            trigger - trigger_lag_s(distance)
        )

    def test_frame_form_matches_the_measured_rig(self):
        """Trigger at frame 74, 468 fps, 1.575 m -> impact near frame 71.9,
        which is what the ball tracker independently reports (71.89 +- 0.77
        across 20 shots)."""
        got = impact_frame_from_trigger(74.0, 467.6, 1.575, 20.0)
        assert got == pytest.approx(71.9, abs=0.3)

    def test_a_distant_unit_shifts_impact_much_earlier(self):
        near = impact_frame_from_trigger(74.0, 467.6, 1.575)
        far = impact_frame_from_trigger(74.0, 467.6, 3.5)
        assert near - far > 2.0, "a 3.5 m install must land several frames earlier"

    def test_rejects_a_nonpositive_frame_rate(self):
        with pytest.raises(ValueError):
            impact_frame_from_trigger(74.0, 0.0, 1.575)

    def test_frame_and_time_forms_agree(self):
        fps, trigger_frame, distance = 467.6, 74.0, 1.9
        frames = impact_frame_from_trigger(trigger_frame, fps, distance)
        seconds = impact_time_from_trigger(trigger_frame / fps, distance)
        assert frames / fps == pytest.approx(seconds, abs=1e-12)


class TestDocumentedPhysics:
    def test_lag_is_distance_over_speed(self):
        distance, temperature = 2.4, 12.0
        assert trigger_lag_s(distance, temperature) == pytest.approx(
            distance / speed_of_sound_ms(temperature)
        )

    def test_hardware_path_latency_is_negligible_by_comparison(self):
        """The SEN-14262 GATE -> HOST_INT path is ~10 us. If that ever stops
        being negligible against the flight time this test should fail."""
        assert trigger_lag_s(1.575) > 100 * 10e-6

    def test_speed_of_sound_formula_is_the_standard_approximation(self):
        for temperature in (-10.0, 0.0, 15.0, 25.0, 40.0):
            assert speed_of_sound_ms(temperature) == pytest.approx(
                331.3 * math.sqrt(1.0 + temperature / 273.15), rel=1e-9
            )

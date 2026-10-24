"""The smash factor, ball speed over club speed, checked by hand."""

import math

import pytest

from openflight.smash import smash_factor


class TestSmashFactor:
    def test_is_ball_speed_over_club_speed(self):
        assert smash_factor(150.0, 100.0) == pytest.approx(1.5)
        assert smash_factor(140.0, 100.0) == pytest.approx(1.4)

    def test_is_unit_free(self):
        mph_to_ms = 0.44704
        assert smash_factor(150.0 * mph_to_ms, 100.0 * mph_to_ms) == pytest.approx(1.5)

    @pytest.mark.parametrize("club", [None, 0.0, -100.0, math.nan, math.inf, -math.inf])
    def test_no_ratio_without_a_positive_finite_club_speed(self, club):
        assert smash_factor(150.0, club) is None

    def test_a_missing_ball_speed_is_no_ratio(self):
        assert smash_factor(None, 100.0) is None

    def test_a_negative_ball_speed_is_no_ratio(self):
        assert smash_factor(-150.0, 100.0) is None

    @pytest.mark.parametrize("ball", [math.nan, math.inf])
    def test_a_non_finite_ball_speed_stays_non_finite(self, ball):
        result = smash_factor(ball, 100.0)
        assert result is not None and not math.isfinite(result)

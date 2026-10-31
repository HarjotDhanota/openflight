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

    @pytest.mark.parametrize("ball", [None, -150.0])
    def test_a_missing_or_negative_ball_speed_is_no_ratio(self, ball):
        assert smash_factor(ball, 100.0) is None

    @pytest.mark.parametrize("ball", [math.nan, math.inf])
    def test_a_non_finite_ball_speed_stays_non_finite(self, ball):
        result = smash_factor(ball, 100.0)
        assert result is not None and not math.isfinite(result)


class TestPlausibility:
    """A smash no club can make says the club reading is wrong, not the ball's."""

    def test_the_bracket_is_named(self):
        from openflight.smash import MAX_PLAUSIBLE_SMASH, MIN_PLAUSIBLE_SMASH

        assert MAX_PLAUSIBLE_SMASH == 1.70
        assert MIN_PLAUSIBLE_SMASH == 0.5

    @pytest.mark.parametrize(
        ("ball", "club", "shown"),
        # 24 Oct shots 20 and 23: the OPS picked a wrong club reading
        [(76.0, 33.1, "2.30"), (69.3, 27.8, "2.49"), (40.0, 100.0, "0.40")],
    )
    def test_an_implausible_smash_is_named_with_its_value(self, ball, club, shown):
        from openflight.smash import implausible_smash_reason

        reason = implausible_smash_reason(ball, club)

        assert reason is not None and shown in reason

    def test_the_club_speed_s_reason_names_the_smash(self):
        from openflight.smash import implausible_club_speed_reason

        assert implausible_club_speed_reason(76.0, 33.1) == (
            "implausible against the ball speed (smash 2.30)"
        )
        assert implausible_club_speed_reason(150.0, 103.0) is None

    @pytest.mark.parametrize(
        ("ball", "club"),
        [
            (150.0, 100.0),
            (170.0, 100.0),  # 1.70 itself is plausible
            (50.0, 100.0),  # 0.5 itself is plausible
            (None, 100.0),
            (100.0, None),
            (100.0, 0.0),
            (float("inf"), 100.0),
            (float("nan"), 100.0),
        ],
    )
    def test_a_plausible_or_undefined_smash_is_not_judged(self, ball, club):
        from openflight.smash import implausible_club_speed_reason, implausible_smash_reason

        assert implausible_smash_reason(ball, club) is None
        assert implausible_club_speed_reason(ball, club) is None

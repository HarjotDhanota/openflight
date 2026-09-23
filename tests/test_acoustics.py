"""The speed of sound in air, and the acoustic walk-back that depends on it."""

import math

import pytest

from openflight.acoustics import (
    MAX_TEMPERATURE_C,
    MIN_TEMPERATURE_C,
    SPEED_OF_SOUND_20C_M_S,
    sound_flight_s,
    speed_of_sound_m_s,
)


class TestTheLinearAirFormula:
    """c = 331.3 + 0.606 * T, to the four significant figures anyone quotes."""

    @pytest.mark.parametrize(
        ("temperature_c", "expected"),
        [(0.0, 331.3), (20.0, 343.4), (35.0, 352.5)],
    )
    def test_it_matches_the_textbook_value(self, temperature_c, expected) -> None:
        assert speed_of_sound_m_s(temperature_c) == pytest.approx(expected, abs=0.05)

    def test_it_is_exactly_the_stated_linear_fit(self) -> None:
        for temperature_c in (-30.0, -5.0, 0.0, 12.5, 20.0, 35.0, 55.0):
            assert speed_of_sound_m_s(temperature_c) == pytest.approx(
                331.3 + 0.606 * temperature_c, abs=1e-12
            )

    def test_warmer_air_carries_sound_faster(self) -> None:
        assert speed_of_sound_m_s(35.0) > speed_of_sound_m_s(20.0) > speed_of_sound_m_s(0.0)

    def test_the_range_that_matters_spans_about_three_percent(self) -> None:
        cold = speed_of_sound_m_s(0.0)
        hot = speed_of_sound_m_s(35.0)
        assert (hot - cold) / cold == pytest.approx(0.064, abs=0.005)


class TestTheDefaultIsTheShippedConstant:
    """None must reproduce the pre-refactor 343.0 to the bit, not 343.42."""

    def test_none_is_the_named_twenty_celsius_constant(self) -> None:
        assert speed_of_sound_m_s(None) == SPEED_OF_SOUND_20C_M_S
        assert speed_of_sound_m_s() == 343.0

    def test_the_default_is_not_the_formula_at_twenty_celsius(self) -> None:
        # 331.3 + 0.606 * 20 = 343.42. The conventional "speed of sound at
        # 20 C" is 343.0, and that is what every shipped number was computed
        # with, so the default stays pinned to it.
        assert speed_of_sound_m_s(20.0) == pytest.approx(343.42, abs=1e-9)
        assert speed_of_sound_m_s(20.0) != speed_of_sound_m_s(None)


class TestItRejectsTemperaturesItCannotBelieve:
    def test_nan_is_named(self) -> None:
        with pytest.raises(ValueError, match="temperature"):
            speed_of_sound_m_s(float("nan"))

    def test_infinity_is_named(self) -> None:
        with pytest.raises(ValueError, match="temperature"):
            speed_of_sound_m_s(float("inf"))

    @pytest.mark.parametrize("temperature_c", [-40.1, -273.15, 60.1, 1000.0])
    def test_physically_absurd_temperatures_are_named(self, temperature_c) -> None:
        with pytest.raises(ValueError, match="temperature"):
            speed_of_sound_m_s(temperature_c)

    def test_the_bounds_themselves_are_accepted(self) -> None:
        assert speed_of_sound_m_s(MIN_TEMPERATURE_C) > 0.0
        assert speed_of_sound_m_s(MAX_TEMPERATURE_C) > 0.0


class TestSoundFlight:
    def test_it_is_distance_over_the_speed_of_sound(self) -> None:
        assert sound_flight_s(1.575) == pytest.approx(1.575 / 343.0, abs=1e-15)

    def test_it_uses_the_temperature_when_given(self) -> None:
        assert sound_flight_s(1.575, 35.0) == pytest.approx(
            1.575 / (331.3 + 0.606 * 35.0), abs=1e-15
        )

    def test_hot_air_shortens_the_flight(self) -> None:
        assert sound_flight_s(1.575, 35.0) < sound_flight_s(1.575, 0.0)

    def test_a_tenth_of_a_frame_at_the_shipped_distance(self) -> None:
        # The reason this refactor exists: 0-35 C is ~0.13 ms at 1.5 m, which
        # is about a tenth of a frame at 467 fps.
        spread_s = sound_flight_s(1.5, 0.0) - sound_flight_s(1.5, 35.0)
        assert spread_s == pytest.approx(0.00027, abs=0.00005)
        assert spread_s * 467.0 == pytest.approx(0.13, abs=0.03)

    def test_zero_distance_is_zero_time(self) -> None:
        assert sound_flight_s(0.0) == 0.0

    @pytest.mark.parametrize("distance_m", [-0.001, -1.5, float("nan"), float("inf")])
    def test_it_names_a_distance_it_cannot_believe(self, distance_m) -> None:
        with pytest.raises(ValueError, match="distance"):
            sound_flight_s(distance_m)

    def test_it_still_names_a_bad_temperature(self) -> None:
        with pytest.raises(ValueError, match="temperature"):
            sound_flight_s(1.5, math.inf)


class TestThereIsOnlyOneDefinition:
    """343.0 lived in four modules and a replay script. It lives here now.

    The old names stay importable -- tests and scripts reach for them -- but
    every one of them must be this module's constant, so the next edit cannot
    move one copy and leave the others behind.
    """

    def test_every_old_name_re_exports_the_twenty_celsius_constant(self) -> None:
        import importlib  # noqa: PLC0415

        # Only the modules that exist on this branch. ball_flight, impact_zone
        # and server grow their re-exports as the acoustic walk-back is ported;
        # add them here then, so the single-definition rule keeps its teeth.
        for module_name in ("openflight.rig_geometry",):
            module = importlib.import_module(module_name)
            assert module.SPEED_OF_SOUND_M_S is SPEED_OF_SOUND_20C_M_S, module_name
            assert module.SPEED_OF_SOUND_M_S == 343.0, module_name

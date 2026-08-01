"""Tests for humid-air density.

Anchored against published psychrometric values rather than against the
implementation, so a refactor that changes the physics fails loudly.
"""

import math

import pytest

from openflight.environment.density import (
    MAX_PRESSURE_PA,
    MIN_PRESSURE_PA,
    R_DRY_AIR,
    R_WATER_VAPOUR,
    air_density,
    density_altitude_ft,
    pressure_from_elevation_pa,
    saturation_vapour_pressure_pa,
)


class TestSaturationVapourPressure:
    """Buck (1981) against reference values."""

    @pytest.mark.parametrize(
        "temp_c,expected_pa,tol",
        [
            (0.0, 611.2, 1.0),
            (10.0, 1228.0, 5.0),
            (20.0, 2339.0, 5.0),
            (30.0, 4246.0, 10.0),
            (40.0, 7385.0, 20.0),
        ],
    )
    def test_matches_published_values(self, temp_c, expected_pa, tol):
        assert saturation_vapour_pressure_pa(temp_c) == pytest.approx(expected_pa, abs=tol)

    def test_rises_monotonically_with_temperature(self):
        temps = [-10.0, 0.0, 10.0, 20.0, 30.0, 40.0, 50.0]
        values = [saturation_vapour_pressure_pa(t) for t in temps]
        assert values == sorted(values)

    def test_rejects_sensor_fault_temperatures(self):
        with pytest.raises(ValueError, match="sensor fault"):
            saturation_vapour_pressure_pa(500.0)


class TestAirDensity:
    """The headline number: kg/m^3 from T, P, RH."""

    def test_isa_sea_level_anchor(self):
        """The value OpenFlight has hardcoded forever must fall out of the model."""
        assert air_density(15.0, 101325.0, 0.0) == pytest.approx(1.225, abs=0.001)

    @pytest.mark.parametrize(
        "temp_c,pressure_pa,humidity_pct,expected,tol",
        [
            (0.0, 101325.0, 0.0, 1.2922, 0.001),
            (20.0, 101325.0, 0.0, 1.2041, 0.001),
            (25.0, 101325.0, 100.0, 1.1701, 0.002),
            (35.0, 101325.0, 0.0, 1.1455, 0.002),
        ],
    )
    def test_matches_published_values(self, temp_c, pressure_pa, humidity_pct, expected, tol):
        assert air_density(temp_c, pressure_pa, humidity_pct) == pytest.approx(expected, abs=tol)

    def test_falls_with_rising_temperature(self):
        assert air_density(35.0, 101325.0) < air_density(15.0, 101325.0)

    def test_rises_with_rising_pressure(self):
        assert air_density(15.0, 101325.0) > air_density(15.0, 95000.0)

    def test_humid_air_is_less_dense_than_dry(self):
        """Counter-intuitive but correct: water vapour displaces heavier N2/O2."""
        assert air_density(25.0, 101325.0, 100.0) < air_density(25.0, 101325.0, 0.0)

    @pytest.mark.parametrize(
        "temp_c,expected_pct,tol",
        [(5.0, -0.33, 0.05), (15.0, -0.64, 0.05), (25.0, -1.18, 0.05), (35.0, -2.10, 0.05)],
    )
    def test_humidity_is_the_smallest_term(self, temp_c, expected_pct, tol):
        """Pins the magnitudes the design doc justifies buying a BME280 on.

        Going bone-dry to saturated is worth at most ~2%. If this test ever
        fails high, the humidity channel matters more than we claimed and the
        BMP280 fallback assumption needs revisiting.
        """
        dry = air_density(temp_c, 101325.0, 0.0)
        wet = air_density(temp_c, 101325.0, 100.0)
        assert 100.0 * (wet / dry - 1.0) == pytest.approx(expected_pct, abs=tol)

    def test_denver_is_about_twenty_percent_thinner(self):
        """The case that motivates the whole subsystem."""
        denver = air_density(25.0, 83500.0, 40.0)
        assert denver == pytest.approx(0.970, abs=0.005)
        assert 100.0 * (denver / 1.225 - 1.0) < -19.0

    def test_reduces_to_ideal_gas_law_when_dry(self):
        """With no vapour the model must be exactly P/(R_d*T)."""
        temp_c, pressure_pa = 22.0, 99000.0
        expected = pressure_pa / (R_DRY_AIR * (temp_c + 273.15))
        assert air_density(temp_c, pressure_pa, 0.0) == pytest.approx(expected, rel=1e-12)

    def test_vapour_uses_its_own_gas_constant(self):
        """Guards against the classic bug of applying R_dry to the vapour term."""
        temp_c, pressure_pa, rh = 30.0, 101325.0, 80.0
        p_v = rh / 100.0 * saturation_vapour_pressure_pa(temp_c)
        temp_k = temp_c + 273.15
        expected = (pressure_pa - p_v) / (R_DRY_AIR * temp_k) + p_v / (R_WATER_VAPOUR * temp_k)
        assert air_density(temp_c, pressure_pa, rh) == pytest.approx(expected, rel=1e-12)

    def test_humidity_defaults_to_dry(self):
        assert air_density(15.0, 101325.0) == air_density(15.0, 101325.0, 0.0)


class TestAirDensityValidation:
    """Bad inputs are sensor faults and must be loud, except RH which clamps."""

    def test_humidity_above_100_clamps_rather_than_raises(self):
        """A working sensor legitimately reports 100.3% in fog."""
        assert air_density(15.0, 101325.0, 100.4) == air_density(15.0, 101325.0, 100.0)

    def test_negative_humidity_clamps_to_zero(self):
        assert air_density(15.0, 101325.0, -0.2) == air_density(15.0, 101325.0, 0.0)

    @pytest.mark.parametrize("temp_c", [-500.0, 500.0, 81.0, -81.0])
    def test_rejects_impossible_temperatures(self, temp_c):
        with pytest.raises(ValueError, match="sensor fault"):
            air_density(temp_c, 101325.0, 50.0)

    def test_rejects_hectopascals_mistaken_for_pascals(self):
        """1013 hPa typed as 1013 Pa is the unit slip this catches."""
        with pytest.raises(ValueError, match="hPa"):
            air_density(15.0, 1013.0, 50.0)

    @pytest.mark.parametrize("pressure_pa", [0.0, -1.0, MIN_PRESSURE_PA - 1, MAX_PRESSURE_PA + 1])
    def test_rejects_impossible_pressures(self, pressure_pa):
        with pytest.raises(ValueError):
            air_density(15.0, pressure_pa, 50.0)


class TestPressureFromElevation:
    """The no-barometer fallback."""

    def test_sea_level_is_isa_standard(self):
        assert pressure_from_elevation_pa(0.0) == pytest.approx(101325.0, abs=1.0)

    @pytest.mark.parametrize(
        "elevation_m,expected_pa,tol",
        [(1609.0, 83400.0, 700.0), (500.0, 95461.0, 300.0), (2500.0, 74692.0, 700.0)],
    )
    def test_matches_isa_table(self, elevation_m, expected_pa, tol):
        assert pressure_from_elevation_pa(elevation_m) == pytest.approx(expected_pa, abs=tol)

    def test_falls_monotonically_with_height(self):
        heights = [0.0, 500.0, 1000.0, 2000.0, 3000.0]
        values = [pressure_from_elevation_pa(h) for h in heights]
        assert values == sorted(values, reverse=True)

    def test_output_is_accepted_by_air_density(self):
        """The two functions must compose without tripping the pressure bounds."""
        for elevation_m in (0.0, 1609.0, 3000.0):
            rho = air_density(20.0, pressure_from_elevation_pa(elevation_m), 40.0)
            assert 0.7 < rho < 1.4

    def test_estimated_pressure_is_close_to_measured(self):
        """Sacramento, elev 9 m: the elevation path should track a real barometer.

        Measured 1010.2 hPa on 2026-07-29 gave rho=1.1315 at 36.1 C / 25% RH.
        The elevation estimate cannot see weather, so it will differ -- but by
        well under 1%, which is a fraction of a yard.
        """
        measured = air_density(36.1, 101020.0, 25.0)
        estimated = air_density(36.1, pressure_from_elevation_pa(9.0), 25.0)
        assert abs(estimated / measured - 1.0) < 0.01

    @pytest.mark.parametrize("elevation_m", [-1000.0, 9000.0, 100000.0])
    def test_rejects_impossible_elevations(self, elevation_m):
        with pytest.raises(ValueError, match="elevation_m"):
            pressure_from_elevation_pa(elevation_m)


class TestPurity:
    """density.py must stay dependency-free so it is trivially testable."""

    def test_module_imports_only_math(self):
        from openflight.environment import density

        assert density.math is math
        assert not hasattr(density, "smbus2")
        assert not hasattr(density, "logging")


class TestDensityAltitude:
    """The density figure in a unit people actually reason in.

    "Plays like 2,700 ft" is checkable against experience; "-7.6%" is not.
    Standard aviation formula, computed from the density already resolved, so
    it introduces no new inputs and cannot disagree with the carry correction.
    """

    def test_isa_sea_level_is_zero(self):
        assert density_altitude_ft(1.225) == pytest.approx(0.0, abs=1.0)

    def test_thin_air_reads_high(self):
        # Denver on a warm day: well above its 5,280 ft of real elevation.
        assert density_altitude_ft(0.97) == pytest.approx(7760, abs=100)

    def test_a_hot_day_at_sea_level_still_reads_thousands_of_feet(self):
        """Sacramento at 97 F. The whole point of the readout: a sea-level
        venue can play like it is most of a mile up."""
        assert density_altitude_ft(1.1316) == pytest.approx(2685, abs=100)

    def test_dense_air_reads_below_sea_level(self):
        assert density_altitude_ft(1.30) < 0

    def test_monotonic_in_density(self):
        altitudes = [density_altitude_ft(rho) for rho in (0.95, 1.05, 1.15, 1.225, 1.30)]
        assert altitudes == sorted(altitudes, reverse=True)

    @pytest.mark.parametrize("bad", [0.0, -1.0])
    def test_non_positive_density_is_refused(self, bad):
        with pytest.raises(ValueError):
            density_altitude_ft(bad)


class TestNonFiniteHumidityIsRejected:
    """Clamping alone laundered nonsense into a plausible answer: NaN survives
    min/max untouched and poisons the density silently."""

    @pytest.mark.parametrize("humidity", [float("nan"), float("inf"), float("-inf")])
    def test_rejected(self, humidity):
        with pytest.raises(ValueError):
            air_density(20.0, 101325.0, humidity)

    def test_the_provider_degrades_rather_than_serving_a_nan_density(self):
        from openflight.environment.config import WeatherConfig
        from openflight.environment.provider import EnvironmentProvider

        provider = EnvironmentProvider(
            WeatherConfig(
                cached={
                    "temp_c": 20.0,
                    "pressure_hpa": 1013.0,
                    "humidity_pct": float("nan"),
                    "fetched_at": 1.0,
                }
            )
        )

        assert provider.current().source == "default"

    @pytest.mark.parametrize("humidity", [0.0, 50.0, 100.0, -5.0, 120.0, 1e308])
    def test_finite_values_including_absurd_ones_still_clamp(self, humidity):
        """1e308 is out of range the same way 120 is, and the clamp is
        documented behaviour for those -- only non-finite input is rejected."""
        assert 1.0 < air_density(20.0, 101325.0, humidity) < 1.3

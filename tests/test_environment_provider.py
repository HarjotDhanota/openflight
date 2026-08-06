"""Tests for the environment provider.

Two sources for now: a fitted sensor, or ISA sea level. Manual entry and an
outdoor weather API are separate changes; the precedence order is written so
they slot in above ``default`` without disturbing what is here.
"""

import pytest

from openflight.environment.bme280 import SensorReading
from openflight.environment.provider import (
    ASSUMED_HUMIDITY_PCT,
    ISA_DENSITY,
    SENSOR_MAX_AGE_S,
    EnvironmentProvider,
)


def reading(temp_c=25.0, pressure_hpa=1013.25, humidity_pct=50.0, chip="bme280"):
    return SensorReading(
        temp_c=temp_c, pressure_hpa=pressure_hpa, humidity_pct=humidity_pct, chip=chip
    )


class TestWithNoSensor:
    """The default install. Every carry number must be exactly as it was."""

    def test_reports_isa_sea_level(self):
        assert EnvironmentProvider().current().air_density_kg_m3 == ISA_DENSITY

    def test_reports_default_as_the_source(self):
        assert EnvironmentProvider().current().source == "default"

    def test_has_no_measured_values_to_show(self):
        current = EnvironmentProvider().current()

        assert current.temp_c is None
        assert current.pressure_hpa is None
        assert current.humidity_pct is None


class TestWithASensor:
    def test_a_reading_becomes_the_density(self):
        provider = EnvironmentProvider()
        provider.set_sensor_reading(reading(temp_c=25.0, pressure_hpa=835.0, humidity_pct=40.0))

        current = provider.current()

        assert current.source == "bme280"
        assert current.air_density_kg_m3 == pytest.approx(0.970, abs=0.005)

    def test_the_measured_values_are_carried_through_for_display(self):
        provider = EnvironmentProvider()
        provider.set_sensor_reading(reading(temp_c=31.2, pressure_hpa=1009.4, humidity_pct=28.0))

        current = provider.current()

        assert current.temp_c == pytest.approx(31.2)
        assert current.pressure_hpa == pytest.approx(1009.4)
        assert current.humidity_pct == pytest.approx(28.0)

    def test_the_source_names_the_chip_that_was_actually_found(self):
        """A BMP280 is a materially different reading -- its humidity is
        assumed, not measured -- so the provenance must say which part it was
        rather than flattening both to "sensor"."""
        provider = EnvironmentProvider()
        provider.set_sensor_reading(reading(chip="bmp280", humidity_pct=None))

        assert provider.current().source == "bmp280"


class TestAMissingHumidityChannel:
    """A BMP280 measures temperature and pressure but not humidity."""

    def test_humidity_is_assumed_rather_than_treated_as_zero(self):
        provider = EnvironmentProvider()
        provider.set_sensor_reading(reading(chip="bmp280", humidity_pct=None))

        assert provider.current().humidity_pct == ASSUMED_HUMIDITY_PCT

    def test_the_assumption_costs_less_than_the_sensor_it_replaces(self):
        """Going bone-dry to saturated is worth ~1.2% of density at 25 C, so
        assuming the midpoint bounds the error at ~0.6% -- about 0.4 yd on a
        driver. Worth stating, not worth blocking on."""
        provider = EnvironmentProvider()

        provider.set_sensor_reading(reading(chip="bmp280", humidity_pct=None, temp_c=25.0))
        assumed = provider.current().air_density_kg_m3

        provider.set_sensor_reading(reading(chip="bme280", humidity_pct=0.0, temp_c=25.0))
        driest = provider.current().air_density_kg_m3
        provider.set_sensor_reading(reading(chip="bme280", humidity_pct=100.0, temp_c=25.0))
        wettest = provider.current().air_density_kg_m3

        assert abs(assumed / driest - 1.0) < 0.01
        assert abs(assumed / wettest - 1.0) < 0.01


class TestAStaleReading:
    """A sensor that stops answering must not pin yesterday's air forever."""

    def test_a_recent_reading_is_used(self):
        provider = EnvironmentProvider(now=lambda: 1000.0)
        provider.set_sensor_reading(reading())

        assert provider.current().source == "bme280"

    def test_a_reading_older_than_the_limit_falls_back_to_isa(self):
        clock = {"t": 1000.0}
        provider = EnvironmentProvider(now=lambda: clock["t"])
        provider.set_sensor_reading(reading())

        clock["t"] += SENSOR_MAX_AGE_S + 1

        current = provider.current()
        assert current.source == "default"
        assert current.air_density_kg_m3 == ISA_DENSITY

    def test_the_age_is_reported_so_the_ui_can_show_it(self):
        clock = {"t": 1000.0}
        provider = EnvironmentProvider(now=lambda: clock["t"])
        provider.set_sensor_reading(reading())
        clock["t"] += 12.0

        assert provider.current().age_s == pytest.approx(12.0)


class TestBadReadingsDegrade:
    """Resolution runs on the shot path, outside the caller's exception guard.
    Anything that raises here loses the shot, not just the correction."""

    @pytest.mark.parametrize(
        "bad",
        [
            {"temp_c": -300.0},
            {"temp_c": 500.0},
            {"pressure_hpa": 0.0},
            {"pressure_hpa": 5000.0},
            {"humidity_pct": float("nan")},
        ],
    )
    def test_an_impossible_reading_falls_back_rather_than_raising(self, bad):
        provider = EnvironmentProvider()
        provider.set_sensor_reading(reading(**bad))

        assert provider.current().source == "default"

    def test_a_good_reading_after_a_bad_one_is_used(self):
        provider = EnvironmentProvider()
        provider.set_sensor_reading(reading(temp_c=-300.0))
        assert provider.current().source == "default"

        provider.set_sensor_reading(reading(temp_c=22.0))

        assert provider.current().source == "bme280"


class TestWireFormat:
    """What the settings screen and the shot record consume."""

    def test_rounds_to_the_precision_anything_downstream_can_use(self):
        provider = EnvironmentProvider()
        provider.set_sensor_reading(reading(temp_c=31.234, pressure_hpa=1009.44, humidity_pct=28.4))

        payload = provider.current().as_dict()

        assert payload["temp_c"] == 31.2
        assert payload["pressure_hpa"] == 1009.4
        assert payload["humidity_pct"] == 28

    def test_reports_density_altitude_because_that_is_the_legible_unit(self):
        provider = EnvironmentProvider()
        provider.set_sensor_reading(reading(temp_c=25.0, pressure_hpa=835.0, humidity_pct=40.0))

        payload = provider.current().as_dict()

        assert payload["density_altitude_ft"] > 7000
        assert payload["deviation_pct"] < -19.0

    def test_the_default_state_is_flagged_so_the_ui_can_say_so(self):
        payload = EnvironmentProvider().current().as_dict()

        assert payload["source"] == "default"
        assert payload["deviation_pct"] == 0.0
        assert payload["density_altitude_ft"] == 0

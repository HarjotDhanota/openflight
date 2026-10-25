"""Which air a shot flies in: a fresh, plausible BME280 reading, else ISA with the reason.

Nothing is filled in: ISA conditions carry no temperature, pressure or humidity, and a
stale, failing or implausible sensor falls back to ISA saying why.
"""

import math
import threading

import pytest

from openflight.air.bme280 import AirSample
from openflight.air.conditions import (
    MAX_READING_AGE_S,
    SOURCE_BME280,
    SOURCE_ISA_DEFAULT,
    density_altitude_ft,
    from_sample,
    isa_default,
)
from openflight.air.density import humid_air_density_kg_m3
from openflight.air.service import AirSensorService
from openflight.ballistics import AIR_DENSITY_STD


def _sample(timestamp=1000.0, temperature_c=30.0, pressure_hpa=990.0, humidity_pct=40.0):
    return AirSample(timestamp, temperature_c, pressure_hpa, humidity_pct)


class TestDensity:
    def test_hot_humid_low_pressure_air_is_thinner_than_isa(self):
        reading = from_sample(_sample(), now=1000.0)
        expected = humid_air_density_kg_m3(30.0, 99000.0, 40.0)
        assert reading.air_density_kg_m3 == pytest.approx(expected)
        assert reading.air_density_kg_m3 < AIR_DENSITY_STD

    def test_density_altitude_is_zero_for_isa_and_rises_as_air_thins(self):
        assert density_altitude_ft(AIR_DENSITY_STD) == pytest.approx(0.0, abs=1.0)
        # ISA at 5,000 ft is 1.0555 kg/m3
        assert density_altitude_ft(1.0555) == pytest.approx(5000.0, abs=25.0)


class TestFromSample:
    def test_a_fresh_reading_is_the_bme280s(self):
        reading = from_sample(_sample(timestamp=990.0), now=1000.0)
        assert reading.source == SOURCE_BME280
        assert reading.measured
        assert (reading.temperature_c, reading.pressure_hpa, reading.humidity_pct) == (
            30.0,
            990.0,
            40.0,
        )
        assert reading.age_s == pytest.approx(10.0)
        assert reading.measured_at == 990.0
        assert reading.reason is None

    def test_a_stale_reading_falls_back_to_isa_with_its_age(self):
        reading = from_sample(_sample(timestamp=1000.0), now=1000.0 + MAX_READING_AGE_S + 5)
        assert reading.source == SOURCE_ISA_DEFAULT
        assert reading.air_density_kg_m3 == AIR_DENSITY_STD
        assert "65 s old" in reading.reason and f"{MAX_READING_AGE_S:.0f} s" in reading.reason

    @pytest.mark.parametrize(
        ("field", "value", "words"),
        [
            ("temperature_c", 120.0, "temperature"),
            ("temperature_c", -60.0, "temperature"),
            ("temperature_c", math.nan, "temperature"),
            ("pressure_hpa", 250.0, "pressure"),
            ("pressure_hpa", 1200.0, "pressure"),
            ("humidity_pct", math.inf, "humidity"),
            ("humidity_pct", -1.0, "humidity"),
        ],
    )
    def test_an_implausible_reading_falls_back_to_isa_naming_the_channel(self, field, value, words):
        reading = from_sample(_sample(**{field: value}), now=1000.0)
        assert reading.source == SOURCE_ISA_DEFAULT
        assert reading.air_density_kg_m3 == AIR_DENSITY_STD
        assert words in reading.reason and "out of range" in reading.reason


class TestIsaDefault:
    def test_isa_fills_in_no_conditions(self):
        reading = isa_default("no BME280 on I2C bus 1")
        assert reading.source == SOURCE_ISA_DEFAULT
        assert not reading.measured
        assert reading.air_density_kg_m3 == AIR_DENSITY_STD
        assert reading.temperature_c is None
        assert reading.pressure_hpa is None
        assert reading.humidity_pct is None
        assert reading.reason == "no BME280 on I2C bus 1"

    def test_the_wire_format_carries_the_source_and_the_reason(self):
        payload = isa_default("why").to_dict()
        assert payload["source"] == SOURCE_ISA_DEFAULT
        assert payload["reason"] == "why"
        assert payload["air_density_kg_m3"] == AIR_DENSITY_STD
        assert payload["density_altitude_ft"] is None

    def test_a_measured_wire_format_rounds_for_display(self):
        payload = from_sample(_sample(timestamp=999.0), now=1000.0).to_dict()
        assert payload["source"] == SOURCE_BME280
        assert payload["temperature_c"] == 30.0
        assert payload["humidity_pct"] == 40.0
        assert payload["age_s"] == 1.0
        assert isinstance(payload["density_altitude_ft"], int)


class FakeSensor:
    def __init__(self, samples=None):
        self.samples = list(samples or [])
        self.initialized = False
        self.closed = False
        self.reads = 0

    def initialize(self):
        self.initialized = True

    def read(self, *, timestamp=None):
        self.reads += 1
        item = self.samples.pop(0) if self.samples else OSError("bus gone")
        if isinstance(item, Exception):
            raise item
        return AirSample(timestamp, *item)

    def close(self):
        self.closed = True


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


class TestService:
    def test_no_reading_yet_is_isa_saying_so(self):
        service = AirSensorService(FakeSensor(), clock=Clock())
        reading = service.conditions()
        assert reading.source == SOURCE_ISA_DEFAULT
        assert reading.reason == "no BME280 reading yet"

    def test_a_good_poll_is_measured(self):
        service = AirSensorService(FakeSensor([(22.0, 1005.0, 55.0)]), clock=Clock())
        reading = service.poll_once()
        assert reading.source == SOURCE_BME280
        assert reading.temperature_c == 22.0
        assert service.conditions().source == SOURCE_BME280

    def test_a_failed_first_read_is_isa_with_the_error(self):
        service = AirSensorService(FakeSensor([OSError("Remote I/O error")]), clock=Clock())
        reading = service.poll_once()
        assert reading.source == SOURCE_ISA_DEFAULT
        assert "Remote I/O error" in reading.reason
        assert service.last_error == "Remote I/O error"

    def test_one_dropped_read_keeps_the_fresh_reading(self):
        clock = Clock()
        sensor = FakeSensor([(22.0, 1005.0, 55.0), OSError("Remote I/O error")])
        service = AirSensorService(sensor, clock=clock)
        service.poll_once()
        clock.now += 5.0
        reading = service.poll_once()
        assert reading.source == SOURCE_BME280
        assert reading.age_s == pytest.approx(5.0)

    def test_a_sensor_that_stops_answering_goes_stale_to_isa_with_the_error(self):
        clock = Clock()
        sensor = FakeSensor([(22.0, 1005.0, 55.0)])
        service = AirSensorService(sensor, clock=clock)
        service.poll_once()
        clock.now += MAX_READING_AGE_S + 1.0
        reading = service.poll_once()
        assert reading.source == SOURCE_ISA_DEFAULT
        assert "61 s old" in reading.reason and "bus gone" in reading.reason

    def test_a_good_read_clears_the_last_error(self):
        sensor = FakeSensor([OSError("Remote I/O error"), (22.0, 1005.0, 55.0)])
        service = AirSensorService(sensor, clock=Clock())
        service.poll_once()
        service.poll_once()
        assert service.last_error is None

    def test_every_poll_is_published_and_a_failing_listener_does_not_stop_it(self):
        seen = []

        def listener(reading):
            seen.append(reading.source)
            raise RuntimeError("socket gone")

        sensor = FakeSensor([(22.0, 1005.0, 55.0), OSError("x")])
        service = AirSensorService(sensor, clock=Clock(), on_update=listener)
        service.poll_once()
        service.poll_once()
        assert seen == [SOURCE_BME280, SOURCE_BME280]

    def test_start_initializes_polls_and_stop_ends_the_thread(self):
        polled = threading.Event()
        sensor = FakeSensor([(22.0, 1005.0, 55.0)])
        service = AirSensorService(
            sensor, poll_interval_s=60.0, on_update=lambda _reading: polled.set()
        )
        service.start()
        assert polled.wait(2.0)
        assert sensor.initialized
        service.stop()
        assert sensor.closed
        assert not service.running

    def test_the_poll_interval_must_be_positive(self):
        with pytest.raises(ValueError):
            AirSensorService(FakeSensor(), poll_interval_s=0.0)

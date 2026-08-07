"""Resolves air density from whatever sources are available.

One object owns the question "what is the air density right now, and how do we
know". Everything else -- the shot path, the settings screen, the logs --
asks it rather than reimplementing the precedence.

Source order, best first:

  1. sensor     a fitted BME280 or BMP280, read on a background poll
  2. manual     the user typed the numbers
  3. open-meteo fetched for the configured location, cached until refreshed
  4. elevation  temperature plus an ISA pressure estimate, no barometer
  5. default    ISA sea level -- the only behaviour before this subsystem

The sensor outranks everything, including manual entry, because an API
returns a grid-cell average of OUTDOOR conditions and a typed value goes
stale the moment the evening cools -- while the sensor measures the air the
ball actually flies through, indoors and out. The one thing that outranks it
is the user saying "no correction" (mode off), which always wins.

Nothing here performs network or bus I/O. Values are pushed in by their
owners; ``current()`` is a pure read so it is safe from the shot path.
"""

import logging
import time
from dataclasses import dataclass
from typing import Callable, Optional

from openflight.environment.bme280 import SensorReading
from openflight.environment.config import (
    DEFAULT_HUMIDITY_PCT,
    MODE_MANUAL,
    MODE_OFF,
    WeatherConfig,
)
from openflight.environment.density import (
    air_density,
    density_altitude_ft,
    pressure_from_elevation_pa,
)

logger = logging.getLogger(__name__)

ISA_DENSITY = 1.225

# Used when the sensor has no humidity channel (BMP280). The midpoint bounds
# the error at roughly half the dry-to-saturated span -- about 0.6% of density
# at 25 C, or 0.4 yd on a driver.
ASSUMED_HUMIDITY_PCT = 50.0

# Older than this and the sensor is treated as absent, falling through to the
# next source. The poll runs every couple of seconds, so reaching this means
# the part stopped answering -- usually a jumper that has worked loose.
SENSOR_MAX_AGE_S = 60.0


@dataclass
class EnvironmentReading:
    """Resolved conditions plus where they came from."""

    air_density_kg_m3: float
    source: str
    temp_c: Optional[float] = None
    pressure_hpa: Optional[float] = None
    humidity_pct: Optional[float] = None
    age_s: Optional[float] = None  # how old the underlying data is, for the UI
    # True when humidity was assumed rather than measured (a BMP280), so the
    # UI can say so instead of presenting an assumption as a reading.
    humidity_assumed: bool = False

    def as_dict(self) -> dict:
        """Wire format for the settings screen and the shot record."""
        return {
            "air_density_kg_m3": round(self.air_density_kg_m3, 4),
            "source": self.source,
            "temp_c": round(self.temp_c, 1) if self.temp_c is not None else None,
            "pressure_hpa": round(self.pressure_hpa, 1) if self.pressure_hpa is not None else None,
            "humidity_pct": round(self.humidity_pct) if self.humidity_pct is not None else None,
            "humidity_assumed": self.humidity_assumed,
            "age_s": round(self.age_s) if self.age_s is not None else None,
            "deviation_pct": round(100.0 * (self.air_density_kg_m3 / ISA_DENSITY - 1.0), 1),
            # The headline figure on the panel: the same density in the unit
            # people can sanity-check against experience.
            "density_altitude_ft": round(density_altitude_ft(self.air_density_kg_m3)),
        }


class EnvironmentProvider:
    """Holds the current environmental state and resolves it to a density.

    Args:
        config: Weather settings (manual values, cached fetch, mode). Defaults
            to a fresh config, which resolves to ISA -- the sensor-only case.
        now: Clock, injected so staleness is testable without sleeping.
    """

    def __init__(
        self, config: Optional[WeatherConfig] = None, now: Optional[Callable[[], float]] = None
    ):
        self.config = config if config is not None else WeatherConfig()
        # Resolved lazily so tests that patch time.time after import still
        # steer every clock read; a bound default would freeze the original.
        self._now_override = now
        self._override: Optional[EnvironmentReading] = None
        self._sensor_reading: Optional[SensorReading] = None
        self._sensor_read_at: Optional[float] = None

    def _now(self) -> float:
        return self._now_override() if self._now_override is not None else time.time()

    def set_sensor_reading(self, reading: SensorReading) -> None:
        """Called by the poll thread after a successful sensor read."""
        self._sensor_reading = reading
        self._sensor_read_at = self._now()

    def set_fetched_weather(self, temp_c: float, pressure_hpa: float, humidity_pct: float) -> None:
        """Called after a successful Open-Meteo fetch. Persisted by the caller."""
        self.config.cached = {
            "temp_c": temp_c,
            "pressure_hpa": pressure_hpa,
            "humidity_pct": humidity_pct,
            "fetched_at": self._now(),
        }

    def set_cli_override(self, reading: Optional[EnvironmentReading]) -> None:
        """Session-only override from CLI flags. Outranks everything, never saved."""
        self._override = reading

    def standard_density(self) -> float:
        """Air density at the user's fixed reference conditions.

        Used for the second, comparable carry figure. Independent of whatever
        the weather is doing right now, which is the entire point.
        """
        pressure_pa = pressure_from_elevation_pa(self.config.standard_elevation_m)
        return air_density(
            self.config.standard_temp_c, pressure_pa, self.config.standard_humidity_pct
        )

    def current(self) -> EnvironmentReading:
        """Resolve conditions now. Never raises, never blocks."""
        if self._override is not None:
            return self._override
        if self.config.mode == MODE_OFF:
            return EnvironmentReading(ISA_DENSITY, "default")

        reading = self._from_sensor() or self._from_manual() or self._from_cache()
        return reading or EnvironmentReading(ISA_DENSITY, "default")

    def _from_sensor(self) -> Optional[EnvironmentReading]:
        if self._sensor_reading is None or self._sensor_read_at is None:
            return None
        age = self._now() - self._sensor_read_at
        if age > SENSOR_MAX_AGE_S:
            return None

        humidity = self._sensor_reading.humidity_pct
        assumed = humidity is None
        if assumed:
            humidity = ASSUMED_HUMIDITY_PCT

        reading = self._build(
            {
                "temp_c": self._sensor_reading.temp_c,
                "pressure_hpa": self._sensor_reading.pressure_hpa,
                "humidity_pct": humidity,
            },
            self._sensor_reading.chip,
            age_s=age,
        )
        if reading is not None:
            reading.humidity_assumed = assumed
        return reading

    def _from_manual(self) -> Optional[EnvironmentReading]:
        if self.config.mode != MODE_MANUAL or self.config.manual_temp_c is None:
            return None
        if self.config.manual_pressure_hpa is not None:
            pressure_hpa = self.config.manual_pressure_hpa
            source = "manual"
        elif self.config.manual_elevation_m is not None:
            pressure_hpa = pressure_from_elevation_pa(self.config.manual_elevation_m) / 100.0
            source = "elevation"
        else:
            pressure_hpa = 1013.25
            source = "elevation"
        values = {
            "temp_c": self.config.manual_temp_c,
            "pressure_hpa": pressure_hpa,
            "humidity_pct": self.config.manual_humidity_pct,
        }
        return self._build(values, source)

    def _from_cache(self) -> Optional[EnvironmentReading]:
        cached = self.config.cached
        if not cached or cached.get("temp_c") is None:
            return None
        values = dict(cached)
        # Indoors, keep the fetched pressure -- buildings are not pressure
        # vessels, so outdoor pressure is right indoors -- but take the
        # temperature from the user, because indoor and outdoor air differ by
        # far more than the pressure does. (A fitted sensor makes this whole
        # branch moot: it measures the indoor air directly and outranks this.)
        #
        # Reads indoor_temp_c, never manual_temp_c: manual entry is a separate
        # set-up, and borrowing from it meant a value typed there silently
        # changed what local weather reported.
        if self.config.indoors and self.config.indoor_temp_c is not None:
            values["temp_c"] = self.config.indoor_temp_c
            values["humidity_pct"] = self.config.indoor_humidity_pct
        fetched_at = cached.get("fetched_at")
        age = self._now() - fetched_at if fetched_at else None
        return self._build(values, "open-meteo", age_s=age)

    def _build(self, values: dict, source: str, age_s: Optional[float] = None):
        """Turn raw values into a reading, degrading to the next source on bad input."""
        temp_c = values.get("temp_c")
        pressure_hpa = values.get("pressure_hpa")
        humidity = values.get("humidity_pct")
        if humidity is None:
            humidity = DEFAULT_HUMIDITY_PCT
        if temp_c is None or pressure_hpa is None:
            return None
        try:
            density = air_density(temp_c, pressure_hpa * 100.0, humidity)
        except (ValueError, TypeError):
            # TypeError as well as ValueError: these values come from a JSON
            # file a user can edit, from socket payloads, and from a sensor
            # that can garble a read. This runs on the shot path, outside the
            # caller's exception guard -- anything raising here loses the
            # shot, not just the correction.
            #
            # %r, not %.1f: the values may not be numbers, and a formatting
            # error inside the handler would raise the very exception this is
            # here to swallow.
            logger.warning(
                "[WEATHER] Rejecting %s reading: %r C, %r hPa, %r%% RH",
                source,
                temp_c,
                pressure_hpa,
                humidity,
            )
            return None
        return EnvironmentReading(density, source, temp_c, pressure_hpa, humidity, age_s)

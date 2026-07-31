"""Resolves air density from whatever sources are available.

One object owns the question "what is the air density right now, and how do we
know". Everything else -- the shot path, the settings screen, the logs --
asks it rather than reimplementing the precedence.

Source order, best first:

  1. bme280     a fitted sensor, measuring the air the ball flies through
  2. manual     the user typed the numbers
  3. open-meteo fetched for the configured location, cached until refreshed
  4. elevation  temperature plus an ISA pressure estimate, no barometer
  5. default    ISA sea level -- the only behaviour before this subsystem

The sensor outranks fetched weather deliberately. An API returns a grid-cell
average of OUTDOOR conditions; in a 22 C garage on a 36 C day that correction
is actively wrong, worse than none. The sensor cannot have that failure.

Nothing here performs network or bus I/O. Values are pushed in by their
owners; ``current()`` is a pure read so it is safe from the shot path.
"""

import logging
import time
from dataclasses import dataclass
from typing import Optional

from openflight.environment.config import (
    DEFAULT_HUMIDITY_PCT,
    MODE_MANUAL,
    MODE_OFF,
    STANDARD_HUMIDITY_PCT,
    WeatherConfig,
)
from openflight.environment.density import (
    air_density,
    density_altitude_ft,
    pressure_from_elevation_pa,
)

logger = logging.getLogger(__name__)

ISA_DENSITY = 1.225

# A sensor reading older than this means the poll thread has died or the bus
# has gone quiet; fall through rather than trust it.
SENSOR_STALE_S = 60.0


@dataclass
class EnvironmentReading:
    """Resolved conditions plus where they came from."""

    air_density_kg_m3: float
    source: str
    temp_c: Optional[float] = None
    pressure_hpa: Optional[float] = None
    humidity_pct: Optional[float] = None
    age_s: Optional[float] = None  # how old the underlying data is, for the UI

    def as_dict(self) -> dict:
        """Wire format for the settings screen and the shot record."""
        return {
            "air_density_kg_m3": round(self.air_density_kg_m3, 4),
            "source": self.source,
            "temp_c": round(self.temp_c, 1) if self.temp_c is not None else None,
            "pressure_hpa": round(self.pressure_hpa, 1) if self.pressure_hpa is not None else None,
            "humidity_pct": round(self.humidity_pct) if self.humidity_pct is not None else None,
            "age_s": round(self.age_s) if self.age_s is not None else None,
            "deviation_pct": round(100.0 * (self.air_density_kg_m3 / ISA_DENSITY - 1.0), 1),
            # The headline figure on the panel: the same density in the unit
            # people can sanity-check against experience.
            "density_altitude_ft": round(density_altitude_ft(self.air_density_kg_m3)),
        }


class EnvironmentProvider:
    """Holds the current environmental state and resolves it to a density."""

    def __init__(self, config: WeatherConfig):
        self.config = config
        self._sensor: Optional[dict] = None
        self._sensor_at: float = 0.0
        self._override: Optional[EnvironmentReading] = None

    def set_sensor_reading(self, temp_c: float, pressure_hpa: float, humidity_pct: float) -> None:
        """Called by the BME280 poll thread. Cheap; safe to call often."""
        self._sensor = {
            "temp_c": temp_c,
            "pressure_hpa": pressure_hpa,
            "humidity_pct": humidity_pct,
        }
        self._sensor_at = time.monotonic()

    def set_fetched_weather(self, temp_c: float, pressure_hpa: float, humidity_pct: float) -> None:
        """Called after a successful Open-Meteo fetch. Persisted by the caller."""
        self.config.cached = {
            "temp_c": temp_c,
            "pressure_hpa": pressure_hpa,
            "humidity_pct": humidity_pct,
            "fetched_at": time.time(),
        }

    def set_cli_override(self, reading: Optional[EnvironmentReading]) -> None:
        """Session-only override from CLI flags. Outranks everything, never saved."""
        self._override = reading

    def sensor_present(self) -> bool:
        """True when a sensor has reported recently.

        Deliberately separate from whether the sensor is the ACTIVE source --
        a fitted sensor sitting unused because the mode is manual or off is a
        misconfiguration the UI should be able to point out.
        """
        if self._sensor is None:
            return False
        return (time.monotonic() - self._sensor_at) <= SENSOR_STALE_S

    def standard_density(self) -> float:
        """Air density at the user's fixed reference conditions.

        Used for the second, comparable carry figure. Independent of whatever
        the weather is doing right now, which is the entire point.
        """
        pressure_pa = pressure_from_elevation_pa(self.config.standard_elevation_m)
        return air_density(self.config.standard_temp_c, pressure_pa, STANDARD_HUMIDITY_PCT)

    def current(self) -> EnvironmentReading:
        """Resolve conditions now. Never raises, never blocks."""
        if self._override is not None:
            return self._override
        if self.config.mode == MODE_OFF:
            return EnvironmentReading(ISA_DENSITY, "default")

        reading = self._from_sensor() or self._from_manual() or self._from_cache()
        return reading or EnvironmentReading(ISA_DENSITY, "default")

    def _from_sensor(self) -> Optional[EnvironmentReading]:
        if self._sensor is None:
            return None
        age = time.monotonic() - self._sensor_at
        if age > SENSOR_STALE_S:
            return None
        return self._build(self._sensor, "bme280", age_s=age)

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
        # far more than the pressure does.
        #
        # Reads indoor_temp_c, never manual_temp_c: manual entry is a separate
        # set-up, and borrowing from it meant a value typed there silently
        # changed what local weather reported.
        if self.config.indoors and self.config.indoor_temp_c is not None:
            values["temp_c"] = self.config.indoor_temp_c
            values["humidity_pct"] = self.config.indoor_humidity_pct
        fetched_at = cached.get("fetched_at")
        age = time.time() - fetched_at if fetched_at else None
        return self._build(values, "open-meteo", age_s=age)

    def _build(self, values: dict, source: str, age_s: Optional[float] = None):
        """Turn raw values into a reading, degrading to default on bad input."""
        temp_c = values.get("temp_c")
        pressure_hpa = values.get("pressure_hpa")
        humidity = values.get("humidity_pct")
        if humidity is None:
            humidity = DEFAULT_HUMIDITY_PCT
        if temp_c is None or pressure_hpa is None:
            return None
        try:
            density = air_density(temp_c, pressure_hpa * 100.0, humidity)
        except ValueError:
            # Out-of-range values mean a faulty sensor or a bad manual entry.
            # Loud in the log, but the shot still gets a number.
            logger.warning(
                "[WEATHER] Rejecting %s reading: %.1f C, %.1f hPa, %.0f%% RH",
                source,
                temp_c,
                pressure_hpa,
                humidity,
            )
            return None
        return EnvironmentReading(density, source, temp_c, pressure_hpa, humidity, age_s)

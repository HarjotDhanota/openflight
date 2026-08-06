"""Resolves air density from whatever sources are available.

One object owns the question "what is the air density right now, and how do we
know". Everything else -- the shot path, the conditions screen, the session
log -- asks it rather than reimplementing the precedence.

Source order, best first:

  1. sensor   a fitted BME280 or BMP280, read on a background poll
  2. default  ISA sea level -- the only behaviour before this subsystem

Manual entry and an outdoor weather API are separate changes. They slot in
between those two: both are better than assuming ISA, and both are worse than
measuring the air the ball actually flies through. An API in particular
reports OUTDOOR conditions, so in a 22 C garage on a 36 C day its correction
is actively wrong -- worse than none. That asymmetry is the whole argument
for fitting a sensor, and it is why the sensor sits at the top rather than
being averaged in.

Nothing here performs I/O. Readings are pushed in by the poll thread;
``current()`` is a pure read, so it is safe to call from the shot path.
"""

import logging
import time
from dataclasses import dataclass
from typing import Callable, Optional

from openflight.environment.bme280 import SensorReading
from openflight.environment.density import air_density, density_altitude_ft

logger = logging.getLogger(__name__)

ISA_DENSITY = 1.225

# Used when the part has no humidity channel (BMP280). The midpoint bounds the
# error at roughly half the dry-to-saturated span -- about 0.6% of density at
# 25 C, or 0.4 yd on a driver. Smaller than the bias the sensor removes by a
# wide margin, so it is worth stating rather than worth blocking on.
ASSUMED_HUMIDITY_PCT = 50.0

# Older than this and the sensor is treated as absent. The poll runs every
# couple of seconds, so reaching this means the part stopped answering --
# usually a jumper that has worked loose. Better to fall back to ISA and say
# so than to keep applying a reading from an hour ago.
SENSOR_MAX_AGE_S = 60.0


@dataclass
class EnvironmentReading:
    """Resolved conditions plus where they came from."""

    air_density_kg_m3: float
    source: str
    temp_c: Optional[float] = None
    pressure_hpa: Optional[float] = None
    humidity_pct: Optional[float] = None
    age_s: Optional[float] = None
    # True when humidity was assumed rather than measured, so the UI can say
    # so instead of presenting an assumption as a reading.
    humidity_assumed: bool = False

    def as_dict(self) -> dict:
        """Wire format for the conditions screen and the shot record."""
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
            # people can sanity-check against experience. "Plays like 2,700 ft"
            # can be checked against a round; "-7.6%" cannot.
            "density_altitude_ft": round(density_altitude_ft(self.air_density_kg_m3)),
        }


class EnvironmentProvider:
    """Holds the latest sensor reading and resolves it to a density.

    Args:
        now: Clock, injected so staleness is testable without sleeping.
    """

    def __init__(self, now: Callable[[], float] = time.time):
        self._now = now
        self._reading: Optional[SensorReading] = None
        self._read_at: Optional[float] = None

    def set_sensor_reading(self, reading: SensorReading) -> None:
        """Called by the poll thread after a successful read."""
        self._reading = reading
        self._read_at = self._now()

    def current(self) -> EnvironmentReading:
        """Resolve conditions now. Never raises, never blocks."""
        resolved = self._from_sensor()
        return resolved or EnvironmentReading(ISA_DENSITY, "default")

    def _from_sensor(self) -> Optional[EnvironmentReading]:
        if self._reading is None or self._read_at is None:
            return None
        age = self._now() - self._read_at
        if age > SENSOR_MAX_AGE_S:
            return None

        humidity = self._reading.humidity_pct
        assumed = humidity is None
        if assumed:
            humidity = ASSUMED_HUMIDITY_PCT

        try:
            density = air_density(
                self._reading.temp_c, self._reading.pressure_hpa * 100.0, humidity
            )
        except (ValueError, TypeError):
            # Out-of-range values mean a faulty part or a garbled read, not
            # weather. Loud in the log, but the shot still gets a number --
            # this runs on the shot path, outside the caller's guard, so
            # raising here would lose the shot rather than the correction.
            #
            # %r rather than %.1f: the value may not be a number at all, and a
            # formatting error inside the handler would raise the very
            # exception this exists to swallow.
            logger.warning(
                "[SENSOR] Rejecting %s reading: %r C, %r hPa, %r%% RH",
                self._reading.chip,
                self._reading.temp_c,
                self._reading.pressure_hpa,
                humidity,
            )
            return None

        return EnvironmentReading(
            air_density_kg_m3=density,
            source=self._reading.chip,
            temp_c=self._reading.temp_c,
            pressure_hpa=self._reading.pressure_hpa,
            humidity_pct=humidity,
            age_s=age,
            humidity_assumed=assumed,
        )

"""Resolves air density from whatever sources are available.

One object owns the precedence so the shot path, the conditions screen and
the session log cannot disagree. Sensor first, then ISA sea level -- the
only behaviour before this subsystem. Manual entry and a weather API slot
in between as separate changes.

Nothing here performs I/O; ``current()`` is a pure read.
"""

import logging
import time
from dataclasses import dataclass
from typing import Callable, Optional

from openflight.environment.bme280 import SensorReading
from openflight.environment.density import air_density, density_altitude_ft

logger = logging.getLogger(__name__)

ISA_DENSITY = 1.225

# BMP280 has no humidity channel. The midpoint bounds the error at ~0.6% of
# density, far smaller than the bias the sensor removes.
ASSUMED_HUMIDITY_PCT = 50.0

# The poll runs every couple of seconds, so reaching this means the part
# stopped answering. Fall back to ISA rather than apply a stale reading.
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
    # Lets the UI mark an assumption rather than present it as a reading.
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
            # The panel's headline: the unit people can check against a round.
            "density_altitude_ft": round(density_altitude_ft(self.air_density_kg_m3)),
        }


class EnvironmentProvider:
    """Holds the latest sensor reading and resolves it to a density.

    The clock is injected so staleness is testable without sleeping.
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
            # A faulty part or garbled read, not weather. This runs on the
            # shot path, so raising would lose the shot, not the correction.
            # %r, not %.1f: the value may not be a number at all.
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

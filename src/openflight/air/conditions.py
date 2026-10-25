"""The air a shot flies in: a fresh, plausible BME280 reading, else ISA with the reason.

Nothing is filled in. ISA conditions carry no temperature, pressure or humidity;
a stale, failing or implausible sensor reading falls back to ISA and says why.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..ballistics import AIR_DENSITY_STD
from ..numeric import finite as _finite
from .bme280 import AirSample
from .density import humid_air_density_kg_m3

SOURCE_BME280 = "bme280"
SOURCE_ISA_DEFAULT = "isa_default"

# The sensor is polled every few seconds, so a reading this old means it stopped answering.
MAX_READING_AGE_S = 60.0
# The BME280's operating range (datasheet table 1): outside it the reading is a fault.
TEMPERATURE_RANGE_C = (-40.0, 85.0)
PRESSURE_RANGE_HPA = (300.0, 1100.0)
HUMIDITY_RANGE_PCT = (0.0, 100.0)


@dataclass(frozen=True)
class AirConditions:
    """The density a flight uses, where it came from, and why when it is ISA."""

    air_density_kg_m3: float
    source: str
    temperature_c: float | None = None
    pressure_hpa: float | None = None
    humidity_pct: float | None = None
    measured_at: float | None = None
    age_s: float | None = None
    reason: str | None = None

    @property
    def measured(self) -> bool:
        """True when the density is the sensor's."""
        return self.source == SOURCE_BME280

    def to_dict(self) -> dict:
        """Wire and log format, rounded to the sensor's resolution."""
        return {
            "air_density_kg_m3": round(self.air_density_kg_m3, 4),
            "source": self.source,
            "temperature_c": _rounded(self.temperature_c, 2),
            "pressure_hpa": _rounded(self.pressure_hpa, 2),
            "humidity_pct": _rounded(self.humidity_pct, 1),
            "measured_at": self.measured_at,
            "age_s": _rounded(self.age_s, 1),
            "reason": self.reason,
            "density_altitude_ft": (
                round(density_altitude_ft(self.air_density_kg_m3)) if self.measured else None
            ),
        }


def _rounded(value: float | None, digits: int) -> float | None:
    return round(value, digits) if value is not None else None


def density_altitude_ft(air_density_kg_m3: float) -> float:
    """The ISA altitude whose air has this density (troposphere, ft)."""
    return 145442.16 * (1.0 - (air_density_kg_m3 / AIR_DENSITY_STD) ** 0.234969)


def isa_default(reason: str) -> AirConditions:
    """ISA sea-level air (1.225 kg/m3), and why no reading was used."""
    return AirConditions(AIR_DENSITY_STD, SOURCE_ISA_DEFAULT, reason=reason)


def _out_of_range(sample: AirSample) -> str | None:
    channels = (
        ("temperature", sample.temperature_c, TEMPERATURE_RANGE_C, "°C"),
        ("pressure", sample.pressure_hpa, PRESSURE_RANGE_HPA, "hPa"),
        ("humidity", sample.humidity_pct, HUMIDITY_RANGE_PCT, "%"),
    )
    for name, value, (low, high), unit in channels:
        if not _finite(value) or not low <= value <= high:
            return f"BME280 {name} {value!r} {unit} out of range ({low:g} to {high:g} {unit})"
    return None


def from_sample(
    sample: AirSample, *, now: float, max_age_s: float = MAX_READING_AGE_S
) -> AirConditions:
    """The sample's air when it is fresh and plausible, else ISA with the reason."""
    age_s = max(0.0, now - sample.timestamp)
    if age_s > max_age_s:
        return isa_default(
            f"the last BME280 reading is {age_s:.0f} s old (limit {max_age_s:.0f} s)"
        )
    fault = _out_of_range(sample)
    if fault is not None:
        return isa_default(fault)
    return AirConditions(
        air_density_kg_m3=humid_air_density_kg_m3(
            sample.temperature_c, sample.pressure_hpa * 100.0, sample.humidity_pct
        ),
        source=SOURCE_BME280,
        temperature_c=sample.temperature_c,
        pressure_hpa=sample.pressure_hpa,
        humidity_pct=sample.humidity_pct,
        measured_at=sample.timestamp,
        age_s=age_s,
    )

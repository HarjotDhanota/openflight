"""Humid-air density from temperature, pressure and relative humidity.

Dry air and water vapour as ideal gases summed by partial pressure: ~0.1% over
golf conditions, well below sensor error. Saturation pressure uses Buck (1981).
"""

import math

R_DRY_AIR = 287.058
R_WATER_VAPOUR = 461.495

# Bounds that catch a disconnected or misread sensor, not unusual weather.
MIN_TEMP_C = -80.0
MAX_TEMP_C = 80.0
MIN_PRESSURE_PA = 30_000.0
MAX_PRESSURE_PA = 110_000.0


def saturation_vapour_pressure_pa(temp_c: float) -> float:
    """Saturation vapour pressure of water over liquid, in pascals."""
    _check_temp(temp_c)
    return 611.21 * math.exp((18.678 - temp_c / 234.5) * (temp_c / (257.14 + temp_c)))


def air_density(temp_c: float, pressure_pa: float, humidity_pct: float = 0.0) -> float:
    """Density of humid air in kg/m^3, from absolute station pressure.

    Station pressure is what a barometer at the course reads; it already
    encodes altitude, so no elevation is needed. ISA sea level is 1.225.
    """
    _check_temp(temp_c)
    if not MIN_PRESSURE_PA <= pressure_pa <= MAX_PRESSURE_PA:
        raise ValueError(
            f"pressure_pa={pressure_pa} is outside {MIN_PRESSURE_PA}-{MAX_PRESSURE_PA} Pa; "
            "check the sensor is reading absolute pressure in pascals, not hPa"
        )

    # NaN survives min/max and would poison the density silently; inf would
    # clamp to "saturated" as though measured.
    if not math.isfinite(humidity_pct):
        raise ValueError(f"humidity_pct={humidity_pct!r} is not a finite number")
    # A working sensor reports 100.3% in fog, so clamp rather than reject.
    humidity = min(100.0, max(0.0, humidity_pct))
    vapour_pa = humidity / 100.0 * saturation_vapour_pressure_pa(temp_c)
    dry_pa = pressure_pa - vapour_pa
    temp_k = temp_c + 273.15

    return dry_pa / (R_DRY_AIR * temp_k) + vapour_pa / (R_WATER_VAPOUR * temp_k)


def density_altitude_ft(air_density: float) -> float:
    """Altitude at which standard air has this density."""
    if air_density <= 0:
        raise ValueError(f"air_density={air_density} must be positive")
    return 145442.16 * (1.0 - (air_density / 1.225) ** 0.234969)


def _check_temp(temp_c: float) -> None:
    if not MIN_TEMP_C <= temp_c <= MAX_TEMP_C:
        raise ValueError(
            f"temp_c={temp_c} is outside {MIN_TEMP_C}-{MAX_TEMP_C} C; "
            "this indicates a sensor fault, not weather"
        )

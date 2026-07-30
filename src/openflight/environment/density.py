"""Humid-air density from temperature, pressure and relative humidity.

Air density is the largest systematic error in the carry pipeline. Every
number OpenFlight has produced assumes ISA sea level -- 15 C, 101325 Pa, dry
-- and a hot afternoon at sea level is already 7-8% off that. Denver is 20%.

Model: dry air and water vapour treated as separate ideal gases sharing a
volume, summed by partial pressure. Accurate to ~0.1% over golf conditions,
which is an order of magnitude below the sensor error budget, so the full
CIPM-2007 formulation (with its compressibility factor and enhancement
factor) is deliberately not used -- it would add real complexity for
precision nothing downstream can consume.

Saturation vapour pressure uses Buck (1981), which is accurate to ~0.05%
between -20 C and +50 C.

Note on humidity: it is the SMALLEST of the three terms. Going from bone dry
to saturated changes density by -0.33% at 5 C and -2.1% at 35 C. Pressure and
temperature do the real work. Humid air is *less* dense than dry air, which
surprises people -- water vapour (18 g/mol) is lighter than the nitrogen and
oxygen (~29 g/mol) it displaces.

These are pure functions: no I/O, no logging, no sensor coupling. They take
floats and return a float, so they can be tested against published
psychrometric tables without any hardware.
"""

import math

# Specific gas constants, J/(kg*K). CODATA molar gas constant divided by the
# molar mass of dry air and of water respectively.
R_DRY_AIR = 287.058
R_WATER_VAPOUR = 461.495

# ISA sea-level reference, used by pressure_from_elevation_pa.
ISA_SEA_LEVEL_PRESSURE_PA = 101325.0
ISA_SEA_LEVEL_TEMP_K = 288.15
ISA_LAPSE_RATE_K_PER_M = 0.0065
ISA_EXPONENT = 5.25588  # g*M / (R*L) for the troposphere

# Input sanity bounds. These catch a disconnected or misread sensor, not
# unusual weather -- a golfer will never legitimately be outside them.
MIN_TEMP_C = -80.0
MAX_TEMP_C = 80.0
MIN_PRESSURE_PA = 30_000.0  # ~9000 m, well above any golf course
MAX_PRESSURE_PA = 110_000.0  # below the record sea-level high
MAX_ELEVATION_M = 6000.0
MIN_ELEVATION_M = -500.0  # Dead Sea is about -430 m


def saturation_vapour_pressure_pa(temp_c: float) -> float:
    """Saturation vapour pressure of water over liquid, in pascals.

    Buck (1981) equation. Valid and accurate to ~0.05% from -20 C to +50 C,
    which comfortably covers any condition a golf shot is struck in.

    Args:
        temp_c: Air temperature in degrees Celsius.

    Returns:
        Saturation vapour pressure in Pa.

    Raises:
        ValueError: If ``temp_c`` is outside the sanity bounds, which
            indicates a sensor fault rather than weather.
    """
    _check_temp(temp_c)
    return 611.21 * math.exp((18.678 - temp_c / 234.5) * (temp_c / (257.14 + temp_c)))


def air_density(temp_c: float, pressure_pa: float, humidity_pct: float = 0.0) -> float:
    """Density of humid air, in kg/m^3.

    Args:
        temp_c: Air temperature in degrees Celsius.
        pressure_pa: ABSOLUTE station pressure in Pa -- what a barometer at
            the course actually reads. Not sea-level-adjusted pressure, and
            not an altimeter setting. Station pressure already encodes
            altitude, which is why this function takes no elevation.
        humidity_pct: Relative humidity, 0-100. Values slightly outside that
            range are clamped rather than rejected: a working sensor
            legitimately reports 100.3% in fog. Defaults to 0 (dry), which
            costs at most 2% versus saturated air.

    Returns:
        Air density in kg/m^3. ISA sea level is 1.225.

    Raises:
        ValueError: If temperature or pressure is outside the sanity bounds.
    """
    _check_temp(temp_c)
    if not MIN_PRESSURE_PA <= pressure_pa <= MAX_PRESSURE_PA:
        raise ValueError(
            f"pressure_pa={pressure_pa} is outside {MIN_PRESSURE_PA}-{MAX_PRESSURE_PA} Pa; "
            "check the sensor is reading absolute pressure in pascals, not hPa"
        )

    humidity = min(100.0, max(0.0, humidity_pct))
    vapour_pa = humidity / 100.0 * saturation_vapour_pressure_pa(temp_c)
    dry_pa = pressure_pa - vapour_pa
    temp_k = temp_c + 273.15

    return dry_pa / (R_DRY_AIR * temp_k) + vapour_pa / (R_WATER_VAPOUR * temp_k)


def pressure_from_elevation_pa(elevation_m: float) -> float:
    """Estimate station pressure from elevation using the ISA atmosphere.

    For users with no barometer. A real pressure reading is always better --
    this cannot see weather, so it misses the 3% swing between a storm low
    and a fair-weather high, worth about 2 yards on a driver. But elevation
    plus a real temperature still captures most of the error, and it beats
    assuming sea level by a wide margin.

    Args:
        elevation_m: Height above mean sea level in metres.

    Returns:
        Estimated absolute pressure in Pa.

    Raises:
        ValueError: If ``elevation_m`` is outside a plausible range.
    """
    if not MIN_ELEVATION_M <= elevation_m <= MAX_ELEVATION_M:
        raise ValueError(
            f"elevation_m={elevation_m} is outside {MIN_ELEVATION_M}-{MAX_ELEVATION_M} m"
        )
    ratio = 1.0 - (ISA_LAPSE_RATE_K_PER_M * elevation_m) / ISA_SEA_LEVEL_TEMP_K
    return ISA_SEA_LEVEL_PRESSURE_PA * ratio**ISA_EXPONENT


def _check_temp(temp_c: float) -> None:
    """Reject temperatures that indicate a sensor fault."""
    if not MIN_TEMP_C <= temp_c <= MAX_TEMP_C:
        raise ValueError(
            f"temp_c={temp_c} is outside {MIN_TEMP_C}-{MAX_TEMP_C} C; "
            "this indicates a sensor fault, not weather"
        )

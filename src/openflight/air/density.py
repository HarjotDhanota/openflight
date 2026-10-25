"""Humid-air density from temperature, pressure and relative humidity."""

from __future__ import annotations

import math

DRY_AIR_GAS_CONSTANT = 287.05  # J/(kg K)
WATER_VAPOUR_GAS_CONSTANT = 461.495  # J/(kg K)


def saturation_vapour_pressure_pa(temperature_c: float) -> float:
    """Saturation vapour pressure over water, Pa: Buck (1981).

    A. L. Buck, "New equations for computing vapor pressure and enhancement
    factor", J. Appl. Meteorol. 20, 1527-1532 (1981):
    e_s = 6.1121 exp(17.502 T / (240.97 + T)) hPa, T in deg C. Buck's enhancement
    factor for moist air (about 1.004 at sea level) is left out: it moves the
    density by under 0.003 %.
    """
    return 611.21 * math.exp(17.502 * temperature_c / (240.97 + temperature_c))


def humid_air_density_kg_m3(temperature_c: float, pressure_pa: float, rh_pct: float) -> float:
    """Density of moist air: dry air and water vapour as ideal gases (Dalton's law).

    rho = (p - e) / (R_d T) + e / (R_v T), with e = RH x e_s(T) (Buck 1981).
    """
    temperature_k = temperature_c + 273.15
    vapour = rh_pct / 100.0 * saturation_vapour_pressure_pa(temperature_c)
    return (pressure_pa - vapour) / (DRY_AIR_GAS_CONSTANT * temperature_k) + vapour / (
        WATER_VAPOUR_GAS_CONSTANT * temperature_k
    )

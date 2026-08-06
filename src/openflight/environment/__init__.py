"""Environmental sensing and air-density correction.

Carry distance scales with air density, and OpenFlight has always assumed
ISA sea-level conditions (1.225 kg/m^3). On a hot day at altitude that is
wrong by more than ten yards on a driver. This package supplies the measured
or estimated density that ``ballistics.simulate`` has always accepted but
never been given.
"""

from openflight.environment.density import (
    R_DRY_AIR,
    R_WATER_VAPOUR,
    air_density,
    pressure_from_elevation_pa,
    saturation_vapour_pressure_pa,
)

__all__ = [
    "R_DRY_AIR",
    "R_WATER_VAPOUR",
    "air_density",
    "pressure_from_elevation_pa",
    "saturation_vapour_pressure_pa",
]

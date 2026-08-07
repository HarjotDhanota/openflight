"""Turning readings into health levels and decisions.

Level functions are pure. The reducer added in a later task wraps them with the
retained state that dwell, hysteresis and shutdown latching require.
"""

from __future__ import annotations

from .config import PowerConfig
from .models import PackLevel, PackReading, RailLevel, RailReading, SourceState
from .pmic import UNDERVOLTAGE_MASK


def rail_level(reading: RailReading, config: PowerConfig) -> RailLevel:
    """Classify 5V-rail health.

    Undervoltage flags outrank voltage: the firmware detecting a droop is a
    stronger signal than a spot reading taken between droops.
    """
    if reading.status != "ok":
        return "unknown"

    flags = reading.throttled or 0
    if flags & 0x1:
        return "red"

    volts = reading.ext5v_volts
    if volts is not None:
        if volts < config.rail_red_volts:
            return "red"
        if volts < config.rail_amber_volts:
            return "amber"

    if flags & UNDERVOLTAGE_MASK:
        # Sticky bit only: healthy now, but it has happened this boot.
        return "amber"
    return "green" if volts is not None else "unknown"


def pack_level(reading: PackReading, source_state: SourceState, config: PowerConfig) -> PackLevel:
    """Classify pack health from voltage.

    Bands are non-overlapping and exhaustive below ``pack_low_volts`` -- an
    earlier draft left 3.2-3.3 V with no level, so a nearly-dead pack fell
    through the table.

    On external power the reading is inflated by charge current, so voltage
    bands do not apply and mains is not a low-battery condition. On an unknown
    source it is evaluated as if on battery: a spurious warning costs a glance,
    a missed one costs the session.
    """
    if reading.status != "ok" or reading.volts is None:
        return "unknown"
    if source_state == "external":
        return "ok"
    if reading.volts >= config.pack_low_volts:
        return "ok"
    if reading.volts >= config.pack_critical_volts:
        return "low"
    return "critical"


def shutdown_eligible(reading: PackReading, config: PowerConfig) -> bool:
    """Whether voltage alone would permit a shutdown.

    Separate from the level so a pack below the shutdown threshold stays
    visibly critical whether or not automatic shutdown is enabled. This is one
    of five conditions; see the reducer.
    """
    if reading.status != "ok" or reading.volts is None:
        return False
    return reading.volts <= config.shutdown_volts

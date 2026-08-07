import pytest

from openflight.power.config import PowerConfig
from openflight.power.models import PackReading, RailReading
from openflight.power.policy import pack_level, rail_level, shutdown_eligible

CONFIG = PowerConfig()


def rail(volts, throttled=0):
    return RailReading(status="ok", timestamp=1.0, ext5v_volts=volts, throttled=throttled)


def pack(volts):
    return PackReading(status="ok", timestamp=1.0, volts=volts, percent=50.0)


@pytest.mark.parametrize(
    "volts,expected",
    [
        (5.211, "green"),  # measured idle baseline
        (5.000, "green"),  # exactly at amber threshold is still green
        (4.999, "amber"),
        (4.900, "amber"),  # exactly at red threshold is still amber
        (4.899, "red"),
    ],
)
def test_rail_level_boundaries(volts, expected):
    assert rail_level(rail(volts), CONFIG) == expected


def test_sticky_undervoltage_forces_amber_even_at_good_voltage():
    assert rail_level(rail(5.21, throttled=0x10000), CONFIG) == "amber"


def test_live_undervoltage_forces_red():
    assert rail_level(rail(5.21, throttled=0x1), CONFIG) == "red"


def test_thermal_throttling_does_not_affect_rail_health():
    # Bit layout (hex): 0=0x00001 UV now; 1=0x00002 frequency capped;
    # 2=0x00004 throttled; 3=0x00008 soft-temp limit; 16=0x10000 UV occurred;
    # 17=0x20000 capping occurred; 18=0x40000 throttling occurred;
    # 19=0x80000 soft-temp limit occurred.
    # 0x60006 = bits 1, 2, 17, 18: frequency capped and throttled, plus their
    # sticky twins. No undervoltage.
    assert rail_level(rail(5.21, throttled=0x60006), CONFIG) == "green"


def test_rail_absent_is_unknown():
    assert rail_level(RailReading(status="absent", timestamp=1.0), CONFIG) == "unknown"


@pytest.mark.parametrize(
    "volts,expected",
    [
        (4.10, "ok"),
        (3.60, "ok"),  # exactly at low threshold is still ok
        (3.59, "low"),
        (3.40, "low"),  # exactly at critical threshold is still low
        (3.39, "critical"),
        (3.10, "critical"),  # below shutdown volts is still just "critical"
    ],
)
def test_pack_level_boundaries_are_non_overlapping(volts, expected):
    assert pack_level(pack(volts), "battery", CONFIG) == expected


def test_pack_on_external_power_is_ok_regardless_of_voltage():
    # Charge current inflates terminal voltage, so voltage levels do not apply.
    assert pack_level(pack(3.10), "external", CONFIG) == "ok"


def test_pack_on_unknown_source_is_evaluated_as_battery():
    # A spurious warning costs a glance; a missed one costs the session.
    assert pack_level(pack(3.39), "unknown", CONFIG) == "critical"


def test_pack_absent_is_unknown():
    assert pack_level(PackReading(status="absent", timestamp=1.0), "battery", CONFIG) == "unknown"


def test_shutdown_eligible_is_separate_from_level():
    # A pack below 3.2 is eligible AND still visibly critical.
    assert shutdown_eligible(pack(3.19), CONFIG) is True
    assert pack_level(pack(3.19), "battery", CONFIG) == "critical"
    assert shutdown_eligible(pack(3.21), CONFIG) is False

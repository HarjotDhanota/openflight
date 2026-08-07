import subprocess

import pytest

from openflight.power.pmic import (
    UNDERVOLTAGE_MASK,
    PmicRail,
    parse_ext5v,
    parse_throttled,
)

ADC_OUTPUT = """\
 3V7_WL_SW_A current(0)=0.09856894A
   VDD_CORE_V volt(15)=0.75052430V
      EXT5V_V volt(24)=5.21528000V
"""


def test_parse_ext5v_from_real_output():
    assert parse_ext5v(ADC_OUTPUT) == pytest.approx(5.21528)


def test_parse_ext5v_missing_field_returns_none():
    assert parse_ext5v("3V3_SYS_V volt(9)=3.31838500V\n") is None


def test_parse_throttled_hex():
    assert parse_throttled("throttled=0x50005") == 0x50005


def test_parse_throttled_garbage_returns_none():
    assert parse_throttled("not a thing") is None


def test_undervoltage_mask_excludes_thermal_bits():
    # Bit layout (hex): 0=0x00001 UV now; 1=0x00002 frequency capped;
    # 2=0x00004 throttled; 3=0x00008 soft-temp limit; 16=0x10000 UV occurred;
    # 17=0x20000 capping occurred; 18=0x40000 throttling occurred;
    # 19=0x80000 soft-temp limit occurred.
    assert UNDERVOLTAGE_MASK == 0x10001
    # 0x60006 = bits 1, 2, 17, 18. Everything thermal and frequency, nothing
    # supply-related.
    assert 0x60006 & UNDERVOLTAGE_MASK == 0
    assert 0x10000 & UNDERVOLTAGE_MASK != 0


def test_read_returns_ok_with_parsed_values():
    def runner(args, timeout):
        return ADC_OUTPUT if "pmic_read_adc" in args else "throttled=0x0"

    reading = PmicRail(runner=runner).read(timestamp=1.0)
    assert reading.status == "ok"
    assert reading.ext5v_volts == pytest.approx(5.21528)
    assert reading.throttled == 0


def test_timeout_becomes_error_status_not_a_hang():
    def runner(args, timeout):
        raise subprocess.TimeoutExpired(cmd=args, timeout=timeout)

    reading = PmicRail(runner=runner).read(timestamp=1.0)
    assert reading.status == "error"
    assert reading.ext5v_volts is None


def test_missing_vcgencmd_is_absent_not_error():
    def runner(args, timeout):
        raise FileNotFoundError("vcgencmd")

    # A Pi 4 has no PMIC ADC. That is a build without the capability, not a fault.
    assert PmicRail(runner=runner).read(timestamp=1.0).status == "absent"

"""5V-rail health from the Raspberry Pi 5 PMIC.

Two facts shape this module.

First, ``get_throttled`` reports far more than supply problems: bits 1-3 and
17-19 are ARM frequency capping and thermal limiting. This launch monitor lives
in a sealed IP54 enclosure on a summer range, where thermal throttling is
expected behaviour -- treating it as a supply fault would show a red rail on a
perfectly healthy machine. Only bits 0 and 16 are undervoltage.

Second, ``vcgencmd`` forks a process, so it gets a hard timeout. A hang must
degrade this one reader, never stall the sampling loop.
"""

from __future__ import annotations

import subprocess
from typing import Callable

from .models import RailReading

# Bit 0: undervoltage now. Bit 16: undervoltage has occurred since boot.
UNDERVOLTAGE_MASK = 0x10001

_EXT5V_FIELD = "EXT5V_V"


def _default_runner(args: list[str], timeout: float) -> str:
    return subprocess.run(  # pylint: disable=subprocess-run-check
        args, capture_output=True, text=True, timeout=timeout
    ).stdout


def parse_ext5v(text: str) -> float | None:
    """Extract EXT5V_V volts from ``vcgencmd pmic_read_adc`` output.

    The field looks like ``EXT5V_V volt(24)=5.21528000V`` -- splitting on '='
    rather than whitespace, because the value is glued to its label.
    """
    for line in text.splitlines():
        if _EXT5V_FIELD not in line or "=" not in line:
            continue
        try:
            return float(line.rsplit("=", 1)[1].strip().rstrip("V"))
        except ValueError:
            return None
    return None


def parse_throttled(text: str) -> int | None:
    """Extract the mask from ``vcgencmd get_throttled`` output."""
    if "=" not in text:
        return None
    try:
        return int(text.split("=", 1)[1].strip(), 16)
    except ValueError:
        return None


class PmicRail:
    """Read 5V-rail voltage and undervoltage flags. Pi 5 only."""

    def __init__(
        self,
        *,
        runner: Callable[[list[str], float], str] | None = None,
        timeout_s: float = 2.0,
    ):
        self._runner = runner or _default_runner
        self._timeout_s = timeout_s

    def read(self, *, timestamp: float) -> RailReading:
        """Read the rail. Never raises."""
        try:
            adc = self._runner(["vcgencmd", "pmic_read_adc"], self._timeout_s)
            throttled_text = self._runner(["vcgencmd", "get_throttled"], self._timeout_s)
        except FileNotFoundError:
            # No vcgencmd at all: not a Pi, or a Pi without it. The capability
            # is missing rather than broken.
            return RailReading(status="absent", timestamp=timestamp)
        except Exception as error:  # pylint: disable=broad-exception-caught
            return RailReading(status="error", timestamp=timestamp, error=str(error))

        volts = parse_ext5v(adc)
        throttled = parse_throttled(throttled_text)
        if volts is None and throttled is None:
            # vcgencmd ran but said nothing we understand -- e.g. a Pi 4, whose
            # pmic_read_adc has no EXT5V_V field.
            return RailReading(status="absent", timestamp=timestamp)
        return RailReading(status="ok", timestamp=timestamp, ext5v_volts=volts, throttled=throttled)

    def close(self) -> None:
        """No resources to release."""

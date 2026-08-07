"""Whether the machine is running on external power.

Separate from the fuel gauge because the two capabilities go missing
independently: plenty of boards have a gauge and no power-loss line.

The central subtlety is that a pulled-up input with nothing wired to it reads
HIGH. On a board with no PLD line, "high" is indistinguishable from "mains
connected" -- so a naive mapping would report battery operation as external,
suppress low-battery warnings, and disable automatic shutdown on precisely the
builds least able to notice. High is therefore only believed when the line has
been declared by a board profile we ship, or has proven itself by reading low
at least once. Low is always trustworthy: a pulled-up floating pin cannot
produce it.
"""

from __future__ import annotations

import logging
from typing import Protocol

from .models import SourceReading

logger = logging.getLogger(__name__)


class PinReader(Protocol):  # pylint: disable=unnecessary-ellipsis
    """Minimal input-pin surface, so tests need no GPIO hardware."""

    def read(self) -> bool:
        """True when the pin is high."""
        ...  # pylint: disable=unnecessary-ellipsis

    def close(self) -> None:
        """Release the line."""
        ...  # pylint: disable=unnecessary-ellipsis


class PowerSourceReader(Protocol):  # pylint: disable=unnecessary-ellipsis
    """Contract the power service requires."""

    def read(self, *, timestamp: float) -> SourceReading:
        """Read the source state. Never raises."""
        ...  # pylint: disable=unnecessary-ellipsis

    def close(self) -> None:
        """Release resources. Idempotent."""
        ...  # pylint: disable=unnecessary-ellipsis


class NullSource:
    """Used when no PLD line is configured. Always absent, always unknown."""

    def read(self, *, timestamp: float) -> SourceReading:
        """Report that nothing is known about the power source."""
        return SourceReading(status="absent", timestamp=timestamp, state="unknown")

    def close(self) -> None:
        """No resources to release."""


class GpioPldSource:
    """Read a power-loss-detect line. Active low: low means running on battery."""

    def __init__(self, *, pin: int, trusted: bool, pin_reader: PinReader):
        self.pin = pin
        self._trusted = trusted
        self._pin_reader = pin_reader
        self._closed = False

    def read(self, *, timestamp: float) -> SourceReading:
        """Read the line, applying the trust rule above."""
        try:
            high = self._pin_reader.read()
        except Exception as error:  # pylint: disable=broad-exception-caught
            return SourceReading(
                status="error", timestamp=timestamp, state="unknown", error=str(error)
            )

        if not high:
            if not self._trusted:
                logger.info(
                    "[POWER] GPIO %d read low; treating the PLD line as wired from now on",
                    self.pin,
                )
            # Latch: a line that has gone low is driven, so its highs mean
            # something from here on.
            self._trusted = True
            return SourceReading(status="ok", timestamp=timestamp, state="battery")

        state = "external" if self._trusted else "unknown"
        return SourceReading(status="ok", timestamp=timestamp, state=state)

    def close(self) -> None:
        """Release the line. Idempotent."""
        if self._closed:
            return
        self._closed = True
        self._pin_reader.close()

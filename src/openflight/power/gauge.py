"""Contract a fuel gauge must satisfy.

Deliberately narrow, and deliberately free of any power-source concept: which
boards can tell you whether mains is connected is unrelated to which boards
have a fuel gauge, and folding the two together would couple capabilities that
go missing independently. See source.py.
"""

from __future__ import annotations

from typing import Protocol

from .models import PackReading


class BatteryGauge(Protocol):  # pylint: disable=unnecessary-ellipsis
    """Sensor contract required by the power service."""

    def initialize(self) -> None:
        """Configure and verify the gauge."""
        ...  # pylint: disable=unnecessary-ellipsis

    def read(self, *, timestamp: float) -> PackReading:
        """Read pack voltage and state of charge. Never raises."""
        ...  # pylint: disable=unnecessary-ellipsis

    def close(self) -> None:
        """Release the bus. Idempotent."""
        ...  # pylint: disable=unnecessary-ellipsis

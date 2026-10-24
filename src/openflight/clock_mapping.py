"""One GATE edge as the host observed it, on bracketed monotonic and wall clocks.

The BCM17 GATE line is the same physical edge as OPS243 HOST_INT. An observation is
monotonic, the GPIO backend's event age, monotonic, plus a bracketed wall/monotonic
pair; only a backend that forwards the kernel event tick can bound the edge.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from openflight.numeric import finite as _finite

QUALIFIED_BACKENDS = frozenset({"LGPIOFactory"})


@dataclass(frozen=True)
class BracketedPair:
    """A wall reading between two monotonic readings: bounds wall − monotonic honestly."""

    mono_before_ns: int
    realtime_ns: int
    mono_after_ns: int

    @classmethod
    def now(cls) -> "BracketedPair":
        """monotonic, wall, monotonic."""
        before = time.monotonic_ns()
        real = time.time_ns()
        return cls(before, real, time.monotonic_ns())

    def wall_minus_mono_bounds_ns(self) -> tuple[int, int]:
        """(low, high) of wall − monotonic at this instant."""
        return self.realtime_ns - self.mono_after_ns, self.realtime_ns - self.mono_before_ns


@dataclass(frozen=True)
class EdgeObservation:  # pylint: disable=too-many-instance-attributes
    """One GATE edge as one observer read it: bracketed age plus a bracketed clock pair."""

    edge_seq: Optional[int]
    mono_before_ns: int
    mono_after_ns: int
    edge_age_ns: Optional[int]
    wall_minus_mono_low_ns: Optional[int]
    wall_minus_mono_high_ns: Optional[int]
    backend: Optional[str]
    previous_callback_mono_ns: Optional[int] = None
    observer_wall_s: Optional[float] = None  # diagnostic only; never used for the mapping

    def readings_valid(self) -> bool:
        """Both monotonic readings are finite and ordered."""
        return (
            _finite(self.mono_before_ns)
            and _finite(self.mono_after_ns)
            and self.mono_before_ns <= self.mono_after_ns
        )

    def kernel_mono_interval_ns(self) -> Optional[tuple[int, int]]:
        """[before − age, after − age] when the readings and the age are valid and age >= 0."""
        if not self.readings_valid() or not _finite(self.edge_age_ns) or self.edge_age_ns < 0:
            return None
        return self.mono_before_ns - self.edge_age_ns, self.mono_after_ns - self.edge_age_ns

    def association_mono_ns(self) -> int:
        """Time used only to associate an edge with a dump (coarse)."""
        interval = self.kernel_mono_interval_ns()
        return (interval[0] + interval[1]) // 2 if interval else self.mono_after_ns

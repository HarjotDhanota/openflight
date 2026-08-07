"""Value types for power monitoring.

Every reader carries its own status. There is deliberately no global status
field: pack gauge, power source, and rail monitor are independently absent on
real builds, and one field cannot express "gauge working, PLD missing, rail
fine" -- see the support matrix in the design doc, section 3.1.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

ReaderStatus = Literal["ok", "absent", "error"]
PackLevel = Literal["ok", "low", "critical", "unknown"]
RailLevel = Literal["green", "amber", "red", "unknown"]
SourceState = Literal["external", "battery", "unknown"]


@dataclass(frozen=True)
class PackReading:
    """One fuel-gauge sample."""

    status: ReaderStatus
    timestamp: float
    volts: float | None = None
    percent: float | None = None
    error: str | None = None


@dataclass(frozen=True)
class RailReading:
    """One 5V-rail sample. ``throttled`` is the raw get_throttled mask."""

    status: ReaderStatus
    timestamp: float
    ext5v_volts: float | None = None
    throttled: int | None = None
    error: str | None = None


@dataclass(frozen=True)
class SourceReading:
    """Whether the machine is on external power."""

    status: ReaderStatus
    timestamp: float
    state: SourceState = "unknown"
    error: str | None = None


@dataclass(frozen=True)
class PowerSnapshot:
    """One pass over all three readers."""

    timestamp: float
    pack: PackReading
    rail: RailReading
    source: SourceReading


@dataclass(frozen=True)
class PendingShutdown:
    """An armed shutdown awaiting its deadline or a cancel.

    The deadline is absolute monotonic rather than a countdown so a client
    reconnecting mid-window computes the remaining time correctly instead of
    restarting the clock.
    """

    id: str
    deadline_monotonic: float
    reason: str


@dataclass(frozen=True)
class PowerView:
    """What the UI receives. Conclusions included so the client never re-derives."""

    pack_volts: float | None
    pack_percent: float | None
    pack_level: PackLevel
    rail_volts: float | None
    rail_level: RailLevel
    source: SourceState
    runtime_minutes: int | None
    shutdown_eligible: bool
    pending_shutdown: PendingShutdown | None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialize for socket emit."""
        return asdict(self)

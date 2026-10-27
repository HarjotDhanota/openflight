"""Value objects shared by inclinometer drivers and consumers."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass


def gravity_roll_deg(x_g: float, y_g: float, z_g: float) -> float:
    """The enclosure's roll from gravity in its axes: + when its +X (target-right) side is up."""
    return math.degrees(math.atan2(float(x_g), math.hypot(float(y_g), float(z_g))))


@dataclass(frozen=True)
class AccelerationSample:
    """One timestamped LIS3DH acceleration reading in units of gravity."""

    timestamp: float
    x_g: float
    y_g: float
    z_g: float


@dataclass(frozen=True)
class OrientationSnapshot:
    """Filtered enclosure orientation produced from a stationary sample window."""

    timestamp: float
    x_g: float
    y_g: float
    z_g: float
    gravity_g: float
    raw_pitch_deg: float
    calibrated_pitch_deg: float
    pitch_std_deg: float
    sample_count: int
    # the window's first sample; None (a snapshot built by hand) is its last
    window_start_timestamp: float | None = None
    roll_std_deg: float = 0.0

    @property
    def roll_deg(self) -> float:
        """The window's roll, from its median gravity."""
        return gravity_roll_deg(self.x_g, self.y_g, self.z_g)

    @property
    def window_start(self) -> float:
        if self.window_start_timestamp is None:
            return self.timestamp
        return self.window_start_timestamp

    def to_dict(self) -> dict:
        """Return JSON-safe rounded diagnostic fields."""
        data = asdict(self)
        for key in ("x_g", "y_g", "z_g", "gravity_g"):
            data[key] = round(data[key], 4)
        for key in ("raw_pitch_deg", "calibrated_pitch_deg", "pitch_std_deg", "roll_std_deg"):
            data[key] = round(data[key], 3)
        data["roll_deg"] = round(self.roll_deg, 3)
        data["stable"] = True
        return data


@dataclass(frozen=True)
class SnapshotSelection:
    """Result of selecting a stable orientation for one impact."""

    snapshot: OrientationSnapshot | None
    status: str
    age_s: float | None = None
    # the rule that chose the reading: recent_stable or confirmed_after_impact
    rule: str | None = None
    reason: str | None = None
    # the first stable reading after impact, against the chosen one
    confirmation: dict | None = None

    def to_dict(self) -> dict:
        """Return selection diagnostics suitable for shot/session logging."""
        data = {
            "status": self.status,
            "age_s": round(self.age_s, 3) if self.age_s is not None else None,
            "rule": self.rule,
            "reason": self.reason,
        }
        if self.confirmation is not None:
            data["confirmation"] = dict(self.confirmation)
        if self.snapshot is not None:
            data.update(self.snapshot.to_dict())
        return data

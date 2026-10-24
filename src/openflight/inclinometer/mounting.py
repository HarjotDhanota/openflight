"""Read a LIS3DH in the enclosure's axes when its board is turned in the housing."""

from __future__ import annotations

import math

from .models import AccelerationSample
from .service import Accelerometer


class MountedAccelerometer:
    """A sensor whose board is turned about the vertical, read in the enclosure's axes.

    Pitch is read from the board's +Y axis (the enclosure's front). ``yaw_deg`` is
    counter-clockwise seen from above; ``upside_down`` turns the board over about
    its own Y axis first (X and Z reversed).
    """

    def __init__(self, sensor: Accelerometer, yaw_deg: float, *, upside_down: bool = False):
        self.sensor = sensor
        self.yaw_deg = float(yaw_deg)
        self.upside_down = bool(upside_down)
        self._cos = math.cos(math.radians(self.yaw_deg))
        self._sin = math.sin(math.radians(self.yaw_deg))

    def initialize(self) -> None:
        self.sensor.initialize()

    def read(self, *, timestamp: float | None = None) -> AccelerationSample:
        sample = self.sensor.read(timestamp=timestamp)
        x_g, z_g = (-sample.x_g, -sample.z_g) if self.upside_down else (sample.x_g, sample.z_g)
        return AccelerationSample(
            timestamp=sample.timestamp,
            x_g=self._cos * x_g - self._sin * sample.y_g,
            y_g=self._sin * x_g + self._cos * sample.y_g,
            z_g=z_g,
        )

    def close(self) -> None:
        self.sensor.close()

"""Read a LIS3DH in the enclosure's axes when its board is turned in the housing."""

from __future__ import annotations

import math

from .models import AccelerationSample
from .service import Accelerometer


class MountedAccelerometer:
    """A sensor whose board is turned about the vertical, read in the enclosure's axes.

    Pitch is read from the board's +Y axis, which the design points at the
    enclosure's front. A board turned by ``yaw_deg`` (counter-clockwise seen from
    above) reads X and Y rotated by that much; at 180 degrees both are reversed,
    and a front tipped up would otherwise read as tipped down.
    """

    def __init__(self, sensor: Accelerometer, yaw_deg: float):
        self.sensor = sensor
        self.yaw_deg = float(yaw_deg)
        self._cos = math.cos(math.radians(self.yaw_deg))
        self._sin = math.sin(math.radians(self.yaw_deg))

    def initialize(self) -> None:
        self.sensor.initialize()

    def read(self, *, timestamp: float | None = None) -> AccelerationSample:
        sample = self.sensor.read(timestamp=timestamp)
        return AccelerationSample(
            timestamp=sample.timestamp,
            x_g=self._cos * sample.x_g - self._sin * sample.y_g,
            y_g=self._sin * sample.x_g + self._cos * sample.y_g,
            z_g=sample.z_g,
        )

    def close(self) -> None:
        self.sensor.close()

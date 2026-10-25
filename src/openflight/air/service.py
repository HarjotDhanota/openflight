"""Background BME280 polling; every shot reads the resolved conditions without I/O."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Protocol

from .bme280 import AirSample
from .conditions import MAX_READING_AGE_S, AirConditions, from_sample, isa_default

logger = logging.getLogger(__name__)

# Forced mode every few seconds keeps the die at the air's temperature; air density
# moves far slower than this.
DEFAULT_POLL_INTERVAL_S = 5.0


class AirSensor(Protocol):  # pylint: disable=unnecessary-ellipsis
    """Sensor contract required by the service."""

    def initialize(self) -> None:
        """Configure and verify the sensor."""
        ...  # pylint: disable=unnecessary-ellipsis

    def read(self, *, timestamp: float | None = None) -> AirSample:
        """Read one sample; raise on failure."""
        ...  # pylint: disable=unnecessary-ellipsis

    def close(self) -> None:
        """Release sensor resources."""
        ...  # pylint: disable=unnecessary-ellipsis


class AirSensorService:
    """Poll one air sensor and resolve the latest sample to the air a shot uses."""

    def __init__(  # pylint: disable=too-many-arguments
        self,
        sensor: AirSensor,
        *,
        poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
        max_age_s: float = MAX_READING_AGE_S,
        clock: Callable[[], float] = time.time,
        on_update: Callable[[AirConditions], None] | None = None,
    ):
        if poll_interval_s <= 0:
            raise ValueError("poll_interval_s must be positive")
        self.sensor = sensor
        self.poll_interval_s = poll_interval_s
        self.max_age_s = max_age_s
        self.clock = clock
        self.on_update = on_update
        self._sample: AirSample | None = None
        self._last_error: str | None = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Initialize the sensor and start the polling thread."""
        self.sensor.initialize()
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="openflight-air-sensor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop polling and release the I2C bus."""
        self._stop_event.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)
        self.sensor.close()

    @property
    def running(self) -> bool:
        """Whether the polling thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    @property
    def last_error(self) -> str | None:
        """The most recent read failure, cleared by the next good read."""
        with self._lock:
            return self._last_error

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self.poll_once()
            self._stop_event.wait(self.poll_interval_s)

    def poll_once(self) -> AirConditions:
        """Read once, resolve, and hand the result to ``on_update``."""
        try:
            sample = self.sensor.read(timestamp=self.clock())
        except Exception as error:  # pylint: disable=broad-exception-caught
            with self._lock:
                self._last_error = str(error)
            logger.warning("[AIR] BME280 read failed: %s", error)
        else:
            with self._lock:
                self._sample = sample
                self._last_error = None
        conditions = self.conditions()
        if self.on_update is not None:
            try:
                self.on_update(conditions)
            except Exception:  # pylint: disable=broad-exception-caught
                logger.warning("[AIR] Conditions listener failed", exc_info=True)
        return conditions

    def conditions(self, now: float | None = None) -> AirConditions:
        """The air a shot taken now flies in. Never raises, never touches the bus."""
        with self._lock:
            sample, error = self._sample, self._last_error
        failed = f"last read failed: {error}" if error else None
        if sample is None:
            return isa_default(
                f"no BME280 reading yet; {failed}" if failed else "no BME280 reading yet"
            )
        conditions = from_sample(
            sample, now=self.clock() if now is None else now, max_age_s=self.max_age_s
        )
        if not conditions.measured and failed:
            return isa_default(f"{conditions.reason}; {failed}")
        return conditions

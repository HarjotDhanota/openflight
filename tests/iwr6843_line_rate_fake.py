"""A real-time IWR6843 serial line for the dump-starvation tests.

Bytes cross the fake UART at a fixed rate in wall time and land in a bounded host
buffer, as the CP2105 and Linux n_tty do with no flow control: whatever arrives
while the buffer is full is dropped, silently. Arrival is computed from the clock
on each access, so it needs no producer thread and keeps flowing however long the
reader is kept from running; a reader that is starved for longer than the buffer
holds loses bytes, deterministically.

``radar_factory`` builds an ``IWR6843Radar`` on this line for the serial process
(``openflight.iwr6843.serial_process``), with JSON-only arguments.
"""

from __future__ import annotations

import os
import time

import numpy as np

from openflight.iwr6843.device_lock import IWR6843DeviceBusyError
from openflight.iwr6843.driver import BAUD, IWR6843Radar
from openflight.iwr6843.dump import pack_dump
from tests.iwr6843_firmware_fake import FirmwareModel, NoLock

FACTORY = "tests.iwr6843_line_rate_fake:radar_factory"
RATE_BYTES_PER_S = 200_000
CAPACITY_BYTES = 50_000  # 0.25 s of line time
READ_TIMEOUT_S = 0.05


def make_dump(frames: int = 25) -> bytes:
    """About 8 KB per frame: the default is about one second of line time."""
    return pack_dump(np.ones((frames, 8, 4, 64), dtype=complex), n_tx=2, version=3)


class WallClock:
    """FirmwareModel's clock, on the host's monotonic time."""

    @property
    def now(self) -> float:
        return time.monotonic()


class LineRatePort:
    """pyserial-shaped port: a rate-limited line into a bounded, drop-when-full buffer."""

    def __init__(self, firmware: FirmwareModel, rate_bytes_per_s: float, capacity_bytes: int):
        self.firmware = firmware
        self.rate = float(rate_bytes_per_s)
        self.capacity = int(capacity_bytes)
        self.received = bytearray()
        self.dropped = 0
        self.max_waiting = 0
        self.closed = False
        self.is_open = True
        self._credit = 0.0
        self._last = time.monotonic()
        self.sent = 0
        self.drop_after = 0  # the line loses ``drop_next`` bytes once this many have crossed
        self.drop_next = 0

    def _arrive(self) -> None:
        now = time.monotonic()
        self.firmware.advance()
        sending = self.firmware.pending
        if not sending:
            self._credit, self._last = 0.0, now  # an idle line banks no time
            return
        self._credit += (now - self._last) * self.rate
        self._last = now
        moved = min(int(self._credit), len(sending))
        if moved <= 0:
            return
        self._credit -= moved
        chunk = bytes(sending[:moved])
        del sending[:moved]
        self.sent += moved
        if self.drop_next and self.sent > self.drop_after:
            keep = max(0, len(chunk) - (self.sent - self.drop_after))  # bytes before the cut
            lost = min(self.drop_next, len(chunk) - keep)
            self.drop_next -= lost
            self.dropped += lost
            chunk = chunk[:keep] + chunk[keep + lost :]
        room = self.capacity - len(self.received)
        self.dropped += max(0, len(chunk) - room)
        self.received += chunk[: max(0, room)]
        self.max_waiting = max(self.max_waiting, len(self.received))

    @property
    def in_waiting(self) -> int:
        self._arrive()
        return len(self.received)

    def reset_input_buffer(self) -> None:
        self._arrive()
        self.received.clear()

    def write(self, data: bytes) -> int:
        self._arrive()
        self.firmware.receive(bytes(data))
        return len(data)

    def read(self, size: int) -> bytes:
        deadline = time.monotonic() + READ_TIMEOUT_S
        self._arrive()
        while not self.received and time.monotonic() < deadline:
            time.sleep(0.001)
            self._arrive()
        chunk = bytes(self.received[:size])
        del self.received[:size]
        return chunk

    def close(self) -> None:
        self.closed = True
        self.is_open = False


class LineRateRadar(IWR6843Radar):
    """The real driver on a LineRatePort, with no device lock or OS port."""

    # pylint: disable=super-init-not-called
    def __init__(self, port: str, ser: LineRatePort, *, events=None, mode: str = "normal"):
        self._events = events
        self.port = port
        self._device_lock = NoLock()
        self.ser = ser
        self.mode = mode

    def read_dump_timed(self, timeout_s: float = 40.0, stall_tolerance_s: float = 4.0):
        if self.mode == "crash_on_dump":
            os._exit(3)
        if self.mode == "hang_on_dump":
            time.sleep(3600)
        if self.mode == "lose_bytes":
            self.ser.drop_after, self.ser.drop_next = self.ser.sent + 10_000, 5_000
        return super().read_dump_timed(timeout_s, stall_tolerance_s)


def build_radar(
    port: str = "/dev/fake-iwr6843",
    *,
    events=None,
    frames: int = 25,
    rate_bytes_per_s: float = RATE_BYTES_PER_S,
    capacity_bytes: int = CAPACITY_BYTES,
    mode: str = "normal",
) -> LineRateRadar:
    """An active board streaming ``make_dump(frames)`` on ``l3dump``."""
    firmware = FirmwareModel(make_dump(frames), WallClock())
    firmware.active = True
    ser = LineRatePort(firmware, rate_bytes_per_s, capacity_bytes)
    return LineRateRadar(port, ser, events=events, mode=mode)


def radar_factory(port=None, baud=BAUD, *, events=None, **options):  # pylint: disable=unused-argument
    """Serial-process factory: ``options`` are ``build_radar``'s keywords plus ``open``."""
    opening = options.pop("open", "ok")
    if opening == "busy":
        raise IWR6843DeviceBusyError(port or "/dev/fake-iwr6843", "pid=1 port=/dev/fake-iwr6843")
    if opening == "no_cli":
        if events is not None:
            events.append(
                {"event": "help_probe", "port": "/dev/fake-iwr6843", "outcome": "no_reply"}
            )
        raise RuntimeError("no IWR6843 CLI found — board on, flashed, single-port fw?")
    if opening == "noisy":
        print("a stray print in the serial process")  # must never reach the replies
    events = events if events is not None else []
    events.append({"event": "opened", "port": port or "/dev/fake-iwr6843"})
    return build_radar(port or "/dev/fake-iwr6843", events=events, **options)

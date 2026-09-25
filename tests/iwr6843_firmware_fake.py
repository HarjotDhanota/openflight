"""Behavioural model of the single-port L3 firmware CLI for host-driver tests.

It reproduces the parts of ``firmware/iwr6843/l3_dump.c`` the host lifecycle
depends on: ``l3dump`` writes the binary payload first, then restarts the HWA
and RF front end, and only then returns so the CLI prints ``Done``. While that
handler runs the CLI task is not reading lines, and the dump-cancel poll
consumes any byte left in the SCI receive register, so host commands written
during the handler are lost rather than queued. Time is simulated so a
multi-second CP2105 stall costs no wall time.
"""

from __future__ import annotations

from types import SimpleNamespace

READ_TIMEOUT_S = 0.3


class FakeClock:
    """Shared simulated clock; empty serial reads advance it by the port timeout."""

    def __init__(self):
        self.now = 1_000.0

    def time(self) -> float:
        return self.now

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(0.0, seconds)

    def module(self) -> SimpleNamespace:
        """Stand-in for the ``time`` module inside the driver."""
        return SimpleNamespace(time=self.time, monotonic=self.monotonic, sleep=self.sleep)


class FirmwareModel:
    """One board; its state survives host serial close/reopen."""

    def __init__(self, raw: bytes, clock: FakeClock, *, restart_s: float = 0.0, trailer=b"Done"):
        self.raw = raw
        self.clock = clock
        self.restart_s = restart_s
        self.trailer = trailer
        self.active = False
        self.pending = bytearray()
        self.handler_returns_at: float | None = None
        self.lost_writes: list[bytes] = []
        self.commands: list[str] = []
        self.dumps = 0
        self.stream = bytearray()  # bytes still to cross the UART, e.g. an abandoned dump

    @property
    def handler_busy(self) -> bool:
        self.advance()
        return self.handler_returns_at is not None

    def advance(self) -> None:
        if self.handler_returns_at is not None and self.clock.now >= self.handler_returns_at:
            self.handler_returns_at = None
            if self.trailer is not None:
                self.pending += self.trailer + b"\r\nl3dump:/>"

    def receive(self, data: bytes) -> None:
        self.advance()
        if self.handler_returns_at is not None:
            self.lost_writes.append(bytes(data))
            return
        for raw_line in data.decode("ascii", errors="replace").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            self.commands.append(line)
            self.pending += line.encode("ascii") + b"\r\n"
            if line == "l3dump":
                if not self.active:
                    self.pending += b"Error -1\r\nl3dump:/>"
                    continue
                self.dumps += 1
                self.pending += self.raw
                self.handler_returns_at = self.clock.now + self.restart_s
                continue
            if line == "sensorStop":
                self.active = False
            elif line == "sensorStart":
                self.active = True
            elif line == "stats":
                self.pending += f"frames=9 active={int(self.active)} rf_faults=0\r\n".encode()
            elif line == "help":
                self.pending += b"sensorStart   Configure + start capture (no args)\r\n"
            self.pending += b"Done\r\nl3dump:/>"


class FakePort:
    """pyserial-shaped handle onto a FirmwareModel."""

    def __init__(self, firmware: FirmwareModel):
        self.firmware = firmware
        self.closed = False

    @property
    def in_waiting(self) -> int:
        self.firmware.advance()
        return len(self.firmware.pending)

    def reset_input_buffer(self) -> None:
        self.firmware.advance()
        self.firmware.pending.clear()

    def write(self, data: bytes) -> int:
        self.firmware.receive(bytes(data))
        return len(data)

    def read(self, size: int) -> bytes:
        self.firmware.advance()
        if not self.firmware.pending and self.firmware.stream:
            moved = self.firmware.stream[:size]
            del self.firmware.stream[:size]
            self.firmware.pending += moved
        if not self.firmware.pending:
            self.firmware.clock.sleep(READ_TIMEOUT_S)
            self.firmware.advance()
        chunk = bytes(self.firmware.pending[:size])
        del self.firmware.pending[:size]
        return chunk

    def close(self) -> None:
        self.closed = True


class SilentPort(FakePort):
    """An open port with nothing answering, such as a wedged or absent CLI."""

    def __init__(self, clock: FakeClock):
        super().__init__(FirmwareModel(b"", clock))
        self.writes: list[bytes] = []

    def write(self, data: bytes) -> int:
        self.writes.append(bytes(data))
        return len(data)


class NoLock:
    """Device lock double for tests that exercise one simulated board."""

    def __init__(self, *_args, **_kwargs):
        pass

    def acquire(self):
        return self

    def release(self):
        return None

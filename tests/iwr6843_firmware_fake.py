"""Behavioural model of the single-port L3 firmware CLI for host-driver tests.

``l3dump`` writes its payload, restarts the front end, then prints ``Done``; host
commands written meanwhile are lost, not queued (as in ``firmware/iwr6843/l3_dump.c``).
Time is simulated, so a multi-second CP2105 stall costs no wall time.
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

    def monotonic_ns(self) -> int:
        return int(round(self.now * 1e9))

    def sleep(self, seconds: float) -> None:
        self.now += max(0.0, seconds)

    def module(self) -> SimpleNamespace:
        """Stand-in for the ``time`` module inside the driver."""
        return SimpleNamespace(
            time=self.time,
            monotonic=self.monotonic,
            monotonic_ns=self.monotonic_ns,
            sleep=self.sleep,
        )


# A made-up perfstats reply in the firmware's format (l3_cli_perfStats), for parser tests only.
PERF_REPLY = (
    b"perf tick_hz=200000000 rearm_max=41000 rearms=900 task_max=90000 task_runs=900 "
    b"margin_min=52000 margin_frames=1200\r\n"
    b"perf scan_max=38000 scan_frames=900 scan_total_hi=1 scan_total_lo=5 "
    b"pack_max=12000 packs=900 pack_overlap=0 iq8_overflow=0 iq8_overflow_saturated=0\r\n"
    b"heap size=32768 free=9000 free_min=8800 largest=8000\r\n"
    b"stacks init=2900/8192 ctrl=700/3072 rearm=600/2048 cli=1500/4096 hwi=900/2048\r\n"
)
BENCH_REPLY = b"bench samples=183168 cycles=4200000 sum_hi=1 sum_lo=5\r\n"


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
            elif line == "perfstats":
                self.pending += PERF_REPLY
            elif line == "l3bench":
                if self.active:
                    self.pending += (
                        b"Error: stop the sensor before l3bench\r\nError -1\r\nl3dump:/>"
                    )
                    continue
                self.pending += BENCH_REPLY
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

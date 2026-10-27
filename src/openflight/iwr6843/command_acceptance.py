"""Opt-in character echo acceptance for the custom, single-UART firmware CLI."""

from __future__ import annotations

import time


class CommandAcceptanceError(OSError):
    """The command boundary is uncertain; this connection must not send again."""

    def __init__(self, message, raw=b"", written_bytes=0):
        super().__init__(message)
        self.raw = raw
        self.timing = None
        self.written_bytes = written_bytes


class CommandAcceptancePort:
    """Preserve the first echo for readers; never retry or flush after a failure."""

    def __init__(self, port, *, events=None, now=None):
        if port.timeout is None or not 0 < port.timeout <= 1.0:
            raise ValueError("command handshake requires a read timeout within one second")
        if port.write_timeout is None:
            port.write_timeout = 0.25
        if not 0 < port.write_timeout <= 1.0:
            raise ValueError("command handshake requires a write timeout within one second")
        self.port = port
        self.events = events
        self.now = now or time.monotonic_ns
        self.pending = bytearray()
        self.failed = False

    def __getattr__(self, name):
        return getattr(self.port, name)

    @property
    def in_waiting(self):
        """Include the already accepted echo exactly once."""
        return len(self.pending) + self.port.in_waiting

    def read(self, size):
        """Return buffered echo before reading more physical bytes."""
        if self.pending and size:
            data = bytes(self.pending[:size])
            del self.pending[:size]
            return data
        return self.port.read(size)

    def reset_input_buffer(self):
        """Allow normal pre-command draining only on an intact connection."""
        if self.failed or self.pending:
            raise CommandAcceptanceError("command acceptance uncertain; reset and reconnect")
        self.port.reset_input_buffer()

    def write(self, data):
        """Withhold the command remainder until its first character is echoed."""
        if self.failed:
            raise CommandAcceptanceError("command acceptance uncertain; reset and reconnect")
        if (
            not isinstance(data, bytes)
            or not 2 <= len(data) <= 255
            or not data.endswith(b"\n")
            or data.count(b"\n") != 1
            or any(c != 10 and not 32 <= c <= 126 for c in data)
            or self.pending
        ):
            raise ValueError("one ASCII command and drained prior echo required")
        row = {
            "event": "command_acceptance",
            "command": data[:-1].decode("ascii"),
            "started_ns": self.now(),
            "first_byte_echo_verified": False,
            "remainder_write_called": False,
            "written_bytes": 0,
        }
        echo = b""
        try:
            deadline = row["started_ns"] + 1_000_000_000
            count = self.port.write(data[:1])
            if type(count) is not int or count != 1:
                raise OSError("partial first-character write")
            row["written_bytes"] = count
            while self.now() < deadline and not echo:
                echo = self.port.read(1)
            row["echo_received_ns"] = self.now()
            row["echo_hex"] = echo.hex()
            if not echo or self.now() >= deadline:
                raise OSError("first-character echo timed out; remainder withheld")
            if echo != data[:1]:
                raise OSError("first-character echo mismatch; remainder withheld")
            self.pending.extend(echo)
            row["first_byte_echo_verified"] = True
            row["remainder_write_called"] = True
            count = self.port.write(data[1:])
            if type(count) is int and count > 0:
                row["written_bytes"] += count
            if type(count) is not int or count != len(data) - 1:
                raise OSError("partial command remainder write")
            return len(data)
        except (OSError, ValueError) as error:
            self.failed = True
            row["error"] = str(error)
            raise CommandAcceptanceError(str(error), echo, row["written_bytes"]) from error
        finally:
            row["completed_ns"] = self.now()
            if self.events is not None:
                self.events.append(row)

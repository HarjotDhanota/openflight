"""Synthetic serial-process board for the live two-round session tests."""

import time
from pathlib import Path

from openflight.iwr6843.command_acceptance import CommandAcceptancePort
from openflight.iwr6843.driver import DumpTransferTiming
from openflight.iwr6843.two_round_sim import SimulatedIWR6843TwoRound, SimulatedSerial
from tests.iwr6843_character_port import CharacterPort


class Radar:
    def __init__(
        self,
        port=None,
        baud=None,
        *,
        events,
        raw_path,
        fail_release=False,
        fail_overview=False,
        corrupt_overviews=0,
        corrupt_cells=0,
        echo=False,
        command_handshake=False,
        acceptance_failure_on=None,
    ):
        self.port = port or "fake-two-round"
        self.events = events
        self.raw = Path(raw_path).read_bytes()
        self.fail_release = fail_release
        self.model = SimulatedIWR6843TwoRound(echo=echo)
        self.ser = SimulatedSerial(self.model)
        original_write = self.ser.write

        def write(data):
            nonlocal corrupt_cells, corrupt_overviews
            if data == b"overview\n" and fail_overview:
                self.ser.inject(b"Error: capture failed\nl3dump:/>")
                return len(data)
            if data == b"overview\n" and not self.model.frozen:
                self.model.freeze(self.raw)
            if data == b"overview\n" and corrupt_overviews:
                self.model.corrupt_next = 1
                corrupt_overviews -= 1
            if data.startswith(b"cells ") and corrupt_cells:
                self.model.corrupt_next = 1
                corrupt_cells -= 1
            if data.startswith(b"release ") and self.fail_release:
                self.ser.inject(b"Error: rearm failed\nl3dump:/>")
                return len(data)
            return original_write(data)

        self.ser.write = write
        if command_handshake:
            self.model.echo = False
            binary = self.ser

            def reply(line):
                binary.write(line)
                return binary.read(2_000_000)

            raw = CharacterPort(
                reply,
                failure="wrong_echo" if acceptance_failure_on else None,
                fail_first=acceptance_failure_on.encode() if acceptance_failure_on else None,
            )
            self.ser = CommandAcceptancePort(raw, events=events)

    def two_round_support(self):
        return {"supported": True, "reason": None}

    def send_config(self, path, *, responses=None):
        self.events.append({"event": "config", "path": path})

    def stats(self):
        return "active=1"

    def read_dump_timed(self, timeout_s, stall_tolerance_s):
        return self.raw, DumpTransferTiming(request_written_ns=time.monotonic_ns())

    def stop_sensor(self):
        self.events.append({"event": "stop"})

    def close(self):
        self.events.append({"event": "closed", "frozen": self.model.frozen})

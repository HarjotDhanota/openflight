"""Synthetic serial-process board for the live two-round session tests."""

from pathlib import Path

from openflight.iwr6843.two_round_sim import SimulatedIWR6843TwoRound, SimulatedSerial


class Radar:
    def __init__(self, port=None, baud=None, *, events, raw_path, fail_release=False):
        self.port = port or "fake-two-round"
        self.events = events
        self.raw = Path(raw_path).read_bytes()
        self.fail_release = fail_release
        self.model = SimulatedIWR6843TwoRound()
        self.ser = SimulatedSerial(self.model)
        original_write = self.ser.write

        def write(data):
            if data == b"overview\n" and not self.model.frozen:
                self.model.freeze(self.raw)
            if data.startswith(b"release ") and self.fail_release:
                self.ser.inject(b"Error: rearm failed\nl3dump:/>")
                return len(data)
            return original_write(data)

        self.ser.write = write

    def two_round_support(self):
        return {"supported": True, "reason": None}

    def send_config(self, path, *, responses=None):
        self.events.append({"event": "config", "path": path})

    def stats(self):
        return "active=1"

    def stop_sensor(self):
        self.events.append({"event": "stop"})

    def close(self):
        self.events.append({"event": "closed", "frozen": self.model.frozen})

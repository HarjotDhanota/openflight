"""Retain original OPS UART traffic around the existing driver calls."""

import base64
import time


class SerialJournal:
    """Forward port state; host read/write brackets do not time ADC sampling."""

    def __init__(self, serial, emit):
        object.__setattr__(self, "serial", serial)
        object.__setattr__(self, "emit", emit)

    def __getattr__(self, name):
        return getattr(self.serial, name)

    def __setattr__(self, name, value):
        setattr(self.serial, name, value)

    def read(self, size=1):
        before = time.monotonic_ns()
        data = self.serial.read(size)
        self.emit(
            {
                "type": "read",
                "before_ns": before,
                "after_ns": time.monotonic_ns(),
                "bytes_base64": base64.b64encode(data).decode(),
            }
        )
        return data

    def write(self, data):
        before = time.monotonic_ns()
        count = self.serial.write(data)
        self.emit(
            {
                "type": "write",
                "before_ns": before,
                "after_ns": time.monotonic_ns(),
                "bytes_base64": base64.b64encode(data).decode(),
                "written_bytes": count,
            }
        )
        return count

    def reset_input_buffer(self):
        self.emit(
            {
                "type": "reset_input_buffer",
                "mono_ns": time.monotonic_ns(),
                "queued_bytes": self.serial.in_waiting,
            }
        )
        return self.serial.reset_input_buffer()

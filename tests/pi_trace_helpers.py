"""Shared helpers for tests that read Pi trace files (fixture, marks reader, fake clock, OPS serial)."""

import json
import time

import serial

from openflight import pi_trace
from openflight.pi_trace import ClockTriple, TraceRecorder


class FakeClock:
    """Deterministic ClockTriple source: every read advances 1 microsecond."""

    def __init__(self):
        self.mono = 1_000_000_000
        self.real = 1_790_000_000_000_000_000

    def __call__(self):
        self.mono += 1_000
        self.real += 1_000
        return ClockTriple(self.mono, self.real, self.mono + 5)


def read_lines(path):
    """Every JSONL line of a trace file as a dict."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def make_recorder(tmp_path, **kw):
    """A TraceRecorder with a fake clock, fixed temperature and fixed boot id."""
    return TraceRecorder(
        tmp_path / "trace.jsonl",
        clock=kw.pop("clock", FakeClock()),
        temp_reader=lambda: 51.5,
        boot_id_reader=kw.pop("boot_id_reader", lambda: "boot-a"),
        **kw,
    )


def installed_recorder(tmp_path):
    """A recorder installed as the process-wide one; removed afterwards (a fixture's body)."""
    rec = pi_trace.TraceRecorder(tmp_path / "t.jsonl", temp_reader=lambda: None)
    pi_trace.set_recorder(rec)
    yield rec
    pi_trace.set_recorder(None)


def read_marks(rec):
    """Close the recorder and return only its mark lines."""
    rec.close()
    return [row for row in read_lines(rec.path) if row["type"] == "mark"]


class ClockAndQuietSerial:
    """Answers C? with a fractional clock; otherwise silent, so re-arm drains at once."""

    def __init__(self, *, write_timeout_on=None):
        self.is_open = True
        self._pending = b""
        self._later = b""
        self.writes = []
        self._write_timeout_on = write_timeout_on

    def reset_input_buffer(self):
        self._pending = b""

    def arrive(self, data: bytes) -> None:
        """Bytes the radar sends unprompted (a dump an edge started)."""
        self._pending += data

    def arrive_after_first_poll(self, data: bytes) -> None:
        """Bytes that arrive after the next ``in_waiting`` poll (a dump after the wait began)."""
        self._later += data

    def write(self, data):
        if self._write_timeout_on is not None and data.startswith(self._write_timeout_on):
            raise serial.SerialTimeoutException("write timeout")
        self.writes.append(data)
        if data == b"C?":
            self._pending = b'{"Clock":"137.429"}'
        return len(data)

    def flush(self):
        pass

    @property
    def in_waiting(self):
        waiting = len(self._pending)
        self._pending, self._later = self._pending + self._later, b""
        return waiting

    def read(self, n):
        chunk, self._pending = self._pending[:n], self._pending[n:]
        return chunk


def dump_bytes(i_samples, q_samples) -> bytes:
    """One OPS rolling-buffer dump: sample/trigger time lines, then I and Q."""
    lines = [
        '{"sample_time": "964.003"}',
        '{"trigger_time": "964.105"}',
        json.dumps({"I": [int(v) for v in i_samples]}),
        json.dumps({"Q": [int(v) for v in q_samples]}),
    ]
    return ("\n".join(lines) + "\n").encode("ascii")


class _TimeWithoutSleep:
    """The ``time`` module as one module sees it, with ``sleep`` a no-op (nothing global)."""

    def __getattr__(self, name):
        return getattr(time, name)

    @staticmethod
    def sleep(_seconds):
        return None


def skip_ops243_sleeps(monkeypatch):
    """Make the OPS243 driver's own sleeps instant without patching the global time.sleep.

    ``monkeypatch.setattr("openflight.ops243.time.sleep", ...)`` replaces ``time.sleep``
    for every module (``openflight.ops243.time is time``), which also turns a test's own
    pauses into no-ops.
    """
    monkeypatch.setattr("openflight.ops243.time", _TimeWithoutSleep())

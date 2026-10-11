"""Shared helpers for tests that read Pi trace files (fixture, marks reader, fake clock)."""

import json

import pytest

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


@pytest.fixture
def recorder(tmp_path):
    """A recorder installed as the process-wide one; removed afterwards."""
    rec = pi_trace.TraceRecorder(tmp_path / "t.jsonl", temp_reader=lambda: None)
    pi_trace.set_recorder(rec)
    yield rec
    pi_trace.set_recorder(None)


def read_marks(rec):
    """Close the recorder and return only its mark lines."""
    rec.close()
    return [row for row in read_lines(rec.path) if row["type"] == "mark"]

"""The trace hooks are no-ops until a recorder is installed, and never raise into callers."""

import pytest

from openflight import instrumentation


class FakeSink:
    """Records marks; can be told to fail every call."""

    def __init__(self, *, fail=False):
        self.fail = fail
        self.marks = []
        self.current_ops_attempt = "a-7"
        self.last_edge_seq = 3

    def mark(self, name, attempt_id=None, **fields):
        if self.fail:
            raise RuntimeError("sink broke")
        self.marks.append((name, attempt_id, fields))

    def bound_attempt(self):
        if self.fail:
            raise RuntimeError("sink broke")
        return "a-bound"


@pytest.fixture
def sink():
    installed = FakeSink()
    instrumentation.set_recorder(installed)
    yield installed
    instrumentation.set_recorder(None)


def test_hooks_are_no_ops_without_a_recorder():
    instrumentation.set_recorder(None)
    instrumentation.mark("anything", "a-1", value=1)
    assert instrumentation.get_recorder() is None
    assert instrumentation.current_ops_attempt() is None
    assert instrumentation.bound_attempt() is None
    assert instrumentation.last_edge_seq() is None


def test_marks_and_reads_reach_the_installed_recorder(sink):
    instrumentation.mark("ops_first_byte", "a-1", bytes_pending=0)
    assert sink.marks == [("ops_first_byte", "a-1", {"bytes_pending": 0})]
    assert instrumentation.get_recorder() is sink
    assert instrumentation.current_ops_attempt() == "a-7"
    assert instrumentation.bound_attempt() == "a-bound"
    assert instrumentation.last_edge_seq() == 3


def test_a_failing_recorder_never_raises_into_the_caller(sink):
    sink.fail = True
    instrumentation.mark("ops_first_byte", "a-1")
    assert instrumentation.bound_attempt() is None


def test_trace_method_marks_start_and_end_with_the_error_name(sink):
    class Box:
        def work(self, value):
            if value < 0:
                raise ValueError("negative")
            return value * 2

    box = Box()
    instrumentation.trace_method(box, "work", "w_started", "w_done")
    assert box.work(2) == 4
    with pytest.raises(ValueError):
        box.work(-1)
    assert [(name, attempt, fields.get("error")) for name, attempt, fields in sink.marks] == [
        ("w_started", "a-bound", None),
        ("w_done", "a-bound", None),
        ("w_started", "a-bound", None),
        ("w_done", "a-bound", "ValueError"),
    ]

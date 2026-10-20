"""Known-answer tests for the Pi trace recorder (no hardware)."""

import math
import threading

import pytest

from openflight import pi_trace
from openflight.pi_trace import BracketedPair, _finite
from tests.pi_trace_helpers import make_recorder, read_lines


def test_header_sequenced_marks_and_complete_footer(tmp_path):
    rec = make_recorder(tmp_path)
    attempt = rec.open_ops_attempt()
    rec.mark("ops_first_byte", attempt, bytes_pending=0)
    assert rec.close() is True
    lines = read_lines(tmp_path / "trace.jsonl")
    assert lines[0]["type"] == "trace_start" and lines[0]["version"] == 2
    assert [m["seq"] for m in lines[1:-1]] == [1, 2]
    assert [m["name"] for m in lines[1:-1]] == ["ops_wait_started", "ops_first_byte"]
    assert lines[1]["attempt_id"] == attempt and lines[1]["cpu_temp_c"] == 51.5
    end = lines[-1]
    assert end["type"] == "trace_end" and end["complete"] is True
    assert (end["enqueued"], end["written"], end["dropped"], end["write_failures"]) == (2, 2, 0, 0)


def test_attempt_ids_are_unique_and_sequential(tmp_path):
    rec = make_recorder(tmp_path)
    first, second = rec.open_ops_attempt(), rec.open_ops_attempt()
    rec.close()
    assert first.endswith("-00001") and second.endswith("-00002")
    assert rec.current_ops_attempt == second


def test_explicit_monotonic_time_is_kept_not_restamped(tmp_path):
    rec = make_recorder(tmp_path)
    rec.mark("camera_trigger", "a-1", monotonic_ns=42)
    rec.close()
    assert read_lines(tmp_path / "trace.jsonl")[1]["monotonic_ns"] == 42


def test_edges_get_their_own_sequence(tmp_path):
    rec = make_recorder(tmp_path)
    first = rec.note_edge(monotonic_ns=7, edge_age_ns=900)
    second = rec.note_edge(edge_age_ns=None)
    rec.close()
    assert (first, second, rec.last_edge_seq) == (1, 2, 2)
    marks = [
        m for m in read_lines(tmp_path / "trace.jsonl") if m.get("name") == "gate_edge_callback"
    ]
    assert [m["edge_seq"] for m in marks] == [1, 2] and marks[0]["monotonic_ns"] == 7


def test_full_queue_counts_drops_and_never_blocks(tmp_path):
    rec = make_recorder(tmp_path, capacity=2, start_writer=False)
    for index in range(5):
        rec.mark("server_callback_started", f"a-{index}")
    assert rec.counts()["dropped"] == 3
    rec.close()
    end = read_lines(tmp_path / "trace.jsonl")[-1]
    assert end["dropped"] == 3 and end["complete"] is True


def test_nonfinite_payload_is_a_write_failure_not_silence(tmp_path):
    rec = make_recorder(tmp_path)
    rec.mark("bad", "a-1", value=math.nan)
    rec.mark("good", "a-1")
    rec.close()
    end = read_lines(tmp_path / "trace.jsonl")[-1]
    assert end["write_failures"] == 1 and end["written"] == 1


class _FailOnceFile:
    """Delegates to the real file; the first write raises, as a full disk would."""

    def __init__(self, real):
        self.real, self.failed = real, False

    def write(self, text):
        if not self.failed:
            self.failed = True
            raise OSError("disk full")
        return self.real.write(text)

    def flush(self):
        self.real.flush()

    def close(self):
        self.real.close()


def test_disk_write_failure_is_counted(tmp_path):
    rec = make_recorder(tmp_path, start_writer=False)
    rec.mark("m", "a-1")
    rec._file = _FailOnceFile(rec._file)  # pylint: disable=protected-access
    rec.close()
    end = read_lines(tmp_path / "trace.jsonl")[-1]
    assert end["write_failures"] == 1 and end["written"] == 0


def test_stalled_writer_close_reports_incomplete_and_writes_no_footer(tmp_path):
    gate = threading.Event()
    rec = make_recorder(tmp_path)
    rec._before_write = gate.wait  # pylint: disable=protected-access
    rec.mark("m", "a-1")
    assert rec.close(timeout_s=0.05) is False
    rows = read_lines(tmp_path / "trace.jsonl")
    assert all(row["type"] != "trace_end" for row in rows)
    gate.set()


def test_thread_binding_is_per_thread(tmp_path):
    rec = make_recorder(tmp_path)
    rec.bind_thread("a-1")
    seen = []
    worker = threading.Thread(target=lambda: seen.append(rec.bound_attempt()))
    worker.start()
    worker.join()
    assert rec.bound_attempt() == "a-1" and seen == [None]
    rec.close()


def test_trace_method_marks_start_and_end_on_the_bound_attempt(tmp_path):
    rec = make_recorder(tmp_path)
    pi_trace.set_recorder(rec)
    try:

        class Box:
            def work(self, x):
                return x * 2

        box = Box()
        pi_trace.trace_method(box, "work", "w_started", "w_done")
        rec.bind_thread("a-9")
        assert box.work(21) == 42
        assert "work" in vars(box) and "work" not in vars(Box()) and Box().work(1) == 2
    finally:
        pi_trace.set_recorder(None)
    rec.close()
    names = [(m["name"], m["attempt_id"]) for m in read_lines(tmp_path / "trace.jsonl")[1:-1]]
    assert names == [("w_started", "a-9"), ("w_done", "a-9")]


def test_trace_method_marks_end_with_the_error_and_reraises(tmp_path):
    rec = make_recorder(tmp_path)
    pi_trace.set_recorder(rec)
    try:

        class Box:
            def work(self):
                raise ValueError("boom")

        box = Box()
        pi_trace.trace_method(box, "work", "w_started", "w_done")
        with pytest.raises(ValueError):
            box.work()
    finally:
        pi_trace.set_recorder(None)
    rec.close()
    done = [m for m in read_lines(tmp_path / "trace.jsonl") if m.get("name") == "w_done"][0]
    assert done["error"] == "ValueError"


def test_bracketed_pair_is_re_exported_from_clock_mapping():
    from openflight.clock_mapping import BracketedPair as product_pair

    assert BracketedPair is product_pair


def test_module_mark_is_a_no_op_without_a_recorder():
    pi_trace.set_recorder(None)
    pi_trace.mark("anything", "a-1")
    assert pi_trace.current_ops_attempt() is None and pi_trace.bound_attempt() is None


def test_marks_from_many_threads_all_arrive_with_unique_sequences(tmp_path):
    rec = make_recorder(tmp_path)
    threads = [threading.Thread(target=lambda n=n: rec.mark("m", f"a-{n}")) for n in range(50)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    rec.close()
    seqs = sorted(m["seq"] for m in read_lines(tmp_path / "trace.jsonl") if m.get("name") == "m")
    assert seqs == list(range(1, 51))


def test_reserved_keys_in_fields_do_not_override(tmp_path):
    rec = make_recorder(tmp_path)
    # name, attempt_id and monotonic_ns are named parameters, so only the other reserved
    # keys can reach **fields; passing them by dict unpacking is the way a caller would.
    rec.mark(
        "real_name",
        "a-1",
        monotonic_ns=42,
        **{"seq": 999, "type": "evil", "thread": "evil", "keep": "yes"},
    )
    rec.close()
    row = read_lines(tmp_path / "trace.jsonl")[1]
    assert (row["type"], row["seq"], row["name"], row["attempt_id"]) == (
        "mark",
        1,
        "real_name",
        "a-1",
    )
    assert row["monotonic_ns"] == 42 and row["thread"] != "evil" and row["keep"] == "yes"


def test_finite_is_re_exported_from_the_shared_numeric_check():
    from openflight.numeric import finite

    assert _finite is finite


class _FailingFile:
    """Delegates to the real file; chosen operations raise."""

    def __init__(self, real, *, fail_text=None, fail_close=False, exc=OSError):
        self.real, self.fail_text, self.fail_close, self.exc = real, fail_text, fail_close, exc

    def write(self, text):
        if self.fail_text is not None and self.fail_text in text:
            raise self.exc("boom")
        return self.real.write(text)

    def flush(self):
        self.real.flush()

    def close(self):
        self.real.close()
        if self.fail_close:
            raise OSError("close failed")


@pytest.mark.parametrize("start_writer", [True, False])
def test_close_is_false_when_the_footer_write_fails(tmp_path, start_writer):
    rec = make_recorder(tmp_path, start_writer=start_writer)
    rec._file = _FailingFile(rec._file, fail_text="trace_end")  # pylint: disable=protected-access
    assert rec.close() is False
    assert all(row["type"] != "trace_end" for row in read_lines(tmp_path / "trace.jsonl"))


@pytest.mark.parametrize("start_writer", [True, False])
def test_close_is_false_when_closing_the_file_fails(tmp_path, start_writer):
    rec = make_recorder(tmp_path, start_writer=start_writer)
    rec._file = _FailingFile(rec._file, fail_close=True)  # pylint: disable=protected-access
    assert rec.close() is False


def test_late_footer_after_a_stalled_close_says_incomplete_and_late(tmp_path):
    gate = threading.Event()
    rec = make_recorder(tmp_path)
    rec._before_write = gate.wait  # pylint: disable=protected-access
    rec.mark("m", "a-1")
    assert rec.close(timeout_s=0.05) is False
    gate.set()
    rec._writer.join(timeout=2)  # pylint: disable=protected-access
    end = read_lines(tmp_path / "trace.jsonl")[-1]
    assert end["type"] == "trace_end" and end["complete"] is False and end["late"] is True


def test_normal_footer_is_not_late(tmp_path):
    rec = make_recorder(tmp_path)
    rec.close()
    end = read_lines(tmp_path / "trace.jsonl")[-1]
    assert end["complete"] is True and end["late"] is False


def test_unexpected_exception_is_a_write_failure_and_the_writer_survives(tmp_path):
    rec = make_recorder(tmp_path)
    rec._file = _FailingFile(  # pylint: disable=protected-access
        rec._file, fail_text='"name": "bad"', exc=OverflowError
    )
    rec.mark("bad", "a-1")
    rec.mark("good", "a-1")
    assert rec.close() is True
    rows = read_lines(tmp_path / "trace.jsonl")
    assert [r["name"] for r in rows if r["type"] == "mark"] == ["good"]
    end = rows[-1]
    assert end["write_failures"] == 1 and end["written"] == 1 and end["complete"] is False


def test_exception_in_before_write_does_not_kill_the_writer(tmp_path):
    rec = make_recorder(tmp_path, start_writer=False)
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("seam")

    rec._before_write = flaky  # pylint: disable=protected-access
    rec.mark("m", "a-1")
    assert rec.close() is True
    assert rec.counts()["write_failures"] == 1


def test_failed_header_write_is_counted_and_footer_is_incomplete(tmp_path, monkeypatch):
    real_open = open

    def failing_open(path, *args, **kwargs):
        return _FailingFile(real_open(path, *args, **kwargs), fail_text="trace_start")

    monkeypatch.setattr("builtins.open", failing_open)
    rec = make_recorder(tmp_path)
    monkeypatch.undo()
    assert rec.counts()["write_failures"] == 1
    assert rec.close() is True
    end = read_lines(tmp_path / "trace.jsonl")[-1]
    assert end["write_failures"] == 1 and end["complete"] is False


def test_close_is_idempotent(tmp_path):
    rec = make_recorder(tmp_path)
    assert rec.close() is True
    assert rec.close() is True
    rows = read_lines(tmp_path / "trace.jsonl")
    assert [r["type"] for r in rows].count("trace_end") == 1

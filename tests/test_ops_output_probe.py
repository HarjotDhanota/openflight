"""Known-answer tests for the OPS output probe (fake streams only; no hardware)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from openflight import ops_output_probe as probe

MAY = Path(__file__).resolve().parents[1] / "session_logs" / "session_20260506_152406_range.jsonl"
MS = 1_000_000


def _line(obj):
    return (json.dumps(obj, separators=(",", ":")) + "\r\n").encode("ascii")


def _iq(i=None, q=None):
    return _line({"I": i if i is not None else [2048] * 4096}) + _line(
        {"Q": q if q is not None else [2047] * 4096}
    )


def _head():
    return _line({"sample_time": "964.003"}) + _line({"trigger_time": "964.071"})


def _chunks(parts):
    """[(arrival_ms, bytes), ...] -> chunks with ns times."""
    return [(ms * MS, data) for ms, data in parts]


def _summary(parts, *, limit_ms=8000, censored=False):
    chunks = _chunks(parts)
    records = probe.classify_stream(chunks, censored_at_limit=censored)
    return probe.stream_summary(chunks, records, baud=230_400, sent_ns=0, limit_ns=limit_ms * MS)


def _records(data, *, censored=False):
    return probe.classify_stream([(0, data)], censored_at_limit=censored)


# --- transfer arithmetic (the plan's recorded numbers; planning only) -------------------------


def test_ideal_transfer_of_the_recorded_dump():
    assert probe.ideal_transfer_s(41_035, 230_400) == pytest.approx(1.7810, abs=1e-4)


def test_ideal_transfer_counts_start_and_stop_bits():
    assert probe.UART_BITS_PER_BYTE == 10
    assert probe.ideal_transfer_s(23_040, 230_400) == pytest.approx(1.0)
    assert probe.ideal_transfer_s(1, 230_400, bits_per_byte=8) == pytest.approx(8 / 230_400)


def test_planning_arithmetic_matches_the_plan():
    numbers = probe.transfer_arithmetic()
    assert numbers["transfer_s"] == pytest.approx(1.781, abs=5e-4)
    assert numbers["last_byte_after_edge_s"] == pytest.approx(1.867, abs=5e-4)
    assert numbers["host_speeds_ready_after_edge_s"] == [
        pytest.approx(1.928, abs=5e-4),
        pytest.approx(1.963, abs=5e-4),
    ]
    assert numbers["ops_allocation_s"] == pytest.approx(1.20)
    assert numbers["short_of_allocation_s"] == [
        pytest.approx(0.728, abs=5e-4),
        pytest.approx(0.763, abs=5e-4),
    ]
    assert "not this run" in numbers["basis"]


@pytest.mark.parametrize(
    "split,received,expected",
    [
        (16, 2049, 1.422),  # first ball window's first Q sample at S#16
        (16, 4096, 1.867),  # last Q sample = last byte
        (2, 512, 1.148),  # S#2: Q through two post-trigger windows
        (4, 768, 1.195),  # S#4: Q through two post-trigger windows
        (0, 0, 1.045),  # S#0: Q starts
        (0, 512, 1.156),
    ],
)
def test_q_arrival_arithmetic_matches_the_plan(split, received, expected):
    got = probe.q_samples_received_after_edge_s(received, pre_trigger_segments=split)
    assert got == pytest.approx(expected, abs=1e-3)


def test_q_arrival_rejects_impossible_inputs():
    with pytest.raises(ValueError):
        probe.q_samples_received_after_edge_s(4097, pre_trigger_segments=16)
    with pytest.raises(ValueError):
        probe.q_samples_received_after_edge_s(-1, pre_trigger_segments=16)
    with pytest.raises(ValueError):
        probe.q_samples_received_after_edge_s(10, pre_trigger_segments=33)


# --- record classification and the stream summary ---------------------------------------------


def test_valid_dump_is_complete_iq_in_arrival_order():
    summary = _summary([(80, _head()), (900, _iq())])
    assert summary["order"] == ["sample_time", "trigger_time", "I", "Q"]
    assert summary["iq_valid"] is True and summary["iq_problems"] == []


def test_iq_completion_time_is_the_later_of_i_and_q():
    i_line, q_line = _line({"I": [1] * 4096}), _line({"Q": [2] * 4096})
    summary = _summary([(80, _head() + i_line), (700, q_line[:100]), (1900, q_line[100:])])
    assert summary["iq_complete_after_sent_s"] == pytest.approx(1.9)
    assert summary["iq_complete_reason"] is None


def test_incomplete_iq_has_no_completion_time_and_says_why():
    summary = _summary([(50, _head() + _line({"I": [1] * 4096}))])
    assert summary["iq_complete_after_sent_s"] is None
    assert summary["iq_complete_reason"] == "iq_invalid"


def test_speed_after_a_pause_is_still_seen():
    summary = _summary([(50, _head() + _iq()), (900, _line({"speed": -41.2}))])
    assert summary["order"][-1] == "speed" and summary["speed_records_before_iq"] == 0
    assert summary["gaps_s"] == [pytest.approx(0.85)]
    assert summary["speed_records"] == 1
    assert summary["first_speed_complete_after_sent_s"] is None
    assert summary["first_speed_complete_reason"] == "no_speed_record_before_iq"


def test_speed_first_uses_the_completion_time_of_the_record():
    speed = _line({"speed": [-41.2, -40.9], "magnitude": [30, 20]})
    summary = _summary([(70, _head() + speed[:5]), (75, speed[5:]), (900, _iq())])
    assert summary["speed_records_before_iq"] == 1
    assert summary["first_speed_complete_after_sent_s"] == pytest.approx(0.075)
    assert summary["speed_complete_after_sent_s"] == [pytest.approx(0.075)]


def test_every_speed_record_has_its_completion_time():
    summary = _summary(
        [
            (60, _line({"speed": 1.5})),
            (90, _line({"speed": 2.5}) + _iq()),
            (950, _line({"speed": 3})),
        ]
    )
    assert summary["speed_complete_after_sent_s"] == [
        pytest.approx(0.06),
        pytest.approx(0.09),
        pytest.approx(0.95),
    ]
    assert summary["speed_records_before_iq"] == 2


def test_metadata_and_errors_are_unknown_not_speed():
    summary = _summary(
        [(50, _head() + _line({"SamplingRate": 30000}) + _line({"error": "x"}) + _iq())]
    )
    assert summary["speed_records_before_iq"] == 0 and summary["unknown_records"] == 2
    assert summary["unknown_records_before_iq"] == 2


@pytest.mark.parametrize(
    "line",
    [
        b'{"speed":"x"}\r\n',
        b'{"speed":"nan"}\r\n',
        b'{"speed":"inf"}\r\n',
        b'{"speed":"-41.2 mph"}\r\n',
        b'{"speed":""}\r\n',
        b'{"speed":["1.5",2.5]}\r\n',  # mixed forms are not one recognized form
        b'{"speed":["1.5","x"]}\r\n',
        b'{"speed":"1e999"}\r\n',  # not finite
        b'{"speed":[]}\r\n',
        b'{"speed":[1.0,"x"]}\r\n',
        b'{"speed":true}\r\n',
        b'{"speed":NaN}\r\n',
        b'{"speed":Infinity}\r\n',
        b'{"speed":"mph","distance":"yards"}\r\n',
        b"nan\r\n",
        b"1_000\r\n",
        b"-41.2 mph\r\n",
        b"[1, 2]\r\n",
    ],
)
def test_unrecognized_speed_shapes_stay_unknown(line):
    (record,) = _records(line)
    assert record.kind == "unknown" and record.valid is None


@pytest.mark.parametrize(
    "line,form",
    [
        (b"-41.2\r\n", "plain"),
        (b"38\r\n", "plain"),
        (b'{"speed":0}\r\n', "number"),
        (b'{"speed":[-1,2.5]}\n', "number"),
        (b'{"speed":"-41.2"}\r\n', "string"),  # ruling I1: numeric strings are speeds
        (b'{"speed":["-41.2","38"],"magnitude":["30","20"]}\r\n', "string"),
    ],
)
def test_recognized_speed_shapes(line, form):
    (record,) = _records(line)
    assert record.kind == "speed" and record.valid is True and record.form == form


def test_speed_forms_are_counted_in_the_summary():
    summary = _summary(
        [(60, _line({"speed": "-41.2"}) + _line({"speed": 1.5}) + b"38\r\n" + _head() + _iq())]
    )
    assert summary["speed_forms"] == {"number": 1, "plain": 1, "string": 1}
    assert summary["speed_records_before_iq"] == 3


def test_unparseable_capture_times_are_invalid():
    records = _records(_line({"sample_time": "abc"}) + _line({"trigger_time": "964.071"}))
    assert [(r.kind, r.valid) for r in records] == [("sample_time", False), ("trigger_time", True)]


def test_late_first_byte_is_not_a_timeout_but_none_is():
    late = _summary([(600, _head() + _iq())])
    assert late["first_byte_timeout"] is False
    assert late["first_byte_after_sent_s"] == pytest.approx(0.6)
    none = _summary([])
    assert none["first_byte_timeout"] is True and none["first_byte_after_sent_s"] is None
    assert none["observed_transfer_s"] is None and none["total_bytes"] == 0


def test_silence_after_the_last_byte_is_reported_up_to_the_limit():
    summary = _summary([(50, _head() + _iq())], limit_ms=8000)
    assert summary["trailing_silence_s"] == pytest.approx(7.95)
    assert summary["observation_limit_s"] == pytest.approx(8.0)


@pytest.mark.parametrize(
    "parts,problem",
    [
        ([(50, _head() + _line({"I": ["x"] * 4096}) + _line({"Q": [1] * 4096}))], "I_invalid"),
        ([(50, _head() + _line({"I": [1] * 4095}) + _line({"Q": [1] * 4096}))], "I_invalid"),
        ([(50, _head() + _line({"I": [5000] * 4096}) + _line({"Q": [1] * 4096}))], "I_invalid"),
        ([(50, _head() + _line({"I": [True] * 4096}) + _line({"Q": [1] * 4096}))], "I_invalid"),
        ([(50, _head() + _line({"I": [1] * 4096}) + _line({"Q": [-1] * 4096}))], "Q_invalid"),
        ([(50, _head() + _iq() + _iq())], "duplicate_iq"),
        ([(50, _head() + _line({"I": [1] * 4096}))], "Q_missing"),
        ([(50, _head() + b'{"I":[1,2,3' + b"\r\n" + _line({"Q": [1] * 4096}))], "I_invalid"),
    ],
)
def test_invalid_or_duplicate_iq_is_never_complete(parts, problem):
    summary = _summary(parts)
    assert summary["iq_valid"] is False and problem in summary["iq_problems"]


def test_a_malformed_iq_line_is_still_named_iq():
    (record,) = _records(b'{"Q":[1,2,3\r\n')
    assert record.kind == "Q" and record.valid is False


def test_split_records_and_censoring():
    data = _head() + _iq()
    cut = data[: len(data) - 1000]
    summary = _summary([(50, cut[:20_000]), (500, cut[20_000:])], censored=True)
    assert summary["censored"] is True and summary["iq_valid"] is False
    assert "Q_invalid" in summary["iq_problems"]
    assert summary["iq_complete_after_sent_s"] is None


def test_censored_speed_record_is_not_counted_as_complete():
    summary = _summary([(60, _line({"speed": 1.5}) + b'{"speed":2.5}')], censored=True)
    assert summary["censored"] is True and summary["unterminated_tail"] is True
    assert summary["speed_records"] == 1
    assert summary["speed_complete_after_sent_s"] == [pytest.approx(0.06)]


def test_unterminated_last_line_is_not_censored_when_the_stream_really_ended():
    (record,) = _records(b'{"speed":2.5}', censored=False)
    assert record.kind == "speed" and record.censored is False and record.terminated is False


def test_a_truncated_speed_line_is_never_a_complete_speed_record():
    """I2: '-41' cut from '-41.25' by a read error must not count as speed-first evidence."""
    for censored in (False, True):
        summary = _summary([(60, _head() + b"-41")], censored=censored)
        assert summary["speed_records"] == 0
        assert summary["speed_complete_after_sent_s"] == []
        assert summary["unterminated_tail"] is True
        assert summary["first_speed_complete_after_sent_s"] is None


def test_without_any_iq_record_before_iq_is_undefined():
    """Minor 2: 'before the I/Q' never defaults to the end of the stream."""
    summary = _summary([(60, _line({"speed": 1.5}) + _line({"x": 1}))])
    assert summary["speed_records"] == 1
    assert summary["speed_records_before_iq"] is None
    assert summary["unknown_records_before_iq"] is None
    assert summary["binary_bytes_before_iq"] is None
    assert summary["first_speed_complete_after_sent_s"] is None
    assert summary["first_speed_complete_reason"] == "iq_missing"
    assert summary["before_iq_reason"] == "iq_missing"


def test_speeds_are_not_counted_when_the_output_is_binary():
    """Minor 5: printable OB bytes can look like a speed line."""
    chunks = _chunks([(60, b"38\r\n" + _head() + _iq())])
    records = probe.classify_stream(chunks, censored_at_limit=True)
    summary = probe.stream_summary(
        chunks, records, baud=230_400, sent_ns=0, limit_ns=8000 * MS, count_speeds=False
    )
    for key in (
        "speed_records",
        "speed_records_before_iq",
        "speed_complete_after_sent_s",
        "first_speed_complete_after_sent_s",
        "speed_forms",
    ):
        assert summary[key] is None, key
    assert summary["first_speed_complete_reason"] == "speeds_not_counted_binary_output"
    assert summary["iq_valid"] is True


def test_speeds_are_counted_unless_binary_output_is_applied():
    assert probe.counts_speed_records([]) is True
    assert probe.counts_speed_records(["SZ"]) is True
    assert probe.counts_speed_records(["OB"]) is False


def test_binary_bytes_are_kept_and_not_parsed():
    summary = _summary([(50, bytes([0x02, 0x3F, 0x01, 0x25]) + _head() + _iq())])
    assert summary["binary_bytes"] == 4 and summary["iq_valid"] is True
    assert summary["order"] == ["binary", "sample_time", "trigger_time", "I", "Q"]
    assert summary["binary_bytes_before_iq"] == 4


def test_binary_without_a_following_object_is_all_binary():
    records = _records(bytes([0x02, 0x41, 0x80, 0x22]) + b"\r\n")
    assert [r.kind for r in records] == ["binary"]
    assert records[0].n_bytes == 6 and records[0].valid is None


def test_recorded_may_capture_rebuilt_matches_the_wire_floor():
    entry = next(
        json.loads(line)
        for line in MAY.read_text(encoding="utf-8").splitlines()
        if '"rolling_buffer_capture"' in line
    )
    summary = _summary([(50, _head() + _iq(entry["i_samples"], entry["q_samples"]))])
    assert summary["iq_valid"] is True and 39_000 <= summary["total_bytes"] <= 41_100


# --- O4 allocation (R-F10) ----------------------------------------------------------------------


def test_o4_allocation_is_absent_without_an_allowance():
    assert probe.o4_allocation(0.9, None) == {
        "within_o4_allocation": None,
        "o4_limit_s": None,
        "o4_reason": "allowance_missing",
    }


def test_o4_allocation_against_the_limit():
    met = probe.o4_allocation(1.19, 5.0)
    assert met["within_o4_allocation"] is True and met["o4_limit_s"] == pytest.approx(1.195)
    assert met["o4_reason"] is None
    missed = probe.o4_allocation(1.80, 5.0)
    assert missed["within_o4_allocation"] is False


def test_o4_allocation_needs_a_complete_iq():
    result = probe.o4_allocation(None, 5.0)
    assert result["within_o4_allocation"] is None and result["o4_reason"] == "iq_incomplete"


@pytest.mark.parametrize("allowance", [-1.0, float("nan"), float("inf")])
def test_o4_allocation_rejects_an_impossible_allowance(allowance):
    with pytest.raises(ValueError):
        probe.o4_allocation(1.0, allowance)


# --- mutation journal and restoration (audit S8, R-F11) ----------------------------------------


class _FakeIO:
    """Serial-like command target that can fail on a chosen command."""

    def __init__(self, fail_on=None):
        self.sent, self.fail_on = [], fail_on

    def command(self, text):
        self.sent.append(text)
        if text == self.fail_on:
            raise OSError(f"write failed: {text}")
        return "ok"


def test_inverse_runs_after_a_body_failure_and_each_cleanup_is_independent():
    io, journal = _FakeIO(), probe.Journal()

    def body():
        raise OSError("read failed")

    with pytest.raises(OSError):
        probe.run_candidate(io, "sz_on", ["SZ"], ["Sz"], body, journal)
    assert io.sent == ["SZ", "Sz"]
    assert [e["step"] for e in journal.entries] == ["apply", "body", "inverse"]
    assert journal.entries[1]["error"] == repr(OSError("read failed"))
    assert {e["candidate"] for e in journal.entries} == {"sz_on"}


def test_a_failing_inverse_does_not_stop_the_next_one():
    io, journal = _FakeIO(fail_on="Ob"), probe.Journal()
    probe.run_candidate(io, "two", ["OB", "SZ"], ["Ob", "Sz"], lambda: None, journal)
    assert io.sent == ["OB", "SZ", "Ob", "Sz"]
    assert [e["error"] is not None for e in journal.entries if e["step"] == "inverse"] == [
        True,
        False,
    ]


def test_a_failed_apply_is_journaled_and_every_inverse_is_still_attempted():
    io, journal = _FakeIO(fail_on="OB"), probe.Journal()
    ran = []
    with pytest.raises(OSError):
        probe.run_candidate(io, "two", ["OB", "SZ"], ["Ob", "Sz"], lambda: ran.append(1), journal)
    assert ran == []  # the body never runs on a half-applied candidate
    assert io.sent == ["OB", "Ob", "Sz"]
    assert [(e["step"], e["command"]) for e in journal.entries] == [
        ("apply", "OB"),
        ("inverse", "Ob"),
        ("inverse", "Sz"),
    ]
    assert journal.entries[0]["reply"] is None
    assert journal.entries[0]["error"] == repr(OSError("write failed: OB"))


def test_journal_attempt_records_the_reply_and_returns_it():
    journal = probe.Journal()
    assert journal.attempt("rearm", "PA S#16 PA", lambda: "rearmed", candidate="x") == "rearmed"
    assert journal.entries == [
        {
            "step": "rearm",
            "command": "PA S#16 PA",
            "reply": "rearmed",
            "error": None,
            "candidate": "x",
        }
    ]


def test_journal_attempt_records_the_error_and_reraises():
    journal = probe.Journal()

    def boom():
        raise TimeoutError("busy")

    with pytest.raises(TimeoutError):
        journal.attempt("trigger", "S!", boom)
    assert journal.entries[0]["error"] == repr(TimeoutError("busy"))
    assert journal.entries[0]["reply"] is None and journal.entries[0]["candidate"] is None


def test_inverse_errors_lists_only_failed_cleanups_of_that_candidate():
    io, journal = _FakeIO(fail_on="Ob"), probe.Journal()
    probe.run_candidate(io, "a", ["SZ"], ["Sz"], lambda: None, journal)
    probe.run_candidate(io, "b", ["OB"], ["Ob"], lambda: None, journal)
    assert journal.inverse_errors("a") == []
    assert [e["command"] for e in journal.inverse_errors("b")] == ["Ob"]


def test_unknown_initial_state_is_never_called_restored():
    assert probe.restoration_verified({"O?": "OJ"}, {"O?": "OJ"}) is True
    assert probe.restoration_verified({"O?": "OJ"}, {"O?": "OB"}) is False
    assert probe.restoration_verified({"O?": ""}, {"O?": ""}) is None
    assert probe.restoration_verified({}, {}) is None
    assert probe.restoration_verified({"O?": None}, {"O?": "OJ"}) is None


def test_a_missing_final_reply_is_not_restored():
    assert probe.restoration_verified({"O?": "OJ", "R?": "R|"}, {"O?": "OJ"}) is False
    assert probe.restoration_verified({"O?": "OJ"}, {}) is False


def test_restoration_ignores_surrounding_whitespace_only():
    assert probe.restoration_verified({"O?": "OJ\r\n"}, {"O?": " OJ"}) is True
    assert probe.restoration_verified({"O?": "O J"}, {"O?": "OJ"}) is False


def test_candidates_are_the_documented_pairs():
    assert probe.CANDIDATES == {
        "baseline": ([], []),
        "sz_on": (["SZ"], ["Sz"]),
        "ob_on": (["OB"], ["Ob"]),
    }


def test_candidates_never_persist_or_switch_modes():
    for name, (apply, inverse) in probe.CANDIDATES.items():
        assert not any(cmd in probe.FORBIDDEN_COMMANDS for cmd in apply + inverse), name
    assert {"A!", "GS", "GC"} <= set(probe.FORBIDDEN_COMMANDS)


def test_run_candidate_refuses_a_forbidden_command_before_sending_anything():
    io, journal = _FakeIO(), probe.Journal()
    with pytest.raises(ValueError):
        probe.run_candidate(io, "bad", ["SZ", "A!"], ["Sz"], lambda: None, journal)
    assert io.sent == [] and journal.entries == []


def test_probe_module_does_not_import_rolling_buffer():
    code = (
        "import sys, openflight.ops_output_probe; "
        "sys.exit(any(m.startswith('openflight.rolling_buffer') for m in sys.modules))"
    )
    assert subprocess.run([sys.executable, "-c", code], check=False).returncode == 0


@pytest.mark.parametrize(
    "reply,expected",
    [
        ('{"error":"unknown command"}', "unknown command"),
        ('OK\r\n{"Error": "bad"}\r\n', "bad"),
        ('{"error":""}', None),
        ("", None),
        ("Sz", None),
        ('{"speed":1}', None),
    ],
)
def test_device_error_reply(reply, expected):
    assert probe.device_error(reply) == expected


class _ErrorReplyIO(_FakeIO):
    def __init__(self, error_on):
        super().__init__()
        self.error_on = error_on

    def command(self, text):
        super().command(text)
        return '{"error":"not supported"}' if text == self.error_on else "ok"


def test_an_inverse_answered_with_an_error_is_a_failed_cleanup():
    io, journal = _ErrorReplyIO("Sz"), probe.Journal()
    probe.run_candidate(io, "sz_on", ["SZ"], ["Sz"], lambda: None, journal)
    (failed,) = journal.inverse_errors("sz_on")
    assert failed["reply"] == '{"error":"not supported"}'
    assert failed["error"] == "device_error: not supported"


def test_an_apply_answered_with_an_error_stops_the_candidate():
    io, journal = _ErrorReplyIO("SZ"), probe.Journal()
    ran = []
    with pytest.raises(probe.DeviceErrorReply):
        probe.run_candidate(io, "sz_on", ["SZ"], ["Sz"], lambda: ran.append(1), journal)
    assert ran == [] and io.sent == ["SZ", "Sz"]
    assert journal.entries[0]["error"] == "device_error: not supported"


def test_journal_entries_are_appended_to_a_jsonl_file_as_they_happen(tmp_path):
    path = tmp_path / "journal.jsonl"
    journal = probe.Journal(path)
    journal.record("apply", "SZ", "", None, candidate="sz_on")
    assert json.loads(path.read_text(encoding="utf-8")) == journal.entries[0]
    journal.record("inverse", "Sz", "", None, candidate="sz_on")
    lines = path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["command"] for line in lines] == ["SZ", "Sz"]


def test_an_unwritable_journal_file_never_stops_journaling(tmp_path):
    journal = probe.Journal(tmp_path / "missing-dir" / "journal.jsonl")
    journal.record("apply", "SZ", "", None)
    assert len(journal.entries) == 1 and journal.sink_error is not None


class _ScriptedIO(_FakeIO):
    """Error replies for some commands, an I/O error for others."""

    def __init__(self, error_replies=(), raises=()):
        super().__init__()
        self.error_replies, self.raises = set(error_replies), set(raises)

    def command(self, text):
        self.sent.append(text)
        if text in self.raises:
            raise OSError(f"write failed: {text}")
        return '{"error":"not supported"}' if text in self.error_replies else "ok"


def test_an_inverse_rejected_after_its_apply_was_rejected_is_not_a_failed_cleanup():
    io, journal = _ScriptedIO(error_replies={"SZ", "Sz"}), probe.Journal()
    with pytest.raises(probe.DeviceErrorReply):
        probe.run_candidate(io, "sz_on", ["SZ"], ["Sz"], lambda: None, journal)
    assert journal.inverse_errors("sz_on") == []
    inverse = journal.entries[-1]
    assert inverse["error"] == "device_error: not supported"
    assert inverse["note"] == "apply_rejected_nothing_applied"


def test_an_inverse_that_raises_after_a_rejected_apply_still_blocks():
    io, journal = _ScriptedIO(error_replies={"SZ"}, raises={"Sz"}), probe.Journal()
    with pytest.raises(probe.DeviceErrorReply):
        probe.run_candidate(io, "sz_on", ["SZ"], ["Sz"], lambda: None, journal)
    assert [e["command"] for e in journal.inverse_errors("sz_on")] == ["Sz"]


def test_only_inverses_of_unapplied_commands_are_excused():
    """OB applied, SZ rejected: Ob's error reply still blocks, Sz's does not."""
    io, journal = _ScriptedIO(error_replies={"SZ", "Ob", "Sz"}), probe.Journal()
    with pytest.raises(probe.DeviceErrorReply):
        probe.run_candidate(io, "two", ["OB", "SZ"], ["Ob", "Sz"], lambda: None, journal)
    assert [e["command"] for e in journal.inverse_errors("two")] == ["Ob"]


def test_an_apply_that_raised_excuses_nothing():
    """An I/O error on apply leaves its effect unknown, so the inverse error still counts."""
    io, journal = _ScriptedIO(raises={"SZ"}, error_replies={"Sz"}), probe.Journal()
    with pytest.raises(OSError):
        probe.run_candidate(io, "sz_on", ["SZ"], ["Sz"], lambda: None, journal)
    assert [e["command"] for e in journal.inverse_errors("sz_on")] == ["Sz"]


def test_unpaired_candidates_excuse_nothing():
    io, journal = _ScriptedIO(error_replies={"SZ", "Sz"}), probe.Journal()
    with pytest.raises(probe.DeviceErrorReply):
        probe.run_candidate(io, "odd", ["SZ"], ["Sz", "Sz"], lambda: None, journal)
    assert len(journal.inverse_errors("odd")) == 2

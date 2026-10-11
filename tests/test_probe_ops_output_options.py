"""The OPS output-options probe script, driven against a fake radar (never a device)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest

from openflight import ops_output_probe as probe_lib

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "hardware-test"
    / "probe_ops_output_options.py"
)
spec = importlib.util.spec_from_file_location("probe_ops_output_options", SCRIPT)
script = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = script
spec.loader.exec_module(script)


def _line(obj):
    return (json.dumps(obj, separators=(",", ":")) + "\r\n").encode("ascii")


DUMP = (
    _line({"sample_time": "964.003"})
    + _line({"trigger_time": "964.071"})
    + _line({"I": [2048] * 4096})
    + _line({"Q": [2047] * 4096})
)


class FakeSerial:
    """Delivers a dump in chunks after S!; can fail a read or a write."""

    def __init__(self, radar):
        self.radar = radar
        self.is_open = True
        self.writes: list[bytes] = []
        self.pending: list[bytes] = []

    def arrive(self, data: bytes) -> None:
        self.pending.append(data)

    def reset_input_buffer(self):
        self.pending = []

    def write(self, data):
        if self.radar.raise_on_write is not None and data == b"S!\r":
            raise self.radar.raise_on_write
        self.writes.append(data)
        if data == b"S!\r":
            self.pending.extend(self.radar.dump_chunks)
        return len(data)

    def flush(self):
        pass

    @property
    def in_waiting(self):
        return len(self.pending[0]) if self.pending else 0

    def read(self, _n):
        if (
            self.radar.read_error_after is not None
            and self.radar.reads >= self.radar.read_error_after
        ):
            raise OSError("device reports readiness to read but returned no data")
        self.radar.reads += 1
        return self.pending.pop(0)


class FakeRadar:  # pylint: disable=too-many-instance-attributes
    """Answers queries from a settings table; apply/inverse commands change it."""

    def __init__(self, port, **options):
        self.port = port
        self.baud = 230_400
        self.serial = None
        self.commands: list[str] = []
        self.rearms: list[int] = []
        self.disconnected = False
        self.reads = 0
        self._pre_trigger_split = (None, "not_written_this_session")
        self.state = {
            "??": '{"Product":"OPS243"}',
            "?V": '{"Version":"1.3.0"}',
            "?B": "B1",
            "I?": "I5",
            "O?": "OJ",
            "R?": "R|",
            "P?": "P7",
            "A?": "A0",
        }
        self.state.update(options.pop("state", {}))
        self.effects = {"SZ": None, "Sz": None, "OB": ("O?", "OB"), "Ob": ("O?", "OJ")}
        self.effects.update(options.pop("effects", {}))
        self.fail_on = options.pop("fail_on", None)
        self.connect_error = options.pop("connect_error", None)
        self.rearm_split = options.pop("rearm_split", None)
        self.dump_chunks = options.pop("dump_chunks", [DUMP[:20_000], DUMP[20_000:]])
        self.stale = options.pop("stale", b"")
        self.raise_on_write = options.pop("raise_on_write", None)
        self.read_error_after = options.pop("read_error_after", None)
        self.error_reply_on = options.pop("error_reply_on", None)
        self.interrupt_rearm_at = options.pop("interrupt_rearm_at", None)
        self.interrupt_queries_after_rearms = options.pop("interrupt_queries_after_rearms", None)
        self.last_baud_negotiation = options.pop(
            "negotiation",
            {"found_baud": 19200, "target_baud": 230400, "switch_command": "I5",
             "final_baud": 230400},
        )  # fmt: skip
        assert not options, options

    def connect(self):
        if self.connect_error is not None:
            raise self.connect_error
        self.serial = FakeSerial(self)
        return True

    def _send_command(self, text):
        limit = self.interrupt_queries_after_rearms
        if limit is not None and len(self.rearms) >= limit and text in script.QUERIES:
            raise KeyboardInterrupt
        self.commands.append(text)
        if text == self.fail_on:
            raise OSError(f"write failed: {text}")
        if text == self.error_reply_on:
            return '{"error":"not supported"}'
        if text in self.state:
            return self.state[text]
        effect = self.effects.get(text)
        if effect:
            self.state[effect[0]] = effect[1]
        return ""

    def rearm_rolling_buffer(self, pre_trigger_segments):
        self.rearms.append(pre_trigger_segments)
        if self.interrupt_rearm_at is not None and len(self.rearms) == self.interrupt_rearm_at:
            raise KeyboardInterrupt
        self._pre_trigger_split = self.rearm_split or (pre_trigger_segments, None)
        if self.stale:
            self.serial.arrive(self.stale)

    def disconnect(self):
        self.disconnected = True


@pytest.fixture(autouse=True)
def _no_settle(monkeypatch):
    monkeypatch.setattr(script, "ARM_SETTLE_S", 0.0)


def _run(tmp_path, *, extra=(), **radar_options):
    radars = []

    def factory(port):
        radar = FakeRadar(port, **radar_options)
        radars.append(radar)
        return radar

    argv = [
        "--rounds", "2", "--pre-trigger-segments", "16", "--observation-limit-s", "0.05",
        "--out", str(tmp_path), *extra,
    ]  # fmt: skip
    code = script.main(argv, radar_factory=factory)
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    return code, report, (radars[0] if radars else None)


def _sent(radar):
    """Every command and raw write the fake received, in one list."""
    raw = [w.decode("ascii", "replace") for w in (radar.serial.writes if radar.serial else [])]
    return radar.commands + raw


def test_full_run_writes_report_raw_files_and_restores(tmp_path):
    code, report, radar = _run(tmp_path, extra=["--allowance-ms", "5"])
    assert code == 0 and report["errors"] == []
    assert list(report["candidates"]) == ["baseline", "sz_on", "ob_on"]
    for name, candidate in report["candidates"].items():
        assert candidate["status"] == "completed"
        assert [candidate["apply"], candidate["inverse"]] == list(probe_lib.CANDIDATES[name])
        assert [r["round"] for r in candidate["rounds"]] == [0, 1]
        for row in candidate["rounds"]:
            assert row["iq_valid"] is True and row["total_bytes"] == len(DUMP)
            assert row["raw_file"] == f"{name}-{row['round']}.npz"
            assert row["within_o4_allocation"] is True
            assert row["o4_limit_s"] == pytest.approx(1.195)
        assert len(candidate["iq_complete_after_sent_s"]) == 2
        assert all(t is not None for t in candidate["iq_complete_after_sent_s"])
    assert report["restoration_verified"] is True
    assert report["queries_before"] == report["queries_after"]
    assert report["firmware_version"] == "1.3.0" and report["firmware_matches"] is True
    assert report["transport"] == "uart" and report["baud"] == 230_400
    assert report["pre_trigger_segments"] == 16 and report["allowance_ms"] == 5.0
    assert report["planning_arithmetic"]["transfer_s"] == pytest.approx(1.781, abs=5e-4)
    assert any("not impact" in note for note in report["notes"])
    assert set(radar.rearms) == {16} and radar.disconnected
    assert not any(cmd.strip() in probe_lib.FORBIDDEN_COMMANDS for cmd in _sent(radar))


def test_journal_orders_every_mutation(tmp_path):
    _code, report, _radar = _run(tmp_path)
    sz = [(e["step"], e["command"]) for e in report["journal"] if e["candidate"] == "sz_on"]
    assert sz == [
        ("apply", "SZ"),
        ("rearm", "PA S#16 PA"),
        ("trigger", "S!"),
        ("rearm", "PA S#16 PA"),
        ("trigger", "S!"),
        ("body", "sz_on"),
        ("inverse", "Sz"),
    ]
    assert [(e["step"], e["reply"]) for e in report["journal"]][-1] == ("final_rearm", "rearmed")


def test_raw_file_keeps_every_byte_with_its_read_time(tmp_path):
    _code, report, _radar = _run(tmp_path, stale=b"OK\r\n")
    row = report["candidates"]["baseline"]["rounds"][0]
    raw = np.load(tmp_path / row["raw_file"])
    assert bytes(raw["data"]) == DUMP
    assert list(raw["lengths"]) == [20_000, len(DUMP) - 20_000]
    assert len(raw["returned_ns"]) == 2 and raw["returned_ns"][0] >= raw["sent_ns"]
    assert raw["limit_ns"] - raw["sent_ns"] == 50_000_000
    assert bytes(raw["before_trigger"]) == b"OK\r\n"
    assert row["bytes_before_trigger"] == 4


def test_without_an_allowance_o4_is_absent_with_a_reason(tmp_path):
    _code, report, _radar = _run(tmp_path)
    row = report["candidates"]["baseline"]["rounds"][0]
    assert row["within_o4_allocation"] is None and row["o4_reason"] == "allowance_missing"


def test_a_failing_apply_is_journaled_its_inverse_runs_and_others_continue(tmp_path):
    code, report, radar = _run(tmp_path, fail_on="SZ")
    assert code == 1
    sz = [e for e in report["journal"] if e["candidate"] == "sz_on"]
    assert [(e["step"], e["command"]) for e in sz] == [("apply", "SZ"), ("inverse", "Sz")]
    assert sz[0]["error"] is not None and sz[1]["error"] is None
    assert report["candidates"]["sz_on"]["status"] == "failed"
    assert report["candidates"]["sz_on"]["rounds"] == []
    assert report["candidates"]["ob_on"]["status"] == "completed"
    assert any(e["candidate"] == "sz_on" for e in report["errors"])
    assert "Sz" in radar.commands


def test_a_failed_cleanup_stops_later_candidates(tmp_path):
    code, report, radar = _run(tmp_path, fail_on="Sz")
    assert code == 1
    assert report["candidates"]["sz_on"]["status"] == "completed"
    assert report["candidates"]["ob_on"]["status"] == "not_run"
    assert "cleanup" in report["candidates"]["ob_on"]["reason"]
    assert "OB" not in radar.commands
    assert report["journal"][-1]["step"] == "final_rearm"


def test_connect_failure_still_writes_a_report_and_sends_nothing(tmp_path):
    code, report, radar = _run(tmp_path, connect_error=ConnectionError("no port"))
    assert code == 1
    assert radar.commands == [] and radar.serial is None and radar.rearms == []
    assert report["restoration_verified"] is None
    assert [(e["step"], e["error"]) for e in report["journal"]] == [
        ("connect", repr(ConnectionError("no port")))
    ]
    assert report["final_rearm"] == "skipped_not_connected"
    assert {(c["status"], c["reason"]) for c in report["candidates"].values()} == {
        ("not_run", "not_reached")
    }
    assert report["errors"][0]["error"] == repr(ConnectionError("no port"))


def test_firmware_mismatch_changes_nothing(tmp_path):
    code, report, radar = _run(tmp_path, state={"?V": '{"Version":"1.3.2"}'})
    assert code == 1
    assert report["firmware_version"] == "1.3.2" and report["firmware_matches"] is False
    assert all(c["status"] == "not_run" for c in report["candidates"].values())
    assert radar.rearms == [] and radar.serial.writes == []
    assert set(radar.commands) <= set(script.QUERIES)
    assert [e["step"] for e in report["journal"]] == ["connect"]
    assert report["final_rearm"] == "skipped_no_mutation"
    assert report["restoration_verified"] is True
    assert any(e["error"].startswith("firmware_mismatch") for e in report["errors"])


def test_unparseable_firmware_reply_is_a_mismatch(tmp_path):
    _code, report, radar = _run(tmp_path, state={"?V": "garbage"})
    assert report["firmware_version"] is None and report["firmware_matches"] is False
    assert radar.rearms == []


def test_state_left_changed_is_not_restored(tmp_path):
    _code, report, _radar = _run(tmp_path, effects={"Ob": ("O?", "OS")})
    assert report["restoration_verified"] is False
    assert report["queries_after"]["O?"] == "OS"


def test_unknown_initial_state_is_null_never_restored(tmp_path):
    _code, report, _radar = _run(tmp_path, state={"P?": ""})
    assert report["restoration_verified"] is None


def test_a_failed_query_is_recorded_as_unknown(tmp_path):
    code, report, _radar = _run(tmp_path, fail_on="R?")
    assert code == 1
    assert report["queries_before"]["R?"] is None
    assert report["restoration_verified"] is None


def test_incomplete_rearm_skips_the_capture(tmp_path):
    _code, report, radar = _run(tmp_path, rearm_split=(None, "rearm_write_timed_out"))
    row = report["candidates"]["baseline"]["rounds"][0]
    assert row["capture"] == "skipped_rearm_incomplete"
    assert row["rearm"] == "not_armed:rearm_write_timed_out"
    assert b"S!\r" not in radar.serial.writes
    assert {c["status"] for c in report["candidates"].values()} == {"completed_no_capture"}


def test_read_error_keeps_the_partial_bytes_and_cleans_up(tmp_path):
    code, report, radar = _run(tmp_path, read_error_after=1)
    assert code == 1
    row = report["candidates"]["baseline"]["rounds"][0]
    assert row["read_error"] is not None and row["total_bytes"] == 20_000
    assert bytes(np.load(tmp_path / row["raw_file"])["data"]) == DUMP[:20_000]
    assert report["candidates"]["baseline"]["status"] == "failed"
    assert row["iq_valid"] is False
    assert report["candidates"]["sz_on"]["status"] == "failed" and "Sz" in radar.commands


def test_interrupt_still_cleans_up_and_writes_the_report(tmp_path):
    code, report, radar = _run(tmp_path, raise_on_write=KeyboardInterrupt())
    assert code == 130
    assert any(e["error"] == "interrupted" for e in report["errors"])
    assert report["journal"][-1]["step"] == "final_rearm"
    assert report["candidates"]["baseline"]["status"] == "interrupted"
    assert report["candidates"]["sz_on"]["status"] == "not_run"
    assert radar.disconnected


def test_refuses_to_overwrite_an_earlier_run(tmp_path):
    (tmp_path / "report.json").write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit) as raised:
        script.main(["--pre-trigger-segments", "16", "--out", str(tmp_path)],
                    radar_factory=pytest.fail)  # fmt: skip
    assert raised.value.code == 2
    assert (tmp_path / "report.json").read_text(encoding="utf-8") == "{}"


@pytest.mark.parametrize(
    "flags",
    [
        ["--pre-trigger-segments", "33"],
        ["--pre-trigger-segments", "-1"],
        ["--pre-trigger-segments", "16", "--rounds", "0"],
        ["--pre-trigger-segments", "16", "--observation-limit-s", "0"],
        ["--pre-trigger-segments", "16", "--observation-limit-s", "nan"],
        ["--pre-trigger-segments", "16", "--allowance-ms", "-1"],
        ["--pre-trigger-segments", "16", "--allowance-ms", "inf"],
        [],  # the split is required: never assumed to be 16
    ],
)
def test_rejects_bad_arguments_before_touching_the_radar(tmp_path, flags):
    with pytest.raises(SystemExit) as raised:
        script.main([*flags, "--out", str(tmp_path)], radar_factory=pytest.fail)
    assert raised.value.code == 2


def test_radar_io_refuses_forbidden_commands():
    radar = FakeRadar("/dev/ttyAMA0")
    io = script.RadarIO(radar)
    for command in probe_lib.FORBIDDEN_COMMANDS:
        with pytest.raises(ValueError):
            io.command(command)
    assert radar.commands == []


def test_interrupt_mid_capture_keeps_the_bytes_read_so_far(tmp_path, monkeypatch):
    real_collect = script._collect  # pylint: disable=protected-access

    def collect_then_interrupt(serial, limit_ns, chunks):
        real_collect(serial, limit_ns, chunks)
        raise KeyboardInterrupt

    monkeypatch.setattr(script, "_collect", collect_then_interrupt)
    code, report, _radar = _run(tmp_path)
    assert code == 130
    (row,) = report["candidates"]["baseline"]["rounds"]
    assert row["capture"] == "interrupted" and row["raw_file"] == "baseline-0.npz"
    assert bytes(np.load(tmp_path / "baseline-0.npz")["data"]) == DUMP
    assert report["candidates"]["baseline"]["status"] == "interrupted"


def test_rows_say_they_captured(tmp_path):
    _code, report, _radar = _run(tmp_path)
    assert {r["capture"] for c in report["candidates"].values() for r in c["rounds"]} == {
        "captured"
    }


def test_a_read_error_mid_number_is_not_a_speed_first_record(tmp_path):
    """I2: '-41' cut from '-41.25' by a read error is not a complete speed record."""
    head = _line({"sample_time": "964.003"}) + _line({"trigger_time": "964.071"})
    code, report, _radar = _run(tmp_path, dump_chunks=[head + b"-41", b".25\r\n"],
                                read_error_after=1)  # fmt: skip
    assert code == 1
    row = report["candidates"]["baseline"]["rounds"][0]
    assert row["read_error"] is not None and row["unterminated_tail"] is True
    assert row["speed_records"] == 0 and row["first_speed_complete_after_sent_s"] is None


def test_interrupting_the_final_rearm_still_writes_report_and_journal(tmp_path):
    """I3: 3 candidates x 2 rounds re-arm 6 times; the 7th is the final re-arm."""
    code, report, _radar = _run(tmp_path, interrupt_rearm_at=7)
    assert code == 130
    assert report["final_steps"] == "interrupted"
    assert any(e["error"] == "interrupted" for e in report["errors"])
    last = report["journal"][-1]
    assert last["step"] == "final_rearm" and last["error"] == repr(KeyboardInterrupt())
    assert report["restoration_verified"] is False  # never verified, never "restored"
    lines = (tmp_path / "journal.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line) for line in lines] == report["journal"]


def test_interrupting_the_final_queries_still_writes_the_report(tmp_path):
    code, report, _radar = _run(tmp_path, interrupt_queries_after_rearms=7)
    assert code == 130
    assert report["final_steps"] == "interrupted"
    assert report["final_rearm"] == "rearmed"
    assert report["restoration_verified"] is False


def test_completed_run_marks_its_final_steps_done(tmp_path):
    _code, report, _radar = _run(tmp_path)
    assert report["final_steps"] == "done"
    assert report["journal_file"] == "journal.jsonl" and report["journal_sink_error"] is None


def test_baud_negotiation_is_journaled(tmp_path):
    _code, report, _radar = _run(tmp_path)
    first = report["journal"][0]
    assert first["step"] == "connect" and first["error"] is None
    assert json.loads(first["reply"])["switch_command"] == "I5"
    assert report["baud_negotiation"]["final_baud"] == 230400


def test_no_negotiation_is_journaled_as_such(tmp_path):
    _code, report, _radar = _run(tmp_path, negotiation=None)
    assert report["journal"][0]["reply"] == "not_negotiated"
    assert report["baud_negotiation"] is None


def test_restoration_note_says_what_it_covers():
    note = next(n for n in script.NOTES if n.startswith("restoration_verified covers"))
    assert "eight query replies" in note and "not established" in note


def test_an_error_reply_to_an_inverse_stops_later_candidates(tmp_path):
    code, report, radar = _run(tmp_path, error_reply_on="Sz")
    assert code == 1
    assert report["candidates"]["ob_on"]["status"] == "not_run"
    assert "OB" not in radar.commands
    (failed,) = [e for e in report["journal"] if e["command"] == "Sz"]
    assert failed["error"] == "device_error: not supported"


def test_an_error_reply_to_an_apply_fails_that_candidate(tmp_path):
    _code, report, _radar = _run(tmp_path, error_reply_on="SZ")
    assert report["candidates"]["sz_on"]["status"] == "failed"
    assert report["candidates"]["sz_on"]["rounds"] == []
    assert report["candidates"]["ob_on"]["status"] == "completed"


def test_binary_output_rounds_do_not_count_speeds(tmp_path):
    _code, report, _radar = _run(tmp_path)
    ob_row = report["candidates"]["ob_on"]["rounds"][0]
    assert ob_row["speed_records"] is None
    assert ob_row["first_speed_complete_reason"] == "speeds_not_counted_binary_output"
    assert report["candidates"]["baseline"]["rounds"][0]["speed_records"] == 0
    assert any("OB" in n and "not counted" in n for n in script.NOTES)


def test_refuses_an_out_dir_holding_a_journal(tmp_path):
    (tmp_path / "journal.jsonl").write_text("", encoding="utf-8")
    with pytest.raises(SystemExit) as raised:
        script.main(["--pre-trigger-segments", "16", "--out", str(tmp_path)],
                    radar_factory=pytest.fail)  # fmt: skip
    assert raised.value.code == 2

"""The owner's W4a run script, against a fake radar and a fake clock (no hardware).

Every dump here is a tiny made-up v3 capture and every edge is scripted: known-answer
tests of the run bookkeeping only, never a hardware result.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import types
from pathlib import Path

import numpy as np
import pytest

from openflight.iwr6843 import driver, perf_diagnostics
from openflight.iwr6843.driver import (
    DumpTransferTiming,
    IWR6843DumpRecoveryError,
    IWR6843Radar,
)
from openflight.iwr6843.dump import pack_dump
from openflight.iwr6843.perf_diagnostics import parse_perf_stats
from tests.iwr6843_firmware_fake import FakeClock, FakePort, NoLock
from tests.iwr6843_perf_fake import BENCH_REPLY, PERF_REPLY, PerfFirmwareModel

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "iwr6843" / "feasibility_run.py"
SPEC = importlib.util.spec_from_file_location("iwr6843_feasibility_run", SCRIPT)
RUN = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = RUN
SPEC.loader.exec_module(RUN)

PLAN = (
    "Capture plan: loops=12 pre=9x53@3000us impact=7x53@3000us ball=8x53@3000us "
    "frames=24 bytes=732672/786432 starts=20/32/47/47 stride=1"
)
CFG = "% profile\nframeCfg 0 2 12 0 3 1 0\ncaptureFormat iq8\niq8Scale 128\nsensorStart\n"
IQ16_CFG = "% profile\nframeCfg 0 2 12 0 3 1 0\ncaptureFormat iq16\nsensorStart\n"
IMAGE = "ab" * 32
MS = 1_000_000
HISTORY_NS = 27 * MS  # PLAN's pre-request history: 9 frames x 3000 us
# Raw CLI replies in the firmware's formats (made-up values; parser known-answer data only).
PERF_TEXT = "perfstats\r\n" + PERF_REPLY.decode() + "Done\r\n"
BENCH_TEXT = "l3bench\r\n" + BENCH_REPLY.decode() + "Done\r\n"
OLD_FIRMWARE_PERF_TEXT = "perfstats\r\n'perfstats' is not a known command\r\nError -1\r\n"
# A build without the scan, nothing re-armed yet and the init task gone: None, never 0.
NO_SCAN_PERF_TEXT = (
    "perf tick_hz=200000000 rearm_max=0 rearms=0 task_max=0 task_runs=0 "
    "margin_min=4294967295 margin_frames=0\r\n"
    "perf scan=not_in_build pack=not_in_build\r\n"
    "heap size=32768 free=9000 free_min=8800 largest=8000\r\n"
    "stacks init=not_created ctrl=700/3072 rearm=600/2048 cli=1500/4096 hwi=900/2048\r\nDone\r\n"
)
# A reply the parser refuses as impossible: a re-arm maximum beside zero re-arms.
REFUSED_PERF_TEXT = PERF_TEXT.replace("rearms=900", "rearms=0")


def _dump_bytes() -> bytes:
    return pack_dump(np.ones((2, 6, 4, 7), dtype=complex), n_tx=3, version=3)


@pytest.fixture(name="placement")
def placement_fixture():
    """The taped placement every triggered or reflector attempt needs."""
    return {"placement_label": "pilot-tee", "placement_m": 1.5}


class Clock:
    """A settable host monotonic clock (ns)."""

    def __init__(self):
        self.now = 10_000 * MS

    def __call__(self):
        return self.now


class FakeRadar:
    """Answers like the W4a firmware; dumps are tiny made-up v3 captures."""

    def __init__(
        self,
        clock,
        *,
        fail_at=None,
        perf_error=False,
        active=True,
        on_dump=None,
        raise_at=None,
        perf_text=None,
        bench_text=None,
        on_cmd=None,
        stop_fails=False,
        log=None,
    ):
        self.clock, self.fail_at, self.perf_error, self.active = clock, fail_at, perf_error, active
        self.on_dump = on_dump or (lambda _n: None)
        self.raise_at = raise_at or {}
        self.perf_text = OLD_FIRMWARE_PERF_TEXT if perf_error else (perf_text or PERF_TEXT)
        self.bench_text = bench_text
        self.on_cmd = on_cmd or (lambda _line: None)
        self.stop_fails = stop_fails
        self.log = log if log is not None else []
        self.requests = 0
        self.stopped = False
        self.sent_config: bytes | None = None

    def send_config(self, cfg_path, *, responses=None):
        self.sent_config = Path(cfg_path).read_bytes()
        self.log.append("config")
        responses.append(("sensorStart", f"sensorStart\r\n{PLAN}\r\nDone\r\n"))

    def read_dump_timed(self):
        self.requests += 1
        self.log.append(f"dump {self.requests}")
        if self.requests in self.raise_at:
            raise self.raise_at[self.requests]
        written = self.clock()
        self.on_dump(self.requests)
        raw = _dump_bytes()
        timing = DumpTransferTiming(
            request_written_ns=written,
            first_byte_ns=written + 30 * MS,
            header_complete_ns=written + 31 * MS,
            last_byte_ns=written + 7_000 * MS,
            cli_ready_ns=written + 7_100 * MS,
        )
        self.clock.now = written + 7_100 * MS
        if self.requests == self.fail_at:
            raise IWR6843DumpRecoveryError(
                "IWR6843 dump ended early (5 of 9 bytes)", raw[:5], timing
            )
        return raw, timing

    def stats(self):
        self.log.append("stats")
        active = self.active(self.requests) if callable(self.active) else self.active
        return f"frames=9 active={int(active)} rf_faults=0 hwa_missed=0\r\nDone"

    def cmd(self, line, window=1.5):
        """Only the raw-reply commands the run script sends itself."""
        assert window > 0
        self.log.append(line)
        self.on_cmd(line)
        if line == "perfstats":
            return self.perf_text
        if line == "l3bench":
            if self.bench_text is not None:
                return self.bench_text
            if not self.stopped:
                return "l3bench\r\nError: stop the sensor before l3bench\r\nError -1\r\n"
            return BENCH_TEXT
        raise AssertionError(f"unexpected command {line!r}")

    def stop_sensor(self):
        if self.stop_fails:
            raise RuntimeError("IWR6843 remained active after sensorStop: active=1")
        self.stopped = True


class ScriptedEdges(RUN.EdgeQueue):
    """Delivers scheduled edges (ms offsets from 'now') right after arming, and one batch
    each time the ring is ready again after a dump."""

    def __init__(self, clock, after_arm=(), after_ready=(), log=None):
        super().__init__(clock=clock)
        self.after_arm, self.after_ready = list(after_arm), [list(b) for b in after_ready]
        self.log = log if log is not None else []

    def arm(self):
        self.log.append("arm")
        super().arm()
        for offset_ms in self.after_arm:
            self.on_edge(self._clock() + offset_ms * MS)

    def set_busy(self, busy):
        self.log.append(f"busy {busy}")
        super().set_busy(busy)
        if not busy and self.after_ready:
            for offset_ms in self.after_ready.pop(0):
                self.on_edge(self._clock() + offset_ms * MS)


def _write_cfg(tmp_path, text=CFG, name="a.cfg") -> Path:
    cfg = tmp_path / name
    cfg.write_bytes(text.encode("utf-8"))
    return cfg


def _kwargs(tmp_path, clock, edges, sleep_hook=None, **kwargs):
    def sleep(seconds):
        clock.now += int(seconds * 1e9)
        if sleep_hook is not None:
            sleep_hook()

    defaults = {
        "profile": "A",
        "config_path": _write_cfg(tmp_path),
        "out_root": tmp_path / "runs",
        "image_sha256": IMAGE,
        "soak": 2,
        "real_ball": 0,
        "clap": 0,
        "edges": edges,
        "sleep": sleep,
        "edge_timeout_s": 0.0,
    }
    defaults.update(kwargs)
    return defaults


def _rows(run_dir):
    return [json.loads(line) for line in (run_dir / "attempts.jsonl").read_text().splitlines()]


def _run(tmp_path, radar, edges, clock, *, sleep_hook=None, **kwargs):
    summary = RUN.run_profile(radar, **_kwargs(tmp_path, clock, edges, sleep_hook, **kwargs))
    run_dir = tmp_path / "runs" / f"{summary['profile']}-{summary['run_id']}"
    return summary, run_dir, _rows(run_dir)


def _only_run_dir(tmp_path):
    (run_dir,) = (tmp_path / "runs").iterdir()
    return run_dir


# --- pure helpers -------------------------------------------------------------------------


def test_helpers_read_the_config_and_the_plan_line():
    assert RUN.override_iq8_scale(CFG, 16) == CFG.replace("iq8Scale 128", "iq8Scale 16")
    with pytest.raises(ValueError, match="no iq8Scale line"):
        RUN.override_iq8_scale("captureFormat iq16\n", 16)
    assert RUN.config_iq8_scale(CFG) == 128
    assert RUN.config_iq8_scale("captureFormat iq16\n") is None
    assert RUN.capture_plan_line([("sensorStart", f"x\r\n{PLAN}\r\nDone")]) == PLAN
    assert RUN.capture_plan_line([("sensorStart", "Done")]) is None
    assert RUN.pre_history_s(PLAN) == pytest.approx(0.027)
    assert RUN.pre_history_s(None) is None


def test_the_iq8_scale_helpers_refuse_what_they_cannot_read_exactly():
    twice = CFG + "iq8Scale 64\n"
    with pytest.raises(ValueError, match="more than one iq8Scale"):
        RUN.override_iq8_scale(twice, 16)
    with pytest.raises(ValueError, match="more than one iq8Scale"):
        RUN.config_iq8_scale(twice)
    # A comment or a longer word is not the iq8Scale command.
    assert RUN.config_iq8_scale("% iq8Scale 128\niq8ScaleX 4\n") is None
    with pytest.raises(ValueError, match="no iq8Scale line"):
        RUN.override_iq8_scale("% iq8Scale 128\niq8ScaleX 4\n", 16)


def test_the_override_changes_only_the_scale_and_keeps_line_endings():
    crlf = CFG.replace("\n", "\r\n")
    assert RUN.override_iq8_scale(crlf, 16) == crlf.replace("iq8Scale 128", "iq8Scale 16")
    assert RUN.config_iq8_scale(RUN.override_iq8_scale(crlf, 16)) == 16
    with pytest.raises(ValueError, match="unreadable iq8Scale"):
        RUN.config_iq8_scale("iq8Scale\n")
    with pytest.raises(ValueError, match="unreadable iq8Scale"):
        RUN.config_iq8_scale("iq8Scale 12x\n")


def test_the_history_bound_is_exact_integer_nanoseconds():
    assert RUN.pre_history_ns(PLAN) == HISTORY_NS
    assert RUN.pre_history_ns("Capture plan: pre=21x53@2000us") == 42 * MS
    assert RUN.pre_history_ns(None) is None
    assert RUN.pre_history_ns("Capture plan: frames=24") is None


def test_every_run_gets_a_fresh_directory_that_cannot_be_reused(tmp_path):
    first_id, first = RUN.new_run_dir(tmp_path, "A")
    second_id, second = RUN.new_run_dir(tmp_path, "A")

    assert first_id != second_id and first != second
    assert RUN.RUN_ID.fullmatch(first_id)
    with pytest.raises(FileExistsError):
        RUN.new_run_dir(tmp_path, "A", run_id=first_id)


@pytest.mark.parametrize("run_id", ["../escape", "r1", "20261011T120000Z-ABCDEF12", ""])
def test_a_run_id_that_is_not_the_fresh_format_is_refused(tmp_path, run_id):
    with pytest.raises(ValueError, match="run ID"):
        RUN.new_run_dir(tmp_path, "A", run_id=run_id)
    assert not list(tmp_path.iterdir())


# --- the edge queue (S11) -------------------------------------------------------------------


def test_edges_before_arming_are_counted_and_never_queued():
    clock = Clock()
    edges = RUN.EdgeQueue(clock=clock)

    edges.on_edge()
    clock.now += 200 * MS
    edges.arm()
    clock.now += 200 * MS
    edges.on_edge()

    assert edges.next_fresh_edge(0.0, HISTORY_NS) == clock.now
    assert edges.next_fresh_edge(0.0, HISTORY_NS) is None
    assert edges.counters()["pre_arm_discarded"] == 1


def test_ringing_edges_within_a_tenth_of_a_second_are_counted_and_dropped():
    clock = Clock()
    edges = RUN.EdgeQueue(clock=clock)
    edges.arm()

    edges.on_edge()
    clock.now += 99 * MS
    edges.on_edge()  # ringing of the first
    clock.now += 101 * MS
    edges.on_edge()  # 200 ms after the first, 101 ms after the ringing edge

    assert edges.counters()["ringing_discarded"] == 1
    assert edges.next_fresh_edge(0.0, 10_000 * MS) == clock.now - 200 * MS
    assert edges.next_fresh_edge(0.0, 10_000 * MS) == clock.now


def test_a_queued_edge_older_than_the_history_is_dropped_when_taken():
    clock = Clock()
    edges = RUN.EdgeQueue(clock=clock)
    edges.arm()
    edges.on_edge()

    clock.now += HISTORY_NS + 1
    assert edges.next_fresh_edge(0.0, HISTORY_NS) is None
    assert edges.counters()["stale_discarded"] == 1


def test_claim_rechecks_the_age_and_counts_a_stale_edge():
    clock = Clock()
    edges = RUN.EdgeQueue(clock=clock)
    edges.arm()
    edge = clock.now

    clock.now += HISTORY_NS
    assert edges.claim(edge, HISTORY_NS) == HISTORY_NS  # exactly the bound is still fresh
    edges.set_busy(False)
    clock.now += 1
    assert edges.claim(edge, HISTORY_NS) is None
    assert edges.counters()["stale_discarded"] == 1


# --- the run --------------------------------------------------------------------------------


def test_every_planned_attempt_has_one_row_with_a_distinct_id(tmp_path, placement):
    clock = Clock()
    edges = ScriptedEdges(clock, after_arm=[0], after_ready=[[0]])

    summary, run_dir, rows = _run(
        tmp_path,
        FakeRadar(clock),
        edges,
        clock,
        soak=2,
        real_ball=2,
        edge_timeout_s=10.0,
        **placement,
    )

    assert [(r["phase"], r["index"]) for r in rows] == [
        ("soak", 0),
        ("soak", 1),
        ("real_ball", 0),
        ("real_ball", 1),
    ]
    assert len({r["attempt_id"] for r in rows}) == 4
    assert rows[3]["attempt_id"] == f"{summary['run_id']}/real_ball/001"
    assert [r["outcome"] for r in rows] == ["captured"] * 4
    assert rows[2]["stimulus"] == "real_ball" and rows[2]["placement_m"] == 1.5
    assert rows[0]["stimulus"] == "none" and rows[0]["edge_ns"] is None
    assert (run_dir / "real_ball_001.l3dump").exists()
    started = json.loads((run_dir / "run_started.json").read_text())
    assert started["planned"] == {"soak": 2, "real_ball": 2, "clap": 0}
    assert started["iq8_scale"] == 128
    assert summary["outcomes"] == {"captured": 4}


def test_each_captured_row_matches_its_dump_file(tmp_path):
    clock = Clock()
    _summary, run_dir, rows = _run(tmp_path, FakeRadar(clock), None, clock, soak=1)

    raw = (run_dir / rows[0]["path"]).read_bytes()
    assert raw == _dump_bytes()
    assert rows[0]["nbytes"] == len(raw)
    assert rows[0]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert rows[0]["timing"]["clock"] == "host_monotonic_ns"
    assert rows[0]["schema"] == "openflight.iwr6843.feasibility_attempt.v2"


def test_the_run_records_the_frozen_config_image_and_script(tmp_path):
    clock = Clock()
    crlf = CFG.replace("\n", "\r\n")  # the hash is the file's bytes, never a decoded copy
    cfg = _write_cfg(tmp_path, crlf, name="crlf.cfg")
    radar = FakeRadar(clock)

    summary, run_dir, _unused_rows = _run(
        tmp_path, radar, None, clock, soak=1, config_path=cfg, note="bench day 1"
    )

    started = json.loads((run_dir / "run_started.json").read_text())
    assert started["config_sha256"] == hashlib.sha256(crlf.encode()).hexdigest()
    assert started["effective_config_sha256"] == started["config_sha256"]
    assert (run_dir / "effective.cfg").read_bytes() == crlf.encode() == radar.sent_config
    assert started["image_sha256"] == IMAGE
    assert started["script_sha256"] == hashlib.sha256(SCRIPT.read_bytes()).hexdigest()
    assert started["schema"] == "openflight.iwr6843.feasibility_run.v2"
    assert started["note"] == "bench day 1"
    for key, value in started.items():
        assert summary[key] == value
    assert summary["capture_plan_line"] == PLAN
    assert summary["pre_history_s"] == pytest.approx(0.027)
    assert summary["responses"] == [["sensorStart", f"sensorStart\r\n{PLAN}\r\nDone\r\n"]]
    assert json.loads((run_dir / "run.json").read_text()) == summary


def test_the_positive_control_sends_scale_16_and_records_it(tmp_path, placement):
    clock = Clock()
    radar = FakeRadar(clock)
    _summary, run_dir, _unused_rows = _run(
        tmp_path,
        radar,
        None,
        clock,
        profile="C16",
        soak=1,
        soak_stimulus="reflector",
        iq8_scale_override=16,
        **placement,
    )

    started = json.loads((run_dir / "run_started.json").read_text())
    assert started["iq8_scale"] == 16
    assert started["config_sha256"] == hashlib.sha256(CFG.encode()).hexdigest()
    effective = CFG.replace("iq8Scale 128", "iq8Scale 16").encode()
    assert radar.sent_config == effective == (run_dir / "effective.cfg").read_bytes()
    assert started["effective_config_sha256"] == hashlib.sha256(effective).hexdigest()
    assert _rows(run_dir)[0]["stimulus"] == "reflector"


def test_an_iq16_profile_records_no_iq8_scale(tmp_path):
    clock = Clock()
    _summary, run_dir, _unused_rows = _run(
        tmp_path,
        FakeRadar(clock),
        None,
        clock,
        soak=1,
        config_path=_write_cfg(tmp_path, IQ16_CFG, "a16.cfg"),
    )

    assert json.loads((run_dir / "run_started.json").read_text())["iq8_scale"] is None


def test_a_failed_dump_is_kept_with_its_error_and_bytes(tmp_path):
    clock = Clock()
    summary, run_dir, rows = _run(tmp_path, FakeRadar(clock, fail_at=2), None, clock, soak=3)

    assert [r["outcome"] for r in rows] == ["captured", "dump_error", "captured"]
    assert "ended early" in rows[1]["error"] and rows[1]["nbytes"] == 5
    assert (run_dir / rows[1]["path"]).read_bytes() == _dump_bytes()[:5]
    assert rows[1]["timing"]["last_byte_ns"] is not None
    assert summary["outcomes"] == {"captured": 2, "dump_error": 1}


def test_a_rejected_request_has_no_bytes_no_file_and_no_timing(tmp_path):
    clock = Clock()
    rejected = RuntimeError("IWR6843 rejected l3dump: Error -1")
    summary, run_dir, rows = _run(
        tmp_path, FakeRadar(clock, raise_at={1: rejected}), None, clock, soak=1
    )

    assert rows[0]["outcome"] == "dump_error" and "rejected l3dump" in rows[0]["error"]
    assert rows[0]["nbytes"] == 0 and rows[0]["path"] is None and rows[0]["sha256"] is None
    assert rows[0]["timing"] is None
    assert not list(run_dir.glob("*.l3dump"))
    assert summary["outcomes"] == {"dump_error": 1}


def test_no_edge_attempts_stay_and_the_run_gives_up_after_two_in_a_row(tmp_path, placement):
    clock = Clock()
    summary, _run_dir, rows = _run(
        tmp_path,
        FakeRadar(clock),
        ScriptedEdges(clock),
        clock,
        soak=0,
        clap=4,
        **placement,
    )

    assert [r["outcome"] for r in rows] == ["no_edge"] * 4
    assert "abandoned" in rows[3]["error"]
    assert summary["outcomes"] == {"no_edge": 4}
    # No request was made, so there is no byte count, not a count of zero.
    assert {r["nbytes"] for r in rows} == {None}
    assert {r["edge_age_at_request_ns"] for r in rows} == {None}


def test_a_soak_phase_edge_is_discarded_at_arming_and_reported(tmp_path, placement):
    clock = Clock()
    edges = ScriptedEdges(clock)
    radar = FakeRadar(clock, on_dump=lambda _n: edges.on_edge())  # a clap during each soak dump

    summary, _run_dir, rows = _run(tmp_path, radar, edges, clock, soak=2, clap=1, **placement)

    assert rows[2]["outcome"] == "no_edge"  # the soak claps never start a triggered capture
    assert summary["edges"]["pre_arm_discarded"] == 2


def test_the_queue_arms_only_after_the_soak_and_a_ready_ring(tmp_path, placement):
    clock = Clock()
    log: list[str] = []
    edges = ScriptedEdges(clock, after_arm=[0], log=log)

    _run(tmp_path, FakeRadar(clock, log=log), edges, clock, soak=2, clap=1, **placement)

    assert log[: log.index("arm") + 1] == ["config", "dump 1", "dump 2", "stats", "arm"]


def test_a_stale_edge_is_never_assigned(tmp_path, placement):
    clock = Clock()
    edges = ScriptedEdges(clock, after_arm=[-28])  # 28 ms old when armed; A's history is 27 ms

    summary, _run_dir, rows = _run(
        tmp_path,
        FakeRadar(clock),
        edges,
        clock,
        soak=0,
        clap=1,
        **placement,
    )

    assert rows[0]["outcome"] == "no_edge"
    assert summary["edges"]["stale_discarded"] == 1


class SlowAfterDequeue(ScriptedEdges):
    """The script is descheduled for 10 ms between taking an edge and writing the request."""

    def next_fresh_edge(self, timeout_s, max_age_ns):
        edge = super().next_fresh_edge(timeout_s, max_age_ns)
        self._clock.now += 10 * MS
        return edge


def test_an_edge_that_ages_past_the_history_before_the_request_is_stale(tmp_path, placement):
    """R-F5: fresh when dequeued (20 ms), stale at the request (30 ms > 27 ms)."""
    clock = Clock()
    radar = FakeRadar(clock)
    announced: list[str] = []
    edges = SlowAfterDequeue(clock, after_arm=[-20])

    summary, _run_dir, rows = _run(
        tmp_path,
        radar,
        edges,
        clock,
        soak=0,
        clap=1,
        announce=announced.append,
        **placement,
    )

    assert radar.requests == 0  # never used for a request
    assert rows[0]["outcome"] == "no_edge" and rows[0]["edge_ns"] is None
    assert summary["edges"]["stale_discarded"] == 1
    assert len(announced) == 2 and "again" in announced[1]  # the owner is asked to swing again


def test_a_stale_edge_at_the_request_is_followed_by_a_fresh_one(tmp_path, placement):
    clock = Clock()
    edges = SlowAfterDequeue(clock, after_arm=[-20])

    def swing_again(message):
        if "again" in message:
            clock.now += 500 * MS  # the owner swings again half a second later
            edges.on_edge()

    summary, _run_dir, rows = _run(
        tmp_path,
        FakeRadar(clock),
        edges,
        clock,
        soak=0,
        clap=1,
        announce=swing_again,
        **placement,
    )

    assert rows[0]["outcome"] == "captured"
    assert rows[0]["edge_age_at_request_ns"] == 10 * MS  # the second edge, never the first
    assert summary["edges"]["stale_discarded"] == 1
    assert summary["edges"]["ringing_discarded"] == 0


def test_the_recorded_edge_ages_are_within_the_history(tmp_path, placement):
    clock = Clock()
    edges = SlowAfterDequeue(clock, after_arm=[-5])

    _summary, _run_dir, rows = _run(
        tmp_path,
        FakeRadar(clock),
        edges,
        clock,
        soak=0,
        clap=1,
        **placement,
    )

    assert rows[0]["edge_age_at_check_ns"] == 15 * MS
    assert rows[0]["edge_age_at_request_ns"] == 15 * MS


def test_edges_during_a_dump_or_the_refill_are_counted_not_queued(tmp_path, placement):
    """R-F6: an edge while the ring refills after a dump is busy, never the next attempt's.

    Swapping ``wait_ring_ready`` and ``set_busy(False)`` queues the refill edge as fresh,
    and the second attempt would capture on it.
    """
    clock = Clock()
    edges = ScriptedEdges(clock, after_arm=[0])
    refill_claps: list[int] = []

    def clap_during_the_dump(_n):
        clock.now += 200 * MS  # past the 0.1 s ringing window, still inside the dump
        edges.on_edge()

    def clap_during_the_refill():
        if radar.requests == 1 and not refill_claps:
            refill_claps.append(clock.now)
            edges.on_edge()  # the clock has just moved through the refill sleep

    radar = FakeRadar(clock, on_dump=clap_during_the_dump)
    summary, _run_dir, rows = _run(
        tmp_path,
        radar,
        edges,
        clock,
        soak=0,
        clap=2,
        sleep_hook=clap_during_the_refill,
        **placement,
    )

    assert [r["outcome"] for r in rows] == ["captured", "no_edge"]
    assert summary["edges"]["busy_discarded"] == 2
    assert radar.requests == 1 and len(refill_claps) == 1


def test_busy_clears_only_after_the_ring_is_ready_again(tmp_path, placement):
    """R-F6, by order: dump, ready check, then the queue reopens."""
    clock = Clock()
    log: list[str] = []
    edges = ScriptedEdges(clock, after_arm=[0], log=log)

    _run(tmp_path, FakeRadar(clock, log=log), edges, clock, soak=0, clap=1, **placement)

    assert log[log.index("arm") :] == [
        "arm",
        "dump 1",
        "stats",
        "busy False",
        "stats",
        "perfstats",
        "l3bench",
    ]


def test_the_fresh_edge_age_is_recorded(tmp_path, placement):
    clock = Clock()
    _summary, _run_dir, rows = _run(
        tmp_path,
        FakeRadar(clock),
        ScriptedEdges(clock, after_arm=[-5]),
        clock,
        soak=0,
        clap=1,
        **placement,
    )

    assert rows[0]["edge_age_at_request_ns"] == 5 * MS
    assert rows[0]["edge_ns"] == rows[0]["timing"]["request_written_ns"] - 5 * MS


def test_a_ring_that_never_becomes_ready_is_not_armed(tmp_path, placement):
    clock = Clock()
    summary, _run_dir, rows = _run(
        tmp_path,
        FakeRadar(clock, active=False),
        ScriptedEdges(clock, after_arm=[0]),
        clock,
        soak=0,
        clap=2,
        **placement,
    )

    assert [r["outcome"] for r in rows] == ["not_armed", "not_armed"]
    assert summary["outcomes"] == {"not_armed": 2}


def test_a_ring_not_ready_after_a_dump_leaves_the_rest_not_armed(tmp_path, placement):
    clock = Clock()
    edges = ScriptedEdges(clock, after_arm=[0], after_ready=[[0]])
    radar = FakeRadar(clock, active=lambda requests: requests == 0)

    summary, _run_dir, rows = _run(tmp_path, radar, edges, clock, soak=0, clap=3, **placement)

    assert [r["outcome"] for r in rows] == ["captured", "not_armed", "not_armed"]
    assert "after the previous dump" in rows[1]["error"]
    assert radar.requests == 1
    assert summary["outcomes"] == {"captured": 1, "not_armed": 2}


def test_a_plan_line_missing_from_the_replies_leaves_triggered_attempts_not_armed(
    tmp_path, placement
):
    clock = Clock()
    radar = FakeRadar(clock)
    radar.send_config = lambda _path, responses: responses.append(("sensorStart", "Done"))

    summary, _run_dir, rows = _run(
        tmp_path, radar, ScriptedEdges(clock, after_arm=[0]), clock, soak=1, clap=1, **placement
    )

    assert [r["outcome"] for r in rows] == ["captured", "not_armed"]
    assert "Capture plan" in rows[1]["error"]
    assert summary["capture_plan_line"] is None and summary["pre_history_s"] is None


def test_ready_is_announced_before_each_triggered_attempt(tmp_path, placement):
    clock = Clock()
    announced: list[str] = []
    edges = ScriptedEdges(clock, after_arm=[0], after_ready=[[0]])

    _run(
        tmp_path,
        FakeRadar(clock),
        edges,
        clock,
        soak=1,
        real_ball=1,
        clap=1,
        announce=announced.append,
        **placement,
    )

    assert announced == [
        "READY: real_ball 1 of 1 at pilot-tee (1.5 m)",
        "READY: clap 1 of 1 at pilot-tee (1.5 m)",
    ]


def test_triggered_attempts_need_a_taped_placement(tmp_path):
    clock = Clock()
    with pytest.raises(ValueError, match="placement"):
        _run(tmp_path, FakeRadar(clock), ScriptedEdges(clock), clock, soak=0, real_ball=1)
    assert not (tmp_path / "runs").exists()


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"soak": 0, "real_ball": 1, "edges": None}, "edge queue"),
        ({"soak": -1}, "attempt counts"),
        ({"soak": 0}, "no attempts"),
        ({"soak_stimulus": "reflector", "placement_label": None}, "placement"),
        ({"soak_stimulus": "reflector", "placement_m": float("nan")}, "placement"),
        ({"soak_stimulus": "reflector", "placement_m": 0.0}, "placement"),
        ({"soak_stimulus": "reflector", "placement_label": " "}, "placement"),
        ({"soak_stimulus": "wind"}, "soak stimulus"),
        ({"image_sha256": "AB" * 32}, "image"),
        ({"image_sha256": "ab" * 31}, "image"),
        ({"profile": "C16"}, "C16"),
        ({"profile": "A", "iq8_scale_override": 16}, "frozen"),
        ({"profile": "../x"}, "profile"),
        ({"run_id": "r1"}, "run ID"),
        ({"max_consecutive_no_edge": 0}, "max_consecutive_no_edge"),
        ({"edge_timeout_s": -1.0}, "edge_timeout_s"),
        ({"soak_gap_s": -1.0}, "soak_gap_s"),
    ],
)
def test_a_bad_invocation_is_refused_before_any_run_directory_exists(
    tmp_path, placement, change, message
):
    """A refused invocation leaves nothing behind for Task 8 to count as an aborted run."""
    clock = Clock()
    kwargs = {**placement, "edges": ScriptedEdges(clock), **change}
    radar = FakeRadar(clock)

    with pytest.raises(ValueError, match=message):
        RUN.run_profile(radar, **_kwargs(tmp_path, clock, **kwargs))

    assert not (tmp_path / "runs").exists()
    assert radar.log == []  # nothing was sent to the radar


def test_an_override_on_an_iq16_config_is_refused_before_any_run_directory(tmp_path):
    clock = Clock()
    radar = FakeRadar(clock)
    with pytest.raises(ValueError, match="no iq8Scale line"):
        _run(
            tmp_path,
            radar,
            None,
            clock,
            profile="smoke",
            soak=1,
            iq8_scale_override=16,
            config_path=_write_cfg(tmp_path, IQ16_CFG, "a16.cfg"),
        )
    assert not (tmp_path / "runs").exists() and radar.log == []


def test_old_firmware_perfstats_is_recorded_as_missing_with_its_reason(tmp_path):
    clock = Clock()
    summary, _run_dir, _unused_rows = _run(
        tmp_path, FakeRadar(clock, perf_error=True), None, clock, soak=1
    )

    assert summary["perf"] is None
    assert "firmware without W4a evidence" in summary["perf_error"]
    assert summary["perf_text"] == OLD_FIRMWARE_PERF_TEXT
    assert summary["bench"]["cycles"] == 4_200_000
    assert summary["bench_text"] == BENCH_TEXT


def test_unmeasured_perf_values_stay_none_with_their_reasons(tmp_path):
    """Task 6's parser output is stored unchanged: None is never coerced to 0."""
    clock = Clock()
    summary, run_dir, _unused_rows = _run(
        tmp_path, FakeRadar(clock, perf_text=NO_SCAN_PERF_TEXT), None, clock, soak=1
    )

    perf = summary["perf"]
    assert perf == parse_perf_stats(NO_SCAN_PERF_TEXT) and summary["perf_error"] is None
    assert perf["rearm_max"] is None and perf["unavailable"]["rearm_max"] == "no samples"
    assert perf["scan_max"] is None and perf["unavailable"]["scan_max"] == "not_in_build"
    assert perf["stack_init"] is None and perf["unavailable"]["stack_init"] == "not_created"
    stored = json.loads((run_dir / "run.json").read_text())
    assert stored["perf"] == perf and stored["perf_text"] == NO_SCAN_PERF_TEXT


def test_a_refused_perfstats_reply_is_kept_raw_with_its_reason(tmp_path):
    """A strict refusal on real hardware must not lose the reply (Task 9 judges the parser)."""
    clock = Clock()
    summary, run_dir, _unused_rows = _run(
        tmp_path, FakeRadar(clock, perf_text=REFUSED_PERF_TEXT), None, clock, soak=1
    )

    assert summary["perf"] is None
    assert "rearm_max=41000 beside rearms=0" in summary["perf_error"]
    stored = json.loads((run_dir / "run.json").read_text())
    assert stored["perf_text"] == REFUSED_PERF_TEXT
    assert (
        "stacks init=2900/8192 ctrl=700/3072 rearm=600/2048 cli=1500/4096 hwi=900/2048"
        in (stored["perf_text"])
    )


def test_a_refused_l3bench_reply_is_kept_raw_with_its_reason(tmp_path):
    clock = Clock()
    garbled = "l3bench\r\nbench samples=1\r\nDone\r\n"
    summary, _run_dir, _unused_rows = _run(
        tmp_path, FakeRadar(clock, bench_text=garbled), None, clock, soak=1
    )

    assert summary["bench"] is None and "no l3bench reply" in summary["bench_error"]
    assert summary["bench_text"] == garbled


def test_a_sensor_that_will_not_stop_is_recorded_and_l3bench_is_not_run(tmp_path):
    clock = Clock()
    summary, run_dir, _unused_rows = _run(
        tmp_path, FakeRadar(clock, stop_fails=True), None, clock, soak=1
    )

    assert "remained active" in summary["stop_error"]
    assert summary["bench"] is None and summary["bench_text"] is None
    assert "sensor did not stop" in summary["bench_error"]
    assert (run_dir / "run.json").exists()


def test_an_interrupted_dump_keeps_its_row_and_leaves_an_aborted_run(tmp_path):
    clock = Clock()
    radar = FakeRadar(clock, raise_at={2: KeyboardInterrupt()})

    with pytest.raises(KeyboardInterrupt):
        RUN.run_profile(radar, **_kwargs(tmp_path, clock, None, soak=3))

    run_dir = _only_run_dir(tmp_path)
    rows = _rows(run_dir)
    assert [r["outcome"] for r in rows] == ["captured", "dump_error"]
    assert "KeyboardInterrupt" in rows[1]["error"]
    assert not (run_dir / "run.json").exists()
    aborted = json.loads((run_dir / "run_aborted.json").read_text())
    assert aborted["run_id"] == rows[0]["run_id"] and "KeyboardInterrupt" in aborted["error"]


def test_a_rejected_config_leaves_an_aborted_run_with_its_reason(tmp_path):
    clock = Clock()
    radar = FakeRadar(clock)

    def reject(_path, responses):
        responses.append(("frameCfg 0 2 12 0 3 1 0", "Error -1"))
        raise RuntimeError("config rejected: 'frameCfg 0 2 12 0 3 1 0': Error -1")

    radar.send_config = reject
    with pytest.raises(RuntimeError, match="config rejected"):
        RUN.run_profile(radar, **_kwargs(tmp_path, clock, None, soak=1))

    run_dir = _only_run_dir(tmp_path)
    assert (run_dir / "run_started.json").exists() and not (run_dir / "run.json").exists()
    assert _rows(run_dir) == []
    aborted = json.loads((run_dir / "run_aborted.json").read_text())
    assert "config rejected" in aborted["error"]
    assert aborted["responses"] == [["frameCfg 0 2 12 0 3 1 0", "Error -1"]]


def test_the_attempt_row_writer_is_public_for_the_gate_checker():
    """Task 8 builds its made-up runs with this writer (R-D5)."""
    timing = DumpTransferTiming(request_written_ns=1_000, first_byte_ns=2_000)
    row = RUN.attempt_row(
        run_id="20261011T120000Z-0123abcd",
        profile="A",
        phase="real_ball",
        index=3,
        stimulus="real_ball",
        placement=("pilot-tee", 1.5),
        outcome="captured",
        path="real_ball_003.l3dump",
        raw=b"abc",
        timing=timing,
        edge_ns=400,
        edge_age_at_check_ns=500,
    )

    assert row == {
        "schema": "openflight.iwr6843.feasibility_attempt.v2",
        "run_id": "20261011T120000Z-0123abcd",
        "attempt_id": "20261011T120000Z-0123abcd/real_ball/003",
        "profile": "A",
        "phase": "real_ball",
        "index": 3,
        "stimulus": "real_ball",
        "placement_label": "pilot-tee",
        "placement_m": 1.5,
        "outcome": "captured",
        "path": "real_ball_003.l3dump",
        "nbytes": 3,
        "sha256": hashlib.sha256(b"abc").hexdigest(),
        "edge_ns": 400,
        "edge_age_at_check_ns": 500,
        "edge_age_at_request_ns": 600,
        "timing": timing.to_dict(),
        "error": None,
    }


def test_edges_after_the_triggered_phase_are_counted_after_the_run(tmp_path, placement):
    """A clap left in the queue, or during close-out, is counted, never left uncounted."""
    clock = Clock()
    edges = ScriptedEdges(clock, after_arm=[0], after_ready=[[0]])  # one left over after the last

    def clap_during_close_out(line):
        if line == "perfstats":
            clock.now += 200 * MS
            edges.on_edge()

    summary, _run_dir, rows = _run(
        tmp_path,
        FakeRadar(clock, on_cmd=clap_during_close_out),
        edges,
        clock,
        soak=0,
        clap=1,
        **placement,
    )

    assert [r["outcome"] for r in rows] == ["captured"]
    assert summary["edges"]["after_run_discarded"] == 2
    assert edges.next_fresh_edge(0.0, 10_000 * MS) is None


def test_the_queue_closes_after_an_abandoned_triggered_phase(tmp_path, placement):
    clock = Clock()
    edges = ScriptedEdges(clock)

    summary, _run_dir, rows = _run(
        tmp_path,
        FakeRadar(clock, on_cmd=lambda _line: edges.on_edge()),
        edges,
        clock,
        soak=0,
        clap=3,
        **placement,
    )

    assert [r["outcome"] for r in rows] == ["no_edge"] * 3
    assert summary["edges"]["after_run_discarded"] == 1


class InterruptedWait(ScriptedEdges):
    """The owner presses Ctrl-C while the script waits for a swing."""

    def next_fresh_edge(self, timeout_s, max_age_ns):
        raise KeyboardInterrupt


def test_ctrl_c_while_waiting_for_an_edge_keeps_the_attempt_row(tmp_path, placement):
    clock = Clock()

    with pytest.raises(KeyboardInterrupt):
        RUN.run_profile(
            FakeRadar(clock),
            **_kwargs(tmp_path, clock, InterruptedWait(clock), soak=1, clap=2, **placement),
        )

    run_dir = _only_run_dir(tmp_path)
    rows = _rows(run_dir)
    assert [r["outcome"] for r in rows] == ["captured", "no_edge"]
    assert "run aborted while waiting" in rows[1]["error"]
    assert "KeyboardInterrupt" in rows[1]["error"] and rows[1]["nbytes"] is None
    assert not (run_dir / "run.json").exists() and (run_dir / "run_aborted.json").exists()


# --- against the simulated firmware, through the real driver --------------------------------


class PlanFirmware(PerfFirmwareModel):
    """The firmware model, plus the W4a ``Capture plan:`` line after ``sensorStart``."""

    def receive(self, data: bytes) -> None:
        super().receive(data)
        if data.strip() == b"sensorStart":
            self.pending = self.pending.replace(
                b"sensorStart\r\nDone", f"sensorStart\r\n{PLAN}\r\nDone".encode()
            )


def test_a_run_through_the_real_driver_on_the_simulated_firmware(tmp_path, monkeypatch, placement):
    clock = FakeClock()
    firmware = PlanFirmware(_dump_bytes(), clock, restart_s=0.5)
    monkeypatch.setattr(driver, "time", clock.module())
    monkeypatch.setattr(driver, "IWR6843DeviceLock", NoLock)
    monkeypatch.setattr(driver, "open_port", lambda *_args, **_kwargs: FakePort(firmware))
    radar = IWR6843Radar(port="/dev/serial/by-id/iwr-if00-port0")
    edges = RUN.EdgeQueue(clock=clock.monotonic_ns)

    def swing(message):
        assert message.startswith("READY")
        edges.on_edge()

    summary = RUN.run_profile(
        radar,
        profile="A",
        config_path=_write_cfg(tmp_path),
        out_root=tmp_path / "runs",
        image_sha256=IMAGE,
        soak=2,
        real_ball=1,
        clap=0,
        edges=edges,
        sleep=clock.sleep,
        edge_timeout_s=0.0,
        announce=swing,
        **placement,
    )

    run_dir = _only_run_dir(tmp_path)
    rows = _rows(run_dir)
    assert summary["outcomes"] == {"captured": 3}
    assert summary["capture_plan_line"] == PLAN
    assert all((run_dir / r["path"]).read_bytes() == firmware.raw for r in rows)
    assert 0 <= rows[2]["edge_age_at_request_ns"] <= HISTORY_NS
    assert summary["perf"]["stack_hwi"] == {"used": 900, "size": 2048}
    assert summary["bench"]["samples"] == 183168
    assert "stacks init=2900/8192" in summary["perf_text"]
    assert "bench samples=183168" in summary["bench_text"]
    assert firmware.dumps == 3 and not firmware.active
    assert firmware.commands[-3:] == ["sensorStop", "stats", "l3bench"]
    assert summary["edges"] == {
        "pre_arm_discarded": 0,
        "busy_discarded": 0,
        "stale_discarded": 0,
        "ringing_discarded": 0,
        "after_run_discarded": 0,
    }


# --- main, with a fake radar and a fake GPIO -----------------------------------------------


class FakeButton:
    """gpiozero ``Button`` double; remembers every instance."""

    instances: list[FakeButton] = []

    def __init__(self, pin, pull_up, bounce_time):
        self.args = (pin, pull_up, bounce_time)
        self.when_pressed = None
        self.closed = False
        FakeButton.instances.append(self)

    def close(self):
        self.closed = True


def _patch_main(monkeypatch, radar):
    calls: list[str] = []
    FakeButton.instances = []
    gpiozero = types.ModuleType("gpiozero")

    def button(*args, **kwargs):
        calls.append("button")
        return FakeButton(*args, **kwargs)

    gpiozero.Button = button
    monkeypatch.setitem(sys.modules, "gpiozero", gpiozero)
    monkeypatch.setattr(
        "openflight.gpio_factory.ensure_lgpio_pin_factory", lambda: calls.append("factory")
    )
    closed: list[bool] = []
    radar.close = lambda: closed.append(True)
    ports: list[str | None] = []

    def open_radar(port=None):
        ports.append(port)
        return radar

    monkeypatch.setattr(RUN, "IWR6843Radar", open_radar)
    return calls, closed, ports


def test_main_wires_the_gpio_prints_ready_and_closes_everything(tmp_path, monkeypatch, capsys):
    radar = FakeRadar(Clock())
    radar.on_dump = lambda _n: FakeButton.instances[0].when_pressed()  # a clap during the soak
    calls, closed, ports = _patch_main(monkeypatch, radar)

    code = RUN.main(
        [
            "--profile",
            "smoke",
            "--config",
            str(_write_cfg(tmp_path)),
            "--out-root",
            str(tmp_path / "runs"),
            "--image-sha256",
            IMAGE,
            "--soak",
            "1",
            "--claps",
            "1",
            "--placement-label",
            "pilot-tee",
            "--placement-m",
            "1.5",
            "--soak-gap-s",
            "0",
            "--edge-timeout-s",
            "0",
            "--port",
            "/dev/ttyUSB0",
            "--gpio-pin",
            "27",
        ]
    )

    out = capsys.readouterr().out
    assert code == 1  # the clap attempt had no edge
    assert "READY: clap 1 of 1 at pilot-tee (1.5 m)" in out
    assert calls == ["factory", "button"]
    assert FakeButton.instances[0].args == (27, False, None)
    assert FakeButton.instances[0].closed and closed == [True] and ports == ["/dev/ttyUSB0"]
    summary = json.loads((_only_run_dir(tmp_path) / "run.json").read_text())
    assert summary["edges"]["pre_arm_discarded"] == 1
    assert summary["outcomes"] == {"captured": 1, "no_edge": 1}
    assert str(_only_run_dir(tmp_path)) in out


def test_main_without_triggered_attempts_never_opens_the_gpio(tmp_path, monkeypatch, capsys):
    clock = Clock()
    radar = FakeRadar(clock)
    calls, closed, _ports = _patch_main(monkeypatch, radar)

    code = RUN.main(
        [
            "--profile",
            "smoke",
            "--config",
            str(_write_cfg(tmp_path)),
            "--out-root",
            str(tmp_path / "runs"),
            "--image-sha256",
            IMAGE,
            "--soak",
            "2",
            "--soak-gap-s",
            "0",
        ]
    )

    assert code == 0 and not calls and closed == [True]
    assert '"captured": 2' in capsys.readouterr().out


def test_main_closes_the_radar_and_gpio_when_the_run_aborts(tmp_path, monkeypatch):
    clock = Clock()
    radar = FakeRadar(clock, raise_at={1: KeyboardInterrupt()})
    _calls, closed, _ports = _patch_main(monkeypatch, radar)

    with pytest.raises(KeyboardInterrupt):
        RUN.main(
            [
                "--profile",
                "smoke",
                "--config",
                str(_write_cfg(tmp_path)),
                "--out-root",
                str(tmp_path / "runs"),
                "--image-sha256",
                IMAGE,
                "--soak",
                "1",
                "--claps",
                "1",
                "--placement-label",
                "pilot-tee",
                "--placement-m",
                "1.5",
                "--soak-gap-s",
                "0",
            ]
        )

    assert closed == [True] and FakeButton.instances[0].closed


@pytest.mark.parametrize(
    ("radar_kwargs", "key"),
    [
        ({"perf_error": True}, "perf_error"),
        ({"stop_fails": True}, "stop_error"),
        ({"bench_text": "l3bench\r\nDone\r\n"}, "bench_error"),
    ],
)
def test_main_fails_when_a_close_out_reading_is_missing(
    tmp_path, monkeypatch, capsys, radar_kwargs, key
):
    radar = FakeRadar(Clock(), **radar_kwargs)
    _patch_main(monkeypatch, radar)

    code = RUN.main(
        [
            "--profile",
            "smoke",
            "--config",
            str(_write_cfg(tmp_path)),
            "--out-root",
            str(tmp_path / "runs"),
            "--image-sha256",
            IMAGE,
            "--soak",
            "1",
            "--soak-gap-s",
            "0",
        ]
    )

    assert code == 1
    summary = json.loads((_only_run_dir(tmp_path) / "run.json").read_text())
    assert summary["outcomes"] == {"captured": 1} and summary[key]
    assert f'"{key}": "' in capsys.readouterr().out


# --- final review: one source for the raw-reply windows (DRY) ------------------------------


def test_the_script_sends_its_raw_replies_with_the_drivers_own_windows(tmp_path):
    """The script keeps raw replies by sending perfstats/l3bench itself, with the windows
    perf_diagnostics.perf_stats and bench_kernel use; it defines no copy of its own."""
    clock, windows = Clock(), {}

    class WindowRadar(FakeRadar):
        def cmd(self, line, window=1.5):
            windows[line] = window
            return super().cmd(line, window)

    _run(tmp_path, WindowRadar(clock), None, clock, soak=1)

    assert windows == {
        "perfstats": perf_diagnostics.PERF_REPLY_WINDOW_S,
        "l3bench": perf_diagnostics.BENCH_REPLY_WINDOW_S,
    }
    assert not hasattr(RUN, "PERF_WINDOW_S") and not hasattr(RUN, "BENCH_WINDOW_S")

"""Synthetic self-trigger lifecycle checks; no physical accuracy claims."""

import base64
import json
import os
import struct
import sys
import threading
import zlib
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from run_joint_clock import load_package

load_package(Path(os.environ["OPENFLIGHT_CLOCK_PACKAGE"]))
from joint_clock_active import CONFIGURATION, fields_for  # noqa: E402
from joint_shot_clock import (  # noqa: E402
    PROFILE,
    TRIGGER_COMMANDS,
    CandidateUnavailable,
    ShotCollector,
)
from test_joint_clock_active import WIRE  # noqa: E402
from usb_collector import ExchangeError  # noqa: E402

from openflight.iwr6843 import driver  # noqa: E402
from openflight.iwr6843.dump import HEADER  # noqa: E402


class ShotPort(WIRE.Port):
    def __init__(self, clock, chunk):
        super().__init__(clock, chunk=chunk)
        self.allowed = ShotCollector.LIFECYCLE_COMMANDS
        self.double = ShotCollector.DOUBLE_COMPLETION_COMMANDS
        self.ball_bin = 51
        self.ball_state, self.ball_reason = "locked", "none"
        self.ball_locks, self.ball_releases = 1, 0
        self.active = self.enabled = False
        self.generation, self.shot = 0, 1
        self.completed_shots = 0
        struct.pack_into("<I", self.result, 4, self.shot)
        self.dump_payload = HEADER.pack(b"ILD1", 1, 1, 1, 1, 1, 1, 0, 0, 0, 3000) + b"\0" * 4
        self.dump_reply = self.dump_payload + b"Done\n" + WIRE.PROMPT

    def complete_shot(self):
        # l3_dump.c builds each result with ++gShotId; rearm never resets that ID.
        self.completed_shots += 1
        self.shot = self.completed_shots
        struct.pack_into("<I", self.result, 4, self.shot)
        self.active, self.held = False, True

    def reset_input_buffer(self):
        assert not self.pending

    def write(self, data):
        command = data.decode().strip()
        if command == "ball status":
            assert not self.pending
            self.writes.append(data)
            self.pending = (
                f"ball state={self.ball_state} follow=1 bin={self.ball_bin} reason={self.ball_reason}\n"
                "balldbg centroid=51.00 width=1 persistence=0.75 reject=none\n"
                "ballangle valid=0\nDone\nDone\nl3dump:/>"
            ).encode()
            return len(data)
        if command == "l3dump":
            assert not self.pending
            self.writes.append(data)
            self.pending = self.dump_reply
            # rc_reset runs on dump/release rearm and sensorStart, not sensorStop.
            self.generation += 1
            self.active, self.held = True, False
            return len(data)
        if command in self.allowed or command == "stats":
            assert not self.pending
            self.writes.append(data)
            if command == "sensorStart":
                self.active, self.enabled, self.held = True, False, False
                self.generation += 1
            elif command == "sensorStop":
                self.active = False
            elif command.startswith("triggerCfg"):
                self.enabled = command.endswith(" 1")
            if command == "stats":
                self.pending = (
                    f"frames={101 if self.generation else 0} active={int(self.active)} rf_faults=0 hwa_missed=0\n"
                    f"trig phase=ARMED enabled={int(self.enabled)} latched={int(self.held)}\n"
                    f"ball state={self.ball_state} follow=1 bin={self.ball_bin} reason={self.ball_reason} "
                    f"locks={self.ball_locks} releases={self.ball_releases} window=20+53 ratio=0.00 confidence=0.00\nDone\nl3dump:/>"
                ).encode()
            else:
                count = 2 if command in self.double else 1
                self.pending = b"Done\n" * count + WIRE.PROMPT
            return len(data)
        count = super().write(data)
        if command == "triggerLog result":
            self.pending = self.pending.replace(
                b"result shot=7", f"result shot={self.shot}".encode()
            )
        elif command.startswith("triggerLog resultClock"):
            values = list(WIRE.PACKET.unpack(self.pending[: WIRE.PACKET.size]))
            values[1 + WIRE.FIELDS.index("shot_id")] = self.shot
            values[1 + WIRE.FIELDS.index("generation")] = self.generation
            packet = WIRE.PACKET.pack(*values)
            self.pending = (
                packet[:-4]
                + struct.pack("<I", zlib.crc32(packet[:-4]))
                + self.pending[WIRE.PACKET.size :]
            )
        return count


def wire(chunk=65536, **plan):
    clock = WIRE.Clock()
    port = ShotPort(clock, chunk)
    tokens = iter(f"{value:016x}" for value in range(1, 1000))
    collector = ShotCollector(
        port,
        build=WIRE.BUILD,
        host_boot="synthetic-boot",
        now=clock,
        nonce=lambda: next(tokens),
        sleep=lambda delay: setattr(clock, "ns", clock.ns + round(delay * 1e9)),
        **plan,
    )
    port.allowed = collector.LIFECYCLE_COMMANDS
    port.double = collector.DOUBLE_COMPLETION_COMMANDS
    collector.establish_fresh_runtime()
    return collector, port


@pytest.mark.parametrize("chunk", [1, 65536])
def test_ball_status_retains_real_framing_and_preceding_non_atomic_stats(chunk):
    collector, port = wire(chunk)
    collector.begin()
    observed = []
    index, state_seen = collector.wait_ball(
        lambda _phase, state: observed.append(state), lambda: False
    )
    rows = [row for row in collector.rows if row["command"] == "ball status"]
    assert len(rows) == 1
    row = rows[0]
    assert row["expected_completions"] == 2 and not row["error"]
    assert b"balldbg centroid=51.00" in base64.b64decode(row["received_b64"])
    context = row["ball_status_context"]
    stats = collector.rows[context["preceding_stats_exchange_index"]]
    assert stats["command"] == "stats" and stats["after_ns"] <= row["before_ns"]
    assert context["preceding_stats_state"] == state_seen == observed[-1]
    assert context["non_atomic"] is True and context["physical_timing_qualified"] is False
    assert index == 51 and not port.pending
    before_arm = len(port.writes)
    collector.arm(index)
    assert port.writes[before_arm : before_arm + 2] == [b"stats\n", b"triggerCfg 51 1 1\n"]


@pytest.mark.parametrize("partial", ["one_completion", "timeout"])
def test_partial_ball_status_poisoning_retains_annotated_reply_without_cleanup(
    monkeypatch, partial
):
    collector, port = wire()
    collector.begin()
    original = port.write

    def broken_status(data):
        count = original(data)
        if data == b"ball status\n":
            port.pending = (
                port.pending.replace(b"Done\nDone\n", b"Done\n")
                if partial == "one_completion"
                else b"balldbg incomplete"
            )
        return count

    monkeypatch.setattr(port, "write", broken_status)
    with pytest.raises(ExchangeError):
        collector.wait_ball(lambda *_: None, lambda: False)
    row = collector.rows[-1]
    assert row["command"] == "ball status" and row["error"]
    assert row["ball_status_context"]["preceding_stats_state"]["ball"]["state"] == "locked"
    assert base64.b64decode(row["received_b64"])
    assert collector.failed and collector.observed_ball is None
    writes = list(port.writes)
    collector.finish()
    assert port.writes == writes


def test_ball_status_changes_are_capped_but_fresh_arm_check_and_next_begin_remain():
    collector, port = wire()
    collector.begin()
    port.ball_state = "waiting"
    collector.deadline_ns = collector.now() + 3_000_000_000

    def changing(_phase, _snapshot):
        port.ball_reason = "none" if port.ball_reason == "no_delta" else "no_delta"

    with pytest.raises(TimeoutError):
        collector.wait_ball(changing, lambda: False)
    assert port.writes.count(b"ball status\n") == 16
    port.ball_state, port.ball_reason = "locked", "none"
    collector.deadline_ns = collector.now() + 500_000_000
    index, _ = collector.wait_ball(lambda *_: None, lambda: False)
    assert port.writes.count(b"ball status\n") == 16
    port.ball_locks, port.ball_releases = 2, 1
    with pytest.raises(CandidateUnavailable):
        collector.arm(index)
    assert b"triggerCfg 51 1 1\n" not in port.writes
    collector.finish()
    collector.begin()
    collector.wait_ball(lambda *_: None, lambda: False)
    assert port.writes.count(b"ball status\n") == 17


def test_unchanged_ball_state_does_not_repeat_status_reads():
    collector, port = wire()
    collector.begin()
    port.ball_state = "waiting"
    collector.deadline_ns = collector.now() + 500_000_000
    with pytest.raises(TimeoutError):
        collector.wait_ball(lambda *_: None, lambda: False)
    assert port.writes.count(b"ball status\n") == 1
    assert port.writes.count(b"stats\n") > 1


def test_stop_after_stats_prevents_diagnostic_and_candidate_acceptance():
    collector, port = wire()
    collector.begin()
    stopped = False

    def stop(_phase, _state):
        nonlocal stopped
        stopped = True

    with pytest.raises(InterruptedError):
        collector.wait_ball(stop, lambda: stopped)
    assert b"ball status\n" not in port.writes
    assert collector.observed_ball is None and not collector.failed


def test_diagnostic_never_reads_enabled_trigger_or_armed_result():
    collector, port = wire()
    collector.begin()
    index, _ = collector.wait_ball(lambda *_: None, lambda: False)
    assert port.writes.count(b"ball status\n") == 1
    collector.arm(index)
    port.active, port.held = False, True
    assert collector.wait_result(lambda *_: None, lambda: False)["record"]["shot_id"] == 1
    assert port.writes.count(b"ball status\n") == 1
    collector.finish()
    collector.begin()
    port.enabled = True
    collector.wait_ball(lambda *_: None, lambda: False)
    assert port.writes.count(b"ball status\n") == 1


def test_candidate_change_during_diagnostic_still_refused_by_fresh_arm(monkeypatch):
    collector, port = wire()
    collector.begin()
    original = port.write

    def lost_during_status(data):
        count = original(data)
        if data == b"ball status\n":
            port.ball_state, port.ball_reason = "waiting", "gone"
        return count

    monkeypatch.setattr(port, "write", lost_during_status)
    index, _ = collector.wait_ball(lambda *_: None, lambda: False)
    with pytest.raises(CandidateUnavailable):
        collector.arm(index)
    assert port.writes.count(b"ball status\n") == 1
    assert b"triggerCfg 51 1 1\n" not in port.writes


def test_ball_scan_stays_outside_bounded_command_scope():
    collector, port = wire()
    with pytest.raises(ValueError, match="outside bounded"):
        collector.command("ball scan 20 53")
    assert b"ball scan 20 53\n" not in port.writes and not collector.failed


@pytest.mark.parametrize(
    "ball",
    [
        "state=waiting follow=1 bin=0",
        "state=locked follow=1 bin=21",
        "state=locked follow=0 bin=20",
    ],
)
def test_arm_rechecks_the_same_current_ball_lock_before_enabling(monkeypatch, ball):
    collector, port = wire()
    port.ball_bin = 20
    collector.begin()
    index, _ = collector.wait_ball(lambda *_: None, lambda: False)
    assert index == 20
    original = port.write

    def changed_ball(data):
        count = original(data)
        if data == b"stats\n":
            port.pending = port.pending.replace(
                b"ball state=locked follow=1 bin=20", b"ball " + ball.encode()
            )
        return count

    monkeypatch.setattr(port, "write", changed_ball)
    before = len(port.writes)
    with pytest.raises(ValueError, match="(?i)ball|lock"):
        collector.arm(index)
    assert b"triggerCfg 20 1 1\n" not in port.writes[before:]


def test_initial_gone_lock_waits_for_healthy_candidate_without_a_confidence_floor():
    collector, port = wire()
    port.ball_reason = "gone"
    collector.begin()
    seen = []

    def observe(_phase, snapshot):
        seen.append(snapshot["ball"]["reason"])
        assert collector.observed_ball is None
        port.ball_reason = "none"

    index, evidence = collector.wait_ball(observe, lambda: False)
    assert seen == ["gone", "none"] and index == 51
    assert evidence["ball"]["ratio"] == evidence["ball"]["confidence"] == "0.00"
    assert not any(
        command in port.writes for command in (b"triggerCfg 20 1 1\n", b"triggerCfg 51 1 1\n")
    )


def test_only_gone_candidate_expires_without_preparing_or_enabling():
    collector, port = wire()
    collector.begin()
    port.ball_reason = "gone"
    collector.deadline_ns = collector.now() + 100_000_000
    with pytest.raises(TimeoutError):
        collector.wait_ball(lambda *_: None, lambda: False)
    assert collector.observed_ball is None
    assert b"triggerCfg 51 1 1\n" not in port.writes


def test_loss_and_reacquisition_at_the_same_bin_does_not_reuse_an_old_lock():
    collector, port = wire()
    collector.begin()
    index, _ = collector.wait_ball(lambda *_: None, lambda: False)
    port.ball_locks, port.ball_releases = 2, 1
    before = len(port.writes)
    with pytest.raises(ValueError, match="(?i)ball|lock"):
        collector.arm(index)
    assert f"triggerCfg {index} 1 1\n".encode() not in port.writes[before:]


@pytest.mark.parametrize("fault", ["gone", "epoch"])
def test_pre_arm_candidate_loss_has_specific_recoverable_classification(fault):
    collector, port = wire()
    collector.begin()
    index, _ = collector.wait_ball(lambda *_: None, lambda: False)
    if fault == "gone":
        port.ball_reason = "gone"
    else:
        port.ball_locks, port.ball_releases = 2, 1
    with pytest.raises(ValueError) as error:
        collector.arm(index)
    assert type(error.value) is CandidateUnavailable


def test_unknown_candidate_reason_remains_a_terminal_contract_error():
    collector, port = wire()
    collector.begin()
    index, _ = collector.wait_ball(lambda *_: None, lambda: False)
    port.ball_reason = "unknown_reason"
    with pytest.raises(ValueError) as error:
        collector.arm(index)
    assert type(error.value) is ValueError


def test_accepted_stop_between_profile_commands_prevents_rf_start_without_poisoning():
    collector, port = wire()
    boundaries = []

    def stopped():
        boundaries.append(True)
        if len(boundaries) == 3:
            raise InterruptedError("accepted stop")

    collector.command_boundary = stopped
    with pytest.raises(InterruptedError, match="accepted stop"):
        collector.begin()
    assert b"sensorStart\n" not in port.writes
    assert not collector.failed and not port.active


def test_cancelled_exchange_boundary_does_not_write_or_poison_uart():
    collector, port = wire()
    before = len(port.writes)

    def stopped():
        raise InterruptedError("accepted stop")

    collector.command_boundary = stopped
    with pytest.raises(InterruptedError, match="accepted stop"):
        collector.snapshot()
    assert len(port.writes) == before and not collector.failed


def test_post_enable_candidate_loss_waits_quietly_then_stops_and_keeps_evidence(
    tmp_path, monkeypatch
):
    import tester_sync_session as tools

    collector, port = wire()
    port.ball_bin = 20
    original = port.write

    def lose_after_enable(data):
        count = original(data)
        if data == b"triggerCfg 20 1 1\n":
            port.ball_state, port.ball_bin = "waiting", 0
            port.ball_releases += 1
        return count

    monkeypatch.setattr(port, "write", lose_after_enable)
    original_begin = collector.begin

    def bounded_begin():
        original_begin()
        collector.deadline_ns = collector.now() + 150_000_000

    monkeypatch.setattr(collector, "begin", bounded_begin)
    monkeypatch.setattr(collector, "wait_notice", lambda budget: collector.sleep(budget))
    closed, started = [], []

    class Rig:
        arm_id, boot_id, trace_attempt_id = "arm", "synthetic-boot", "trace"
        native_capture = False

        def __init__(self, **_kwargs):
            self.stop = threading.Event()

        def prepare(self):
            pass

        def start_wait(self):
            started.append(True)

        def close(self):
            closed.append(self.stop.is_set())

    monkeypatch.setitem(sys.modules, "joint_clock_rig", SimpleNamespace(RigControl=Rig))
    monkeypatch.setattr(
        tools, "camera_evidence", lambda *_a, **_k: {"status": "missing_or_ambiguous", "clips": []}
    )
    files = tools.SessionFiles(tmp_path / "session")
    first_exchange = len(collector.rows)
    result = tools.attempt(
        collector,
        files,
        files.folder / "attempt-001",
        camera=SimpleNamespace(output_dir=tmp_path / "camera"),
        radar=object(),
    )
    assert port.writes.index(b"sensorStop\n") > port.writes.index(b"triggerCfg 20 1 1\n")
    assert collector.state["stop_verified"] and not port.active
    assert closed == [True]
    assert started == [True] and "TimeoutError" in result["error"]
    rows = [
        json.loads(line)
        for line in (files.folder / "attempt-001/iwr-observations.jsonl").read_text().splitlines()
    ]
    assert rows[-1]["phase"] == "candidate_lost"
    assert all(row["phase"] != "armed" for row in rows)
    assert "do not swing" in files.state["message"]
    assert (files.folder / "attempt-001/RESULT.json").is_file()
    assert files.state["attempts"][0]["status"] == "partial"
    saved = json.loads((files.folder / "attempt-001/RESULT.json").read_text())
    exchanges = dict(enumerate(saved["iwr_exchanges"], start=first_exchange))
    diagnostics = [row for row in exchanges.values() if row["command"] == "ball status"]
    assert len(diagnostics) == 1
    assert b"balldbg centroid=" in base64.b64decode(diagnostics[0]["received_b64"])
    context = diagnostics[0]["ball_status_context"]
    assert exchanges[context["preceding_stats_exchange_index"]]["command"] == "stats"
    assert context["non_atomic"] is True
    for observation in rows:
        exchange = exchanges[observation["exchange_index"]]
        assert exchange["command"] == "stats"
        raw = base64.b64decode(exchange["received_b64"])
        for group, prefix in (
            ("capture", "frames="),
            ("trigger", "trig phase="),
            ("ball", "ball state="),
        ):
            assert (
                fields_for(raw, prefix, observation["state"][group]) == observation["state"][group]
            )


def test_waiting_ball_cannot_remain_published_as_armed_before_any_latch(monkeypatch):
    collector, port = wire()
    port.ball_bin = 20
    collector.begin()
    index, _ = collector.wait_ball(lambda *_: None, lambda: False)
    collector.arm(index)
    original = port.write

    def released_ball(data):
        count = original(data)
        if data == b"stats\n":
            port.pending = port.pending.replace(
                b"ball state=locked follow=1 bin=20", b"ball state=waiting follow=1 bin=0"
            )
        return count

    monkeypatch.setattr(port, "write", released_ball)
    monkeypatch.setattr(collector, "wait_notice", lambda budget: collector.sleep(budget))
    collector.deadline_ns = collector.now() + 100_000_000
    published = []
    with pytest.raises(TimeoutError):
        collector.wait_result(lambda phase, state: published.append((phase, state)), lambda: False)
    assert not any(phase == "armed" for phase, _state in published)
    assert published and all(phase == "candidate_lost" for phase, _state in published)


def test_latched_result_remains_retrievable_after_ball_candidate_departure(monkeypatch):
    collector, port = wire()
    port.ball_bin = 20
    collector.begin()
    index, _ = collector.wait_ball(lambda *_: None, lambda: False)
    collector.arm(index)
    port.active, port.held = False, True
    original = port.write

    def departed_ball(data):
        count = original(data)
        if data == b"stats\n":
            port.pending = port.pending.replace(
                b"ball state=locked follow=1 bin=20", b"ball state=waiting follow=1 bin=0"
            )
        return count

    monkeypatch.setattr(port, "write", departed_ball)
    held = collector.wait_result(lambda *_: None, lambda: False)
    assert held["record"]["shot_id"] == 1
    assert b"triggerLog result\n" in port.writes


@pytest.mark.parametrize("attribute,value", [("active", False), ("enabled", True), ("held", True)])
def test_pre_enable_rf_and_trigger_state_must_still_be_fresh(attribute, value):
    collector, port = wire()
    collector.begin()
    index, _ = collector.wait_ball(lambda *_: None, lambda: False)
    setattr(port, attribute, value)
    before = len(port.writes)
    with pytest.raises(ValueError, match="before arming"):
        collector.arm(index)
    assert f"triggerCfg {index} 1 1\n".encode() not in port.writes[before:]


@pytest.mark.parametrize("cancel", [False, True])
def test_immediate_post_enable_latch_preserves_observation_and_honours_stop(monkeypatch, cancel):
    collector, port = wire()
    collector.begin()
    index, _ = collector.wait_ball(lambda *_: None, lambda: False)
    original = port.write

    def latch_after_enable(data):
        count = original(data)
        if data == f"triggerCfg {index} 1 1\n".encode():
            port.complete_shot()
            port.ball_state, port.ball_bin = "waiting", 0
            port.ball_releases += 1
        return count

    monkeypatch.setattr(port, "write", latch_after_enable)
    state_after_arm = collector.arm(index)
    assert state_after_arm["capture"]["active"] == "0"
    observed = deepcopy(collector.trigger_observation)
    assert observed["exchange_index"] == len(collector.rows) - 1
    callbacks = []
    if cancel:
        with pytest.raises(InterruptedError):
            collector.wait_result(lambda *_: None, lambda: True, on_trigger=callbacks.append)
        assert callbacks == [] and collector.next_shot == 1
        assert b"triggerLog result\n" not in port.writes
    else:
        held = collector.wait_result(lambda *_: None, lambda: False, on_trigger=callbacks.append)
        assert held["record"]["shot_id"] == 1
        assert callbacks == [observed]
        assert (
            callbacks[0]["stats_exchange"]["cli_ready_ns"]
            == collector.rows[observed["exchange_index"]]["cli_ready_ns"]
        )


def test_gone_then_reacquired_candidate_stays_quiet_until_delayed_trigger_notice(monkeypatch):
    collector, port = wire()
    collector.begin()
    index, _ = collector.wait_ball(lambda *_: None, lambda: False)
    collector.arm(index)
    port.ball_reason = "gone"
    polls = []

    def notice(budget):
        polls.append(True)
        collector.sleep(budget)
        if len(polls) == 1:
            port.ball_reason = "none"
            port.ball_locks, port.ball_releases = 2, 1
            return None
        port.complete_shot()
        port.ball_state, port.ball_bin = "waiting", 0
        return port.clock()

    monkeypatch.setattr(collector, "wait_notice", notice)
    published, callbacks = [], []
    held = collector.wait_result(
        lambda phase, state: published.append((phase, state)),
        lambda: False,
        on_trigger=callbacks.append,
    )
    assert held["record"]["shot_id"] == 1 and len(callbacks) == 1
    assert [phase for phase, _ in published] == ["candidate_lost", "candidate_lost", "armed"]
    assert published[1][1]["ball"]["reason"] == "none"
    assert published[1][1]["candidate_loss"] == published[0][1]["candidate_loss"]
    assert published[-1][1]["trigger_observed"] is True
    assert callbacks[0]["notice_source"] == "triggered_line"


@pytest.mark.parametrize("chunk", [1, 17, 65536])
def test_real_serial_parser_collects_bound_result_before_any_raw_dump(chunk):
    collector, port = wire(chunk)
    collector.begin()
    index, _ = collector.wait_ball(lambda *_: None, lambda: False)
    collector.arm(index)
    port.active, port.held = False, True
    held = collector.wait_result(lambda *_: None, lambda: False)
    assert held["record"]["shot_id"] == 1 and held["record"]["generation"] == 1
    assert base64.b64decode(held["result_b64"]) == port.result
    assert len(held["clock_observations"]) == 2
    assert held["mapping"]["impact_mono_interval_ns"] is None
    assert all(b"l3dump\n" != value and b"l3release" not in value for value in port.writes)
    collector.finish()
    assert collector.state["stop_verified"]


def dump_reader(port, monkeypatch):
    radar = object.__new__(driver.IWR6843Radar)
    radar.ser = port

    def seconds():
        port.clock.ns += 10_000_000
        return port.clock.ns / 1e9

    monkeypatch.setattr(
        driver,
        "time",
        SimpleNamespace(time=seconds, monotonic=seconds, monotonic_ns=port.clock),
    )
    return radar


@pytest.mark.parametrize("chunk", [1, 17, 65536])
def test_successive_parser_results_keep_independent_shot_and_rearm_identities(chunk, monkeypatch):
    collector, port = wire(chunk)
    radar = dump_reader(port, monkeypatch)
    first = None
    for shot_id, generation in ((1, 1), (2, 3)):
        collector.begin()
        index, _ = collector.wait_ball(lambda *_: None, lambda: False)
        collector.arm(index)
        port.complete_shot()
        held = collector.wait_result(lambda *_: None, lambda: False)
        assert held["record"]["shot_id"] == shot_id
        assert held["record"]["generation"] == generation
        assert struct.unpack_from("<I", base64.b64decode(held["result_b64"]), 4)[0] == shot_id
        assert collector.next_shot == shot_id + 1
        if first is None:
            first, saved = held, deepcopy(held)
        raw, evidence = collector.read_raw_dump(radar)
        assert raw == port.dump_payload
        assert evidence["transfer_timing"]["cli_ready_ns"] is not None
        assert collector.generation == port.generation == generation + 1
        assert not port.held and port.active and not port.pending
        collector.finish()
        assert collector.state["stop_verified"] and not port.active
        assert first == saved
    assert port.writes.count(b"l3dump\n") == 2
    assert b"l3release\n" not in port.writes


@pytest.mark.parametrize("stale_field", ["shot_id", "generation"])
def test_previous_result_identity_is_refused_after_successful_dump_and_rearm(
    stale_field, monkeypatch
):
    collector, port = wire()
    collector.begin()
    port.complete_shot()
    first = collector.wait_result(lambda *_: None, lambda: False)
    saved = deepcopy(first)
    collector.read_raw_dump(dump_reader(port, monkeypatch))
    collector.finish()
    collector.begin()
    port.complete_shot()
    if stale_field == "shot_id":
        port.shot = 1
        struct.pack_into("<I", port.result, 4, 1)
    else:
        port.generation = 1
    with pytest.raises(ValueError):
        collector.wait_result(lambda *_: None, lambda: False)
    assert collector.failed and collector.next_shot == 2
    writes = list(port.writes)
    collector.finish()
    with pytest.raises(ValueError, match="fresh identified runtime required"):
        collector.begin()
    assert port.writes == writes and first == saved


@pytest.mark.parametrize("failure", ["partial_payload", "missing_completion", "restart_error"])
def test_dump_failure_keeps_generation_and_blocks_all_further_writes(failure, monkeypatch):
    collector, port = wire()
    collector.begin()
    port.complete_shot()
    held = collector.wait_result(lambda *_: None, lambda: False)
    saved = deepcopy(held)
    replies = {
        "partial_payload": port.dump_payload[:-2],
        "missing_completion": port.dump_payload,
        "restart_error": port.dump_payload + b"Error: RF restart failed\nError -1\n" + WIRE.PROMPT,
    }
    port.dump_reply = replies[failure]
    with pytest.raises(driver.IWR6843DumpRecoveryError) as raised:
        collector.read_raw_dump(dump_reader(port, monkeypatch))
    assert raised.value.raw == replies[failure][: len(port.dump_payload)]
    assert collector.failed and collector.generation == 1 and collector.next_shot == 2
    writes = list(port.writes)
    collector.finish()
    with pytest.raises(ValueError, match="fresh identified runtime required"):
        collector.begin()
    with pytest.raises(ExchangeError, match="uncertain"):
        collector.snapshot()
    with pytest.raises(ValueError, match="uncertain UART"):
        collector.read_raw_dump(dump_reader(port, monkeypatch))
    assert port.writes == writes and held == saved


def test_quiet_timeout_can_stop_and_rearm_without_consuming_a_shot_id():
    collector, port = wire()
    collector.begin()
    index, _ = collector.wait_ball(lambda *_: None, lambda: False)
    collector.arm(index)
    collector.deadline_ns = collector.now() + 200_000_000
    with pytest.raises(TimeoutError, match="no held IWR result"):
        collector.wait_result(lambda *_: None, lambda: False)
    assert collector.trigger_observation is None and collector.next_shot == 1
    assert not collector.failed
    collector.finish()
    collector.begin()
    port.complete_shot()
    held = collector.wait_result(lambda *_: None, lambda: False)
    assert held["record"]["shot_id"] == 1 and held["record"]["generation"] == 2
    collector.finish()


def test_result_lost_after_trigger_does_not_resynchronize_from_later_unbound_result():
    """Losing a built result requires a fresh runtime to recover the shot counter."""
    collector, port = wire()
    collector.begin()
    index, _ = collector.wait_ball(lambda *_: None, lambda: False)
    collector.arm(index)
    port.active, port.held = True, True
    collector.deadline_ns = collector.now() + 200_000_000
    with pytest.raises(TimeoutError):
        collector.wait_result(lambda *_: None, lambda: False)
    assert collector.trigger_observation is not None and collector.next_shot == 1
    port.complete_shot()
    collector.finish()
    collector.begin()
    port.complete_shot()
    with pytest.raises(ValueError):
        collector.wait_result(lambda *_: None, lambda: False)
    assert port.shot == 2 and collector.next_shot == 1 and collector.failed


@pytest.mark.parametrize("field,value", [("shot", 8), ("generation", 0)])
def test_real_parser_rejects_stale_result_and_retains_original_reply(field, value):
    collector, port = wire()
    collector.begin()
    port.active, port.held = False, True
    setattr(port, field, value)
    with pytest.raises(ValueError):
        collector.wait_result(lambda *_: None, lambda: False)
    assert collector.failed and collector.next_shot == 1
    assert any(base64.b64decode(row["received_b64"]).startswith(b"OFR1") for row in collector.rows)


def test_interrupted_exchange_never_sends_a_cleanup_command():
    collector, port = wire()
    collector.begin()

    def interrupt(_size):
        raise KeyboardInterrupt()

    port.read = interrupt
    with pytest.raises(KeyboardInterrupt):
        collector.snapshot()
    collector.finish()
    assert collector.failed and port.writes[-1] == b"stats\n"


def fake():
    collector = object.__new__(ShotCollector)
    collector.fresh_runtime = True
    collector.failed = False
    collector.generation = 0
    collector.next_shot = 1
    collector.observed_ball = None
    collector.state = {"rf_start_attempted": False, "stop_verified": False}
    collector.commands = []
    collector.rows = []

    def command(value):
        collector.commands.append(value)
        collector.rows.append({"command": value})

    collector.command = command
    collector.now = lambda: 1_000_000_000
    collector.sleep = lambda _: None
    return collector


def state(ball="locked", index=51, active="1", latched="0", enabled="1"):
    return {
        "capture": {"active": active},
        "trigger": {"enabled": enabled, "latched": latched},
        "ball": {
            "state": ball,
            "follow": "1",
            "bin": str(index),
            "reason": "none",
            "locks": "1",
            "releases": "0",
            "window": "20+53",
        },
    }


def test_finite_profile_and_detector_enabled_after_start():
    c = fake()
    c.begin()
    assert "frameCfg 0 2 12 20000 3 1 0" in PROFILE
    assert c.generation == 1 and c.next_shot == 1
    assert c.commands.index("sensorStart") < c.commands.index("ball cfg 1 1")
    assert "triggerCfg 32 1 1" not in c.commands


def test_observed_lock_seeds_region_and_not_a_nominal_default():
    c = fake()
    c.begin()
    c.snapshot = lambda: state(enabled="1" if c.commands[-1] == "triggerCfg 51 1 1" else "0")
    index, evidence = c.wait_ball(lambda *_: None, lambda: False)
    assert index == 51 and evidence["ball"]["state"] == "locked"
    c.arm(index)
    assert c.commands[-1] == "triggerCfg 51 1 1"


@pytest.mark.parametrize("index", [32, True, 53, 128])
def test_without_current_attempt_lock_no_bin_can_be_armed(index):
    c = fake()
    c.begin()
    with pytest.raises(ValueError):
        c.arm(index)


def test_previous_attempt_lock_cannot_seed_next_attempt():
    c = fake()
    c.begin()
    c.snapshot = lambda: state()
    c.wait_ball(lambda *_: None, lambda: False)
    c.state["stop_verified"] = True
    c.begin()
    with pytest.raises(ValueError):
        c.arm(51)
    assert c.generation == 2


def test_no_ball_timeout_and_user_cancel_preserve_no_fake_result():
    c = fake()
    c.begin()
    c.snapshot = lambda: state("waiting")
    with pytest.raises(InterruptedError):
        c.wait_ball(lambda *_: None, lambda: True)
    c.deadline_ns = 0
    with pytest.raises(TimeoutError):
        c.wait_result(lambda *_: None, lambda: False)
    assert c.next_shot == 1


def test_result_uses_independent_expected_ids_then_advances_only_on_success():
    c = fake()
    c.begin()
    c.snapshot = lambda: state(active="0", latched="1")
    c.exchange = lambda *_a, **_k: (b"result shot=1 ready=1\n", None)
    seen = []

    def held(**kwargs):
        seen.append(kwargs)
        return {"original": "held"}

    c.collect_held_result = held
    assert c.wait_result(lambda *_: None, lambda: False) == {"original": "held"}
    assert seen[0]["expected_shot_id"] == 1 and seen[0]["expected_generation"] == 1
    assert c.next_shot == 2


def test_rejected_result_does_not_advance_expected_shot():
    c = fake()
    c.begin()
    c.snapshot = lambda: state(active="0", latched="1")
    c.exchange = lambda *_a, **_k: (b"result shot=8 ready=1\n", None)

    def reject(**_):
        raise ValueError("wrong shot identity")

    c.collect_held_result = reject
    with pytest.raises(ValueError):
        c.wait_result(lambda *_: None, lambda: False)
    assert c.next_shot == 1


DEFAULT_PHASE = "phaseCaptureCfg 20 53 9 32 53 7 47 53 47 8 1"
CLUB51_PHASE = "phaseCaptureCfg 16 32 17 32 53 7 47 53 47 8 1"


def test_default_plan_is_todays_profile_byte_for_byte():
    assert PROFILE == tuple(
        "frameCfg 0 2 12 20000 3 1 0" if row.startswith("frameCfg ") else row
        for row in CONFIGURATION
    )
    assert DEFAULT_PHASE in PROFILE and DEFAULT_PHASE in CONFIGURATION
    assert TRIGGER_COMMANDS == tuple(f"triggerCfg {index} 1 1" for index in range(20, 73))
    collector, _port = wire()
    assert collector.profile == PROFILE
    assert collector.LIFECYCLE_COMMANDS == ShotCollector.LIFECYCLE_COMMANDS
    assert collector.DOUBLE_COMPLETION_COMMANDS == ShotCollector.DOUBLE_COMPLETION_COMMANDS
    assert collector.capture_plan["name"] == "default"
    assert collector.capture_plan["commands"] == list(PROFILE)


def test_club51_sends_only_its_pre_phase_and_accepts_only_its_own_lines():
    collector, port = wire(capture_plan="club51")
    assert collector.profile == tuple(
        CLUB51_PHASE if row == DEFAULT_PHASE else row for row in PROFILE
    )
    assert CLUB51_PHASE in collector.LIFECYCLE_COMMANDS
    assert DEFAULT_PHASE not in collector.LIFECYCLE_COMMANDS
    assert {row for row in collector.LIFECYCLE_COMMANDS if row.startswith("triggerCfg ")} == {
        "triggerCfg 32 1 0",
        *(f"triggerCfg {index} 1 1" for index in range(16, 48)),
    }
    with pytest.raises(ValueError, match="outside bounded RF clock scope"):
        collector.command(DEFAULT_PHASE)
    assert not collector.failed
    collector.begin()
    sent = [line.decode().strip() for line in port.writes]
    assert CLUB51_PHASE in sent and DEFAULT_PHASE not in sent
    assert collector.capture_plan["name"] == "club51"
    assert collector.capture_plan["commands"] == list(collector.profile)
    collector.finish()


def test_default_collector_refuses_the_club51_line():
    collector, _port = wire()
    with pytest.raises(ValueError, match="outside bounded RF clock scope"):
        collector.command(CLUB51_PHASE)


def test_club51_shot_completes_on_the_real_parser_with_a_near_address_bin():
    collector, port = wire(capture_plan="club51")
    port.ball_bin = 30
    collector.begin()
    index, _ = collector.wait_ball(lambda *_: None, lambda: False)
    collector.arm(index)
    assert b"triggerCfg 30 1 1\n" in port.writes
    port.active, port.held = False, True
    held = collector.wait_result(lambda *_: None, lambda: False)
    assert held["record"]["shot_id"] == 1
    collector.finish()
    assert collector.state["stop_verified"]


@pytest.mark.parametrize("index", [15, 48, 51])
def test_club51_refuses_a_ball_outside_its_retained_pre_window(index):
    collector, port = wire(capture_plan="club51")
    port.ball_bin = index
    collector.begin()
    with pytest.raises(ValueError, match="outside retained profile"):
        collector.wait_ball(lambda *_: None, lambda: False)


def test_unknown_capture_plan_opens_nothing():
    with pytest.raises(ValueError, match="unknown IWR capture plan"):
        wire(capture_plan="club64")

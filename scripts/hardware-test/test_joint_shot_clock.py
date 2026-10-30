"""Synthetic self-trigger lifecycle checks; no physical accuracy claims."""

import base64
import os
import struct
import zlib
from pathlib import Path

import pytest
from run_joint_clock import load_package

load_package(Path(os.environ["OPENFLIGHT_CLOCK_PACKAGE"]))
from joint_shot_clock import PROFILE, ShotCollector  # noqa: E402
from test_joint_clock_active import WIRE  # noqa: E402


class ShotPort(WIRE.Port):
    def __init__(self, clock, chunk):
        super().__init__(clock, chunk=chunk)
        self.active = self.enabled = False
        self.generation, self.shot = 0, 1
        struct.pack_into("<I", self.result, 4, self.shot)

    def write(self, data):
        command = data.decode().strip()
        if command in ShotCollector.LIFECYCLE_COMMANDS or command == "stats":
            assert not self.pending
            self.writes.append(data)
            if command == "sensorStart":
                self.active, self.enabled = True, False
                self.generation += 1
            elif command == "sensorStop":
                self.active = False
            elif command.startswith("triggerCfg"):
                self.enabled = command.endswith(" 1")
            if command == "stats":
                self.pending = (
                    f"frames={101 if self.generation else 0} active={int(self.active)} rf_faults=0 hwa_missed=0\n"
                    f"trig phase=ARMED enabled={int(self.enabled)} latched={int(self.held)}\n"
                    "ball state=locked follow=1 bin=51\nDone\nl3dump:/>"
                ).encode()
            else:
                count = 2 if command in ShotCollector.DOUBLE_COMPLETION_COMMANDS else 1
                self.pending = b"Done\n" * count + WIRE.PROMPT
            return len(data)
        count = super().write(data)
        if command == "triggerLog result":
            self.pending = self.pending.replace(b"result shot=7", b"result shot=1")
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


def wire(chunk=65536):
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
    )
    collector.establish_fresh_runtime()
    return collector, port


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
    collector.command = collector.commands.append
    collector.now = lambda: 1_000_000_000
    collector.sleep = lambda _: None
    return collector


def state(ball="locked", index=51, active="1", latched="0"):
    return {
        "capture": {"active": active},
        "trigger": {"enabled": "1", "latched": latched},
        "ball": {"state": ball, "follow": "1", "bin": str(index)},
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
    c.snapshot = lambda: state()
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

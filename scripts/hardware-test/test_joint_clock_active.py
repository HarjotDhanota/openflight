"""RF lifecycle simulations; use OPENFLIGHT_CLOCK_PACKAGE for the sealed dependencies."""

import base64
import importlib.util
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from run_joint_clock import load_package  # noqa: E402

PACKAGE = Path(os.environ["OPENFLIGHT_CLOCK_PACKAGE"]).resolve()
load_package(PACKAGE)
from joint_clock_active import (  # noqa: E402
    ARM_COMMANDS,
    CONFIGURATION,
    ActiveCollector,
    counter_maps,
    fields_for,
)
from usb_collector import ExchangeError  # noqa: E402

SPEC = importlib.util.spec_from_file_location(
    "clock_wire_fixture", PACKAGE / "test_usb_collector.py"
)
WIRE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WIRE)


class ActivePort(WIRE.Port):
    def __init__(self, clock):
        super().__init__(clock)
        self.active = False
        self.enabled = False
        self.bad_status = None
        self.frame = 0
        self.start_fault = False

    def write(self, data):
        command = data.decode().strip()
        if command in (*CONFIGURATION, *ARM_COMMANDS, "sensorStart", "sensorStop", "stats"):
            self.writes.append(data)
            assert not self.pending
            if command == "sensorStart":
                self.active = True
                self.enabled = False
                if self.start_fault:
                    self.pending = b"Error: start failed\nError -1\nl3dump:/>"
                    return len(data)
            if command == "sensorStop":
                self.active = False
            if command == "triggerCfg 32 1 1":
                self.enabled = True
            if command == "stats":
                raw = (
                    f"frames={self.frame} active={int(self.active)} rf_faults=0 hwa_missed=0\n"
                    f"trig phase=ARMED latched=0 enabled={int(self.enabled)}\n"
                    "Done\nl3dump:/>"
                ).encode()
                if self.bad_status and self.active:
                    raw = self.bad_status(raw)
            else:
                count = 2 if command in ("debugCfg 0", "triggerCfg 32 1 1") else 1
                raw = b"Done\n" * count + WIRE.PROMPT
            self.pending = self.edit(command, raw)
            return len(data)
        result = super().write(data)
        if command.startswith("triggerLog cp") and self.active:
            self.frame += 2
            import struct  # pylint: disable=import-outside-toplevel
            import zlib  # pylint: disable=import-outside-toplevel

            fields = list(WIRE.COMPACT.unpack(self.pending[: WIRE.COMPACT.size]))
            fields[-2] = self.frame
            packet = WIRE.COMPACT.pack(*fields)
            self.pending = (
                packet[:-4]
                + struct.pack("<I", zlib.crc32(packet[:-4]))
                + self.pending[WIRE.COMPACT.size :]
            )
        return result


def setup():
    clock = WIRE.Clock()
    port = ActivePort(clock)
    tokens = iter(f"{value:016x}" for value in range(1, 1000))
    collector = ActiveCollector(
        port,
        build=WIRE.BUILD,
        host_boot="test-boot",
        now=clock,
        nonce=lambda: next(tokens),
        sleep=lambda delay: setattr(clock, "ns", clock.ns + round(delay * 1e9)),
    )
    return collector, port


def test_active_frames_map_and_stop_without_fabricating_impact():
    collector, port = setup()
    result = collector.collect_active(count=4)
    assert result["after"]["capture"]["frames"] == "8"
    assert len(result["conditional_maps"]) == 4
    assert result["finite_rf_duration_ms_configured"] == 6000
    assert not result["physical_timing_qualified"]
    assert collector.state["stop_verified"] and not port.active
    assert b"frameCfg 0 2 12 2000 3 1 0\n" in port.writes
    assert port.writes[-2:] == [b"sensorStop\n", b"stats\n"]
    assert all(base64.b64decode(row["received_b64"]) for row in collector.rows)
    assert all("impact_mono_interval_ns" not in row for row in result["conditional_maps"])


@pytest.mark.parametrize("command", ["l3dump", "l3release", "reset", "frameCfg 0 2 12 0 3 1 0"])
def test_no_unbounded_rf_flash_dump_or_automatic_rearm(command):
    collector, port = setup()
    with pytest.raises(ValueError, match="scope"):
        collector.command(command)
    assert port.writes == []


@pytest.mark.parametrize(
    "count,pause", [(1, 0.01), (65, 0.01), (True, 0.01), (4, 1.0), (4, float("nan"))]
)
def test_bad_limits_do_not_send_anything(count, pause):
    collector, port = setup()
    with pytest.raises(ValueError):
        collector.collect_active(count=count, pause_s=pause)
    assert not port.writes


@pytest.mark.parametrize(
    "edit",
    [
        lambda raw: raw.replace(b"rf_faults=0", b"rf_faults=1"),
        lambda raw: raw.replace(b"hwa_missed=0", b"hwa_missed=2"),
        lambda raw: raw.replace(b"latched=0", b"latched=1"),
        lambda raw: raw.replace(b"active=1", b"active=0"),
    ],
)
def test_rf_fault_or_unexpected_capture_retains_evidence_and_stops(edit):
    collector, port = setup()
    port.bad_status = edit
    with pytest.raises(ValueError):
        collector.collect_active(count=2)
    assert collector.state["stop_verified"]
    assert not port.active
    assert any(row.get("error") == "StatusQualificationError" for row in collector.rows)


def test_uncertain_reply_is_not_followed_by_speculative_stop_or_rearm():
    collector, port = setup()
    port.start_fault = True
    with pytest.raises(ExchangeError):
        collector.collect_active(count=2)
    assert collector.failed
    assert port.writes[-1] == b"sensorStart\n"
    assert not collector.state["stop_verified"]
    assert "Error" in base64.b64decode(collector.rows[-1]["received_b64"]).decode()
    with pytest.raises(ExchangeError):
        collector.command("sensorStop")


def test_keyboard_interrupt_stops_and_verifies_the_front_end():
    collector, port = setup()
    collector.sleep = lambda _delay: (_ for _ in ()).throw(KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        collector.collect_active(count=2)
    assert collector.state["stop_verified"] and not port.active


def test_duplicate_status_is_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        fields_for(b"frames=3 active=1 active=0\n", "frames=", ("active",))


def test_counter_reset_and_missing_rf_frames_fail():
    from dataclasses import replace  # pylint: disable=import-outside-toplevel

    from clock_evidence import (  # pylint: disable=import-outside-toplevel
        ProbeObservation,
        ProbeReply,
    )

    collector, _ = setup()
    result = collector.collect_active(count=3)

    def observations_from_result():
        return [
            ProbeObservation(**{**item, "reply": ProbeReply(**item["reply"])})
            for item in result["clock_observations"]
        ]

    observations = observations_from_result()
    second = observations[1]
    observations[1] = replace(second, reply=replace(second.reply, rx_tick=0))
    with pytest.raises(ValueError):
        counter_maps(observations, WIRE.BUILD, collector.context.boot)
    observations = observations_from_result()
    observations = [replace(item, reply=replace(item.reply, frame_seq=4)) for item in observations]
    with pytest.raises(ValueError, match="no RF frame"):
        counter_maps(observations, WIRE.BUILD, collector.context.boot)


def test_package_tampering_fails_before_import_or_device_open(tmp_path):
    (tmp_path / "MANIFEST.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="sealed"):
        load_package(tmp_path)


def test_baseline_and_joint_cycles_share_one_runtime_and_require_stop():
    collector, port = setup()
    with pytest.raises(ValueError, match="previous verified stop"):
        collector.collect_active(count=2, fresh=False)
    assert port.writes == []
    first = collector.collect_active(count=3)
    second = collector.collect_active(count=4, fresh=False)
    assert first["runtime"] == second["runtime"]
    assert first["connection"] == second["connection"]
    assert len(second["conditional_maps"]) == 4
    assert collector.state["stop_verified"]
    requests = [row["command"] for row in collector.rows if row["command"].startswith("triggerLog")]
    assert len(requests) == len(set(requests))


@pytest.mark.parametrize("command", ["debugCfg 0", "triggerCfg 32 1 1"])
@pytest.mark.parametrize("chunk", [1, 7, 65536])
def test_custom_cli_handler_and_sdk_each_supply_one_completion(command, chunk):
    collector, port = setup()
    port.chunk = chunk
    port.edit = lambda _command, _raw: b"Done\nDone\nl3dump:/>"
    assert collector.command(command) == b"Done\nDone\nl3dump:/>"


@pytest.mark.parametrize("count", [0, 1, 3])
def test_custom_completion_count_is_exact_not_an_arbitrary_extra_done(count):
    collector, port = setup()
    port.edit = lambda _command, _raw: b"Done\n" * count + WIRE.PROMPT
    with pytest.raises(ExchangeError):
        collector.command("debugCfg 0")


def test_regular_sdk_command_does_not_accept_a_double_completion():
    collector, port = setup()
    port.edit = lambda _command, _raw: b"Done\nDone\nl3dump:/>"
    with pytest.raises(ExchangeError):
        collector.command("flushCfg")


def test_attach_to_identified_stopped_image_builds_fresh_probe_maps():
    collector, port = setup()
    port.runtime, port.frame = "123456789abcdef0", 12
    attached = collector.attach_stopped_runtime(port.runtime)
    assert attached["reused_clock_mapping"] is False
    result = collector.collect_active(count=3, fresh=False)
    assert result["runtime"] == port.runtime
    assert result["before"]["trigger"]["enabled"] == "1"
    assert collector.state["stop_verified"]


def test_attach_refuses_a_reset_runtime_before_state_changing_commands():
    collector, port = setup()
    with pytest.raises(ValueError, match="runtime identity differs"):
        collector.attach_stopped_runtime("123456789abcdef0")
    assert len(port.writes) == 1
    assert port.writes[0].startswith(b"triggerLog clockProbe")


@pytest.mark.parametrize("active,latched", [(True, False), (False, True)])
def test_attach_never_stops_an_active_or_held_capture(active, latched):
    collector, port = setup()
    port.runtime, port.active = "123456789abcdef0", active
    if latched:
        port.edit = (
            lambda command, raw: raw.replace(b"latched=0", b"latched=1")
            if command == "stats"
            else raw
        )
    with pytest.raises(ValueError):
        collector.attach_stopped_runtime(port.runtime)
    assert b"sensorStop\n" not in port.writes


def test_attach_cannot_clear_a_poisoned_collector():
    collector, port = setup()
    collector.failed = True
    with pytest.raises(ExchangeError):
        collector.attach_stopped_runtime("123456789abcdef0")
    assert port.writes == []


@pytest.mark.parametrize("error_type", [InterruptedError, KeyboardInterrupt])
def test_interrupted_lifecycle_reply_prevents_cleanup_writes(error_type):
    collector, port = setup()

    def interrupt(_size):
        raise error_type("tester stop during UART reply")

    port.read = interrupt
    with pytest.raises((ExchangeError, error_type)):
        collector.command("sensorStart")
    assert collector.failed
    with pytest.raises(ExchangeError):
        collector.command("sensorStop")
    assert port.writes == [b"sensorStart\n"]

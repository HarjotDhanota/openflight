"""First-character acceptance before sending configuration or binary commands."""

import pytest

from openflight.iwr6843 import driver
from openflight.iwr6843.command_acceptance import CommandAcceptanceError, CommandAcceptancePort
from tests.iwr6843_character_port import CharacterPort
from tests.iwr6843_firmware_fake import FakeClock, FirmwareModel, NoLock


def test_whole_command_write_reproduces_prompt_race(monkeypatch):
    clock = FakeClock()
    port = CharacterPort(lambda _line: b"Error: unknown command\n", clock=clock)
    monkeypatch.setattr(driver, "time", clock.module())
    radar = driver.IWR6843Radar.__new__(driver.IWR6843Radar)
    radar.ser = port
    assert "Error" in radar.cmd("stats")
    assert port.commands == [b"ats"]


def test_opt_in_driver_preserves_echo_and_sends_remainder_after_acceptance(monkeypatch):
    clock = FakeClock()
    port = CharacterPort(lambda _line: b"Done\nl3dump:/>", clock=clock)
    monkeypatch.setattr(driver, "time", clock.module())
    monkeypatch.setattr(driver, "open_port", lambda *_args: port)
    monkeypatch.setattr(driver, "IWR6843DeviceLock", NoLock)
    radar = driver.IWR6843Radar(port="/dev/test-iwr", command_handshake=True)
    assert radar.cmd("stats") == "stats\nDone\nl3dump:/>"
    assert port.writes == [b"s", b"tats\n"]
    radar.close()
    assert port.closed


@pytest.mark.parametrize(
    "failure", ["missing_echo", "wrong_echo", "short_first", "short_remainder"]
)
def test_failed_acceptance_never_retries_or_resynchronizes(failure):
    clock = FakeClock()
    raw = CharacterPort(lambda _line: b"Done\nl3dump:/>", clock=clock, failure=failure)
    events = []
    port = CommandAcceptancePort(raw, events=events, now=clock.monotonic_ns)
    with pytest.raises(CommandAcceptanceError) as raised:
        port.write(b"stats\n")
    assert raw.writes == ([b"s", b"tats\n"] if failure == "short_remainder" else [b"s"])
    original = list(raw.writes)
    for action in (lambda: port.write(b"stats\n"), port.reset_input_buffer):
        with pytest.raises(CommandAcceptanceError, match="reset and reconnect"):
            action()
    assert raw.writes == original and raw.commands == []
    assert events[0]["error"]
    assert events[0]["started_ns"] <= events[0]["completed_ns"]
    assert raised.value.raw == (
        b"?" if failure == "wrong_echo" else b"s" if failure == "short_remainder" else b""
    )


@pytest.mark.parametrize(
    "data", [b"", b"s", b"stats\nsensorStart\n", b"stats\r\n", b"\xff\n", b"x" * 255 + b"\n"]
)
def test_invalid_command_never_writes(data):
    raw = CharacterPort(lambda _line: b"")
    port = CommandAcceptancePort(raw)
    with pytest.raises(ValueError):
        port.write(data)
    assert raw.writes == []


def test_echo_is_visible_once_and_next_write_requires_reader_to_consume_it():
    raw = CharacterPort(lambda _line: b"Done\nl3dump:/>")
    port = CommandAcceptancePort(raw)
    assert port.write(b"stats\n") == 6
    with pytest.raises(ValueError):
        port.write(b"stats\n")
    assert port.read(0) == b""
    assert port.read(1) == b"s"
    assert port.read(port.in_waiting) == b"tats\nDone\nl3dump:/>"
    assert port.in_waiting == 0
    assert port.write(b"stats\n") == 6
    assert raw.writes == [b"s", b"tats\n", b"s", b"tats\n"]
    assert "timeout" not in raw.__dict__ and "write_timeout" not in raw.__dict__


def test_late_echo_is_retained_but_remainder_is_withheld():
    clock = FakeClock()
    raw = CharacterPort(lambda _line: b"")
    original_read = raw.read

    def late_read(size):
        clock.sleep(1.0)
        return original_read(size)

    raw.read = late_read
    port = CommandAcceptancePort(raw, now=clock.monotonic_ns)
    with pytest.raises(CommandAcceptanceError) as raised:
        port.write(b"stats\n")
    assert raised.value.raw == b"s" and raw.writes == [b"s"]


def test_constructor_failure_closes_physical_port_and_releases_lock(monkeypatch):
    raw = CharacterPort(lambda _line: b"")
    raw.timeout = None
    events = []
    monkeypatch.setattr(driver, "open_port", lambda *_args: raw)
    monkeypatch.setattr(driver, "IWR6843DeviceLock", NoLock)
    with pytest.raises(ValueError, match="read timeout"):
        driver.IWR6843Radar(port="/dev/test-iwr", command_handshake=True, events=events)
    assert raw.closed and events[-1]["event"] == "lock_released"


def test_monitor_passes_opt_in_to_serial_child_without_opening_hardware(monkeypatch, tmp_path):
    from openflight.iwr6843 import monitor

    options = []
    monkeypatch.setattr(
        monitor, "IWR6843SerialProcess", lambda **kwargs: options.append(kwargs) or object()
    )
    monitor.IWR6843CaptureMonitor(
        config_path=tmp_path / "test.cfg", output_dir=tmp_path, command_handshake=True
    )
    assert options == [{"port": None, "factory_kwargs": {"command_handshake": True}}]


@pytest.mark.parametrize("autodetect", [False, True])
def test_configuration_help_full_dump_and_stop_all_use_handshake(monkeypatch, tmp_path, autodetect):
    from tests.test_iwr6843_driver import _active_dump

    clock = FakeClock()
    expected = _active_dump()
    model = FirmwareModel(
        expected, clock, extra_help=b"overview Read overview\ncells Read cells\nrelease Release\n"
    )

    def reply(line):
        model.receive(line)
        model.advance()
        result = bytes(model.pending)
        model.pending.clear()
        return result[len(line.rstrip(b"\n")) + 2 :]

    opened = []

    def open_port(*_args):
        physical = CharacterPort(reply, clock=clock)
        opened.append(physical)
        return physical

    monkeypatch.setattr(driver, "time", clock.module())
    monkeypatch.setattr(driver, "open_port", open_port)
    monkeypatch.setattr(driver, "IWR6843DeviceLock", NoLock)
    monkeypatch.setattr(
        driver.glob,
        "glob",
        lambda pattern: ["/dev/test-iwr"] if pattern == driver._PORT_GLOBS[0] else [],
    )
    monkeypatch.setattr(driver, "_usb_serial_identity", lambda _port: ("10c4", "ea70", 0))
    events = []
    radar = driver.IWR6843Radar(
        port=None if autodetect else "/dev/test-iwr", events=events, command_handshake=True
    )
    config = tmp_path / "test.cfg"
    config.write_text("profileCfg 0\nsensorStart\n", encoding="utf-8")
    radar.send_config(config)
    assert radar.two_round_support()["supported"]
    assert radar.read_dump() == expected
    radar.stop_sensor()
    radar.close()
    assert not model.active and all(port.closed for port in opened)
    assert {
        "sensorStop",
        "flushCfg",
        "profileCfg 0",
        "sensorStart",
        "stats",
        "help",
        "l3dump",
    } <= set(model.commands)
    for raw in opened:
        assert len(raw.writes) % 2 == 0
        assert all(
            len(first) == 1 and rest.endswith(b"\n")
            for first, rest in zip(raw.writes[::2], raw.writes[1::2])
        )
    assert all(
        row["first_byte_echo_verified"] for row in events if row["event"] == "command_acceptance"
    )


@pytest.mark.parametrize(
    "timeout,write_timeout", [(None, 0.25), (0, 0.25), (2, 0.25), (0.05, 0), (0.05, 2)]
)
def test_unsafe_blocking_settings_are_refused(timeout, write_timeout):
    raw = CharacterPort(lambda _line: b"")
    raw.timeout, raw.write_timeout = timeout, write_timeout
    with pytest.raises(ValueError, match="timeout"):
        CommandAcceptancePort(raw)
    assert raw.writes == []

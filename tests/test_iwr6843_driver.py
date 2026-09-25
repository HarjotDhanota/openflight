"""Tests for the IWR6843 CLI and dump serial contract."""

from __future__ import annotations

import numpy as np
import pytest

from openflight.iwr6843.device_lock import IWR6843DeviceBusyError
from openflight.iwr6843.driver import IWR6843DumpRecoveryError, IWR6843Radar
from openflight.iwr6843.dump import TEMP_REPORT_KEYS, pack_dump
from tests.iwr6843_firmware_fake import FakeClock, FakePort, FirmwareModel, NoLock, SilentPort


def test_send_config_rejects_missing_cli_acknowledgement(tmp_path, monkeypatch):
    """A wedged board must not be reported as configured and armed."""
    config = tmp_path / "radar.cfg"
    config.write_text("sensorStart\n", encoding="utf-8")
    radar = IWR6843Radar.__new__(IWR6843Radar)
    monkeypatch.setattr(radar, "drain_stale_output", lambda: 0)
    monkeypatch.setattr(radar, "cmd", lambda *_args, **_kwargs: "")

    with pytest.raises(RuntimeError, match="did not acknowledge"):
        radar.send_config(str(config))


class FakeDeviceLock:
    """Fail on nested ownership so auto-detection lock scope is observable."""

    held = set()

    def __init__(self, port, **_kwargs):
        self.port = port
        self.acquired = False

    def acquire(self):
        if self.port in self.held:
            raise IWR6843DeviceBusyError(self.port)
        self.held.add(self.port)
        self.acquired = True
        return self

    def release(self):
        if self.acquired:
            self.held.remove(self.port)
            self.acquired = False


class OpenedSerial:
    def __init__(self, response=b"sensorStart\n"):
        self.response = bytearray(response)
        self.closed = False

    def reset_input_buffer(self):
        pass

    def write(self, _data):
        pass

    def read(self, size):
        chunk = self.response[:size]
        del self.response[:size]
        return bytes(chunk)

    def close(self):
        self.closed = True


def test_radar_holds_device_lock_until_close(monkeypatch):
    from openflight.iwr6843 import driver

    locks = []
    serial_handle = OpenedSerial()

    def lock_factory(port, **_kwargs):
        lock = FakeDeviceLock(port)
        locks.append(lock)
        return lock

    monkeypatch.setattr(driver, "IWR6843DeviceLock", lock_factory)
    monkeypatch.setattr(driver, "open_port", lambda *_args, **_kwargs: serial_handle)

    radar = IWR6843Radar(port="/dev/test-iwr")

    assert locks[0].acquired is True
    radar.close()
    assert serial_handle.closed is True
    assert locks[0].acquired is False


def test_close_releases_device_lock_when_serial_close_fails(monkeypatch):
    from openflight.iwr6843 import driver

    lock = FakeDeviceLock("/dev/test-iwr")

    class CloseFailureSerial(OpenedSerial):
        def close(self):
            raise OSError("close failed")

    monkeypatch.setattr(driver, "IWR6843DeviceLock", lambda *_args, **_kwargs: lock)
    monkeypatch.setattr(driver, "open_port", lambda *_args, **_kwargs: CloseFailureSerial())
    radar = IWR6843Radar(port="/dev/test-iwr")

    with pytest.raises(OSError, match="close failed"):
        radar.close()

    assert lock.acquired is False


def test_serial_open_failure_releases_device_lock(monkeypatch):
    from openflight.iwr6843 import driver

    locks = []

    def lock_factory(port, **_kwargs):
        lock = FakeDeviceLock(port)
        locks.append(lock)
        return lock

    monkeypatch.setattr(driver, "IWR6843DeviceLock", lock_factory)
    monkeypatch.setattr(
        driver, "open_port", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("open failed"))
    )

    with pytest.raises(OSError, match="open failed"):
        IWR6843Radar(port="/dev/test-iwr")

    assert locks[0].acquired is False


def test_auto_detection_releases_probe_lock_before_owning_radar(monkeypatch):
    from openflight.iwr6843 import driver

    FakeDeviceLock.held.clear()
    handles = []

    def opened(*_args, **_kwargs):
        handle = OpenedSerial()
        handles.append(handle)
        return handle

    monkeypatch.setattr(driver.glob, "glob", lambda _pattern: ["/dev/test-iwr"])
    monkeypatch.setattr(driver, "IWR6843DeviceLock", FakeDeviceLock)
    monkeypatch.setattr(driver, "open_port", opened)

    radar = IWR6843Radar()

    assert radar.port == "/dev/test-iwr"
    assert len(handles) == 2
    assert handles[0].closed is True
    assert "/dev/test-iwr" in FakeDeviceLock.held
    radar.close()
    assert not FakeDeviceLock.held


def test_auto_detection_reports_busy_candidate(monkeypatch):
    from openflight.iwr6843 import driver

    class BusyLock:
        def __init__(self, port, **_kwargs):
            self.port = port

        def acquire(self):
            raise IWR6843DeviceBusyError(self.port)

        def release(self):
            pass

    monkeypatch.setattr(driver.glob, "glob", lambda _pattern: ["/dev/busy-iwr"])
    monkeypatch.setattr(driver, "IWR6843DeviceLock", BusyLock)

    with pytest.raises(IWR6843DeviceBusyError, match="/dev/busy-iwr"):
        IWR6843Radar()


def test_stop_sensor_requires_acknowledgement_and_inactive_health(monkeypatch):
    """Shutdown must leave firmware idle rather than merely close the host UART."""
    radar = IWR6843Radar.__new__(IWR6843Radar)
    responses = iter(["sensorStop\nDone\nl3dump:/>", "stats\nactive=0\nDone\nl3dump:/>"])
    calls = []

    def fake_cmd(command, window):
        calls.append((command, window))
        return next(responses)

    monkeypatch.setattr(radar, "cmd", fake_cmd)

    radar.stop_sensor()

    assert calls == [("sensorStop", 6.0), ("stats", 6.0)]


def test_stop_sensor_rejects_firmware_that_remains_active(monkeypatch):
    radar = IWR6843Radar.__new__(IWR6843Radar)
    responses = iter(["sensorStop\nDone\n", "stats\nactive=1\nDone\n"])
    monkeypatch.setattr(radar, "cmd", lambda *_args: next(responses))

    with pytest.raises(RuntimeError, match="remained active"):
        radar.stop_sensor()


def test_send_config_flushes_previous_mmwave_profile_when_config_omits_flush(tmp_path, monkeypatch):
    """Repeated startup must not exhaust the firmware's mmWave profile slots."""
    config = tmp_path / "radar.cfg"
    config.write_text("dfeDataOutputMode 1\nsensorStart\n", encoding="utf-8")
    commands = []
    radar = IWR6843Radar.__new__(IWR6843Radar)
    monkeypatch.setattr(radar, "drain_stale_output", lambda: 0)

    def command(line, *_args, **_kwargs):
        commands.append(line)
        if line == "stats":
            return "active=1\nDone\n"
        return "Done\n"

    monkeypatch.setattr(radar, "cmd", command)

    radar.send_config(str(config))

    assert commands == [
        "sensorStop",
        "flushCfg",
        "dfeDataOutputMode 1",
        "sensorStart",
        "stats",
    ]


def test_send_config_waits_for_sensor_to_become_active(tmp_path, monkeypatch):
    """sensorStart may acknowledge before RF calibration and HWA startup finish."""
    config = tmp_path / "radar.cfg"
    config.write_text("sensorStart\n", encoding="utf-8")
    statuses = iter(
        (
            "active=0 calib=0x0 hwa_frames=0\nDone\n",
            "active=0 calib=0x1ffe hwa_frames=0\nDone\n",
            "active=1 calib=0x1ffe hwa_frames=2\nDone\n",
        )
    )
    commands = []
    radar = IWR6843Radar.__new__(IWR6843Radar)
    monkeypatch.setattr(radar, "drain_stale_output", lambda: 0)

    def command(line, *_args, **_kwargs):
        commands.append(line)
        return next(statuses) if line == "stats" else "Done\n"

    monkeypatch.setattr(radar, "cmd", command)

    radar.send_config(str(config))

    assert commands == ["sensorStop", "flushCfg", "sensorStart", "stats", "stats", "stats"]


class FakeSerial:
    """Serial double that exposes the in_waiting/read/write pieces read_dump uses."""

    def __init__(self, payload: bytes):
        self.payload = bytearray(payload)
        self.writes = []

    @property
    def in_waiting(self):
        return len(self.payload)

    def reset_input_buffer(self):
        pass

    def write(self, data: bytes):
        self.writes.append(data)

    def read(self, nbytes: int):
        nbytes = min(nbytes, len(self.payload))
        chunk = self.payload[:nbytes]
        del self.payload[:nbytes]
        return bytes(chunk)


def test_read_dump_waits_for_cli_ready_after_binary_payload():
    raw = pack_dump(np.ones((2, 6, 4, 7), dtype=complex), n_tx=3, version=3)

    class ChunkedSerial:
        def __init__(self):
            self.chunks = [bytearray(b"l3dump\r\n" + raw), bytearray(b"Done\r\nl3dump:/>")]
            self.writes = []
            self.delay_next_chunk = False

        @property
        def in_waiting(self):
            if self.delay_next_chunk:
                return 0
            return len(self.chunks[0]) if self.chunks else 0

        def reset_input_buffer(self):
            return None

        def write(self, value):
            self.writes.append(value)

        def read(self, count):
            if self.delay_next_chunk:
                self.delay_next_chunk = False
                return b""
            if not self.chunks:
                return b""
            chunk = self.chunks[0]
            data = bytes(chunk[:count])
            del chunk[:count]
            if not chunk:
                self.chunks.pop(0)
                if self.chunks:
                    self.delay_next_chunk = True
            return data

    radar = IWR6843Radar.__new__(IWR6843Radar)
    radar.ser = ChunkedSerial()

    assert radar.read_dump(timeout_s=0.1) == raw
    assert radar.ser.chunks == []
    assert radar.ser.writes == [b"l3dump\n"]


def test_read_dump_reports_firmware_restart_error_after_binary_payload():
    raw = pack_dump(np.ones((1, 3, 4, 4), dtype=complex), n_tx=3, version=3)
    radar = IWR6843Radar.__new__(IWR6843Radar)
    radar.ser = FakeSerial(b"l3dump\r\n" + raw + b"Error: RF restart failed\r\n")

    with pytest.raises(IWR6843DumpRecoveryError, match="RF restart failed") as raised:
        radar.read_dump(timeout_s=0.1)

    assert raised.value.raw == raw


def test_read_dump_rejects_complete_payload_without_trailing_cli_ready():
    raw = pack_dump(np.ones((1, 3, 4, 4), dtype=complex), n_tx=3, version=3)
    radar = IWR6843Radar.__new__(IWR6843Radar)
    radar.ser = FakeSerial(b"l3dump\r\n" + raw)

    with pytest.raises(IWR6843DumpRecoveryError, match="did not return to its CLI") as raised:
        radar.read_dump(timeout_s=0.01)

    assert raised.value.raw == raw


def test_read_dump_rejects_partial_transport_without_sending_a_followup_command():
    radar = IWR6843Radar.__new__(IWR6843Radar)
    radar.ser = FakeSerial(b"l3dump\r\nILD1-partial")

    with pytest.raises(IWR6843DumpRecoveryError, match="complete dump header") as raised:
        radar.read_dump(timeout_s=0.01, stall_tolerance_s=0.0)

    assert raised.value.raw.endswith(b"ILD1-partial")
    assert radar.ser.writes == [b"l3dump\n"]


def test_post_dump_health_requires_active_cli_response(monkeypatch):
    radar = IWR6843Radar.__new__(IWR6843Radar)
    monkeypatch.setattr(radar, "cmd", lambda command, window: f"{command}\nactive=1\nDone\n")

    radar.verify_post_dump_cli()


def test_post_dump_health_rejects_wedged_cli(monkeypatch):
    radar = IWR6843Radar.__new__(IWR6843Radar)
    monkeypatch.setattr(radar, "cmd", lambda *_args: "")

    with pytest.raises(RuntimeError, match="post-dump CLI health check"):
        radar.verify_post_dump_cli()


def test_read_dump_sizes_v5_header_extension():
    report = {key: index + 40 for index, key in enumerate(TEMP_REPORT_KEYS)}
    raw = pack_dump(
        np.zeros((2, 4, 4, 8), dtype=complex),
        n_tx=2,
        version=5,
        temperature_report=report,
    )
    serial = FakeSerial(b"cli echo\r\n" + raw + b"Done\r\nl3dump:/>")
    radar = IWR6843Radar.__new__(IWR6843Radar)
    radar.ser = serial

    dump = radar.read_dump(timeout_s=0.1)

    assert dump == raw
    assert serial.writes == [b"l3dump\n"]


def _simulated_radar(monkeypatch, firmware, clock):
    from openflight.iwr6843 import driver

    monkeypatch.setattr(driver, "time", clock.module())
    monkeypatch.setattr(driver, "IWR6843DeviceLock", NoLock)
    monkeypatch.setattr(driver, "open_port", lambda *_args, **_kwargs: FakePort(firmware))
    return IWR6843Radar(port="/dev/serial/by-id/iwr-if00-port0")


def _active_dump() -> bytes:
    return pack_dump(np.ones((2, 6, 4, 7), dtype=complex), n_tx=3, version=3)


def test_dump_waits_through_a_multi_second_stall_for_done_before_stopping(monkeypatch):
    """A complete payload is not a returned handler: commands sent before Done are lost."""
    clock = FakeClock()
    firmware = FirmwareModel(_active_dump(), clock, restart_s=2.5)
    firmware.active = True
    radar = _simulated_radar(monkeypatch, firmware, clock)

    assert radar.read_dump() == firmware.raw
    radar.verify_post_dump_cli()
    radar.stop_sensor()
    radar.close()

    assert firmware.lost_writes == []
    assert firmware.commands == ["l3dump", "stats", "sensorStop", "stats"]
    assert firmware.active is False


def test_dump_without_trailing_done_fails_without_sending_another_command(monkeypatch):
    clock = FakeClock()
    firmware = FirmwareModel(_active_dump(), clock, restart_s=1_000.0)
    firmware.active = True
    radar = _simulated_radar(monkeypatch, firmware, clock)

    with pytest.raises(IWR6843DumpRecoveryError, match="did not return to its CLI") as raised:
        radar.read_dump(timeout_s=40.0)

    assert raised.value.raw == firmware.raw
    assert firmware.commands == ["l3dump"]
    assert firmware.lost_writes == []


def test_dump_restart_error_after_payload_preserves_the_payload(monkeypatch):
    clock = FakeClock()
    firmware = FirmwareModel(
        _active_dump(), clock, restart_s=0.5, trailer=b"Error: RF restart failed\r\nError -1"
    )
    firmware.active = True
    radar = _simulated_radar(monkeypatch, firmware, clock)

    with pytest.raises(IWR6843DumpRecoveryError, match="RF restart failed") as raised:
        radar.read_dump()

    assert raised.value.raw == firmware.raw


def test_dump_rejected_by_an_idle_cli_is_an_ordinary_error_not_raw_evidence(monkeypatch):
    """`l3dump` on a reset/idle board answers Error with no header; the CLI is responsive."""
    clock = FakeClock()
    firmware = FirmwareModel(_active_dump(), clock)
    radar = _simulated_radar(monkeypatch, firmware, clock)
    started = clock.now

    with pytest.raises(RuntimeError, match="rejected l3dump") as raised:
        radar.read_dump()

    assert not isinstance(raised.value, IWR6843DumpRecoveryError)
    assert "Error -1" in str(raised.value)
    assert clock.now - started < 2.0


def _probe_fixture(monkeypatch, ports, identities):
    """Auto-detection over named fake handles; returns the list of opened ports."""
    from openflight.iwr6843 import driver

    opened = []

    def open_probe(port, *_args, **_kwargs):
        opened.append(port)
        handle = ports[port]
        if isinstance(handle, Exception):
            raise handle
        return handle

    monkeypatch.setattr(
        driver.glob, "glob", lambda pattern: list(ports) if "USB" in pattern else []
    )
    monkeypatch.setattr(driver, "_usb_serial_identity", identities.get)
    monkeypatch.setattr(driver, "IWR6843DeviceLock", NoLock)
    monkeypatch.setattr(driver, "open_port", open_probe)
    return opened


CP2105_ENHANCED = ("10c4", "ea70", 0)
CP2105_STANDARD = ("10c4", "ea70", 1)


def test_auto_detection_never_opens_the_cp2105_standard_interface(monkeypatch):
    """Opening a tty raises DTR/RTS; the Standard interface carries no CLI to find."""
    from openflight.iwr6843 import driver

    clock = FakeClock()
    monkeypatch.setattr(driver, "time", clock.module())
    firmware = FirmwareModel(b"", clock)
    opened = _probe_fixture(
        monkeypatch,
        {"/dev/ttyUSB0": SilentPort(clock), "/dev/ttyUSB1": FakePort(firmware)},
        {"/dev/ttyUSB0": CP2105_STANDARD, "/dev/ttyUSB1": CP2105_ENHANCED},
    )

    assert IWR6843Radar.detect_port() == "/dev/ttyUSB1"
    assert opened == ["/dev/ttyUSB1"]


def test_auto_detection_probes_the_cp2105_enhanced_interface_before_unknown_ports(monkeypatch):
    from openflight.iwr6843 import driver

    clock = FakeClock()
    monkeypatch.setattr(driver, "time", clock.module())
    firmware = FirmwareModel(b"", clock)
    opened = _probe_fixture(
        monkeypatch,
        {"/dev/ttyUSB0": SilentPort(clock), "/dev/ttyUSB1": FakePort(firmware)},
        {"/dev/ttyUSB1": CP2105_ENHANCED},
    )

    assert IWR6843Radar.detect_port() == "/dev/ttyUSB1"
    assert opened == ["/dev/ttyUSB1"]


def test_missing_cli_reports_what_each_candidate_did(monkeypatch):
    """`no CLI found` must distinguish silence, foreign bytes, open failures and skips."""
    from openflight.iwr6843 import driver

    clock = FakeClock()
    monkeypatch.setattr(driver, "time", clock.module())
    streaming = FirmwareModel(b"", clock)
    streaming.stream += b"\x00\x7f" * 40
    streaming.handler_returns_at = clock.now + 60.0  # an abandoned dump is still streaming
    _probe_fixture(
        monkeypatch,
        {
            "/dev/ttyUSB0": SilentPort(clock),
            "/dev/ttyUSB1": PermissionError(13, "Permission denied"),
            "/dev/ttyUSB2": FakePort(streaming),
            "/dev/ttyUSB3": SilentPort(clock),
        },
        {"/dev/ttyUSB0": CP2105_ENHANCED, "/dev/ttyUSB3": CP2105_STANDARD},
    )

    with pytest.raises(RuntimeError) as raised:
        IWR6843Radar()

    message = str(raised.value)
    assert message.startswith("no IWR6843 CLI found")
    assert "/dev/ttyUSB0 (CP2105 Enhanced if00): no reply to help" in message
    assert "/dev/ttyUSB1: could not open (" in message and "Permission denied" in message
    assert "/dev/ttyUSB2: 80 bytes without the CLI help" in message
    assert "/dev/ttyUSB3 (CP2105 Standard if01): not probed" in message


def test_usb_serial_identity_reads_linux_sysfs(tmp_path, monkeypatch):
    from openflight.iwr6843 import driver

    (tmp_path / "idVendor").write_text("10C4\n", encoding="ascii")
    (tmp_path / "idProduct").write_text("ea70\n", encoding="ascii")
    for name, number in (("ttyUSB0", "00"), ("ttyUSB1", "01")):
        interface = tmp_path / name
        (interface / "device").mkdir(parents=True)
        (interface / "bInterfaceNumber").write_text(f"{number}\n", encoding="ascii")
    monkeypatch.setattr(driver, "_SYSFS_TTY", str(tmp_path))

    assert driver._usb_serial_identity("/dev/ttyUSB0") == CP2105_ENHANCED
    assert driver._usb_serial_identity("/dev/ttyUSB1") == CP2105_STANDARD
    assert driver._usb_serial_identity("/dev/ttyACM0") is None

"""Tests for the IWR6843 CLI and dump serial contract."""

from __future__ import annotations

import numpy as np
import pytest

from openflight.iwr6843.device_lock import IWR6843DeviceBusyError
from openflight.iwr6843.driver import IWR6843Radar
from openflight.iwr6843.dump import TEMP_REPORT_KEYS, pack_dump


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

    assert calls == [("sensorStop", 3.0), ("stats", 2.0)]


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

    with pytest.raises(RuntimeError, match="RF restart failed"):
        radar.read_dump(timeout_s=0.1)


def test_read_dump_sizes_v5_header_extension():
    report = {key: index + 40 for index, key in enumerate(TEMP_REPORT_KEYS)}
    raw = pack_dump(
        np.zeros((2, 4, 4, 8), dtype=complex),
        n_tx=2,
        version=5,
        temperature_report=report,
    )
    serial = FakeSerial(b"cli echo\r\n" + raw + b"trailing cli noise")
    radar = IWR6843Radar.__new__(IWR6843Radar)
    radar.ser = serial

    dump = radar.read_dump(timeout_s=0.1)

    assert dump == raw
    assert serial.writes == [b"l3dump\n"]

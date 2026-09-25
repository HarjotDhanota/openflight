"""OPS243-A native hardware-trigger contract tests."""

from unittest.mock import MagicMock

import pytest
import serial

from openflight.ops243 import OPS243Radar
from openflight.rolling_buffer import (
    HardwareTriggeredCapture,
    IQCapture,
    SpeedReading,
    SpeedTimeline,
    create_trigger,
)
from openflight.rolling_buffer.monitor import RollingBufferMonitor


class _Serial:
    is_open = True
    timeout = 1.0

    def __init__(self, chunks=(), *, fail_write=False):
        self.chunks = list(chunks)
        self.fail_write = fail_write
        self.writes = []

    @property
    def in_waiting(self):
        return len(self.chunks[0]) if self.chunks else 0

    def read(self, size):
        if not self.chunks:
            return b""
        chunk = self.chunks.pop(0)
        return chunk[:size]

    def reset_input_buffer(self):
        pass

    def write(self, data):
        if self.fail_write:
            raise serial.SerialTimeoutException("radar busy")
        self.writes.append(data)
        return len(data)

    def flush(self):
        pass


def _radar(serial_obj=None):
    radar = OPS243Radar.__new__(OPS243Radar)
    radar.serial = serial_obj or _Serial()
    radar.baud = 230400
    radar._internal_speed_trigger_config = None
    radar._internal_trigger_firmware_version = None
    radar._hardware_trigger_recovery_required = False
    return radar


def test_internal_trigger_configuration_restores_all_settings_under_guard(monkeypatch):
    radar = _radar()
    commands = []
    monkeypatch.setattr("openflight.ops243.time.sleep", lambda _seconds: None)

    def send(command):
        commands.append(command)
        return ""

    monkeypatch.setattr(radar, "_send_command", send)
    monkeypatch.setattr(radar, "_probe_firmware_version", lambda: "1.3.2")

    radar.configure_for_internal_speed_trigger(
        trigger_threshold_mph=25,
        pre_trigger_segments=6,
        trigger_magnitude=40,
        sample_rate_ksps=30,
    )

    assert commands == [
        "PI",
        "GC",
        "S=30",
        "US",
        "P0",
        "S(",
        "X=2",
        "R-",
        "R>25",
        "OJ",
        "OM",
        "W0",
        "S#6",
    ]
    assert radar.serial.writes == [
        b"ST-90\r",
        b"ST-90\r",
        b"ST-200\r",
        b"SM40\r",
        b"ST-25\r",
    ]
    assert b"A!" not in radar.serial.writes
    assert radar._internal_trigger_firmware_version == "1.3.2"


@pytest.mark.parametrize(
    "version",
    ["1.3.1", "1.3.0", "1.2.9", "1.4.0", "unknown", "1.3", "v1.3.2", None],
)
def test_internal_trigger_rejects_unqualified_firmware(monkeypatch, version):
    radar = _radar()
    monkeypatch.setattr(radar, "_probe_firmware_version", lambda: version)

    with pytest.raises(RuntimeError, match="firmware v1.3.2"):
        radar.validate_internal_trigger_firmware()


@pytest.mark.parametrize("version", ["1.3.2", "1.3.3", "1.3.99"])
def test_internal_trigger_accepts_supported_1_3_firmware(monkeypatch, version):
    radar = _radar()
    monkeypatch.setattr(radar, "_probe_firmware_version", lambda: version)
    assert radar.validate_internal_trigger_firmware() == version


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"trigger_threshold_mph": -1}, "non-negative"),
        ({"trigger_threshold_mph": float("nan")}, "non-negative"),
        ({"pre_trigger_segments": 33}, "0 to 32"),
        ({"trigger_magnitude": 0}, "1 and 2000"),
        ({"trigger_magnitude": 2001}, "1 and 2000"),
        ({"sample_rate_ksps": 25}, "30 ksps"),
    ],
)
def test_internal_trigger_validates_settings_before_firmware_probe(monkeypatch, kwargs, message):
    radar = _radar()
    probe = MagicMock(return_value="1.3.2")
    monkeypatch.setattr(radar, "_probe_firmware_version", probe)

    with pytest.raises(ValueError, match=message):
        radar.configure_for_internal_speed_trigger(**kwargs)
    probe.assert_not_called()


def test_internal_trigger_rearm_restores_cached_settings(monkeypatch):
    radar = _radar()
    radar._internal_speed_trigger_config = (25.0, 6, 40, 30)
    commands = []
    monkeypatch.setattr("openflight.ops243.time.sleep", lambda _seconds: None)
    monkeypatch.setattr(radar, "_drain_rearm_serial", lambda: None)
    monkeypatch.setattr(radar, "_send_command", lambda command: commands.append(command) or "")

    assert radar.rearm_internal_speed_trigger() is True
    assert radar.serial.writes == [
        b"ST-90\r",
        b"GC",
        b"ST-90\r",
        b"ST-200\r",
        b"SM40\r",
        b"ST-25\r",
    ]
    assert commands[-11:] == [
        "S=30",
        "US",
        "P0",
        "S(",
        "X=2",
        "R-",
        "R>25",
        "OJ",
        "OM",
        "W0",
        "S#6",
    ]


def test_internal_trigger_rearm_timeout_marks_recovery(monkeypatch):
    radar = _radar(_Serial(fail_write=True))
    radar._internal_speed_trigger_config = (25.0, 6, 40, 30)
    monkeypatch.setattr(radar, "_drain_rearm_serial", lambda: None)

    assert radar.rearm_internal_speed_trigger() is False
    assert radar._hardware_trigger_recovery_required is True


def test_recovery_wait_ignores_stale_output_until_fresh_marker():
    dump = b'{"sample_time":0}\r\n{"trigger_time":0.1}\r\n{"I":[1]}\r\n{"Q":[1]}'
    radar = _radar(_Serial([b'{"speed":-12}\r\n', dump]))
    radar._hardware_trigger_recovery_required = True

    response = radar.wait_for_hardware_trigger(timeout=0.2, dump_grace=0.2)

    assert response == dump.decode("ascii")
    assert radar._hardware_trigger_recovery_required is False


def test_factory_and_monitor_configure_hardware_mode_on_connect():
    trigger = create_trigger("hardware")
    assert isinstance(trigger, HardwareTriggeredCapture)
    assert trigger.trigger_threshold_mph == 25.0
    assert trigger.trigger_magnitude == 25
    assert trigger.pre_trigger_segments == 6
    assert trigger.sample_rate_ksps == 30

    monitor = RollingBufferMonitor(
        trigger_type="hardware",
        trigger_threshold_mph=31,
        trigger_magnitude=55,
        pre_trigger_segments=20,
    )
    monitor.radar = MagicMock()
    assert monitor.connect() is True
    monitor.radar.configure_for_internal_speed_trigger.assert_called_once_with(
        trigger_threshold_mph=31,
        pre_trigger_segments=20,
        trigger_magnitude=55,
        sample_rate_ksps=30,
    )


def test_hardware_strategy_rearms_accepts_and_records_diagnostics():
    capture = IQCapture(sample_time=0.0, trigger_time=0.1, i_samples=[2048], q_samples=[2048])
    radar = MagicMock()
    radar.wait_for_hardware_trigger.return_value = '{"Q":[1]}'
    radar.last_hardware_trigger_first_byte_timestamp = 12345.678
    radar.rearm_internal_speed_trigger.return_value = True
    radar._internal_trigger_firmware_version = "1.3.2"
    processor = MagicMock()
    processor.parse_capture.return_value = capture
    processor.process_standard.return_value = SpeedTimeline(
        readings=[SpeedReading(100.0, 900.0, 68.0, "outbound")],
        sample_rate_hz=937.5,
    )

    trigger = HardwareTriggeredCapture()
    assert trigger.wait_for_trigger(radar, processor, timeout=1.0) is capture
    radar.rearm_internal_speed_trigger.assert_called_once_with(30)
    diagnostic = trigger.drain_diagnostics()[0]
    assert diagnostic["accepted"] is True
    assert diagnostic["reason"] == "accepted"
    assert diagnostic["firmware_version"] == "1.3.2"
    assert diagnostic["iwr_camera_fanout"] == "unavailable_no_qualified_trigger_edge"


@pytest.mark.parametrize("parse_result", [None, ValueError("bad dump")])
def test_hardware_strategy_rearms_after_parse_failure(parse_result):
    radar = MagicMock()
    radar.wait_for_hardware_trigger.return_value = "not-json"
    radar.rearm_internal_speed_trigger.return_value = True
    processor = MagicMock()
    if isinstance(parse_result, Exception):
        processor.parse_capture.side_effect = parse_result
    else:
        processor.parse_capture.return_value = parse_result

    trigger = HardwareTriggeredCapture()
    assert trigger.wait_for_trigger(radar, processor) is None
    radar.rearm_internal_speed_trigger.assert_called_once_with(30)
    assert trigger.drain_diagnostics()[0]["reason"] == "parse_failed"


def test_hardware_strategy_rejects_false_trigger_after_rearm():
    radar = MagicMock()
    radar.wait_for_hardware_trigger.return_value = '{"Q":[1]}'
    radar.rearm_internal_speed_trigger.return_value = True
    processor = MagicMock()
    processor.parse_capture.return_value = IQCapture(
        sample_time=0.0,
        trigger_time=0.1,
        i_samples=[2048],
        q_samples=[2048],
    )
    processor.process_standard.return_value = SpeedTimeline([], 937.5)

    trigger = HardwareTriggeredCapture()
    assert trigger.wait_for_trigger(radar, processor) is None
    assert trigger.drain_diagnostics()[0]["reason"] == "no_ball_speed"

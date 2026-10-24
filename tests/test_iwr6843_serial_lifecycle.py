"""Repeated open/close ownership of the IWR6843 CLI port, with the real device lock.

The serial device is simulated (``tests/iwr6843_firmware_fake.py``).
"""

from __future__ import annotations

import functools
import importlib.util
import sys
from pathlib import Path

import pytest
import serial

from openflight.iwr6843 import device_lock, driver
from openflight.iwr6843.device_lock import IWR6843DeviceBusyError, IWR6843DeviceLock
from openflight.iwr6843.driver import IWR6843Radar
from tests.iwr6843_firmware_fake import FakeClock, FakePort, FirmwareModel, NoLock

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "iwr6843" / "check_cli.py"
SPEC = importlib.util.spec_from_file_location("iwr6843_check_cli_lifecycle", SCRIPT)
CHECK = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = CHECK
SPEC.loader.exec_module(CHECK)

BY_ID = "/dev/serial/by-id/usb-Silicon_Labs_CP2105_Dual_USB_to_UART_Bridge-if00-port0"
TTY = "/dev/ttyUSB0"
CP2105_ENHANCED = ("10c4", "ea70", 0)
CP2105_STANDARD = ("10c4", "ea70", 1)


class TrackedPort(FakePort):
    """A host handle that refuses I/O once closed, like pyserial."""

    def __init__(self, firmware, *, fail_read: Exception | None = None, fail_close=None):
        super().__init__(firmware)
        self.fail_read = fail_read
        self.fail_close = fail_close

    def read(self, size: int) -> bytes:
        if self.closed:
            raise serial.PortNotOpenError()
        if self.fail_read is not None:
            raise self.fail_read
        return super().read(size)

    def close(self) -> None:
        if self.fail_close is not None:
            raise self.fail_close
        super().close()


class SimulatedTty:
    """One CP2105 Enhanced tty: every open is a new handle onto the same board."""

    def __init__(self, clock: FakeClock):
        self.firmware = FirmwareModel(b"", clock)
        self.handles: list[TrackedPort] = []
        self.next_open: list = []

    def open(self, port, *_args, **_kwargs) -> TrackedPort:
        planned = self.next_open.pop(0) if self.next_open else {}
        if isinstance(planned, Exception):
            raise planned
        assert not self.open_handles(), f"second host handle opened on {port}"
        handle = TrackedPort(self.firmware, **planned)
        self.handles.append(handle)
        return handle

    def open_handles(self) -> list[TrackedPort]:
        return [handle for handle in self.handles if not handle.closed]


@pytest.fixture
def board(tmp_path, monkeypatch):
    return make_board(tmp_path, monkeypatch)


def make_board(tmp_path, monkeypatch) -> SimulatedTty:
    """One simulated CP2105 tty behind the real device lock, auto-detected as if00."""
    clock = FakeClock()
    tty = SimulatedTty(clock)
    lock_root = tmp_path / "locks"
    monkeypatch.setattr(driver, "time", clock.module())
    monkeypatch.setattr(driver, "open_port", tty.open)
    monkeypatch.setattr(
        driver, "IWR6843DeviceLock", functools.partial(IWR6843DeviceLock, lock_root=lock_root)
    )
    monkeypatch.setattr(driver, "_resolved_device", lambda port: TTY if port == BY_ID else port)
    monkeypatch.setattr(
        device_lock, "_device_identity", lambda port: TTY if port == BY_ID else port
    )
    monkeypatch.setattr(
        driver.glob,
        "glob",
        lambda pattern: [TTY] if "ttyUSB" in pattern else ([BY_ID] if "by-id" in pattern else []),
    )
    monkeypatch.setattr(
        driver,
        "_usb_serial_identity",
        lambda port: CP2105_ENHANCED if port in (TTY, BY_ID) else None,
    )
    tty.lock_root = lock_root
    return tty


def _lock_is_free(tty: SimulatedTty, port: str = TTY) -> bool:
    probe = IWR6843DeviceLock(port, lock_root=tty.lock_root)
    try:
        probe.acquire()
    except IWR6843DeviceBusyError:
        return False
    probe.release()
    return True


def test_repeated_open_help_close_cycles_leave_no_owner(board):
    for _cycle in range(5):
        radar = IWR6843Radar(port=BY_ID)
        assert not _lock_is_free(board)
        assert "sensorStart" in radar.cmd("help")
        radar.close()
        assert board.open_handles() == []
        assert _lock_is_free(board)

    assert board.firmware.commands == ["help"] * 5
    assert len(board.handles) == 5


def test_by_id_and_resolved_tty_share_one_owner(board):
    radar = IWR6843Radar(port=BY_ID)
    try:
        with pytest.raises(IWR6843DeviceBusyError):
            IWR6843Radar(port=TTY)
    finally:
        radar.close()
    assert len(board.handles) == 1


def test_probe_io_error_on_one_candidate_does_not_hide_the_next(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(driver, "time", clock.module())
    ports = {
        "/dev/ttyUSB0": TrackedPort(
            FirmwareModel(b"", clock), fail_read=serial.SerialException("device vanished")
        ),
        "/dev/ttyUSB1": TrackedPort(FirmwareModel(b"", clock)),
    }
    monkeypatch.setattr(
        driver.glob, "glob", lambda pattern: list(ports) if "USB" in pattern else []
    )
    monkeypatch.setattr(driver, "_usb_serial_identity", lambda _port: None)
    monkeypatch.setattr(driver, "IWR6843DeviceLock", NoLock)
    monkeypatch.setattr(driver, "open_port", lambda port, *_a, **_k: ports[port])

    port, probes = IWR6843Radar.probe_ports()

    assert port == "/dev/ttyUSB1"
    assert probes == ["/dev/ttyUSB0: I/O error during help (device vanished)"]
    assert ports["/dev/ttyUSB0"].closed and ports["/dev/ttyUSB1"].closed

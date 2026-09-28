"""Repeated open/close ownership of the IWR6843 CLI port, with the real device lock.

The serial device is simulated (``tests/iwr6843_firmware_fake.py``); these tests
prove host-side ownership and evidence, not how the physical board behaves.
"""

from __future__ import annotations

import functools
import importlib.util
import json
import sys
from pathlib import Path

import pytest
import serial

from openflight.iwr6843 import device_lock, driver
from openflight.iwr6843.device_lock import IWR6843DeviceBusyError, IWR6843DeviceLock
from openflight.iwr6843.driver import IWR6843Radar
from openflight.iwr6843.static_capture import StaticCaptureInputs, capture_static_range
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


def _evidence(stdout: str) -> dict:
    lines = [line for line in stdout.splitlines() if line.startswith(CHECK.EVIDENCE_PREFIX)]
    assert len(lines) == 1
    return json.loads(lines[0][len(CHECK.EVIDENCE_PREFIX) :])


def test_repeated_open_help_close_cycles_leave_no_owner(board):
    for _cycle in range(5):
        radar = IWR6843Radar(port=BY_ID)
        assert not _lock_is_free(board)
        assert b"sensorStart" in radar.probe_help()
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


def test_failure_mid_help_releases_the_port_and_the_next_open_answers(board, capsys):
    board.next_open.append({"fail_read": serial.SerialException("device disconnected")})

    assert CHECK.main(["--port", BY_ID]) == 1
    assert board.open_handles() == []
    assert _lock_is_free(board)
    failed = _evidence(capsys.readouterr().out)
    assert [event["event"] for event in failed["events"]][-3:] == [
        "help_probe",
        "closed",
        "lock_released",
    ]
    assert failed["events"][-3]["outcome"] == "io_error"

    assert CHECK.main(["--port", BY_ID]) == 0
    assert board.open_handles() == []
    assert _lock_is_free(board)


@pytest.mark.parametrize(
    "planned",
    [
        OSError(16, "Device or resource busy"),
        {"fail_read": serial.SerialException("read failed")},
        {"fail_close": OSError("close failed")},
    ],
    ids=["open_fails", "help_io_error", "close_fails"],
)
def test_every_check_failure_releases_the_device_lock(board, capsys, planned):
    board.next_open.append(planned)

    assert CHECK.main(["--port", BY_ID]) == 1

    assert _lock_is_free(board)
    assert [handle for handle in board.open_handles() if handle.fail_close is None] == []
    assert capsys.readouterr().err.startswith("IWR6843 CLI check failed:")


def test_silent_cli_releases_the_device_lock_and_names_port_and_action(board, capsys, monkeypatch):
    monkeypatch.setattr(board.firmware, "receive", lambda _data: None)

    assert CHECK.main(["--port", BY_ID]) == 1

    assert board.open_handles() == []
    assert _lock_is_free(board)
    captured = capsys.readouterr()
    assert captured.err.strip() == (
        "IWR6843 CLI check failed: IWR6843 CLI did not answer help within 1.5 s on "
        f"{BY_ID} -> {TTY} (CP2105 Enhanced if00); press RESET on the IWR6843 board "
        "once with its switches in functional mode, then run this check again on the same port"
    )
    evidence = _evidence(captured.out)
    assert evidence["requested_port"] == BY_ID
    assert evidence["events"][0]["identity"]["resolved"] == TTY
    assert evidence["error"]["type"] == "RuntimeError"
    probe = [event for event in evidence["events"] if event["event"] == "help_probe"]
    assert probe == [
        {
            "event": "help_probe",
            "port": BY_ID,
            "at_unix_s": probe[0]["at_unix_s"],
            "outcome": "no_reply",
            "window_s": 1.5,
            "elapsed_s": pytest.approx(1.5, abs=0.31),
            "reply_nbytes": 0,
            "reply_head": "",
        }
    ]


def test_probe_of_a_busy_port_never_opens_it_and_leaves_the_owner_intact(board, capsys):
    owner = IWR6843DeviceLock(BY_ID, lock_root=board.lock_root)
    owner.acquire()
    try:
        assert CHECK.main([]) == 1
        assert board.handles == []
        assert not _lock_is_free(board)
        assert "busy" in capsys.readouterr().err
    finally:
        owner.release()

    assert CHECK.main([]) == 0
    assert _lock_is_free(board)
    assert board.open_handles() == []


def test_auto_detection_evidence_records_every_transition(board, capsys):
    assert CHECK.main(["--operator-reset", "pressed"]) == 0

    out = capsys.readouterr().out
    assert out.splitlines()[-1] == f"IWR6843 CLI ready on {TTY}"
    evidence = _evidence(out)
    assert evidence["schema"] == "openflight.iwr6843.cli_check.v1"
    assert evidence["result"] == "ready"
    assert evidence["requested_port"] is None
    assert evidence["port"] == TTY
    assert evidence["operator_reset"] == "pressed"
    assert evidence["error"] is None
    assert [event["event"] for event in evidence["events"]] == [
        "port_identity",
        "lock_acquired",
        "opened",
        "help_probe",
        "closed",
        "lock_released",
        "lock_acquired",
        "opened",
        "help_probe",
        "closed",
        "lock_released",
    ]
    assert evidence["events"][0]["identity"] == {
        "requested": TTY,
        "resolved": TTY,
        "by_id": [BY_ID],
        "usb_vendor_product": "10c4:ea70",
        "usb_interface": 0,
        "cp2105_interface": "CP2105 Enhanced if00",
    }


def test_operator_reset_is_not_recorded_unless_stated(board, capsys):
    assert CHECK.main(["--port", BY_ID]) == 0

    assert _evidence(capsys.readouterr().out)["operator_reset"] is None


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


def test_standard_interface_check_names_the_enhanced_port_to_use(board, capsys, monkeypatch):
    standard = BY_ID.replace("if00", "if01")
    monkeypatch.setattr(
        driver, "_usb_serial_identity", lambda port: CP2105_STANDARD if port == standard else None
    )
    monkeypatch.setattr(board.firmware, "receive", lambda _data: None)

    assert CHECK.main(["--port", standard]) == 1

    assert (
        capsys.readouterr()
        .err.strip()
        .endswith(
            "(CP2105 Standard if01); this is the CP2105 Standard interface, which has no CLI: "
            "run this check with the same adapter's Enhanced if00 port "
            "(/dev/serial/by-id/...-if00-port0)"
        )
    )


def test_failed_static_capture_and_failed_cleanup_leave_the_port_free(board, tmp_path):
    for name in ("radar.cfg", "firmware.bin", "rig.json", "calibration.json"):
        (tmp_path / name).write_bytes(b"sensorStart\n")
    inputs = StaticCaptureInputs(
        capture_id="empty-001",
        capture_kind="empty",
        output_dir=tmp_path / "iwr",
        config_path=tmp_path / "radar.cfg",
        firmware_path=tmp_path / "firmware.bin",
        rig_geometry_path=tmp_path / "rig.json",
        calibration_path=tmp_path / "calibration.json",
        port=BY_ID,
        settle_s=0.25,
    )
    answer = board.firmware.receive
    board.firmware.receive = lambda _data: None

    result = capture_static_range(inputs, wait_for_settle=lambda *_args: False)

    assert result["error"]["stage"] == "configure"
    assert [error["operation"] for error in result["cleanup_errors"]] == ["stop_sensor"]
    assert board.open_handles() == []
    assert _lock_is_free(board)

    board.firmware.receive = answer
    assert CHECK.main(["--port", BY_ID]) == 0
    assert _lock_is_free(board)

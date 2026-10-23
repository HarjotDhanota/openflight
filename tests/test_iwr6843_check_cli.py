"""The tester's IWR6843 hardware check must contact the CLI and explain a miss."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
import serial

from openflight.iwr6843 import driver
from openflight.iwr6843.device_lock import IWR6843DeviceLock
from tests.iwr6843_firmware_fake import FakeClock, FakePort, FirmwareModel, NoLock, SilentPort
from tests.test_iwr6843_serial_lifecycle import (
    BY_ID,
    CP2105_STANDARD,
    TTY,
    _lock_is_free,
    make_board,
)

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "iwr6843" / "check_cli.py"
SPEC = importlib.util.spec_from_file_location("iwr6843_check_cli", SCRIPT)
CHECK = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = CHECK
SPEC.loader.exec_module(CHECK)


@pytest.fixture(name="board")
def _simulated_board(tmp_path, monkeypatch):
    return make_board(tmp_path, monkeypatch)


def _board(monkeypatch, handle, clock):
    monkeypatch.setattr(driver, "time", clock.module())
    monkeypatch.setattr(driver, "IWR6843DeviceLock", NoLock)
    monkeypatch.setattr(driver, "open_port", lambda *_args, **_kwargs: handle)
    monkeypatch.setattr(driver, "_usb_serial_identity", lambda _port: None)
    monkeypatch.setattr(driver, "_resolved_device", lambda port: port)


def test_answering_cli_on_the_configured_port_passes(monkeypatch, capsys):
    clock = FakeClock()
    firmware = FirmwareModel(b"", clock)
    _board(monkeypatch, FakePort(firmware), clock)

    assert CHECK.main(["--port", "/dev/serial/by-id/iwr-if00-port0"]) == 0

    assert firmware.commands == ["help"]
    assert "IWR6843 CLI ready on /dev/serial/by-id/iwr-if00-port0" in capsys.readouterr().out


def test_silent_configured_port_fails_with_one_readable_line(monkeypatch, capsys):
    clock = FakeClock()
    _board(monkeypatch, SilentPort(clock), clock)

    assert CHECK.main(["--port", "/dev/serial/by-id/iwr-if00-port0"]) == 1

    lines = capsys.readouterr().err.strip().splitlines()
    assert lines == [
        "IWR6843 CLI check failed: IWR6843 CLI did not answer help within 1.5 s on "
        "/dev/serial/by-id/iwr-if00-port0; confirm it is the CP2105 Enhanced/UARTA interface "
        "(if00), then press RESET on the IWR6843 board once with its switches in functional "
        "mode, then run this check again on the same port"
    ]


def test_missing_cli_during_autodetection_reports_the_probes(monkeypatch, capsys):
    clock = FakeClock()
    _board(monkeypatch, SilentPort(clock), clock)
    monkeypatch.setattr(
        driver.glob, "glob", lambda pattern: ["/dev/ttyUSB0"] if "USB" in pattern else []
    )
    monkeypatch.setattr(driver, "_usb_serial_identity", lambda _port: ("10c4", "ea70", 0))

    assert CHECK.main([]) == 1

    error = capsys.readouterr().err.strip()
    assert error.startswith("IWR6843 CLI check failed: no IWR6843 CLI found")
    assert "/dev/ttyUSB0 (CP2105 Enhanced if00): no reply to help" in error


def _evidence(stdout: str) -> dict:
    lines = [line for line in stdout.splitlines() if line.startswith(CHECK.EVIDENCE_PREFIX)]
    assert len(lines) == 1
    return json.loads(lines[0][len(CHECK.EVIDENCE_PREFIX) :])


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

"""The tester's IWR6843 hardware check must contact the CLI and explain a miss."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from openflight.iwr6843 import driver
from tests.iwr6843_firmware_fake import FakeClock, FakePort, FirmwareModel, NoLock, SilentPort

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "iwr6843" / "check_cli.py"
SPEC = importlib.util.spec_from_file_location("iwr6843_check_cli", SCRIPT)
CHECK = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = CHECK
SPEC.loader.exec_module(CHECK)


def _board(monkeypatch, handle, clock):
    monkeypatch.setattr(driver, "time", clock.module())
    monkeypatch.setattr(driver, "IWR6843DeviceLock", NoLock)
    monkeypatch.setattr(driver, "open_port", lambda *_args, **_kwargs: handle)


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
        "IWR6843 CLI check failed: IWR6843 CLI did not answer help on "
        "/dev/serial/by-id/iwr-if00-port0; use the CP2105 Enhanced/UARTA interface (if00), "
        "set functional mode, and press RESET"
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

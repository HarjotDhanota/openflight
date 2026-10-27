"""The boot service can name its OPS243 port without changing ExecStart."""

import os

import pytest

from tests.test_start_kiosk import _dry_run


@pytest.fixture
def ops_port(monkeypatch):
    def select(value):
        monkeypatch.setenv("OPENFLIGHT_OPS_PORT", value)
        forwarded = ":".join(
            part for part in (os.environ.get("WSLENV"), "OPENFLIGHT_OPS_PORT") if part
        )
        monkeypatch.setenv("WSLENV", forwarded)

    return select


def test_the_ops_port_setting_reaches_the_server(ops_port):
    ops_port("/dev/ttyAMA0")
    assert _dry_run() == ["openflight-server", "--web-port", "8080", "--port", "/dev/ttyAMA0"]


@pytest.mark.parametrize("alias", ["--radar-port", "--ops-port"])
def test_an_explicit_radar_port_flag_wins_over_the_setting(ops_port, alias):
    ops_port("/dev/ttyAMA0")
    command = _dry_run(alias, "/dev/ttyACM0")
    assert command.count("--port") == 1
    assert command[command.index("--port") + 1] == "/dev/ttyACM0"
    assert "/dev/ttyAMA0" not in command


def test_an_empty_ops_port_setting_leaves_detection_to_the_driver(ops_port):
    ops_port("")
    assert "--port" not in _dry_run()

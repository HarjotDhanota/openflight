"""Known-answer command framing and failures, not target ADC-handler validation."""

import base64
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from openflight.iwr6843.config_trace import record_config_trace, trace_command
from tests.test_iwr6843_clock_evidence import BUILD, reply_text


class Port:
    def __init__(self, chunks=None, transform=None):
        self.time = 1_000_000_000
        self.chunks = list(chunks or [])
        self.transform = transform
        self.commands = []
        self.timeout = 0.3
        self.write_timeout = None

    def now(self):
        return self.time

    def write(self, command):
        self.commands.append(command.decode().strip())
        if self.transform:
            self.chunks = self.transform(command)
        return len(command)

    @property
    def in_waiting(self):
        return len(self.chunks[0][1]) if self.chunks else 0

    def read(self, count):
        if not self.chunks:
            self.time += 100_000_000
            return b""
        delay, value = self.chunks.pop(0)
        assert len(value) <= count
        self.time += delay
        return value

    def reset_input_buffer(self):
        pytest.fail("diagnostic discarded original bytes")


def trace(port, command="adcCfg 2 1", **kwargs):
    return trace_command(port, command, timeout_ns=10_000_000_000, now=port.now, **kwargs)


def test_delayed_completion_retains_raw_bytes_and_actual_read_marks():
    port = Port([(10_000, b"adcCfg 2 1\n"), (2_000_000_000, b"Done\n"), (500_000, b"l3dump:/>")])
    result = trace(port)
    assert result["status"] == "complete"
    assert result["terminal_ns"] == 3_000_010_000
    assert result["cli_ready_ns"] == 3_000_510_000
    assert result["cli_ready_ns"] - result["before_ns"] > 1_500_000_000
    assert base64.b64decode(result["received_b64"]) == b"adcCfg 2 1\nDone\nl3dump:/>"
    assert len(result["reads"]) == 3
    assert port.timeout == 0.3 and port.write_timeout is None


@pytest.mark.parametrize("response", [b"adcCfg 2 1\n", b"Done\n", b"almostDone\nl3dump:/>"])
def test_echo_or_terminal_without_complete_prompt_is_unavailable(response):
    result = trace(Port([(10_000, response)]))
    assert result["status"] == "timeout"
    assert base64.b64decode(result["received_b64"]) == response
    assert result["cli_ready_ns"] is None


def test_rejection_consumes_and_retains_the_prompt():
    result = trace(Port([(10_000, b"Error: rejected\nError -1\nl3dump:/>")]))
    assert result["status"] == "rejected"
    assert result["cli_ready_ns"] is not None


@pytest.mark.parametrize(
    "response",
    [
        b"Done\nDone\nl3dump:/>",
        b"Done\nError -1\nl3dump:/>",
        b"Done\nl3dump:/>extra",
        b"Done\nunexpected\nl3dump:/>",
    ],
)
def test_duplicate_or_ambiguous_completion_is_not_accepted(response):
    assert trace(Port([(1000, response)]))["status"] == "invalid_reply"


def test_crlf_and_optional_prompt_prefix_are_retained():
    raw = b"l3dump:/>adcCfg 2 1\r\nDone\r\nl3dump:/>"
    result = trace(Port([(1000, raw)]))
    assert result["status"] == "complete"
    assert base64.b64decode(result["received_b64"]) == raw


def test_response_read_after_deadline_is_retained_but_not_accepted():
    result = trace(Port([(11_000_000_000, b"Done\nl3dump:/>")]))
    assert result["status"] == "timeout"
    assert result["reads"]


@pytest.mark.parametrize(
    "command", ["sensorStart", "l3dump", "adcCfg 2 1\nhelp", "profileCfg 0", "flash", ""]
)
def test_only_configuration_diagnostic_allowlist_can_be_sent(command):
    port = Port()
    with pytest.raises(ValueError):
        trace(port, command)
    assert not port.commands


def test_write_failure_is_recorded_and_port_timeout_settings_are_restored():
    port = Port()

    def failed(_command):
        raise OSError("synthetic write failure")

    port.write = failed
    result = trace(port)
    assert result["status"] == "io_error"
    assert result["error"] == "OSError"
    assert port.timeout == 0.3 and port.write_timeout is None


def protocol(command):
    text = command.decode().strip()
    if text.startswith("clockProbe"):
        _, hi, lo, seq = text.split()
        response = reply_text(hi + lo, int(seq, 16)).encode()
    elif text == "stats":
        response = command + b"frames=0 active=0 rf_faults=0\nDone\nl3dump:/>"
    else:
        response = command + b"Done\nl3dump:/>"
    return [(100_000, response)]


def record(port):
    rows = []
    nonces = iter(["0000000000000002", "0000000000000003"])
    record_config_trace(
        port,
        rows.append,
        build=BUILD,
        now=port.now,
        nonce=lambda: next(nonces),
        boot=lambda: "synthetic-host",
    )
    return rows


def test_successful_session_uses_fixed_commands_and_matching_identity():
    port = Port(transform=protocol)
    rows = record(port)
    assert rows[-1]["status"] == "complete"
    assert port.commands[1:-1] == [
        "sensorStop",
        "flushCfg",
        "dfeDataOutputMode 1",
        "channelCfg 15 7 0",
        "adcCfg 2 1",
        "stats",
    ]
    assert not any(command.startswith("sensorStart") for command in port.commands)


def test_timeout_stops_without_sending_any_followup_command():
    def delayed(command):
        return [(1000, command)] if command.startswith(b"adcCfg") else protocol(command)

    port = Port(transform=delayed)
    rows = record(port)
    assert rows[-1]["status"] == "unavailable"
    assert rows[-2]["status"] == "timeout"
    assert port.commands[-1] == "adcCfg 2 1"


def test_wrong_build_stops_before_any_configuration():
    def changed(command):
        return [
            (delay, data.replace(BUILD.encode(), b"b" * 64)) for delay, data in protocol(command)
        ]

    port = Port(transform=changed)
    rows = record(port)
    assert rows[-1]["status"] == "unavailable"
    assert len(port.commands) == 1


@pytest.mark.parametrize("fail_adc", [False, True])
def test_cli_writes_fresh_evidence_closes_port_and_never_retries(tmp_path, monkeypatch, fail_adc):
    spec = importlib.util.spec_from_file_location(
        "iwr_config_trace", Path(__file__).parents[1] / "scripts/hardware-test/iwr_config_trace.py"
    )
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)

    def response(command):
        if fail_adc and command.startswith(b"adcCfg"):
            return [(1000, command)]
        return protocol(command)

    port = Port(transform=response)
    closed = []

    class Radar:
        def __enter__(self):
            return SimpleNamespace(ser=port)

        def __exit__(self, *_args):
            closed.append(True)

    monkeypatch.setattr(cli, "IWR6843Radar", lambda **_kwargs: Radar())
    original = cli.record_config_trace
    monkeypatch.setattr(
        cli,
        "record_config_trace",
        lambda *args, **kwargs: original(
            *args, now=port.now, boot=lambda: "synthetic-host", **kwargs
        ),
    )
    output = tmp_path / "trace.jsonl"
    args = ["--port", "synthetic", "--build", BUILD, "--output", str(output)]
    assert cli.main(args) == int(fail_adc)
    records = [json.loads(line) for line in output.read_text().splitlines()]
    assert records[-1]["status"] == ("unavailable" if fail_adc else "complete")
    assert closed == [True]
    commands = port.commands[:]
    assert cli.main(args) == 2
    assert port.commands == commands


def test_runtime_change_stops_after_post_identity_without_claiming_success():
    def changed(command):
        rows = protocol(command)
        if command.startswith(b"clockProbe") and command.endswith(b"00000001\n"):
            rows = [
                (delay, data.replace(b"boot=0000000000000001", b"boot=0000000000000009"))
                for delay, data in rows
            ]
        return rows

    result = record(Port(transform=changed))
    assert result[-1]["status"] == "unavailable"
    assert "runtime identity" in result[-1]["reason"]

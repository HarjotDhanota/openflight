"""Synthetic delayed transfers test the diagnostic, not RF timing or hardware."""

import hashlib

import numpy as np
import pytest

from openflight.iwr6843.dump import pack_dump
from openflight.iwr6843.dump_trace import record_dump_trace
from tests.test_iwr6843_config_trace import Port


def trace(port, **kwargs):
    return record_dump_trace(port, now=port.now, **kwargs)


def payload():
    return pack_dump(np.zeros((1, 2, 4, 8), dtype=complex), n_tx=2)


def test_keeps_listening_through_long_silence_and_retains_every_byte():
    binary = payload()
    prefix = b"l3dump\n\nL3STAGE dump_enter tick=12 frame=5 ring=4 hwa=4 out=4 rearms=4 missed=0 status=0\n"
    raw = prefix + binary + b"Done\nl3dump:/>"
    port = Port([(1000, prefix), (5_000_000_000, binary), (1000, b"Done\nl3dump:/>")])
    saved, result = trace(port)
    assert saved == raw
    assert result["status"] == "complete"
    assert result["payload_offset"] == len(prefix)
    assert result["payload_bytes"] == len(binary)
    assert result["raw_sha256"] == hashlib.sha256(raw).hexdigest()
    assert result["stages"][0]["stage"] == "dump_enter"
    assert result["stages"][0]["tick"] == 12
    assert result["after_ns"] - result["before_ns"] >= 15_000_000_000
    assert port.commands == ["l3dump"]
    assert port.timeout == 0.3 and port.write_timeout is None
    assert result["physical_timing_qualified"] is False


@pytest.mark.parametrize("raw", [b"l3dump\n", b"Done\nl3dump:/>", payload()[:-1], payload()])
def test_echo_prompt_partial_or_payload_without_ready_is_unavailable(raw):
    saved, result = trace(Port([(1, raw)]))
    assert saved == raw
    assert result["status"] == "unavailable"


def test_bytes_after_deadline_retained_but_not_accepted():
    raw = payload() + b"Done\nl3dump:/>"
    saved, result = trace(Port([(16_000_000_000, raw)]))
    assert saved == raw and result["status"] == "unavailable"
    assert result["bytes_by_deadline"] == 0


def test_port_failure_preserves_prefix_and_restores_timeouts():
    port = Port([(1, b"l3dump\n")])
    original = port.read

    def read(count):
        if not port.chunks:
            raise OSError("synthetic")
        return original(count)

    port.read = read
    raw, result = trace(port)
    assert raw == b"l3dump\n" and result["error"] == "OSError"
    assert result["status"] == "io_error"
    assert port.timeout == 0.3 and port.write_timeout is None


def test_partial_write_is_not_retried():
    port = Port()
    port.write = lambda _: 2
    raw, result = trace(port)
    assert not raw and result["status"] == "io_error"


@pytest.mark.parametrize("window", [0, -1, True, 31_000_000_000])
def test_invalid_window_never_writes(window):
    port = Port()
    with pytest.raises(ValueError):
        trace(port, timeout_ns=window)
    assert not port.commands


def test_error_after_valid_payload_is_not_success():
    raw = payload() + b"Error -1\nl3dump:/>"
    _, result = trace(Port([(1, raw)]))
    assert result["status"] == "unavailable"
    assert result["payload_bytes"] == len(payload())


def test_reply_limit_is_bounded_and_retains_the_prefix(monkeypatch):
    monkeypatch.setattr("openflight.iwr6843.dump_trace.MAX_BYTES", 8)
    raw, result = trace(Port([(1, b"12345678")]))
    assert raw == b"12345678" and result["status"] == "reply_limit"


def test_duplicate_completion_is_not_accepted():
    raw = payload() + b"Done\nDone\nl3dump:/>"
    _, result = trace(Port([(1, raw)]))
    assert result["status"] == "unavailable"

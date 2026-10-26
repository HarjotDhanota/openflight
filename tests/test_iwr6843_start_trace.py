"""Synthetic startup responses and command ordering; no RF hardware exercised."""

import base64

import pytest

from openflight.iwr6843.config_trace import send_config_with_start_trace, trace_sensor_start
from tests.test_iwr6843_config_trace import Port, trace


def test_delayed_start_retains_reply_and_marks_after_legacy_window():
    port = Port([(1000, b"sensorStart\n"), (8_000_000_000, b"Done\nl3dump:/>")])
    result = trace_sensor_start(port, now=port.now)
    assert result["status"] == "complete"
    assert result["cli_ready_ns"] - result["before_ns"] > 6_000_000_000
    assert base64.b64decode(result["received_b64"]) == b"sensorStart\nDone\nl3dump:/>"
    assert port.commands == ["sensorStart"]
    assert port.timeout == 0.3 and port.write_timeout is None


@pytest.mark.parametrize("raw", [b"sensorStart\n", b"Done\n", b"almostDone\nl3dump:/>"])
def test_incomplete_start_is_not_retried_or_reported_complete(raw):
    port = Port([(1, raw)])
    result = trace_sensor_start(port, now=port.now)
    assert result["status"] == "timeout" and port.commands == ["sensorStart"]
    assert base64.b64decode(result["received_b64"]) == raw


def test_rf_start_still_forbidden_in_adc_only_entrypoint():
    port = Port()
    with pytest.raises(ValueError):
        trace(port, "sensorStart")
    assert not port.commands


@pytest.mark.parametrize("window", [0, True, -1, 31_000_000_000])
def test_invalid_window_never_starts_rf(window):
    port = Port()
    with pytest.raises(ValueError):
        trace_sensor_start(port, timeout_ns=window, now=port.now)
    assert not port.commands


def test_late_completion_is_preserved_but_unavailable():
    port = Port([(21_000_000_000, b"Done\nl3dump:/>")])
    result = trace_sensor_start(port, now=port.now)
    assert result["status"] == "timeout" and result["received_b64"]


class Radar:
    def __init__(self, raw, *, twice=False):
        self.ser = Port([(1, raw)])
        self.legacy = []
        self.twice = twice

    def cmd(self, text, window):
        self.legacy.append((text, window))
        return "Done\nl3dump:/>"

    def send_config(self, profile, *, responses):
        assert profile == "synthetic-profile"
        commands = ["sensorStop", "adcCfg 2 1", "sensorStart"]
        commands += ["sensorStart"] if self.twice else ["stats"]
        for text in commands:
            responses.append((text, self.cmd(text, 6.0 if text == "sensorStart" else 1.5)))


@pytest.mark.parametrize(
    "raw,ok",
    [(b"Done\nl3dump:/>", True), (b"sensorStart\n", False), (b"Error -1\nl3dump:/>", False)],
)
def test_start_interception_restores_method_and_stops_on_failure(raw, ok):
    radar = Radar(raw)
    original = radar.cmd
    rows, responses = [], []
    if ok:
        send_config_with_start_trace(
            radar, "synthetic-profile", rows.append, responses=responses, now=radar.ser.now
        )
    else:
        with pytest.raises(ValueError):
            send_config_with_start_trace(
                radar, "synthetic-profile", rows.append, responses=responses, now=radar.ser.now
            )
    assert radar.cmd == original
    assert len(rows) == 1 and radar.ser.commands == ["sensorStart"]
    assert rows[0]["caller_window_s"] == 6.0
    assert [text for text, _ in radar.legacy] == ["sensorStop", "adcCfg 2 1"] + (
        ["stats"] if ok else []
    )


def test_duplicate_start_is_never_sent():
    radar = Radar(b"Done\nl3dump:/>", twice=True)
    original = radar.cmd
    with pytest.raises(ValueError):
        send_config_with_start_trace(
            radar, "synthetic-profile", lambda _: None, responses=[], now=radar.ser.now
        )
    assert radar.ser.commands == ["sensorStart"] and radar.cmd == original

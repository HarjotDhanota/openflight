"""Synthetic shutdown ordering and failures, not hardware RF validation."""

import base64

import pytest

from openflight.iwr6843.config_trace import stop_sensor_with_trace
from openflight.iwr6843.driver import IWR6843Radar
from tests.test_iwr6843_config_trace import Port


class Radar:
    stop_sensor = IWR6843Radar.stop_sensor
    _require_done = staticmethod(IWR6843Radar._require_done)

    def __init__(self, *, stop=b"Done\nl3dump:/>", stats=b"active=0 rf_faults=0\nDone\nl3dump:/>"):
        self.ser = Port(transform=lambda data: [(1, stop if data == b"sensorStop\n" else stats)])

    def cmd(self, *_args):
        pytest.fail("cleanup delegated to untraced/flushing command path")


def run(radar, rows):
    return stop_sensor_with_trace(radar, rows.append, now=radar.ser.now)


def test_stop_and_idle_stats_are_retained_and_original_method_is_restored():
    radar = Radar()
    original, rows = radar.cmd, []
    run(radar, rows)
    assert radar.cmd == original
    assert radar.ser.commands == ["sensorStop", "stats"]
    assert [row["command"] for row in rows] == ["sensorStop", "stats"]
    assert all(row["status"] == "complete" and row["caller_window_s"] == 6.0 for row in rows)
    assert radar.ser.timeout == 0.3 and radar.ser.write_timeout is None


@pytest.mark.parametrize("reply", [b"sensorStop\n", b"Error -1\nl3dump:/>", b"Done\n"])
def test_failed_stop_never_sends_stats_or_retries(reply):
    radar = Radar(stop=reply)
    original, rows = radar.cmd, []
    with pytest.raises(ValueError):
        run(radar, rows)
    assert radar.cmd == original and radar.ser.commands == ["sensorStop"]
    assert len(rows) == 1 and base64.b64decode(rows[0]["received_b64"]) == reply


@pytest.mark.parametrize(
    "stats",
    [
        b"stats\n",
        b"active=1 rf_faults=0\nDone\nl3dump:/>",
        b"active=0 rf_faults=1\nDone\nl3dump:/>",
        b"active=0\nDone\nl3dump:/>",
    ],
)
def test_unavailable_active_or_faulted_stats_never_claim_idle(stats):
    radar = Radar(stats=stats)
    original, rows = radar.cmd, []
    with pytest.raises((ValueError, RuntimeError)):
        run(radar, rows)
    assert radar.cmd == original and radar.ser.commands == ["sensorStop", "stats"]
    assert len(rows) == 2


def test_unexpected_shutdown_command_never_reaches_uart():
    radar, rows = Radar(), []
    radar.stop_sensor = lambda: radar.cmd("sensorStart", 6.0)
    original = radar.cmd
    with pytest.raises(ValueError):
        run(radar, rows)
    assert not radar.ser.commands and not rows and radar.cmd == original

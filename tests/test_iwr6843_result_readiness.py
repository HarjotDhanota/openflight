"""Result readiness before releasing a capture, through the production driver."""

import threading
import time
from types import SimpleNamespace

import pytest
from test_iwr6843_driver import _ChunkedReplySerial
from test_iwr6843_monitor import (
    FROZEN_STATS,
    SelfTriggerRadar,
    _raw_dump,
    _self_triggered_capture,
    _started,
    _wait_until,
)

from openflight.iwr6843 import driver
from openflight.iwr6843.driver import IWR6843Radar


def result_reply(ready, shot_id=9):
    packet = bytearray(164)
    packet[0], packet[4], packet[92] = 2, shot_id, 2
    return f"result v2 shot={shot_id} ready={ready}\npacket {packet.hex()}\nDone\nl3dump:/>"


class ResultRadar(SelfTriggerRadar):
    shot_result = IWR6843Radar.shot_result

    def __init__(self, replies):
        super().__init__(_raw_dump())
        self.replies = list(replies)
        self.result_events = []

    def cmd(self, line, window=1.5):
        if line != "triggerLog result":
            return super().cmd(line, window)
        self.result_reads += 1
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        self.result_events.append(("query", threading.current_thread().name))
        if isinstance(reply, Exception):
            raise reply
        return reply

    def release_sparse_freeze(self):
        self.result_events.append(("release", threading.current_thread().name))
        super().release_sparse_freeze()


@pytest.mark.parametrize("self_trigger", [True, False])
def test_result_only_waits_for_ready_before_release(tmp_path, self_trigger):
    radar = ResultRadar([result_reply(0, 8), result_reply(1)])
    radar.stats_replies = [FROZEN_STATS]
    monitor = _started(tmp_path, radar, self_trigger=self_trigger, readback=False)
    try:
        if self_trigger:
            capture = _self_triggered_capture(monitor, radar)
        else:
            edge = time.time()
            assert monitor.notify_trigger(edge)
            capture = monitor.capture_for_shot(edge, timeout_s=1.0)
        assert capture is not None
        assert radar.read_started_at is None
        assert _wait_until(lambda: radar.releases == 1)
    finally:
        monitor.stop()
    assert capture.onboard_result is not None
    assert capture.onboard_result.shot_id == 9
    assert radar.result_events == [
        (name, "iwr6843-capture") for name in ("query", "query", "release")
    ]


def test_result_queries_share_one_deadline(monkeypatch, caplog):
    clock = SimpleNamespace(now=0.0)
    waits, calls = [], []

    def sleep(seconds):
        waits.append(seconds)
        clock.now += seconds

    monkeypatch.setattr(driver, "time", SimpleNamespace(monotonic=lambda: clock.now, sleep=sleep))
    radar = IWR6843Radar.__new__(IWR6843Radar)

    def cmd(line, window):
        calls.append((line, window, clock.now))
        clock.now += min(window, 1.0)
        return result_reply(0, 8)

    monkeypatch.setattr(radar, "cmd", cmd)
    assert radar.shot_result() is None
    assert len(calls) == 3
    assert calls[0][1] == 3.0
    assert all(0 < window <= 3.0 - started for _, window, started in calls)
    assert all(0 < wait <= 0.01 for wait in waits)
    assert clock.now == pytest.approx(3.0)
    assert "not ready" in caplog.text


def test_readiness_queries_consume_chunked_replies_through_the_prompt():
    replies = iter([result_reply(0, 8), result_reply(1)])

    class ResultSerial(_ChunkedReplySerial):
        def write(self, data):
            assert not self.chunks
            reply = ("triggerLog result\r\n" + next(replies)).encode()
            self.pending = [bytearray(reply[i : i + 7]) for i in range(0, len(reply), 7)]
            return super().write(data)

    radar = IWR6843Radar.__new__(IWR6843Radar)
    radar.ser = ResultSerial([])
    radar._trigger_pending = b""
    result = radar.shot_result()
    assert result is not None and result.shot_id == 9
    assert radar.ser.writes == [b"triggerLog result\n"] * 2
    assert not radar.ser.chunks


@pytest.mark.parametrize(
    "reply",
    [
        "'triggerLog' is not recognized as a CLI command\nl3dump:/>",
        "Error: stopped\nDone\nl3dump:/>",
        "result v2 ready=0\npacket incomplete",
        result_reply(10),
        "other ready=0\nDone\nl3dump:/>",
    ],
)
def test_only_explicit_complete_not_ready_reply_is_retried(monkeypatch, reply):
    radar = IWR6843Radar.__new__(IWR6843Radar)
    calls = []
    monkeypatch.setattr(radar, "cmd", lambda *args: calls.append(args) or reply)
    assert radar.shot_result() is None
    assert len(calls) == 1


def test_io_failure_after_not_ready_stops_queries(monkeypatch):
    radar = IWR6843Radar.__new__(IWR6843Radar)
    calls = []

    def cmd(*args):
        calls.append(args)
        if len(calls) == 1:
            return result_reply(0)
        raise OSError("disconnected")

    monkeypatch.setattr(radar, "cmd", cmd)
    with pytest.raises(OSError, match="disconnected"):
        radar.shot_result()
    assert len(calls) == 2


@pytest.mark.parametrize("self_trigger", [True, False])
def test_failed_result_wait_still_rearms_and_allows_the_next_shot(tmp_path, self_trigger):
    radar = ResultRadar([result_reply(0), OSError("disconnected")])
    radar.stats_replies = [FROZEN_STATS]
    monitor = _started(tmp_path, radar, self_trigger=self_trigger, readback=False)

    def capture():
        if self_trigger:
            return _self_triggered_capture(monitor, radar)
        edge = time.time()
        assert monitor.notify_trigger(edge)
        return monitor.capture_for_shot(edge, timeout_s=1.0)

    try:
        first = capture()
        assert first is not None and first.onboard_result is None
        assert radar.result_reads == 2
        assert _wait_until(lambda: radar.releases == 1)
        radar.replies = [result_reply(1, 1)]
        radar.stats_replies = [FROZEN_STATS]
        time.sleep(0.11)
        second = capture()
        assert second is not None and second.onboard_result.shot_id == 1
        assert _wait_until(lambda: radar.releases == 2)
        assert radar.read_started_at is None
    finally:
        monitor.stop()


@pytest.mark.parametrize("self_trigger", [True, False])
def test_never_ready_result_is_unavailable_and_capture_is_rearmed(
    tmp_path, monkeypatch, caplog, self_trigger
):
    clock = SimpleNamespace(now=0.0)

    def sleep(seconds):
        clock.now += seconds

    monkeypatch.setattr(driver, "time", SimpleNamespace(monotonic=lambda: clock.now, sleep=sleep))

    class NeverReadyRadar(ResultRadar):
        def cmd(self, line, window=1.5):
            if line == "triggerLog result":
                clock.now += min(window, 1.0)
            return super().cmd(line, window)

    radar = NeverReadyRadar([result_reply(0, 8)])
    radar.stats_replies = [FROZEN_STATS]
    monitor = _started(tmp_path, radar, self_trigger=self_trigger, readback=False)
    try:
        if self_trigger:
            capture = _self_triggered_capture(monitor, radar)
        else:
            edge = time.time()
            assert monitor.notify_trigger(edge)
            capture = monitor.capture_for_shot(edge, timeout_s=1.0)
        assert capture is not None and capture.onboard_result is None
        assert _wait_until(lambda: radar.releases == 1)
        assert radar.read_started_at is None
    finally:
        monitor.stop()
    assert radar.result_reads == 3
    assert clock.now == pytest.approx(3.0)
    assert "not ready" in caplog.text

"""The first polled IWR latch is observable before the complete result, without a force."""

import base64
from copy import deepcopy

import pytest
from test_joint_shot_clock import dump_reader, wire
from usb_collector import ExchangeError


def capture_sequence(collector, port, states):
    collector.begin()
    index, _ = collector.wait_ball(lambda *_: None, lambda: False)
    collector.arm(index)
    original = port.write
    pending = iter(states)

    def write(data):
        if data == b"stats\n":
            state = next(pending, None)
            if state is not None:
                port.held, port.active = state
        return original(data)

    port.write = write


@pytest.mark.parametrize("chunk", [1, 17, 65536])
def test_latch_callback_precedes_result_fetch_and_fires_only_once(chunk):
    collector, port = wire(chunk)
    capture_sequence(collector, port, [(False, True), (True, True), (True, True), (True, False)])
    notifications = []

    def observed(event):
        assert b"triggerLog result\n" not in port.writes
        assert port.active
        notifications.append(deepcopy(event))

    held = collector.wait_result(lambda *_: None, lambda: False, on_trigger=observed)
    assert held["record"]["shot_id"] == 1
    assert len(notifications) == 1
    event = notifications[0]
    assert event["expected_shot_id"] == event["expected_generation"] == 1
    assert event["capture_active_in_status"] is True
    assert event["device_timestamp"] is None and not event["physical_timing_qualified"]
    assert event["host_received_mono_ns"] >= event["stats_exchange"]["cli_ready_ns"]
    assert collector.rows[event["exchange_index"]]["command"] == "stats"
    assert collector.trigger_observation == event
    assert all(command not in (b"l3dump\n", b"l3release\n") for command in port.writes)


def test_latch_first_observed_after_freeze_is_recorded_as_late():
    collector, port = wire()
    capture_sequence(collector, port, [(True, False)])
    notifications = []
    collector.wait_result(lambda *_: None, lambda: False, on_trigger=notifications.append)
    assert len(notifications) == 1 and not notifications[0]["capture_active_in_status"]


def test_callback_failure_keeps_observation_and_does_not_fetch_or_force_result():
    collector, port = wire()
    capture_sequence(collector, port, [(True, True)])

    def reject(_event):
        raise RuntimeError("downstream notification failed")

    with pytest.raises(RuntimeError, match="downstream notification failed"):
        collector.wait_result(lambda *_: None, lambda: False, on_trigger=reject)
    assert collector.trigger_observation["capture_active_in_status"]
    assert b"triggerLog result\n" not in port.writes
    collector.finish()
    assert collector.state["stop_verified"]


def test_cancellation_before_latch_never_sends_notification():
    collector, port = wire()
    capture_sequence(collector, port, [])
    notifications = []
    with pytest.raises(InterruptedError):
        collector.wait_result(lambda *_: None, lambda: True, on_trigger=notifications.append)
    assert notifications == [] and collector.trigger_observation is None


def deliver_notice_after(port, polls, notice=b"Triggered\n"):
    """The image writes its Triggered line, unsolicited, after this many stats polls."""
    original = port.write
    seen = {"stats": 0}

    def write(data):
        count = original(data)
        if data == b"stats\n":
            seen["stats"] += 1
            if seen["stats"] == polls:
                port.unsolicited = notice
        return count

    def read(n):
        if not port.pending and getattr(port, "unsolicited", b""):
            port.pending, port.unsolicited = port.unsolicited, b""
        size = min(n, port.chunk)
        result, port.pending = port.pending[:size], port.pending[size:]
        return result

    port.write, port.read = write, read


@pytest.mark.parametrize("chunk", [1, 3, 65536])
def test_the_triggered_notice_fires_before_any_latched_poll(chunk):
    collector, port = wire(chunk)
    capture_sequence(collector, port, [(False, True), (False, True), (True, True), (True, False)])
    deliver_notice_after(port, 1)
    notifications = []

    def observed(event):
        assert not port.held
        notifications.append(deepcopy(event))

    held = collector.wait_result(lambda *_: None, lambda: False, on_trigger=observed)
    assert held["record"]["shot_id"] == 1
    assert len(notifications) == 1
    event = notifications[0]
    assert event["notice_source"] == "triggered_line"
    assert event["host_received_mono_ns"] == collector.notice_rows[-1]["notice_ns"]
    assert not event["physical_timing_qualified"]
    assert all(b"Triggered" not in data for data in port.writes)


def test_without_a_notice_the_polled_latch_still_notifies():
    collector, port = wire()
    capture_sequence(collector, port, [(False, True), (True, True), (True, False)])
    notifications = []
    collector.wait_result(lambda *_: None, lambda: False, on_trigger=notifications.append)
    assert [event["notice_source"] for event in notifications] == ["polled_latch"]


@pytest.mark.parametrize("chunk", [1, 3, 65536])
def test_each_successive_attempt_delivers_its_own_triggered_notice(chunk, monkeypatch):
    collector, port = wire(chunk)
    notifications = []
    held_results = []
    radar = dump_reader(port, monkeypatch)

    def observed(event):
        assert port.active and not port.held
        notifications.append(deepcopy(event))
        port.complete_shot()

    for shot_id, generation in ((1, 1), (2, 3)):
        collector.begin()
        assert collector.trigger_observation is None
        index, _ = collector.wait_ball(lambda *_: None, lambda: False)
        collector.arm(index)
        deliver_notice_after(port, 1)
        held_results.append(
            collector.wait_result(lambda *_: None, lambda: False, on_trigger=observed)
        )
        assert len(notifications) == shot_id
        event = notifications[-1]
        assert event["notice_source"] == "triggered_line"
        assert event["expected_shot_id"] == shot_id
        assert event["expected_generation"] == generation
        assert event["host_received_mono_ns"] == collector.notice_rows[-1]["notice_ns"]
        assert not event["physical_timing_qualified"]
        if shot_id == 1:
            saved_event, saved_result = deepcopy(event), deepcopy(held_results[0])
        collector.read_raw_dump(radar)
        collector.finish()

    assert notifications[0] == saved_event and held_results[0] == saved_result
    assert notifications[0]["notice_read_index"] < notifications[1]["notice_read_index"]
    assert notifications[0]["exchange_index"] < notifications[1]["exchange_index"]
    assert notifications[0]["host_received_mono_ns"] < notifications[1]["host_received_mono_ns"]
    assert all(b"Triggered" not in data for data in port.writes)


def test_unexpected_unsolicited_output_marks_the_connection_uncertain():
    collector, port = wire()
    capture_sequence(collector, port, [(False, True), (False, True)])
    deliver_notice_after(port, 1, notice=b"garbage\n")
    with pytest.raises(Exception, match="unexpected unsolicited"):
        collector.wait_result(lambda *_: None, lambda: False, on_trigger=lambda _e: None)
    assert collector.failed
    assert collector.notice_rows[-1]["error"] == "ValueError"


@pytest.mark.parametrize("notice", [b"Triggered\n", b"Triggered\r\n"])
def test_notice_accepts_every_fragmentation_and_keeps_first_byte_time(notice):
    for mask in range(1 << (len(notice) - 1)):
        collector, port = wire()
        port.pending = notice
        boundaries = [0] + [i for i in range(1, len(notice)) if mask & (1 << (i - 1))]
        sizes = iter(b - a for a, b in zip(boundaries, boundaries[1:] + [len(notice)]))
        original = port.read
        port.read = lambda n: original(min(n, next(sizes)))
        writes = list(port.writes)

        received = collector.wait_notice(0.05)

        row = collector.notice_rows[-1]
        assert received == row["notice_ns"] == row["reads"][0]["received_ns"]
        assert row["reads"][-1]["bytes_total"] == len(notice)
        assert base64.b64decode(row["received_b64"]) == notice
        assert row["error"] is None and not collector.failed and not port.pending
        assert port.writes == writes


@pytest.mark.parametrize(
    "notice",
    [b"Triggered\rX", b"Triggered\r\r\n", b"Triggered\nextra", b"Triggered\nTriggered\n"],
)
def test_malformed_extra_or_duplicate_notice_is_retained_and_refuses_commands(notice):
    collector, port = wire()
    port.pending = notice
    writes = list(port.writes)
    with pytest.raises(ExchangeError, match="unexpected unsolicited"):
        collector.wait_notice(0.05)
    assert collector.failed
    assert base64.b64decode(collector.notice_rows[-1]["received_b64"]) == notice
    with pytest.raises(ExchangeError, match="uncertain"):
        collector.command("sensorStop")
    assert port.writes == writes


def test_incomplete_crlf_notice_times_out_with_original_bytes():
    collector, port = wire()
    port.pending = b"Triggered\r"
    with pytest.raises(ExchangeError, match="incomplete unsolicited"):
        collector.wait_notice(0.05)
    row = collector.notice_rows[-1]
    assert collector.failed and row["error"] == "TimeoutError"
    assert base64.b64decode(row["received_b64"]) == b"Triggered\r"
    assert row["after_ns"] >= row["notice_ns"] + 50_000_000


@pytest.mark.parametrize("error_type", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("prefix", [b"", b"T", b"Triggered\r"])
def test_notice_control_exception_poisoning_retains_raw_and_refuses_cleanup(error_type, prefix):
    collector, port = wire()
    chunks = iter([prefix] if prefix else [])
    error = error_type("interrupted notice read")

    def interrupted(_count):
        chunk = next(chunks, None)
        if chunk is None:
            raise error
        return chunk

    port.read = interrupted
    collector.state["rf_start_attempted"] = True
    writes = list(port.writes)
    with pytest.raises(error_type) as caught:
        collector.wait_notice(0.05)
    assert caught.value is error
    row = collector.notice_rows[-1]
    assert collector.failed and row["error"] == error_type.__name__
    assert base64.b64decode(row["received_b64"]) == prefix
    assert [read["bytes_total"] for read in row["reads"]] == ([len(prefix)] if prefix else [])
    assert row["notice_ns"] == (row["reads"][0]["received_ns"] if prefix else None)
    assert row["after_ns"] >= row["before_ns"]
    collector.finish()
    with pytest.raises(ExchangeError, match="uncertain"):
        collector.exchange("stats")
    assert port.writes == writes


@pytest.mark.parametrize("error_type", [OSError, TimeoutError])
def test_notice_read_failure_keeps_exchange_error_contract(error_type):
    collector, port = wire()
    error = error_type("notice read failed")

    def failed_read(_count):
        raise error

    port.read = failed_read
    with pytest.raises(ExchangeError, match="notice read failed") as caught:
        collector.wait_notice(0.05)
    assert caught.value.__cause__ is error
    row = collector.notice_rows[-1]
    assert collector.failed and row["error"] == error_type.__name__
    assert row["received_b64"] == "" and row["reads"] == []

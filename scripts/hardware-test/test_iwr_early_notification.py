"""The first polled IWR latch is observable before the complete result, without a force."""

from copy import deepcopy

import pytest
from test_joint_shot_clock import wire


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

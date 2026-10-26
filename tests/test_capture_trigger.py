"""Known-answer trigger routing, without GPIO, serial ports or timing claims."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from threading import Event

import pytest

from openflight.capture_trigger import CaptureRequest, CaptureTriggerRouter


def request(arm, source="sound_gate", **changes):
    fields = dict(
        event_id="event-a",
        source=source,
        boot_id="boot-a",
        arm_id=arm,
        kernel_mono_interval_ns=(200, 220),
        received_mono_ns=300,
        observer_wall_s=10.0,
    )
    fields.update(changes)
    return CaptureRequest(**fields)


def router(source="sound_gate", consumers=None):
    instance = CaptureTriggerRouter(source, "boot-a", consumers or {"camera": lambda _: True})
    arm_id = instance.arm(100)
    return instance, arm_id


def test_request_keeps_identity_and_unknown_physical_accuracy():
    event = request("arm-a")
    with pytest.raises(FrozenInstanceError):
        event.source = "ops_speed_trigger"
    saved = event.to_dict()
    assert saved["event_id"] == "event-a"
    assert saved["kernel_mono_interval_ns"] == [200, 220]
    assert saved["physical_timing_qualified"] is False
    assert saved["physical_edge_uncertainty_ns"] is None
    assert "impact_timestamp_ns" not in saved


@pytest.mark.parametrize(
    "changes",
    [
        {"event_id": ""},
        {"boot_id": ""},
        {"arm_id": ""},
        {"source": "unknown"},
        {"kernel_mono_interval_ns": [200, 220]},
        {"kernel_mono_interval_ns": (True, 220)},
        {"kernel_mono_interval_ns": (220, 200)},
        {"kernel_mono_interval_ns": (-1, 220)},
        {"received_mono_ns": 210},
        {"received_mono_ns": True},
        {"observer_wall_s": float("nan")},
        {"observer_wall_s": True},
    ],
)
def test_malformed_request_is_rejected_before_dispatch(changes):
    with pytest.raises(ValueError):
        request("arm-a", **changes)


@pytest.mark.parametrize("source", ["sound_gate", "ops_speed_trigger"])
def test_selected_source_reaches_all_consumers_once_with_the_same_request(source):
    seen = []
    instance, arm_id = router(
        source,
        {
            "camera": lambda event: seen.append(("camera", event)) or True,
            "iwr": lambda event: seen.append(("iwr", event)) or True,
        },
    )
    event = request(arm_id, source)
    result = instance.dispatch(event)
    assert result["status"] == "dispatched"
    assert result["consumers"] == {"camera": {"status": "accepted"}, "iwr": {"status": "accepted"}}
    assert seen == [("camera", event), ("iwr", event)]
    assert all(item is event for _, item in seen)
    assert instance.dispatch(event)["reason"] == "capture_already_requested"
    assert (
        instance.dispatch(replace(event, event_id="event-b"))["reason"]
        == "capture_already_requested"
    )
    assert len(seen) == 2


@pytest.mark.parametrize("source", ["sound_gate", "ops_speed_trigger"])
def test_other_sources_and_capture_end_cannot_request_an_extra_capture(source):
    seen = []
    instance, arm_id = router(source, {"camera": lambda event: seen.append(event) or True})
    other = "sound_gate" if source == "ops_speed_trigger" else "ops_speed_trigger"
    for ignored in (other, "ops_capture_end"):
        result = instance.dispatch(request(arm_id, ignored))
        assert result["status"] == "ignored"
        assert result["reason"] == "source_not_selected"
    assert seen == []
    assert instance.dispatch(request(arm_id, source))["status"] == "dispatched"
    assert len(seen) == 1


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"boot_id": "boot-b"}, "boot_mismatch"),
        ({"arm_id": "stale-arm"}, "arm_mismatch"),
        ({"kernel_mono_interval_ns": (80, 90)}, "edge_before_arm"),
        ({"kernel_mono_interval_ns": (90, 110)}, "edge_before_arm"),
        ({"kernel_mono_interval_ns": None}, "kernel_time_unavailable"),
    ],
)
def test_missing_or_stale_clock_evidence_never_falls_back_to_arrival_time(changes, reason):
    instance, arm_id = router(consumers={"camera": lambda _: pytest.fail("must not dispatch")})
    result = instance.dispatch(request(arm_id, **changes))
    assert result["status"] == "rejected"
    assert result["reason"] == reason
    assert result["consumers"] == {}


def test_consumer_failure_does_not_skip_others_or_retry_a_partial_capture():
    seen = []

    def failed(_event):
        raise OSError("synthetic consumer failure")

    instance, arm_id = router(
        consumers={
            "failed": failed,
            "busy": lambda _: False,
            "camera": lambda event: seen.append(event) or True,
        }
    )
    event = request(arm_id)
    result = instance.dispatch(event)
    assert result["consumers"] == {
        "failed": {"status": "error", "error_type": "OSError"},
        "busy": {"status": "rejected"},
        "camera": {"status": "accepted"},
    }
    assert result["request"] == event.to_dict()
    assert instance.dispatch(event)["reason"] == "capture_already_requested"
    assert seen == [event]


def test_rearm_requires_explicit_disarm_and_rejects_stale_request_identity():
    instance, arm_a = router()
    event = request(arm_a)
    instance.dispatch(event)
    with pytest.raises(RuntimeError, match="disarm"):
        instance.arm(400)
    instance.disarm()
    arm_b = instance.arm(400)
    assert arm_a != arm_b
    assert instance.dispatch(event)["reason"] == "arm_mismatch"
    replay = replace(event, arm_id=arm_b, kernel_mono_interval_ns=(500, 520), received_mono_ns=600)
    assert instance.dispatch(replay)["reason"] == "event_reused"
    fresh = replace(replay, event_id="event-b")
    assert instance.dispatch(fresh)["status"] == "dispatched"


def test_callbacks_run_without_the_lock_and_cannot_disarm_in_flight():
    entered, release = Event(), Event()

    def capture(_event):
        entered.set()
        assert release.wait(2)
        return True

    instance, arm_id = router(consumers={"camera": capture})
    event = request(arm_id)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(instance.dispatch, event)
        assert entered.wait(2)
        try:
            duplicate = pool.submit(instance.dispatch, event)
            assert duplicate.result(timeout=2)["reason"] == "capture_already_requested"
            with pytest.raises(RuntimeError, match="in flight"):
                instance.disarm()
        finally:
            release.set()
        assert first.result(timeout=2)["status"] == "dispatched"


@pytest.mark.parametrize("source", ["unknown", "ops_capture_end"])
def test_capture_end_cannot_be_selected_as_the_capture_trigger(source):
    with pytest.raises(ValueError):
        router(source)


def test_unarmed_router_never_requests_capture():
    instance, arm_id = router()
    instance.disarm()
    assert instance.dispatch(request(arm_id))["reason"] == "not_armed"

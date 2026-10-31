"""Native GPIO requests reach the actual camera ring without fabricating sound timing."""

import importlib.util
import json
import time
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import numpy as np
import pytest

from openflight.camera import capture_runtime
from openflight.camera.capture_runtime import CameraCaptureRuntime, CameraCaptureSettings
from openflight.camera.triggered_buffer import CameraFrame
from tests.test_record_ops_sync import cli, fake_api

SCRIPT = Path(__file__).parents[1] / "scripts/hardware-test/joint_clock_rig.py"
SPEC = importlib.util.spec_from_file_location("joint_clock_rig", SCRIPT)
rig_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rig_module)


@pytest.fixture(autouse=True)
def script_imports(monkeypatch):
    """Dynamic loading must provide the same sibling imports as the real launcher."""
    monkeypatch.syspath_prepend(str(SCRIPT.parent))


def test_recorder_stops_cleanly_on_companion_cancellation():
    api, request, _ = fake_api()
    stop = Event()
    stop.set()
    rows = []
    cli.record_gpio(
        "/dev/test", 27, 30, rows.append, api=api, now=lambda: 100, boot_id="boot", stop_event=stop
    )
    assert request.closed
    assert [row["type"] for row in rows] == ["start", "end"]
    assert rows[-1]["pending_at_end"] is False


def control(tmp_path, camera):
    return rig_module.RigControl(
        folder=tmp_path,
        boot_id="boot-a",
        radar=SimpleNamespace(),
        camera=camera,
        recorder=lambda *_args, **_kwargs: None,
        now=lambda: 90,
    )


def test_native_gpio_routes_once_to_camera_and_uses_sensor_tail(tmp_path, monkeypatch):
    ticks = iter([250, 260])
    monkeypatch.setattr(capture_runtime.time, "monotonic_ns", lambda: next(ticks))
    monkeypatch.setattr(capture_runtime, "_boottime_ns", lambda: 300)
    camera = CameraCaptureRuntime(
        output_dir=tmp_path / "camera",
        use_gpio_trigger=False,
        settings=CameraCaptureSettings(fps=300, pre_ms=10, post_ms=10, auto_exposure=False),
    )
    camera._running = True

    def frame(stamp):
        return CameraFrame(np.zeros((2, 3), dtype=np.uint8), stamp, stamp + 40, 300, 4)

    for stamp in (120, 130, 140):
        camera._ring.add_frame(frame(stamp))
    rig = control(tmp_path, camera)
    rig.arm_id = rig.router.arm(90)
    edge = {"type": "edge", "edge": "rising", "timestamp_ns": 100, "received_mono_ns": 110}
    rig.observe_gpio(edge)
    rig.observe_gpio(edge)
    rows = [json.loads(line) for line in (tmp_path / "routing.jsonl").read_text().splitlines()]
    assert rows[0]["consumers"]["camera"]["status"] == "accepted"
    assert rows[1]["reason"] == "capture_already_requested"
    clocks = camera._trigger_clocks.get_nowait()
    assert clocks["trigger_source"] == "ops_speed_trigger"
    assert clocks["capture_request"]["arm_id"] == rig.arm_id
    assert clocks["capture_event_boottime_interval_ns"] == [140, 150]
    assert "gate_boottime_interval_ns" not in clocks
    for stamp in (145, 160, 170):
        camera._ring.add_frame(frame(stamp))
    assert camera._ring.capture_busy
    camera._ring.add_frame(frame(180))
    assert not camera._ring.capture_busy


@pytest.mark.parametrize("edge", ["rising", "falling"])
def test_setup_edges_do_not_trigger_camera_before_arm(tmp_path, edge):
    seen = []
    rig = control(tmp_path, SimpleNamespace(notify_capture_request=lambda item: seen.append(item)))
    rig.observe_gpio({"type": "edge", "edge": edge, "timestamp_ns": 100})
    assert seen == []


def test_boot_or_initial_high_refuses_preparation(tmp_path):
    rig = control(tmp_path, SimpleNamespace(notify_capture_request=lambda _item: True))
    with pytest.raises(ValueError, match="initial level"):
        rig.observe_gpio({"type": "start", "boot_id": "boot-a", "initial_level": 1})
    assert not rig.gpio_ready.is_set()


def test_native_wait_joins_the_existing_trace_without_adding_a_gate_observer(tmp_path, monkeypatch):
    calls = []
    timer = SimpleNamespace(
        attempt_id="owned-trace",
        first_marker_callback=lambda: calls.append("first_byte"),
        response_completed=lambda: calls.append("completed"),
    )
    monkeypatch.setattr(rig_module, "OpsCaptureTimer", SimpleNamespace(start=lambda: timer))
    rig = control(tmp_path, SimpleNamespace(notify_capture_request=lambda _r: True))

    def wait(**kwargs):
        kwargs["on_first_byte"]()
        return ""

    rig.radar = SimpleNamespace(
        wait_for_hardware_trigger=wait, last_hardware_trigger_outcome="timeout"
    )
    rig._capture()
    assert calls == ["first_byte", "completed"]
    assert rig.trace_attempt_id == "owned-trace"
    assert not rig.native_capture and not rig.errors


@pytest.mark.parametrize("complete", [False, True])
def test_native_capture_requires_complete_original_ops_arrays(tmp_path, complete):
    raw = json.dumps(
        {
            "I": [2048] * (4096 if complete else 10),
            "Q": [2048] * 4096,
            "sample_time": 1.0,
            "trigger_time": 1.0256,
        }
    )
    rig = control(tmp_path, SimpleNamespace(notify_capture_request=lambda _: True))
    rig.radar = SimpleNamespace(
        wait_for_hardware_trigger=lambda **_: raw, last_hardware_trigger_outcome="received"
    )
    rig._capture()
    assert (tmp_path / "ops-response.raw").read_text() == raw
    assert rig.native_capture == complete
    assert bool(rig.errors) != complete


@pytest.mark.parametrize("accept", [False, None])
def test_camera_rejection_or_invalid_acknowledgement_marks_trial_failed(tmp_path, accept):
    camera = SimpleNamespace(notify_capture_request=lambda _item: accept, stop=lambda: None)
    rig = control(tmp_path, camera)
    rig.arm_id = rig.router.arm(90)
    rig.observe_gpio(
        {"type": "edge", "edge": "rising", "timestamp_ns": 100, "received_mono_ns": 110}
    )
    with pytest.raises(RuntimeError, match="joint companion failed"):
        rig.close()
    result = json.loads((tmp_path / "RESULT.json").read_text())
    assert result["status"] == "failed"
    assert result["errors"][0]["component"] == "capture-routing"
    assert (tmp_path / "routing.jsonl").is_file()


@pytest.mark.parametrize("edge_during_rearm", [False, True])
def test_native_trial_rearms_after_setup_before_waiting(tmp_path, edge_during_rearm):
    seen = []

    class Radar:
        serial = None
        native_ready = False
        last_hardware_trigger_outcome = "timeout"

        def connect(self):
            return True

        def validate_internal_trigger_firmware(self):
            return "1.3.3"

        def configure_for_internal_speed_trigger(self, **_kwargs):
            self.native_ready = False

        def _send_command(self, command):
            return '{"PowerReduction":3}' if command == "P3" else ""

        def rearm_internal_speed_trigger(self):
            self.native_ready = True
            if edge_during_rearm:
                stamp = time.monotonic_ns()
                rig.observe_gpio(
                    {
                        "type": "edge",
                        "edge": "rising",
                        "timestamp_ns": stamp,
                        "received_mono_ns": stamp,
                    }
                )
            return True

        def wait_for_hardware_trigger(self, **_kwargs):
            if not self.native_ready:
                raise RuntimeError("setup left the radar idle")
            return ""

    def recorder(_chip, _line, _duration, emit, **kwargs):
        emit({"type": "start", "boot_id": "boot-a", "initial_level": 0})
        kwargs["stop_event"].wait(3)

    camera = SimpleNamespace(
        start=lambda: None,
        stop=lambda: None,
        notify_capture_request=lambda item: seen.append(item) is None,
        _ring=SimpleNamespace(latest_frame=None),
    )
    rig = rig_module.RigControl(
        folder=tmp_path / "control",
        boot_id="boot-a",
        radar=Radar(),
        camera=camera,
        recorder=recorder,
    )
    try:
        rig.prepare()
        rig.start_wait()
    finally:
        rig.close()
    assert json.loads((rig.folder / "RESULT.json").read_text())["status"] == "complete"
    assert len(seen) == int(edge_during_rearm)


def iwr_rig(tmp_path, camera, **kwargs):
    return rig_module.RigControl(
        folder=tmp_path,
        boot_id="boot-a",
        radar=SimpleNamespace(),
        camera=camera,
        recorder=lambda *_args, **_kwargs: None,
        now=lambda: 90,
        mode="iwr_request",
        **{"pre_trigger_segments": 16, **kwargs},
    )


@pytest.mark.parametrize("split", [-1, 33, 16.0, True, None])
def test_iwr_request_mode_needs_a_whole_ops_split(tmp_path, split):
    with pytest.raises(ValueError, match="split"):
        iwr_rig(tmp_path, SimpleNamespace(), pre_trigger_segments=split)


def test_iwr_request_mode_guards_the_speed_trigger_and_sets_the_chosen_split(tmp_path):
    configured = []

    class Radar:
        serial = None
        last_hardware_trigger_outcome = "timeout"

        def connect(self):
            return True

        def validate_internal_trigger_firmware(self):
            return "1.3.3"

        def configure_for_internal_speed_trigger(self, **kwargs):
            configured.append(kwargs)

        def _send_command(self, command):
            return '{"PowerReduction":3}' if command == "P3" else ""

        def rearm_internal_speed_trigger(self):
            return True

        def wait_for_hardware_trigger(self, **_kwargs):
            return ""

    def recorder(_chip, _line, _duration, emit, **kwargs):
        emit({"type": "start", "boot_id": "boot-a", "initial_level": 0})
        kwargs["stop_event"].wait(3)

    camera = SimpleNamespace(
        start=lambda: None,
        stop=lambda: None,
        notify_capture_request=lambda _item: True,
        _ring=SimpleNamespace(latest_frame=None),
    )
    rig = rig_module.RigControl(
        folder=tmp_path / "control",
        boot_id="boot-a",
        radar=Radar(),
        camera=camera,
        recorder=recorder,
        mode="iwr_request",
        pre_trigger_segments=16,
    )
    try:
        rig.prepare()
    finally:
        rig.close()
    assert configured == [
        {
            "trigger_threshold_mph": 198,
            "trigger_magnitude": 2000,
            "pre_trigger_segments": 16,
            "sample_rate_ksps": 30,
        }
    ]


def test_one_iwr_notice_requests_one_owned_ops_capture(tmp_path):
    rig = iwr_rig(tmp_path, SimpleNamespace(notify_capture_request=lambda _item: True))
    notice = {"notice_source": "triggered_line", "host_received_mono_ns": 80}
    assert not rig.request_ops_capture(notice)
    assert not rig.request_event.is_set()
    rig.arm_id = rig.router.arm(90)
    assert rig.request_ops_capture(notice)
    assert rig.request_event.is_set()
    assert not rig.request_ops_capture({**notice, "notice_source": "polled_latch"})
    rows = [json.loads(line) for line in (tmp_path / "ops-control.jsonl").read_text().splitlines()]
    assert [row["outcome"] for row in rows] == ["refused_unarmed", "requested", "refused_duplicate"]
    assert rows[1]["arm_id"] == rig.arm_id
    assert rows[1]["iwr_notice"] == notice
    assert rows[1]["physical_timing_qualified"] is False


def test_the_request_reaches_the_single_ops_reader_as_this_attempts_event(tmp_path):
    seen = {}
    rig = iwr_rig(tmp_path, SimpleNamespace(notify_capture_request=lambda _item: True))

    def wait(**kwargs):
        seen.update(kwargs)
        return ""

    rig.radar = SimpleNamespace(
        wait_for_hardware_trigger=wait, last_hardware_trigger_outcome="timeout"
    )
    rig._capture()
    assert seen["software_trigger_event"] is rig.request_event


def test_a_requested_capture_retains_the_camera_under_its_own_source(tmp_path):
    seen = []
    rig = iwr_rig(
        tmp_path, SimpleNamespace(notify_capture_request=lambda item: seen.append(item) is None)
    )
    rig.arm_id = rig.router.arm(90)
    rig.observe_gpio(
        {"type": "edge", "edge": "rising", "timestamp_ns": 100, "received_mono_ns": 110}
    )
    assert [item.source for item in seen] == ["ops_requested_capture"]


def test_a_native_speed_dump_is_refused_in_iwr_request_mode(tmp_path):
    raw = json.dumps(
        {"I": [2048] * 4096, "Q": [2048] * 4096, "sample_time": 1.0, "trigger_time": 1.0256}
    )
    raw = '{"trigger_velocity_detected": -150.0}\n' + raw
    rig = iwr_rig(tmp_path, SimpleNamespace(notify_capture_request=lambda _item: True))
    rig.radar = SimpleNamespace(
        wait_for_hardware_trigger=lambda **_: raw, last_hardware_trigger_outcome="received"
    )
    rig._capture()
    assert (tmp_path / "ops-response.raw").read_text() == raw
    assert not rig.native_capture
    assert rig.ops_capture_origin == "native_speed"
    assert rig.errors[-1]["component"] == "ops-origin"

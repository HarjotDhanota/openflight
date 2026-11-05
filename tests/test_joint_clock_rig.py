"""Native GPIO requests reach the actual camera ring without fabricating sound timing."""

import base64
import importlib.util
import json
import time
from pathlib import Path
from threading import Barrier, Event, Thread, get_ident
from types import SimpleNamespace

import numpy as np
import pytest
import serial

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


def test_simultaneous_iwr_notices_request_exactly_one_capture(tmp_path):
    """Competing notice sources share one atomic request decision."""
    rig = iwr_rig(tmp_path, SimpleNamespace(notify_capture_request=lambda _item: True))
    rig.arm_id = rig.router.arm(90)
    start = Barrier(3)
    accepted = []

    def request(source):
        start.wait(timeout=3)
        accepted.append(rig.request_ops_capture({"notice_source": source}))

    workers = [
        Thread(target=request, args=(source,)) for source in ("triggered_line", "polled_latch")
    ]
    for worker in workers:
        worker.start()
    start.wait(timeout=3)
    for worker in workers:
        worker.join(3)
        assert not worker.is_alive()
    assert sorted(accepted) == [False, True]
    rows = [json.loads(line) for line in (tmp_path / "ops-control.jsonl").read_text().splitlines()]
    assert sorted(row["outcome"] for row in rows) == ["refused_duplicate", "requested"]
    assert {row["arm_id"] for row in rows} == {rig.arm_id}


@pytest.mark.parametrize("terminal", ["cancelled", "completed", "closed"])
def test_first_notice_after_terminal_wait_is_refused_and_journalled(tmp_path, terminal):
    """Terminal attempts cannot accept an event that no reader will consume."""
    rig = iwr_rig(tmp_path, SimpleNamespace(notify_capture_request=lambda _item: True))
    rig.keep_camera = True
    rig.arm_id = rig.router.arm(90)
    if terminal == "cancelled":
        rig.stop.set()
    elif terminal == "completed":
        rig.radar = SimpleNamespace(
            wait_for_hardware_trigger=lambda **_: "", last_hardware_trigger_outcome="timeout"
        )
        rig.start_wait()
        rig.capture_thread.join(3)
        assert not rig.capture_thread.is_alive()
    else:
        rig.close()
    assert not rig.request_ops_capture({"notice_source": "late_latch"})
    assert not rig.request_event.is_set()
    rows = [json.loads(line) for line in (tmp_path / "ops-control.jsonl").read_text().splitlines()]
    assert rows[-1]["outcome"] == "refused_late"


def test_start_wait_cannot_create_a_second_uart_reader(tmp_path):
    """A second launch cannot replace the worker that owns serial input."""
    entered, release = Event(), Event()
    rig = iwr_rig(tmp_path, SimpleNamespace(notify_capture_request=lambda _item: True))
    calls = []

    def wait(**_kwargs):
        calls.append(True)
        entered.set()
        assert release.wait(3)
        return ""

    rig.radar = SimpleNamespace(
        wait_for_hardware_trigger=wait, last_hardware_trigger_outcome="timeout"
    )
    rig.start_wait()
    first_worker = rig.capture_thread
    try:
        assert entered.wait(3)
        with pytest.raises(RuntimeError, match="already started"):
            rig.start_wait()
    finally:
        release.set()
        first_worker.join(3)
        rig.capture_thread.join(3)
    assert calls == [True]


def borrowed_iwr_rig(tmp_path, monkeypatch):
    """Use the real reader, replacing only devices and setup exchanges."""
    from tests.test_ops243_software_trigger_wait import RequestedCapturePort, make_radar

    port = RequestedCapturePort()
    radar = make_radar(port)
    monkeypatch.setattr(radar, "validate_internal_trigger_firmware", lambda: "1.3.3")
    monkeypatch.setattr(radar, "configure_for_internal_speed_trigger", lambda **_: None)
    monkeypatch.setattr(radar, "rearm_internal_speed_trigger", lambda: True)
    monkeypatch.setattr(
        radar, "_send_command", lambda command: '{"PowerReduction":3}' if command == "P3" else ""
    )
    disconnected = []
    monkeypatch.setattr(radar, "disconnect", lambda: disconnected.append(True))

    def original_callback(_row):
        pass

    radar.capture_evidence_callback = original_callback
    camera = SimpleNamespace(
        start=lambda: None,
        stop=lambda: pytest.fail("the borrowed camera must remain running"),
        notify_capture_request=lambda _item: True,
        _ring=SimpleNamespace(latest_frame=None),
    )

    def recorder(_chip, _line, _duration, emit, **kwargs):
        emit({"type": "start", "boot_id": "boot-a", "initial_level": 0})
        assert kwargs["stop_event"].wait(3)

    rig = rig_module.RigControl(
        folder=tmp_path / "attempt-1",
        boot_id="boot-a",
        radar=radar,
        camera=camera,
        recorder=recorder,
        mode="iwr_request",
        keep_radar=True,
        keep_camera=True,
    )
    return rig, port, original_callback, disconnected


def test_consecutive_iwr_attempts_restore_borrowed_devices_and_separate_journals(
    tmp_path, monkeypatch
):
    """New rig instances have fresh ownership and release their borrowed journal."""
    rig, port, callback, disconnected = borrowed_iwr_rig(tmp_path, monkeypatch)
    raw = (
        json.dumps(
            {"sample_time": "1", "trigger_time": "1.068", "I": [2048] * 4096, "Q": [2048] * 4096}
        ).encode("ascii")
        + b"\r\n"
    )

    def write(command):
        port.writes.append((command, get_ident()))
        port.chunks.append(raw)
        return len(command)

    monkeypatch.setattr(port, "write", write)
    arms, events = [], []
    first_folder = rig.folder
    first_journal = None
    for index in (1, 2):
        if index == 2:
            rig = rig_module.RigControl(
                folder=tmp_path / "attempt-2",
                boot_id="boot-a",
                radar=rig.radar,
                camera=rig.camera,
                recorder=rig.recorder,
                mode="iwr_request",
                keep_radar=True,
                keep_camera=True,
            )
        try:
            rig.prepare()
            assert rig.radar.serial.serial is port
            arms.append(rig.arm_id)
            events.append(rig.request_event)
            assert not rig.request_event.is_set()
            assert rig.request_ops_capture({"notice_source": "triggered_line"})
            rig.start_wait()
        finally:
            rig.close()
        assert rig.native_capture and not rig.errors
        assert rig.radar.serial is port
        assert rig.radar.capture_evidence_callback is callback
        assert (rig.folder / "ops-response.raw").read_bytes() == raw
        if index == 1:
            first_journal = (first_folder / "ops-serial.jsonl").read_bytes()
        else:
            assert (first_folder / "ops-serial.jsonl").read_bytes() == first_journal
    assert arms[0] != arms[1] and events[0] is not events[1]
    assert len(port.writes) == 2 and disconnected == []
    assert all(command == b"S!\r" for command, _thread in port.writes)
    assert {writer for _command, writer in port.writes} == port.reader_threads
    assert get_ident() not in port.reader_threads


@pytest.mark.parametrize(
    "failure", ["short_write", "flush_error", "read_error", "partial", "cancelled"]
)
def test_failed_requested_transfer_retains_evidence_and_restores_borrowed_port(
    tmp_path, monkeypatch, failure
):
    """Command/read failures retain raw evidence without accepting partial captures."""
    rig, port, callback, disconnected = borrowed_iwr_rig(tmp_path, monkeypatch)
    partial = b'{"sample_time":"1"}\r\n{"I":[2048'
    if failure == "short_write":
        port.write_result = 2
    elif failure == "flush_error":
        port.flush_error = True
    elif failure in ("read_error", "partial"):

        def write(command):
            port.writes.append((command, get_ident()))
            port.chunks.append(partial)
            return len(command)

        monkeypatch.setattr(port, "write", write)
        if failure == "read_error":
            original_read = port.read

            def fail_read(_size):
                raise serial.SerialException("transfer failed")

            def read(size):
                data = original_read(size)
                port.chunks.append(b"trailing")
                monkeypatch.setattr(port, "read", fail_read)
                return data

            monkeypatch.setattr(port, "read", read)
    ticks = iter(range(1000, 1100))
    monkeypatch.setattr("openflight.ops243.time.time", lambda: next(ticks))
    monkeypatch.setattr("openflight.ops243.time.sleep", lambda _seconds: None)
    rig.prepare()
    assert rig.request_ops_capture({"notice_source": "triggered_line"})
    if failure == "cancelled":
        entered, release = Event(), Event()
        original_wait = rig.radar.wait_for_hardware_trigger

        def wait(**kwargs):
            entered.set()
            assert release.wait(3)
            return original_wait(**kwargs)

        monkeypatch.setattr(rig.radar, "wait_for_hardware_trigger", wait)
        rig.start_wait()
        assert entered.wait(3)
        rig.stop.set()
        release.set()
    else:
        rig.start_wait()
    if failure == "cancelled":
        rig.close()
    else:
        with pytest.raises(RuntimeError, match="original evidence retained"):
            rig.close()
    assert rig.radar.serial is port and rig.radar.capture_evidence_callback is callback
    assert disconnected == [] and not rig.native_capture
    assert len(port.writes) == int(failure != "cancelled")
    evidence = json.loads((rig.folder / "ops.jsonl").read_text().splitlines()[-1])
    assert evidence["outcome"] == (
        "cancelled" if failure == "cancelled" else "capture" if failure == "partial" else "error"
    )
    if failure in ("read_error", "partial"):
        assert base64.b64decode(evidence["raw_capture_base64"]) == partial
    if failure == "partial":
        assert (rig.folder / "ops-response.raw").read_bytes() == partial
    result = json.loads((rig.folder / "RESULT.json").read_text())
    assert result["status"] == ("complete" if failure == "cancelled" else "failed")
    assert result["physical_timing_qualified"] is False


def test_failed_prepare_closes_request_admission_and_restores_borrowed_devices(
    tmp_path, monkeypatch
):
    """An allocated arm ID cannot admit requests after explicit rearm failed."""
    rig, port, callback, disconnected = borrowed_iwr_rig(tmp_path, monkeypatch)
    monkeypatch.setattr(rig.radar, "rearm_internal_speed_trigger", lambda: False)
    try:
        with pytest.raises(RuntimeError, match="rearm failed"):
            rig.prepare()
        assert rig.arm_id is not None
        assert not rig.request_ops_capture({"notice_source": "late_latch"})
        with pytest.raises(RuntimeError, match="closed"):
            rig.start_wait()
    finally:
        with pytest.raises(RuntimeError, match="original evidence retained"):
            rig.close()
    assert rig.radar.serial is port and rig.radar.capture_evidence_callback is callback
    assert disconnected == [] and port.writes == []


def test_close_in_progress_refuses_a_notice_while_wait_finishes(tmp_path, monkeypatch):
    """Closing stops admission before waiting for the existing reader to return."""
    rig = iwr_rig(tmp_path, SimpleNamespace(notify_capture_request=lambda _item: True))
    rig.keep_camera = True
    rig.arm_id = rig.router.arm(90)
    entered, release, joining = Event(), Event(), Event()

    def wait(**_kwargs):
        entered.set()
        assert release.wait(3)
        return ""

    rig.radar = SimpleNamespace(
        wait_for_hardware_trigger=wait, last_hardware_trigger_outcome="timeout"
    )
    rig.start_wait()
    assert entered.wait(3)
    original_join = rig.capture_thread.join

    def join(timeout):
        joining.set()
        original_join(timeout)

    monkeypatch.setattr(rig.capture_thread, "join", join)
    closer = Thread(target=rig.close)
    closer.start()
    try:
        assert joining.wait(3)
        assert not rig.request_ops_capture({"notice_source": "closing_latch"})
        assert not rig.request_event.is_set()
    finally:
        release.set()
        closer.join(3)
    assert not closer.is_alive() and not rig.errors
    with pytest.raises(RuntimeError, match="closed"):
        rig.start_wait()


def test_exhausted_reader_bound_is_recorded_as_failed_without_claiming_recovery(tmp_path):
    """A fatal reader timeout stays failed in the retained result."""
    rig = iwr_rig(tmp_path, SimpleNamespace(notify_capture_request=lambda _item: True))
    rig.keep_camera = rig.keep_radar = True
    disconnected = []
    rig.radar = SimpleNamespace(disconnect=lambda: disconnected.append(True))
    rig.capture_thread = SimpleNamespace(join=lambda _timeout: None, is_alive=lambda: True)
    with pytest.raises(RuntimeError, match="did not return"):
        rig.close()
    assert disconnected == [True] and rig.stop.is_set()
    result = json.loads((tmp_path / "RESULT.json").read_text())
    assert result["status"] == "failed"
    assert result["errors"] == [
        {"component": "ops", "error": "OPS collector did not return within its bound"}
    ]
    assert not rig.request_ops_capture({"notice_source": "late_latch"})


def test_notice_after_reader_return_cannot_relabel_unrequested_raw(tmp_path, monkeypatch):
    """Post-read processing cannot extend the request admission window."""
    entered, release = Event(), Event()
    raw = json.dumps(
        {"sample_time": "1", "trigger_time": "1.068", "I": [2048] * 4096, "Q": [2048] * 4096}
    )
    rig = iwr_rig(tmp_path, SimpleNamespace(notify_capture_request=lambda _item: True))
    rig.arm_id = rig.router.arm(90)
    rig.radar = SimpleNamespace(
        wait_for_hardware_trigger=lambda **_: raw, last_hardware_trigger_outcome="capture"
    )

    def completed():
        entered.set()
        assert release.wait(3)

    timer = SimpleNamespace(first_marker_callback=lambda: None, response_completed=completed)
    monkeypatch.setattr(rig_module, "OpsCaptureTimer", SimpleNamespace(start=lambda: timer))
    rig.start_wait()
    try:
        assert entered.wait(3)
        assert not rig.request_ops_capture({"notice_source": "late_latch"})
    finally:
        release.set()
        rig.capture_thread.join(3)
    assert rig.ops_capture_origin == "unrequested" and not rig.native_capture
    assert (tmp_path / "ops-response.raw").read_text() == raw


def test_notice_during_an_existing_dump_does_not_claim_a_software_requested_capture(
    tmp_path, monkeypatch
):
    """An event alone cannot prove that this reader issued the capture command."""
    rig, port, _callback, _disconnected = borrowed_iwr_rig(tmp_path, monkeypatch)
    raw = (
        json.dumps(
            {"sample_time": "1", "trigger_time": "1.068", "I": [2048] * 4096, "Q": [2048] * 4096}
        ).encode("ascii")
        + b"\r\n"
    )
    entered, release = Event(), Event()
    original_read = port.read

    def read(size):
        data = original_read(size)
        if not entered.is_set():
            entered.set()
            assert release.wait(3)
        return data

    monkeypatch.setattr(port, "read", read)
    rig.prepare()
    port.chunks.extend([raw[:100], raw[100:]])
    rig.start_wait()
    try:
        assert entered.wait(3)
        assert rig.request_ops_capture({"notice_source": "triggered_line"})
    finally:
        release.set()
        rig.capture_thread.join(3)
    with pytest.raises(RuntimeError, match="original evidence retained"):
        rig.close()
    assert port.writes == []
    assert rig.radar.last_hardware_capture_evidence["software_trigger_command"] is None
    assert not rig.native_capture and rig.ops_capture_origin == "unrequested"
    assert (rig.folder / "ops-response.raw").read_bytes() == raw

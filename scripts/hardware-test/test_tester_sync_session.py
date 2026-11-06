"""Operator phase identity and actual saved camera evidence checks."""

import base64
import json
from types import SimpleNamespace

import numpy as np
import pytest
from tester_sync_session import SessionFiles, camera_evidence


@pytest.mark.parametrize("error_type", [None, OSError, KeyboardInterrupt])
def test_attempt_persists_full_or_interrupted_notice_with_global_index(
    tmp_path, monkeypatch, error_type
):
    import threading

    import joint_clock_rig
    import tester_sync_session as session
    from test_iwr_early_notification import deliver_notice_after
    from test_joint_shot_clock import wire

    from openflight.camera import capture_runtime

    collector, port = wire(1)
    port.pending = b"Triggered\r\n"
    collector.wait_notice(0.05)
    original_wait = collector.wait_result

    def wait_result(publish, cancelled, *, on_trigger):
        def triggered(event):
            port.complete_shot()
            on_trigger(event)

        return original_wait(publish, cancelled, on_trigger=triggered)

    class Rig:
        arm_id, boot_id, native_capture = "arm", "synthetic-boot", False
        trace_attempt_id = "notice-retention"

        def __init__(self, **_kwargs):
            self.stop = threading.Event()

        def prepare(self):
            pass

        def start_wait(self):
            deliver_notice_after(port, 1)
            original = port.read

            def read(count):
                if error_type is not None and port.pending == b"riggered\n":
                    raise error_type("interrupted notice")
                return original(count)

            port.read = read

        def close(self):
            pass

    monkeypatch.setattr(joint_clock_rig, "RigControl", Rig)
    monkeypatch.setattr(capture_runtime, "CameraCaptureRuntime", lambda **_kwargs: object())
    monkeypatch.setattr(collector, "wait_result", wait_result)
    monkeypatch.setattr(collector, "read_raw_dump", lambda _radar: (b"raw", {"bytes": 3}))
    files = SessionFiles(tmp_path)
    folder = tmp_path / "attempt-001"
    if error_type is None:
        session.attempt(collector, files, folder)
    else:
        expected = error_type if error_type is KeyboardInterrupt else RuntimeError
        with pytest.raises(expected):
            session.attempt(collector, files, folder)
    result = json.loads((folder / "RESULT.json").read_text())
    assert result["iwr_notice_start_index"] == 1
    assert result["iwr_notices"] == collector.notice_rows[1:]
    row = result["iwr_notices"][0]
    assert base64.b64decode(row["received_b64"]) == (b"T" if error_type else b"Triggered\n")
    assert row["error"] == (error_type.__name__ if error_type else None)
    assert row["reads"][0]["received_ns"] == row["notice_ns"]
    if error_type is None:
        observation = result["iwr_trigger_observation"]
        assert observation["notice_read_index"] == result["iwr_notice_start_index"]
        assert not observation["physical_timing_qualified"]
        previous_bytes = (folder / "RESULT.json").read_bytes()
        next_folder = tmp_path / "attempt-002"
        session.attempt(collector, files, next_folder)
        later = json.loads((next_folder / "RESULT.json").read_text())
        assert later["iwr_notice_start_index"] == 2
        assert later["iwr_notices"] == collector.notice_rows[2:]
        assert later["iwr_trigger_observation"]["notice_read_index"] == 2
        assert (folder / "RESULT.json").read_bytes() == previous_bytes


@pytest.mark.parametrize("early_failure", [False, True])
def test_runtime_exit_persists_notice_journal_before_any_attempt(
    tmp_path, monkeypatch, early_failure
):
    import sys
    from contextlib import nullcontext

    import tester_sync_session as session
    from test_joint_shot_clock import wire

    collector, port = wire(1)
    error = KeyboardInterrupt("interrupted before first attempt")

    def observe():
        port.pending = b"Triggered\n"
        original = port.read

        def read(count):
            if early_failure and port.pending == b"riggered\n":
                raise error
            return original(count)

        monkeypatch.setattr(port, "read", read)
        collector.wait_notice(0.05)

    monkeypatch.setattr(collector, "establish_fresh_runtime", observe)
    monkeypatch.setitem(
        sys.modules, "joint_shot_clock", SimpleNamespace(ShotCollector=lambda *_a, **_kw: collector)
    )
    helper = SimpleNamespace(
        require_unowned=lambda: None,
        enhanced_port=lambda: "fake-port",
        IWR6843DeviceLock=lambda _port: nullcontext(),
        open_serial=lambda _port: nullcontext(SimpleNamespace()),
        RamOnlyLoader=lambda _handle: SimpleNamespace(load_ram=lambda _raw: None),
        startup=lambda _handle: b"synthetic startup",
        verify_package=lambda: {"source_fingerprint": collector.build},
        boot_identity=lambda: "synthetic-boot",
    )
    monkeypatch.setattr(session, "load_package", lambda *_a, **_kw: helper)
    monkeypatch.setattr(session, "resource_ownership", lambda: {"resources_released": True})
    (tmp_path / "clock.ram.bin").write_bytes(b"synthetic RAM")
    files = SessionFiles(tmp_path / "session")
    monkeypatch.setattr(files, "wait", lambda _phase: None)
    if early_failure:
        with pytest.raises(KeyboardInterrupt) as caught:
            with session.open_runtime(tmp_path, files):
                pytest.fail("interrupted initialization must not yield a runtime")
        assert caught.value is error
    else:
        with session.open_runtime(tmp_path, files):
            pass
    saved = json.loads((files.folder / "iwr-notices.json").read_text())
    assert saved == collector.notice_rows
    assert base64.b64decode(saved[0]["received_b64"]) == (b"T" if early_failure else b"Triggered\n")
    assert saved[0]["error"] == ("KeyboardInterrupt" if early_failure else None)
    assert files.state["attempts"] == []


def test_old_operator_confirmation_cannot_advance_a_new_phase(tmp_path):
    io = SessionFiles(tmp_path)
    io.publish("switches")
    old = io.state["phase_token"]
    io.publish("reset")
    (tmp_path / "commands.jsonl").write_text(
        json.dumps({"phase_token": old, "command": "reset_pressed"}) + "\n"
    )
    assert io.command() is None


def test_phase_updates_keep_token_and_partial_write_is_not_consumed(tmp_path):
    io = SessionFiles(tmp_path)
    io.publish("armed")
    token = io.state["phase_token"]
    io.publish("armed", frames=12)
    assert io.state["phase_token"] == token
    row = json.dumps({"phase_token": token, "command": "stop"})
    (tmp_path / "commands.jsonl").write_text(row)
    assert io.command() is None and io.offset == 0
    (tmp_path / "commands.jsonl").write_text(row + "\n")
    with pytest.raises(InterruptedError):
        io.command()


def test_camera_request_acceptance_without_saved_pixels_is_not_success(tmp_path):
    rig = SimpleNamespace(arm_id="arm", boot_id="boot")
    shot = tmp_path / "camera/capture-001"
    shot.mkdir(parents=True)
    (shot / "metadata.json").write_text(
        json.dumps({"trigger_clocks": {"capture_request": {"arm_id": "arm", "boot_id": "boot"}}})
    )
    assert camera_evidence(tmp_path, rig)["status"] == "missing_or_ambiguous"
    (shot / "frames.npz").write_bytes(b"truncated capture")
    assert camera_evidence(tmp_path, rig)["status"] == "missing_or_ambiguous"
    np.savez(
        shot / "frames.npz",
        frames=np.zeros((2, 4, 4), np.uint8),
        sensor_timestamp_ns=np.array([10, 20]),
        host_timestamp_ns=np.array([30, 40]),
    )
    (shot / "metadata.json").write_text(
        json.dumps(
            {
                "trigger_clocks": {"capture_request": {"arm_id": "arm", "boot_id": "boot"}},
                "pre_trigger_frames": 1,
                "post_trigger_frames": 1,
            }
        )
    )
    assert camera_evidence(tmp_path, rig)["status"] == "captured"
    rig.arm_id = "other-arm"
    assert camera_evidence(tmp_path, rig)["status"] == "missing_or_ambiguous"


def test_ownership_failure_is_recorded_without_hiding_cleanup(monkeypatch):
    import tester_sync_session as session

    def fail(*_args, **_kwargs):
        raise OSError("fuser unavailable")

    monkeypatch.setattr(session.subprocess, "run", fail)
    result = session.resource_ownership()
    assert result["resources_released"] is False
    assert "fuser unavailable" in result["ownership_error"]


@pytest.mark.parametrize("plan", ["default", "club51"])
@pytest.mark.parametrize("stop_fails", [False, True])
def test_attempt_saves_real_parser_output_and_original_bytes(
    tmp_path, monkeypatch, stop_fails, plan
):
    import threading

    import joint_clock_rig
    import tester_sync_session as session
    from test_joint_shot_clock import wire

    from openflight.camera import capture_runtime

    collector, port = wire(capture_plan=plan)
    port.ball_bin = 30  # inside both plans' pre windows

    class Rig:
        arm_id, boot_id, native_capture = "arm", "synthetic-boot", True
        trace_attempt_id = "parser-attempt-trace"

        def __init__(self, **_kwargs):
            self.stop = threading.Event()

        def prepare(self):
            pass

        def start_wait(self):
            port.active, port.held = False, True

        def close(self):
            pass

    monkeypatch.setattr(joint_clock_rig, "RigControl", Rig)
    monkeypatch.setattr(capture_runtime, "CameraCaptureRuntime", lambda **_kwargs: object())

    def raw(_radar):
        assert collector.next_shot == 2
        assert (tmp_path / "attempt-001/iwr-held.json").is_file()
        return b"original raw dump fixture", {"bytes": 25}

    monkeypatch.setattr(collector, "read_raw_dump", raw)
    files = SessionFiles(tmp_path)
    if stop_fails:

        def bad_stop():
            raise RuntimeError("stop not verified")

        monkeypatch.setattr(collector, "finish", bad_stop)
        with pytest.raises(RuntimeError, match="stop not verified"):
            session.attempt(collector, files, tmp_path / "attempt-001")
    else:
        session.attempt(collector, files, tmp_path / "attempt-001")
    held = json.loads((tmp_path / "attempt-001/iwr-held.json").read_text())
    assert len(held["clock_observations"]) == 2
    assert held["record"]["shot_id"] == 1
    assert (tmp_path / "attempt-001/iwr-result.bin").read_bytes() == port.result
    result = json.loads((tmp_path / "attempt-001/RESULT.json").read_text())
    assert result["status"] == "partial"
    assert result["trace_attempt_id"] == "parser-attempt-trace"
    assert ("stop_error" in result) == stop_fails
    assert result["iwr_capture_plan"] == collector.capture_plan
    assert result["iwr_capture_plan"]["name"] == plan
    sent = [row["command"] for row in result["iwr_exchanges"] if "command" in row]
    phase = [line for line in result["iwr_capture_plan"]["commands"] if "phaseCapture" in line]
    assert phase == [line for line in sent if line.startswith("phaseCaptureCfg")]
    assert collector.state["stop_verified"] != stop_fails


@pytest.mark.parametrize("new_fault", [None, "missing_pixels", "duplicate"])
def test_current_attempt_is_not_poisoned_by_a_previous_incomplete_camera_clip(
    tmp_path, monkeypatch, new_fault
):
    import threading

    import joint_clock_rig
    import tester_sync_session as session
    from test_joint_shot_clock import wire

    collector, port = wire()
    camera_root = tmp_path / "run"
    old = camera_root / "camera/capture-old/metadata.json"
    old.parent.mkdir(parents=True)
    old.write_text('{"interrupted":')
    camera = SimpleNamespace(output_dir=camera_root / "camera")

    def save_clip(name):
        folder = camera.output_dir / name
        folder.mkdir()
        (folder / "metadata.json").write_text(
            json.dumps(
                {
                    "pre_trigger_frames": 1,
                    "post_trigger_frames": 1,
                    "trigger_clocks": {
                        "capture_request": {"arm_id": "current", "boot_id": "synthetic-boot"}
                    },
                }
            )
        )
        if new_fault != "missing_pixels":
            np.savez(
                folder / "frames.npz",
                frames=np.zeros((2, 4, 4), np.uint8),
                sensor_timestamp_ns=np.array([10, 20]),
                host_timestamp_ns=np.array([30, 40]),
            )

    class Rig:
        arm_id, boot_id, native_capture = "current", "synthetic-boot", True
        trace_attempt_id = "camera-attempt-trace"

        def __init__(self, **_kwargs):
            self.stop = threading.Event()

        def prepare(self):
            pass

        def start_wait(self):
            port.active, port.held = False, True

        def close(self):
            save_clip("capture-current")
            if new_fault == "duplicate":
                save_clip("capture-duplicate")

    monkeypatch.setattr(joint_clock_rig, "RigControl", Rig)
    monkeypatch.setattr(collector, "read_raw_dump", lambda _radar: (b"original IWR", {"bytes": 12}))
    files = SessionFiles(tmp_path / "control")
    session.attempt(collector, files, tmp_path / "attempt-001", camera=camera)
    result = json.loads((tmp_path / "attempt-001/RESULT.json").read_text())
    assert result["status"] == ("captured_unqualified" if new_fault is None else "partial")
    assert result["trace_attempt_id"] == "camera-attempt-trace"
    assert old.read_text() == '{"interrupted":'


@pytest.mark.parametrize("failed_at", ["switches", "port_lookup", "reset"])
def test_boot_restore_requirement_is_persisted_before_uart_setup_or_reset(
    tmp_path, monkeypatch, failed_at
):
    from contextlib import nullcontext

    import tester_sync_session as session
    from test_joint_shot_clock import wire

    wire()
    files, observed = SessionFiles(tmp_path), []

    def wait(expected):
        observed.append(json.loads((tmp_path / "status.json").read_text()))
        if (expected == "switches_set" and failed_at == "switches") or expected == "reset_pressed":
            raise RuntimeError("test interruption before RAM load")

    def port():
        if failed_at == "port_lookup":
            raise RuntimeError("test interruption during port lookup")
        return "/dev/test"

    helper = SimpleNamespace(
        require_unowned=lambda: None,
        enhanced_port=port,
        IWR6843DeviceLock=lambda _port: nullcontext(),
        open_serial=lambda _port: nullcontext(object()),
    )
    monkeypatch.setattr(session, "load_package", lambda _path, **_kwargs: helper)
    monkeypatch.setattr(session, "resource_ownership", lambda: {"resources_released": True})
    monkeypatch.setattr(files, "wait", wait)
    with pytest.raises(RuntimeError, match="test interruption"):
        with session.open_runtime(tmp_path, files):
            pytest.fail("RAM loader must not run in this control")
    assert observed and all(state.get("normal_boot_restore_needed") is True for state in observed)
    assert files.state["normal_boot_restore_needed"] is True
    assert files.state["resources_released"] is True


@pytest.mark.parametrize("selected,split", [("iwr_led", 12), ("ops_led", None)])
def test_iwr_master_asks_the_ops_for_one_capture_on_the_iwr_notice(
    tmp_path, monkeypatch, selected, split
):
    import threading

    import joint_clock_rig
    import tester_sync_session as session
    from test_joint_shot_clock import wire

    from openflight.camera import capture_runtime, trigger_policy

    collector, port = wire()
    built, requests = [], []

    class Rig:
        arm_id, boot_id, native_capture = "arm", "synthetic-boot", True
        trace_attempt_id = "iwr-master-trace"

        def __init__(self, **kwargs):
            built.append(kwargs)
            self.stop = threading.Event()

        def prepare(self):
            pass

        def start_wait(self):
            port.active, port.held = False, True

        def request_ops_capture(self, notice):
            requests.append(notice)
            return True

        def close(self):
            pass

    monkeypatch.setattr(joint_clock_rig, "RigControl", Rig)
    monkeypatch.setattr(capture_runtime, "CameraCaptureRuntime", lambda **_kwargs: object())
    monkeypatch.setattr(collector, "read_raw_dump", lambda _radar: (b"raw", {"bytes": 3}))
    files = SessionFiles(tmp_path)
    files.state["trigger_policy"] = trigger_policy.snapshot(selected, split)
    session.attempt(collector, files, tmp_path / "attempt-001")
    result = json.loads((tmp_path / "attempt-001/RESULT.json").read_text())
    if selected == "iwr_led":
        assert built[0]["mode"] == "iwr_request"
        assert built[0]["pre_trigger_segments"] == 12
        assert len(requests) == 1
        assert requests[0]["expected_generation"] == collector.generation
        assert result["ops_trigger_source"] == "iwr_request"
    else:
        assert "mode" not in built[0]
        assert requests == []
        assert result["ops_trigger_source"] == "native_speed"


@pytest.mark.parametrize("plan", ["default", "club51"])
def test_selected_capture_plan_reaches_the_collector_and_the_session_header(
    tmp_path, monkeypatch, plan
):
    from contextlib import nullcontext

    import tester_sync_session as session

    built = []

    class Collector:
        rows = []
        notice_rows = []
        state = {}

        def __init__(self, _handle, **kwargs):
            built.append(kwargs["capture_plan"])
            self.capture_plan = {"name": kwargs["capture_plan"], "commands": ["line"]}

        def establish_fresh_runtime(self):
            pass

    helper = SimpleNamespace(
        verify_package=lambda: {"source_fingerprint": "b" * 64},
        require_unowned=lambda: None,
        enhanced_port=lambda: "fake-port",
        IWR6843DeviceLock=lambda _port: nullcontext(),
        open_serial=lambda _port: nullcontext(SimpleNamespace()),
        RamOnlyLoader=lambda _handle: SimpleNamespace(load_ram=lambda _raw: None),
        startup=lambda _handle: b"startup",
        boot_identity=lambda: "test-boot",
    )
    monkeypatch.setattr(session, "load_package", lambda _package, **_kwargs: helper)
    monkeypatch.setattr(session, "resource_ownership", lambda: {"resources_released": True})
    monkeypatch.setitem(
        __import__("sys").modules, "joint_shot_clock", SimpleNamespace(ShotCollector=Collector)
    )
    (tmp_path / "clock.ram.bin").write_bytes(b"test-image")
    files = SessionFiles(tmp_path / "session")
    monkeypatch.setattr(files, "wait", lambda _phase: None)
    with session.open_runtime(tmp_path, files, capture_plan=plan):
        header = json.loads((files.folder / "status.json").read_text())
        assert header["iwr_capture_plan"] == {"name": plan, "commands": ["line"]}
    assert built == [plan]


def test_capture_plan_flag_defaults_to_today_and_refuses_unknown_names(tmp_path, monkeypatch):
    import sys

    import tester_sync_session as session

    runs = []
    monkeypatch.setattr(session, "load_package", lambda *_a, **_kw: None)
    monkeypatch.setattr(session, "run", lambda *args, **kwargs: runs.append((args, kwargs)))
    base = ["tester_sync_session.py", "--clock-package", str(tmp_path), "--execute"]
    for extra, expected in [([], "default"), (["--iwr-capture-plan", "club51"], "club51")]:
        monkeypatch.setattr(sys, "argv", [*base, "--output", str(tmp_path / expected), *extra])
        session.main()
        assert runs[-1][1] == {"capture_plan": expected}
    monkeypatch.setattr(sys, "argv", [*base, "--iwr-capture-plan", "club64"])
    with pytest.raises(SystemExit):
        session.main()

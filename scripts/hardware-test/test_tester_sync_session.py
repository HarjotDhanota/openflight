"""Operator phase identity and actual saved camera evidence checks."""

import json
from types import SimpleNamespace

import numpy as np
import pytest
from tester_sync_session import SessionFiles, camera_evidence


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


@pytest.mark.parametrize("stop_fails", [False, True])
def test_attempt_saves_real_parser_output_and_original_bytes(tmp_path, monkeypatch, stop_fails):
    import threading

    import joint_clock_rig
    import tester_sync_session as session
    from test_joint_shot_clock import wire

    from openflight.camera import capture_runtime

    collector, port = wire()

    class Rig:
        arm_id, boot_id, native_capture = "arm", "synthetic-boot", True
        trace_attempt_id = None

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
    assert result["trace_attempt_id"] is None
    assert ("stop_error" in result) == stop_fails
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
        trace_attempt_id = None

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
    assert result["trace_attempt_id"] is None
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
    monkeypatch.setattr(session, "load_package", lambda _path: helper)
    monkeypatch.setattr(session, "resource_ownership", lambda: {"resources_released": True})
    monkeypatch.setattr(files, "wait", wait)
    with pytest.raises(RuntimeError, match="test interruption"):
        with session.open_runtime(tmp_path, files):
            pytest.fail("RAM loader must not run in this control")
    assert observed and all(state.get("normal_boot_restore_needed") is True for state in observed)
    assert files.state["normal_boot_restore_needed"] is True
    assert files.state["resources_released"] is True

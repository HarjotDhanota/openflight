"""Native acquisition stays inside the ordinary fusion and publication boundaries."""

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from openflight.camera import tester_server as ts
from openflight.camera.native_free_play import (
    FreePlayNativeControls,
    capture_mode,
    configure_command,
)
from openflight.camera.native_fusion import (
    NativeCaptureMonitor,
    NativeFusionExtension,
    bind_attempt,
)
from tests.test_internal_sync_routes import Jobs
from tests.test_tester_server import TESTER_SETUP, params


def bundle(tmp_path):
    folder = tmp_path / "attempt-001"
    folder.mkdir()
    root = tmp_path / "run"
    camera = root / "camera/capture-001"
    camera.mkdir(parents=True)
    request = {
        "boot_id": "boot",
        "arm_id": "arm",
        "event_id": "event",
        "kernel_mono_interval_ns": [100, 100],
        "observer_wall_s": 12.0,
    }
    metadata = {
        "trigger_clocks": {
            "capture_request": request,
            "boottime_minus_monotonic_bounds_ns": [10, 12],
        }
    }
    (camera / "metadata.json").write_text(json.dumps(metadata))
    np.savez(camera / "frames.npz", sensor_timestamp_ns=np.array([80, 90, 110, 130, 150]))
    (folder / "iwr-held.json").write_text(
        json.dumps(
            {
                "host_boot": "boot",
                "record": {"shot_id": 1, "generation": 1},
                "mapping": {
                    "observations": {
                        "frame_start_isr": {"conditional_mono_interval_ns": [110, 115]}
                    }
                },
            }
        )
    )
    radar = folder / "ops-camera"
    radar.mkdir()
    (radar / "routing.jsonl").write_text(
        json.dumps({"status": "dispatched", "request": request}) + "\n"
    )
    from tests.test_ops_sync import recording

    edges = recording(((100, "rising"), (120, "falling")))
    edges[0].update(boot_id="boot", mono_ns=90)
    (radar / "gpio.jsonl").write_text("".join(json.dumps(row) + "\n" for row in edges))
    result = {
        "status": "captured_unqualified",
        "camera_root": str(root),
        "camera": {"clips": [{"metadata": "camera/capture-001/metadata.json"}]},
    }
    return folder, result, camera


def test_real_command_remains_normal_free_play_with_native_capture_flags(tmp_path):
    command = ts.action_commands(
        "free_play", params(), tmp_path, ts.DEFAULT_RIG_GEOMETRY, tester_setup=TESTER_SETUP
    )[0][0]
    original = command[:]
    control = configure_command(command, tmp_path / "sealed", "a" * 32)
    assert command[: len(original)] == original
    assert "--tester-free-play" in command and "--setup-file" in command
    assert "--iwr6843" in command and "--camera-capture" in command
    assert "--tester-setup-required" in command and "--iwr6843-cal" in command
    assert command[command.index("--trigger") + 1] == "hardware"
    assert control.parent.parent == Path(command[command.index("--log-dir") + 1])


def test_default_and_invalid_mode_do_not_silently_change_capture():
    with pytest.raises(ValueError, match="Choose a trigger architecture"):
        capture_mode({}, "free_play", Path("sealed"))
    assert capture_mode({}, "swings", None) == "sound"
    assert (
        capture_mode({"capture_mode": "native_internal"}, "free_play", Path("sealed"))
        == "native_internal"
    )
    for action, package in [("swings", Path("sealed")), ("free_play", None)]:
        with pytest.raises(ValueError):
            capture_mode({"capture_mode": "native_internal"}, action, package)
    with pytest.raises(ValueError):
        capture_mode({"capture_mode": "anything"}, "free_play", Path("sealed"))


def test_one_owned_event_with_actual_clip_coverage_is_only_a_candidate(tmp_path):
    folder, result, camera = bundle(tmp_path)
    path, association = bind_attempt(folder, result)
    assert path == camera and association["event_id"] == "event"
    assert association["physical_timing_qualified"] is False
    assert association["impact_mono_interval_ns"] is None
    assert association["status"] == "experimental_software_candidate"


@pytest.mark.parametrize("fault", ["outside_clip", "different_event", "duplicate_pulse", "partial"])
def test_unrelated_native_captures_cannot_enter_fusion(tmp_path, fault):
    folder, result, camera = bundle(tmp_path)
    if fault == "outside_clip":
        held = json.loads((folder / "iwr-held.json").read_text())
        held["mapping"]["observations"]["frame_start_isr"]["conditional_mono_interval_ns"] = [
            2000,
            2010,
        ]
        (folder / "iwr-held.json").write_text(json.dumps(held))
    elif fault == "different_event":
        meta = json.loads((camera / "metadata.json").read_text())
        meta["trigger_clocks"]["capture_request"]["event_id"] = "previous-attempt"
        (camera / "metadata.json").write_text(json.dumps(meta))
    elif fault == "duplicate_pulse":
        with (folder / "ops-camera/gpio.jsonl").open("a") as stream:
            stream.write(json.dumps({"type": "edge", "edge": "rising", "timestamp_ns": 140}) + "\n")
    else:
        result["status"] = "partial"
    with pytest.raises(ValueError):
        bind_attempt(folder, result)


def test_camera_match_consumes_only_the_bound_capture_not_the_nearest_timestamp():
    owner = object.__new__(NativeFusionExtension)
    owner.camera_path = Path("current/capture")
    stale = SimpleNamespace(path=Path("previous/capture"))
    current = SimpleNamespace(path=owner.camera_path)
    captures = iter([stale, current])
    owner.original_camera_match = lambda *_args, **_kwargs: next(captures, None)
    assert owner.camera_for_shot(None) is current
    assert owner.camera_for_shot(None) is None


def test_iwr_adapter_returns_this_attempt_once_and_opens_no_gpio_or_serial():
    capture = object()
    owner = SimpleNamespace(
        collector=SimpleNamespace(port=SimpleNamespace(port="enhanced")), iwr_capture=capture
    )
    adapter = NativeCaptureMonitor(owner)
    adapter.start(armed=False)
    adapter.arm()
    assert adapter.capture_for_shot(None) is capture
    assert adapter.capture_for_shot(None) is None


def test_normal_page_phase_controls_use_existing_job_owner(tmp_path):
    from flask import Flask

    jobs = Jobs()
    controls = FreePlayNativeControls(tmp_path / "sealed", jobs, tmp_path)
    app = Flask(__name__)
    controls.register(app)
    control = tmp_path / "run/native-sync" / ("a" * 32)
    controls.prepare(control, "a" * 32)
    (control / "status.json").write_text(json.dumps({"phase": "reset", "phase_token": "fresh"}))
    client = app.test_client()
    assert client.get("/api/tester/free-play/native-sync").json["enabled"]
    assert (
        client.post(
            "/api/tester/free-play/native-sync/command",
            json={"command": "reset_pressed", "phase_token": "old"},
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/tester/free-play/native-sync/command",
            json={"command": "reset_pressed", "phase_token": "fresh"},
        ).status_code
        == 200
    )
    assert not jobs.started


@pytest.mark.parametrize("paired", [False, True])
def test_native_capture_enters_existing_ops_parser_without_an_invented_epoch(tmp_path, paired):
    import threading

    from openflight.rolling_buffer.processor import RollingBufferProcessor

    owner = object.__new__(NativeFusionExtension)
    owner.publishing, owner.sequence = False, 0
    owner.collector, owner.camera = object(), object()
    owner.files = SimpleNamespace(
        folder=tmp_path,
        command=lambda: "next",
        publish=lambda *_a, **_kw: None,
        cancelled=lambda: False,
    )

    def saved_attempt(_collector, _files, folder, **_kwargs):
        actual, result, _camera = bundle(tmp_path)
        assert actual == folder
        raw = "\n".join(
            json.dumps({key: value})
            for key, value in {
                "sample_time": 1.0,
                "trigger_time": 1.0256,
                "I": [2048] * 4096,
                "Q": [2048] * 4096,
            }.items()
        )
        (folder / "ops-camera/ops-response.raw").write_text(raw)
        (folder / "iwr-dump.bin").write_bytes(b"original IWR bytes")
        result.update(
            ops_capture_observed=True,
            iwr_raw={"transfer_timing": {"cli_ready_ns": 500, "request_written_ns": 100}},
        )
        if not paired:
            result["status"] = "partial"
        return result

    owner.tools = SimpleNamespace(attempt=saved_attempt)
    capture = owner.acquire(None, RollingBufferProcessor(), threading.Event(), None)
    assert capture.num_samples == 4096 and capture.trigger_timestamp is None
    assert capture.trigger_type == "hardware"
    assert (owner.iwr_capture is not None) == paired
    if paired:
        assert owner.iwr_capture.raw == b"original IWR bytes"
        assert owner.iwr_capture.dump_duration_ns == 400
    else:
        assert owner.association["status"] == "unpaired_ops_only"


def test_pending_fusion_release_requires_the_same_attempt():
    import threading

    owner = object.__new__(NativeFusionExtension)
    owner.association = {"event_id": "current"}
    owner.finished = threading.Event()
    owner.after_shot_published(
        SimpleNamespace(extras={"native_sensor_timing": {"event_id": "previous"}}),
        update_kind="first",
    )
    assert not owner.finished.is_set()
    owner.after_shot_published(
        SimpleNamespace(extras={"native_sensor_timing": {"event_id": "current"}}),
        update_kind="first",
    )
    assert owner.finished.is_set()


def test_rejected_shot_does_not_leave_native_capture_waiting_for_publication():
    import threading

    owner = object.__new__(NativeFusionExtension)
    owner.association = {"event_id": "current"}
    owner.finished = threading.Event()
    owner.finished.set()
    owner.publishing = False
    shot = SimpleNamespace(extras={})
    owner.on_shot_detected(shot)
    assert not owner.publishing and owner.finished.is_set()
    owner.on_shot_accepted(shot)
    assert owner.publishing and not owner.finished.is_set()


def test_native_boot_restoration_requirement_survives_tester_restart(tmp_path):
    run_id = "b" * 32
    folder = tmp_path / "run/native-sync" / run_id
    controls = FreePlayNativeControls(tmp_path / "sealed", Jobs(), tmp_path, sessions_root=tmp_path)
    controls.prepare(folder, run_id)
    (folder / "status.json").write_text(
        json.dumps(
            {
                "phase": "stopped",
                "phase_token": "final",
                "normal_boot_restore_needed": True,
                "resources_released": True,
            }
        )
    )
    recovered = FreePlayNativeControls(
        tmp_path / "sealed", Jobs(), tmp_path, sessions_root=tmp_path
    )
    with pytest.raises(RuntimeError, match="Restore normal IWR"):
        recovered.require_restored()
    recovered.controller.command("restored", "final")
    recovered.require_restored()
    assert not recovered.status()["normal_boot_restore_needed"]


def test_stop_joins_acquisition_before_releasing_the_shared_uart():
    import threading

    owner = object.__new__(NativeFusionExtension)
    calls = []
    owner.monitor = SimpleNamespace(
        _running=True,
        _stop_event=threading.Event(),
        _capture_thread=SimpleNamespace(
            join=lambda seconds: calls.append(seconds), is_alive=lambda: False
        ),
    )
    owner.stop_acquisition()
    assert not owner.monitor._running and owner.monitor._stop_event.is_set()
    assert calls == [45]


def test_stop_timeout_keeps_the_uart_owner_marked_failed():
    import threading

    owner = object.__new__(NativeFusionExtension)
    owner.collector = SimpleNamespace(failed=False)
    owner.monitor = SimpleNamespace(
        _running=True,
        _stop_event=threading.Event(),
        _capture_thread=SimpleNamespace(join=lambda _seconds: None, is_alive=lambda: True),
    )
    with pytest.raises(RuntimeError, match="did not release its UART"):
        owner.stop_acquisition()
    assert owner.collector.failed


def test_native_readiness_checks_actual_shared_worker_without_requiring_a_sound_gpio(monkeypatch):
    from openflight import server, tester_hooks
    from tests.test_tester_setup_readiness import _ready

    _ready(monkeypatch)
    monkeypatch.setattr(
        tester_hooks, "free_play_config", {"enabled": True, "native_sync_enabled": True}
    )
    snapshot = {"running": True, "worker_alive": True, "serial_open": True, "armed": False}
    monkeypatch.setattr(
        server.iwr6843_runtime, "capture_monitor", SimpleNamespace(ready_snapshot=lambda: snapshot)
    )
    state = tester_hooks._tester_setup_readiness()
    assert state["ready"], state["blockers"]
    snapshot["worker_alive"] = False
    assert not tester_hooks._tester_setup_readiness()["ready"]


def test_borrowed_ops_port_remains_open_and_journal_is_removed_after_attempt(tmp_path):
    import importlib.util

    path = Path(__file__).parents[1] / "scripts/hardware-test/joint_clock_rig.py"
    spec = importlib.util.spec_from_file_location("native_test_rig", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    original = SimpleNamespace(is_open=True)
    disconnected = []
    radar = SimpleNamespace(
        serial=original,
        _send_command=lambda _command: "idle",
        disconnect=lambda: disconnected.append(True),
    )
    rig = module.RigControl(
        folder=tmp_path,
        boot_id="boot",
        radar=radar,
        camera=SimpleNamespace(notify_capture_request=lambda _request: None),
        recorder=None,
        keep_radar=True,
        keep_camera=True,
    )
    rig.close()
    assert radar.serial is original and not disconnected
    assert radar.capture_evidence_callback is None


def test_normal_stop_packages_native_originals_and_association(tmp_path, monkeypatch):
    import zipfile

    from tests.test_free_play_workflow import _package_free_play

    originals = {
        "iwr-dump.bin": b"original raw IWR",
        "iwr-result.bin": b"held result",
        "iwr-clock.bin": b"CRC clock packet",
        "ops-camera/ops-response.raw": b"original OPS",
        "ops-camera/gpio.jsonl": b'{"edge":"rising"}\n',
        "fusion-association.json": b'{"physical_timing_qualified":false}\n',
        "trigger-policy.json": b'{"policy":"hybrid"}\n',
        "policy-decision.json": b'{"publication_allowed":false}\n',
        "iwr-observations.jsonl": b'{"detector":"iwr"}\n',
        "publication.jsonl": b'{"stage":"processing_started"}\n',
    }

    def save_originals():
        run = next((tmp_path / "sessions").rglob("session_*.jsonl")).parent
        for name, content in originals.items():
            path = run / "native-sync" / ("a" * 32) / "attempt-001" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)

    bundle_path, _log, _state = _package_free_play(
        tmp_path, monkeypatch, lambda _event: None, finish=save_originals
    )
    with zipfile.ZipFile(bundle_path) as archive:
        for name, content in originals.items():
            matches = [path for path in archive.namelist() if path.endswith("attempt-001/" + name)]
            assert len(matches) == 1 and archive.read(matches[0]) == content


def test_ops_only_publication_does_not_rearm_before_pending_auxiliary_capture_is_consumed():
    import threading

    owner = object.__new__(NativeFusionExtension)
    owner.publishing = True
    owner.finished = threading.Event()
    owner.finished.set()
    owner.iwr_capture, owner.camera_path = object(), Path("owned/camera")
    stop = threading.Event()
    stop.set()
    with pytest.raises(InterruptedError):
        owner.acquire(None, None, stop, None)
    assert owner.iwr_capture is not None and owner.camera_path == Path("owned/camera")


def test_normal_stop_quiesces_native_owner_before_closing_ports_and_finalizes_before_packaging(
    monkeypatch,
):
    from openflight import kiosk_extensions, server, tester_hooks

    calls = []
    monkeypatch.setattr(tester_hooks, "resume_free_play", lambda: None)
    monkeypatch.setattr(tester_hooks, "free_play_study", None)
    monkeypatch.setattr(kiosk_extensions, "call_all", lambda name: calls.append(name))
    monkeypatch.setattr(
        server,
        "monitor",
        SimpleNamespace(
            stop=lambda: calls.append("monitor.stop"),
            disconnect=lambda: calls.append("monitor.disconnect"),
            _capture_thread=None,
        ),
    )
    monkeypatch.setattr(server, "shot_processing_pending", lambda: False)
    for name in ("camera_capture_runtime", "iwr6843_runtime", "inclinometer_service"):
        monkeypatch.setattr(server, name, None)
    monkeypatch.setattr(
        server,
        "get_session_logger",
        lambda: SimpleNamespace(end_session=lambda: calls.append("log.end")),
    )
    tester_hooks._finish_free_play_capture()
    assert calls == [
        "before_capture_stop",
        "monitor.stop",
        "monitor.disconnect",
        "after_capture_stop",
        "log.end",
    ]


@pytest.mark.parametrize("fault", [None, "moved_rig", "different_session", "missing"])
def test_native_admission_uses_its_own_saved_trigger_evidence(monkeypatch, tmp_path, fault):
    from copy import deepcopy
    from datetime import datetime

    from openflight import server, tester_hooks
    from openflight.launch_monitor import Shot
    from tests.test_tester_setup_readiness import _active_logger, _trigger_evidence

    _active_logger(monkeypatch, tmp_path)
    evidence = _trigger_evidence(tmp_path)
    if fault == "moved_rig":
        evidence.update(ready=False, blockers=[{"id": "lis3dh", "reason": "rig moved"}])
    elif fault == "different_session":
        evidence["observations"]["runtime"]["session_uuid"] = "previous"
    elif fault == "missing":
        evidence = None
    owner = object.__new__(NativeFusionExtension)
    owner.trigger_evidence = deepcopy(evidence)
    camera = SimpleNamespace(
        capture_for_shot=lambda *_a, **_kw: None, trigger_evidence_for_shot=lambda _timestamp: None
    )
    monkeypatch.setattr(server, "camera_capture_runtime", camera)
    monkeypatch.setattr(server, "camera_capture_config", {})
    monkeypatch.setattr(server, "iwr6843_runtime_config", {})
    monkeypatch.setattr(
        server, "monitor", SimpleNamespace(radar=SimpleNamespace(_send_command=lambda _cmd: ""))
    )
    monkeypatch.setattr(server, "log_session_error", lambda *_a, **_kw: None)
    monkeypatch.setattr(tester_hooks, "tester_setup_required", True)
    monkeypatch.setattr(tester_hooks, "free_play_config", {"enabled": True})
    owner.before_monitor_start()
    shot = Shot(ball_speed_mph=100, timestamp=datetime.now(), impact_timestamp=None)
    assert tester_hooks._admit_shot(shot) == (fault not in {"moved_rig", "different_session"})
    assert ("missing_trigger_evidence" in shot.extras) == (fault == "missing")
    assert camera.trigger_evidence_for_shot(None) is None
    assert evidence is None or evidence.get("blockers") == (
        [{"id": "lis3dh", "reason": "rig moved"}] if fault == "moved_rig" else None
    )


@pytest.mark.parametrize(
    "fault", ["lost_edge", "pending", "missing_end", "wrong_boot", "wrong_clock"]
)
def test_native_fusion_requires_a_complete_gpio_recording_not_just_two_edges(tmp_path, fault):
    folder, result, _camera = bundle(tmp_path)
    path = folder / "ops-camera/gpio.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    if fault == "lost_edge":
        rows[2]["line_seqno"] += 2
    elif fault == "pending":
        rows[-1]["pending_at_end"] = True
    elif fault == "missing_end":
        rows.pop()
    elif fault == "wrong_boot":
        rows[0]["boot_id"] = "other"
    else:
        rows[0]["event_clock"] = "realtime"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    with pytest.raises(ValueError):
        bind_attempt(folder, result)


def test_each_native_attempt_saves_its_ops_buffer_coverage_with_the_split(tmp_path):
    import threading

    from openflight.camera import trigger_policy

    owner = object.__new__(NativeFusionExtension)
    owner.association = {"event_id": "current"}
    owner.finished = threading.Event()
    owner.publishing = False
    owner.policy = trigger_policy.snapshot("iwr_led", 12)
    owner.current_attempt = tmp_path
    coverage = {"ball_first_ms": 40.0, "ball_reaches_buffer_end": False}
    owner.on_shot_detected(SimpleNamespace(extras={}, ops_buffer_coverage=coverage))
    saved = json.loads((tmp_path / "ops-coverage.json").read_text())
    assert saved["ops_pre_trigger_segments"] == 12
    assert saved["planned_history_before_request_ms"] == pytest.approx(12 * 128 / 30)
    assert saved["planned_after_request_ms"] == pytest.approx(20 * 128 / 30)
    assert saved["ball_first_ms"] == 40.0 and saved["ball_reaches_buffer_end"] is False

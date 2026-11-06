"""Cooperative controls retain one runtime and never discard an unconsumed shot."""

import importlib
import json
import struct
import threading
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests import test_internal_sync_routes as route_tests


@pytest.fixture
def setup(tmp_path):
    return route_tests.setup.__wrapped__(tmp_path)


def test_stop_attempt_does_not_kill_job_and_duplicate_is_idempotent(setup):
    client, controller, jobs, _ = setup
    client.post("/api/tester/internal-sync/start")
    route_tests.phase(controller, "armed", attempt_number=1)
    for _ in range(2):
        assert (
            client.post(
                "/api/tester/internal-sync/command",
                json={"command": "stop", "phase_token": "token"},
            ).status_code
            == 200
        )
    assert jobs.cancelled is False
    rows = (controller.root / controller.session / "commands.jsonl").read_text().splitlines()
    assert len(rows) == 1
    assert json.loads(rows[0])["attempt_number"] == 1


def test_retry_requires_safe_pause_and_end_is_separate_cooperative_command(setup):
    client, controller, jobs, _ = setup
    client.post("/api/tester/internal-sync/start")
    route_tests.phase(controller, "paused", retry_available=False)
    assert (
        client.post(
            "/api/tester/internal-sync/command", json={"command": "retry", "phase_token": "token"}
        ).status_code
        == 409
    )
    route_tests.phase(controller, "paused", retry_available=True)
    assert (
        client.post(
            "/api/tester/internal-sync/command", json={"command": "retry", "phase_token": "token"}
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/tester/internal-sync/command", json={"command": "end", "phase_token": "token"}
        ).status_code
        == 200
    )
    assert jobs.cancelled is False


@pytest.mark.parametrize("job", [{"state": "idle"}, {"state": "running", "action": "gain"}])
def test_orphaned_saved_pause_is_not_a_live_worker_or_retry_permission(setup, job):
    client, controller, jobs, _ = setup
    client.post("/api/tester/internal-sync/start")
    route_tests.phase(controller, "paused", retry_available=True)
    jobs.state = job
    status = client.get("/api/tester/internal-sync").json
    assert status["phase"] == "failed" and status["retry_available"] is False
    assert status["orphaned_reason"]
    assert (
        client.post(
            "/api/tester/internal-sync/command", json={"command": "retry", "phase_token": "token"}
        ).status_code
        == 409
    )
    assert not jobs.cancelled


def test_failed_worker_never_advertises_retained_retry_flag(setup):
    client, controller, jobs, _ = setup
    client.post("/api/tester/internal-sync/start")
    route_tests.phase(controller, "paused", retry_available=True)
    jobs.state = {"state": "error", "action": "internal_sync", "message": "worker failed"}
    status = client.get("/api/tester/internal-sync").json
    assert status["phase"] == "failed" and status["retry_available"] is False


def test_native_free_play_keeps_legacy_stop_and_cannot_use_standalone_retry_or_end(tmp_path):
    from flask import Flask

    from openflight.camera.native_free_play import FreePlayNativeControls

    jobs = route_tests.Jobs()
    controls = FreePlayNativeControls(tmp_path / "package", jobs, tmp_path)
    app = Flask(__name__)
    controls.register(app)
    control = tmp_path / "native" / ("a" * 32)
    controls.prepare(control, "a" * 32)
    (control / "status.json").write_text(
        json.dumps({"phase": "paused", "phase_token": "fresh", "retry_available": True})
    )
    client = app.test_client()
    assert client.get("/api/tester/free-play/native-sync").json["retry_available"] is False
    for command in ["retry", "end"]:
        assert (
            client.post(
                "/api/tester/free-play/native-sync/command",
                json={"command": command, "phase_token": "fresh"},
            ).status_code
            == 409
        )
    assert (
        client.post(
            "/api/tester/free-play/native-sync/command",
            json={"command": "stop", "phase_token": "fresh"},
        ).status_code
        == 200
    )
    assert jobs.cancelled


def test_native_tokenless_starting_stop_keeps_legacy_cancel_without_command_file(tmp_path):
    from flask import Flask

    from openflight.camera.native_free_play import FreePlayNativeControls

    jobs = route_tests.Jobs()
    controls = FreePlayNativeControls(tmp_path / "package", jobs, tmp_path)
    app = Flask(__name__)
    controls.register(app)
    control = tmp_path / "native" / ("a" * 32)
    controls.prepare(control, "a" * 32)
    response = app.test_client().post(
        "/api/tester/free-play/native-sync/command", json={"command": "stop", "phase_token": None}
    )
    assert response.status_code == 200 and jobs.cancelled
    assert not (control / "commands.jsonl").exists()


def test_standalone_tokenless_starting_stop_remains_refused(setup):
    client, _, jobs, _ = setup
    client.post("/api/tester/internal-sync/start")
    assert (
        client.post(
            "/api/tester/internal-sync/command", json={"command": "stop", "phase_token": None}
        ).status_code
        == 409
    )
    assert not jobs.cancelled


@pytest.fixture
def tools(monkeypatch):
    scripts = Path(__file__).parents[1] / "scripts/hardware-test"
    monkeypatch.syspath_prepend(str(scripts))
    return importlib.import_module("tester_sync_session")


def append(files, command, token=None, **fields):
    with (files.folder / "commands.jsonl").open("a") as f:
        f.write(
            json.dumps(
                {"command": command, "phase_token": token or files.state["phase_token"], **fields}
            )
            + "\n"
        )


def test_accepted_stop_survives_phase_transition_but_not_a_retry_attempt(tools, tmp_path):
    files = tools.SessionFiles(tmp_path, cooperative=True)
    files.publish("place_ball", attempt_number=1)
    token = files.state["phase_token"]
    append(files, "stop", token, session_id=tmp_path.name, attempt_number=1)
    files.publish("preparing_sensors")
    with pytest.raises(tools.AttemptStopped):
        files.cancelled()
    files.publish("place_ball", attempt_number=2)
    append(files, "stop", token, session_id=tmp_path.name, attempt_number=1)
    assert files.cancelled() is False


@pytest.fixture
def parser(monkeypatch, tools):
    wire = importlib.import_module("test_joint_shot_clock").wire
    collector, port = wire()
    original = port.write
    port.freeze_requests = 0
    port.pending_result = False
    port.post_seen = 0

    def write(data):
        count = original(data)
        if data == b"stats\n":
            port.pending = port.pending.replace(
                b"hwa_missed=0",
                f"hwa_missed=0 hwa_rearm_err=0 freeze_req={port.freeze_requests} freeze_done=0 freeze_to=0".encode(),
            )
            port.pending = port.pending.replace(
                b"ball state=",
                f"iq8_packed=0 pending=0 post_seen={port.post_seen} post_kept=0\nball state=".encode(),
            )
        elif data == b"triggerLog result\n":
            port.pending = port.pending.replace(
                b"ready=1", f"ready={int(port.pending_result)}".encode()
            )
            port.pending = port.pending.replace(
                f"result shot={port.shot}".encode(), f"result shot={port.completed_shots}".encode()
            )
            packet = bytearray(port.result) if port.completed_shots else bytearray(164)
            struct.pack_into("<I", packet, 4, port.completed_shots)
            port.pending = port.pending.replace(
                b"result shot=", f"result v{2 if port.completed_shots else 0} shot=".encode()
            )
            lines = port.pending.splitlines()
            for i, line in enumerate(lines):
                if line.startswith(b"packet "):
                    lines[i] = b"packet " + packet[:82].hex().encode()
                if line.startswith(b"packet+ "):
                    lines[i] = b"packet+ " + packet[82:].hex().encode()
            port.pending = b"\n".join(lines)
        elif data == b"sensorStop\n":
            port.held = False
        elif data == b"sensorStart\n":
            port.freeze_requests = 0
            port.post_seen = 0
            port.pending_result = False
        elif data == b"l3dump\n":
            port.pending_result = False
        return count

    monkeypatch.setattr(port, "write", write)
    return collector, port


def test_safe_stopped_retry_keeps_context_and_advances_generation_only_on_next_begin(tools, parser):
    collector, port = parser
    context = collector.context.boot
    collector.begin()
    collector.finish()
    proof = tools.retry_safety(collector)
    assert proof["available"] and proof["state"]["capture"]["active"] == "0"
    assert collector.generation == 1 and collector.next_shot == 1
    collector.begin()
    assert (
        collector.context.boot == context and collector.generation == 2 and collector.next_shot == 1
    )
    assert port.writes.count(b"sensorStart\n") == 2


@pytest.mark.parametrize("race", ["held", "freeze_without_result", "notice"])
def test_stop_cannot_silently_discard_late_unconsumed_event(tools, parser, race):
    collector, port = parser
    collector.begin()
    if race == "held":
        port.complete_shot()
        port.pending_result = True
        port.freeze_requests = 1
    elif race == "freeze_without_result":
        port.freeze_requests = 1
    else:
        collector.trigger_observation = {"notice_source": "triggered_line"}
    collector.finish()
    before = len(port.writes)
    proof = tools.retry_safety(collector)
    assert not proof["available"] and proof["reason"]
    assert b"sensorStart\n" not in port.writes[before:]
    assert not any(cmd.startswith(b"triggerCfg") for cmd in port.writes[before:])


@pytest.mark.parametrize("poisoned", [False, True])
def test_pre_stop_diagnostic_never_skips_known_framing_stop(
    tools, parser, tmp_path, monkeypatch, poisoned
):
    collector, port = parser
    files = tools.SessionFiles(tmp_path, cooperative=True)
    calls = []

    def cancelled():
        calls.append(True)
        if len(calls) > 1:
            raise tools.AttemptStopped("stop")
        return False

    monkeypatch.setattr(files, "cancelled", cancelled)

    def diagnostic():
        collector.failed = poisoned
        raise ValueError("pre-stop diagnostic failed")

    monkeypatch.setattr(collector, "snapshot", diagnostic)
    with pytest.raises(ValueError, match="diagnostic"):
        tools.attempt(collector, files, tmp_path / "attempt-001")
    assert (b"sensorStop\n" in port.writes) is not poisoned
    result = json.loads((tmp_path / "attempt-001/RESULT.json").read_text())
    assert "diagnostic" in result["pre_stop_error"]


def test_cooperative_stop_mid_reply_retry_and_end_keep_one_runtime_and_original_attempts(
    tools, parser, tmp_path, monkeypatch
):
    collector, port = parser
    runtime, connection = collector.context.boot, collector.connection
    holders, events = {}, []

    @contextmanager
    def opened(_package, files, **_kwargs):
        holders["files"] = files
        try:
            yield collector
        finally:
            events.append("owner closed")
            files.state["resources_released"] = True

    monkeypatch.setattr(tools, "open_runtime", opened)
    original_read = port.read
    injected = set()

    def read(count):
        data = original_read(count)
        files = holders["files"]
        attempt = files.state.get("attempt_number")
        if port.active and attempt not in injected and data:
            append(files, "stop")
            injected.add(attempt)
        return data

    monkeypatch.setattr(port, "read", read)
    original_wait = tools.SessionFiles.wait

    def wait(files, expected):
        if expected == "next":
            append(files, "next")
        else:
            assert expected == "retry" and files.state["phase"] == "paused"
            assert not port.active and collector.state["stop_verified"]
            assert files.state["planned_activity_index"] == 1
            assert "RF is stopped" not in files.state["message"]
            assert "do not swing" in files.state["message"]
            assert not events
            append(files, "retry" if len(injected) == 1 else "end")
        return original_wait(files, expected)

    monkeypatch.setattr(tools.SessionFiles, "wait", wait)
    tools.run(tmp_path / "package", tmp_path / "session")
    status = json.loads((tmp_path / "session/status.json").read_text())
    assert status["phase"] == "stopped" and events == ["owner closed"]
    assert collector.context.boot == runtime and collector.connection == connection
    assert collector.generation == 2 and collector.next_shot == 1
    assert [a["name"] for a in status["attempts"]] == ["attempt-001", "attempt-002"]
    assert all(a["aborted"] and a["planned_activity_index"] == 1 for a in status["attempts"])
    assert all(r.get("error") is None for r in collector.rows)
    assert port.writes.count(b"sensorStart\n") == port.writes.count(b"sensorStop\n") == 2
    assert len(list((tmp_path / "session/control-proofs").glob("*-retry.json"))) == 1


@pytest.mark.parametrize("change", ["reset", "held", "post", "freeze"])
def test_retry_proof_is_fresh_and_refuses_changed_paused_runtime_before_rf(
    tools, parser, tmp_path, monkeypatch, change
):
    collector, port = parser
    collector.begin()
    collector.finish()
    files = tools.SessionFiles(tmp_path, cooperative=True)
    files.publish("stopping", attempt_number=1)
    checks = []
    original = tools.retry_safety

    def proof(*args):
        checks.append(True)
        if len(checks) == 2:
            if change == "reset":
                port.runtime = None
            if change == "held":
                port.complete_shot()
                port.active = False
                port.held = False
                port.pending_result = True
            if change == "freeze":
                port.freeze_requests += 1
            if change == "post":
                port.post_seen = 1
        return original(*args)

    monkeypatch.setattr(tools, "retry_safety", proof)
    waits = []

    def wait(_expected):
        waits.append(True)
        if len(waits) > 1:
            raise tools.SessionEnded("end after refused retry")

    monkeypatch.setattr(files, "wait", wait)
    before = port.writes.count(b"sensorStart\n")
    with pytest.raises((ValueError, tools.SessionEnded)):
        tools.wait_retry(collector, files)
    assert len(checks) == 2 and port.writes.count(b"sensorStart\n") == before


def test_end_queued_with_next_never_starts_rf_and_remains_sticky(tools, tmp_path):
    files = tools.SessionFiles(tmp_path, cooperative=True)
    files.publish("next", attempt_number=1)
    append(files, "next")
    append(files, "end")
    with pytest.raises(tools.SessionEnded):
        files.wait("next")
    with pytest.raises(tools.SessionEnded):
        files.cancelled()


def test_two_consumed_parser_shots_keep_ids_generations_and_refuse_new_late_freeze(tools, parser):
    from openflight.iwr6843.driver import IWR6843Radar

    collector, port = parser
    reader = object.__new__(IWR6843Radar)
    reader.ser = port
    runtime = collector.context.boot
    for expected in (1, 2):
        collector.begin()
        index, _ = collector.wait_ball(lambda *_: None, lambda: False)
        collector.arm(index)
        port.complete_shot()
        port.pending_result = True
        port.freeze_requests = 1
        held = collector.wait_result(lambda *_: None, lambda: False)
        assert held["record"]["shot_id"] == expected
        assert held["record"]["generation"] == expected * 2 - 1
        raw, meta = collector.read_raw_dump(reader)
        collector.finish()
        result = {"iwr_raw": meta, "iwr_shot_id": expected, "consumed_freeze_req": "1"}
        assert raw and tools.retry_safety(collector, result)["available"]
        assert collector.next_shot == expected + 1 and collector.generation == expected * 2
        assert collector.context.boot == runtime
    port.freeze_requests = 2
    assert not tools.retry_safety(collector, result)["available"]
    port.freeze_requests = 1
    port.post_seen = 1
    assert not tools.retry_safety(collector, result)["available"]


def test_stop_accepted_by_api_during_retry_proof_does_not_advance_to_rf(
    tools, parser, setup, monkeypatch
):
    collector, port = parser
    collector.begin()
    collector.finish()
    client, controller, _, _ = setup
    client.post("/api/tester/internal-sync/start")
    files = tools.SessionFiles(controller.root / controller.session, cooperative=True)
    files.publish("stopping", attempt_number=1)
    collector.command_boundary = files.cancelled
    original = tools.retry_safety
    checks = []

    def proof(*args):
        checks.append(True)
        if len(checks) == 2:
            response = client.post(
                "/api/tester/internal-sync/command",
                json={"command": "stop", "phase_token": files.state["phase_token"]},
            )
            assert response.status_code == 200
        return original(*args)

    monkeypatch.setattr(tools, "retry_safety", proof)
    waits = []
    original_wait = files.wait

    def wait(expected):
        waits.append(True)
        append(files, "retry" if len(waits) == 1 else "end")
        return original_wait(expected)

    monkeypatch.setattr(files, "wait", wait)
    count = port.writes.count(b"sensorStart\n")
    with pytest.raises(tools.SessionEnded):
        tools.wait_retry(collector, files)
    assert len(checks) == 2 and len(waits) == 2
    assert port.writes.count(b"sensorStart\n") == count and not collector.failed


def test_end_accepted_during_profile_reply_prevents_sensor_start(
    tools, parser, tmp_path, monkeypatch
):
    collector, port = parser
    files = tools.SessionFiles(tmp_path, cooperative=True)
    files.publish("preparing_attempt", attempt_number=1)
    collector.command_boundary = files.cancelled
    original = port.read
    sent = []

    def read(count):
        data = original(count)
        if data and port.writes[-1] == b"flushCfg\n" and not sent:
            append(files, "end")
            sent.append(True)
        return data

    monkeypatch.setattr(port, "read", read)
    with pytest.raises(tools.SessionEnded):
        tools.attempt(collector, files, tmp_path / "attempt-001")
    assert sent and b"sensorStart\n" not in port.writes
    assert b"dfeDataOutputMode 1\n" not in port.writes
    assert not collector.failed and collector.generation == 0


def test_prearm_candidate_loss_is_recoverable_setup_abort_after_safe_cleanup(
    tools, parser, tmp_path, monkeypatch
):
    import joint_clock_rig

    collector, port = parser
    files = tools.SessionFiles(tmp_path, cooperative=True)
    files.publish("preparing_attempt", attempt_number=1, planned_activity_index=1)
    closed = []

    class Rig:
        native_capture = False
        trace_attempt_id = None

        def __init__(self, **_kwargs):
            self.stop = threading.Event()

        def prepare(self):
            port.ball_state = "waiting"
            port.ball_bin = 0

        def close(self):
            closed.append(True)

    monkeypatch.setattr(joint_clock_rig, "RigControl", Rig)
    monkeypatch.setattr(
        tools,
        "camera_evidence",
        lambda *_args, **_kwargs: {"status": "missing_or_ambiguous", "clips": []},
    )
    result = tools.attempt(
        collector,
        files,
        tmp_path / "attempt-001",
        camera=SimpleNamespace(output_dir=tmp_path / "camera"),
        radar=object(),
    )
    assert result["aborted"] and result["pause_requested"] and closed == [True]
    assert collector.state["stop_verified"] and not collector.failed
    assert tools.retry_safety(collector, result)["available"]
    assert files.state["attempts"][0]["planned_activity_index"] == 1

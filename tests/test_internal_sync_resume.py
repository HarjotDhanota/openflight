"""Zero-attempt resume retains evidence and never guesses post-RF counters."""

import base64
import hashlib
import importlib
import json
import shutil
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests import test_internal_sync_routes as route_tests

BUILD = "9378430e391e8da317ce7552c6173159c8416490a5090f659b1da04fc37d0879"
RUNTIME = "1234567890abcdef"
MANIFEST = "21f9d6c4d14b44b9d5ea0dbd23a8ba885dd1f49e52bcae04f92b4aab715d2573"
PROMPT = b"l3dump:/>"
STATS = (
    b"frames=0 wraps=0 active=0 calib=0x0 rf_faults=0 hwa_frames=0 hwa_out=0 "
    b"hwa_rearms=0 hwa_rearm_err=0 hwa_missed=0 freeze_req=0 freeze_done=0 freeze_to=0 "
    b"format=iq16 plan=0pre/16post loops=0 used=0/786432\n"
    b"iq8_packed=0 iq8_overrun=0 iq8_clipped=0 pending=0 pre_seen=0 post_kept=0 post_seen=0 "
    b"stride=2 iq8_edma_done=0 iq8_edma_err=0 iq8_edma_wait=0 iq8_busy=0/0 iq8_scale=128\n"
    b"ball state=off follow=0 bin=0\ntrig phase=off enabled=0 latched=0\nDone\n" + PROMPT
)


@pytest.fixture
def setup(tmp_path):
    return route_tests.setup.__wrapped__(tmp_path)


def probe(request=RUNTIME, runtime=RUNTIME, build=BUILD, frame=0, seq=0):
    return (
        f"OFCLKPROBE1 build={build} request={request} boot={runtime} seq={seq} "
        "tick_hz=200000000 counter_bits=32\n"
        f"OFCLKSTAMP1 rx_tick=100 tx_tick=110 frame_seq={frame} physical=unqualified\n"
        f"OFCLKPROBEEND1 request={request} seq={seq}\nDone\nl3dump:/>"
    ).encode()


def row(command, raw, before=100):
    return {
        "command": command,
        "received_b64": base64.b64encode(raw).decode(),
        "error": None,
        "before_ns": before,
        "write_return_ns": before + 1,
        "written_bytes": len(command) + 1,
        "cli_ready_ns": before + 2,
        "after_ns": before + 3,
    }


@pytest.fixture
def prior(tmp_path):
    path = tmp_path / ("a" * 32)
    path.mkdir()
    status = {
        "phase": "stopped",
        "phase_token": "old-token",
        "attempts": [],
        "attempt_number": 1,
        "resources_released": True,
        "normal_boot_restore_needed": True,
        "iwr_lifecycle": {"rf_start_attempted": False},
        "iwr_capture_plan": {"name": "default"},
    }
    (path / "status.json").write_text(json.dumps(status))
    (path / "startup.bin").write_bytes(b"OpenFlight usb-result-freeze-fix\nl3dump:/>")
    (path / "commands.jsonl").write_text(
        "\n".join(
            json.dumps({"command": cmd, "phase_token": cmd})
            for cmd in ("switches_set", "reset_pressed")
        )
        + "\n"
    )
    (path / "iwr-exchanges.json").write_text(
        json.dumps(
            [
                row("triggerLog clockProbe 12345678 90abcdef 00000000", probe()),
                row("stats", STATS, 200),
            ]
        )
    )
    return path


def test_only_complete_never_rf_metadata_can_resume(prior):
    from openflight.camera.sync_resume import inspect_prior

    proof = inspect_prior(prior, manifest_sha256=MANIFEST, capture_plan="default")
    assert proof["runtime"] == RUNTIME and proof["build"] == BUILD
    assert proof["prior_session_id"] == prior.name
    assert set(proof["files"]) == {
        "status.json",
        "startup.bin",
        "commands.jsonl",
        "iwr-exchanges.json",
    }


@pytest.mark.parametrize(
    "change",
    [
        "attempt",
        "rf",
        "next",
        "partial",
        "owned",
        "restored",
        "other_runtime",
        "frames",
        "plan",
        "resumed",
    ],
)
def test_metadata_refuses_uncertain_or_previously_used_session(prior, change):
    from openflight.camera.sync_resume import inspect_prior

    status = json.loads((prior / "status.json").read_text())
    rows = json.loads((prior / "iwr-exchanges.json").read_text())
    if change == "attempt":
        (prior / "attempt-001").mkdir()
    elif change == "rf":
        status["iwr_lifecycle"]["rf_start_attempted"] = True
    elif change == "next":
        with (prior / "commands.jsonl").open("a") as f:
            f.write('{"command":"next"}\n')
    elif change == "partial":
        rows[-1]["error"] = "TimeoutError"
    elif change == "owned":
        status["resources_released"] = False
    elif change == "restored":
        (prior / "owner-restoration.json").write_text("{}")
    elif change == "other_runtime":
        rows[0] = row(rows[0]["command"], probe(runtime="abcdef1234567890"))
    elif change == "frames":
        rows[1] = row("stats", STATS.replace(b"frames=0", b"frames=1"), 200)
    elif change == "plan":
        status["iwr_capture_plan"]["name"] = "club51"
    else:
        (prior / "resume.json").write_text("{}")
    (prior / "status.json").write_text(json.dumps(status))
    (prior / "iwr-exchanges.json").write_text(json.dumps(rows))
    with pytest.raises(ValueError):
        inspect_prior(prior, manifest_sha256=MANIFEST, capture_plan="default")


class Port:
    timeout = 0.05
    write_timeout = 0.25

    def __init__(self, *, runtime=RUNTIME, frame=0, stats=STATS, ready=0, shot=0):
        self.runtime, self.frame, self.stats, self.ready, self.shot = (
            runtime,
            frame,
            stats,
            ready,
            shot,
        )
        self.buffer = bytearray()
        self.writes = []

    @property
    def in_waiting(self):
        return len(self.buffer)

    def write(self, raw):
        cmd = raw.decode().strip()
        self.writes.append(cmd)
        if cmd.startswith("triggerLog clockProbe"):
            _, _, hi, lo, seq = cmd.split()
            reply = probe(hi + lo, self.runtime, frame=self.frame, seq=int(seq, 16))
        elif cmd == "stats":
            reply = self.stats
        elif cmd == "triggerLog result":
            reply = f"result shot={self.shot} ready={self.ready}\nDone\nDone\nl3dump:/>".encode()
        elif cmd == "sensorStop":
            reply = b"Done\n" + PROMPT
        else:
            raise AssertionError(f"Unexpected device command {cmd}")
        self.buffer.extend(reply)
        return len(raw)

    def read(self, count):
        chunk = bytes(self.buffer[:count])
        del self.buffer[:count]
        return chunk


@pytest.fixture
def collector_type(monkeypatch):
    folder = (
        Path(__file__).resolve().parents[2]
        / "research/native-fusion-continuation-2026-10-30/clock-freeze/package-001"
    )
    monkeypatch.syspath_prepend(str(folder))
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "scripts/hardware-test"))
    return importlib.import_module("joint_shot_clock").ShotCollector


def test_exact_zero_frame_runtime_creates_new_context_without_rf(collector_type):
    port = Port()
    collector = collector_type(port, build=BUILD, host_boot="host", sleep=lambda _: None)
    proof = collector.attach_zero_attempt_runtime(RUNTIME)
    assert proof["reused_clock_mapping"] is False
    assert collector.fresh_runtime and collector.generation == 0 and collector.next_shot == 1
    assert collector.context.boot == RUNTIME
    assert not any(cmd == "sensorStart" for cmd in port.writes)


@pytest.mark.parametrize(
    "case", ["runtime", "frames", "active", "enabled", "latched", "held", "old_shot"]
)
def test_live_unsafe_state_refused_before_mutating_command(collector_type, case):
    kwargs = {}
    if case == "runtime":
        kwargs["runtime"] = "abcdef1234567890"
    elif case == "frames":
        kwargs["frame"] = 1
    elif case == "held":
        kwargs["ready"] = 1
    elif case == "old_shot":
        kwargs["shot"] = 1
    else:
        kwargs["stats"] = STATS.replace(f"{case}=0".encode(), f"{case}=1".encode())
    port = Port(**kwargs)
    collector = collector_type(port, build=BUILD, host_boot="host", sleep=lambda _: None)
    with pytest.raises(ValueError):
        collector.attach_zero_attempt_runtime(RUNTIME)
    assert not collector.fresh_runtime
    assert "sensorStop" not in port.writes and "sensorStart" not in port.writes


@pytest.mark.parametrize(
    "bad",
    [
        STATS.replace(b"hwa_out=0", b"hwa_out=1"),
        STATS.replace(b"loops=0", b"loops=12"),
        STATS.replace(b"used=0/", b"used=1/"),
        STATS.replace(b"pre_seen=0", b"pre_seen=1"),
        STATS.replace(b"hwa_missed=0", b""),
        STATS.replace(b"active=0", b"active=0 active=0"),
        STATS.replace(b"Done\n", b"Error: invalid\nDone\n"),
    ],
)
def test_contradictory_or_partial_pristine_stats_refused_archived_and_live(
    prior, collector_type, bad
):
    from openflight.camera.sync_resume import inspect_prior

    rows = json.loads((prior / "iwr-exchanges.json").read_text())
    rows[-1] = row("stats", bad, 200)
    (prior / "iwr-exchanges.json").write_text(json.dumps(rows))
    with pytest.raises(ValueError):
        inspect_prior(prior, manifest_sha256=MANIFEST, capture_plan="default")
    port = Port(stats=bad)
    collector = collector_type(port, build=BUILD, host_boot="host", sleep=lambda _: None)
    with pytest.raises((ValueError, RuntimeError)):
        collector.attach_zero_attempt_runtime(RUNTIME)
    assert not collector.fresh_runtime and not any(cmd == "sensorStop" for cmd in port.writes)


def test_resume_nonce_cannot_reseed_reset_firmware_with_old_identity(collector_type):
    port = Port()
    collector = collector_type(
        port, build=BUILD, host_boot="host", sleep=lambda _: None, nonce=lambda: RUNTIME
    )
    with pytest.raises(ValueError, match="re-seed"):
        collector.attach_zero_attempt_runtime(RUNTIME)
    assert not port.writes


def test_resume_route_creates_new_owned_child_and_keeps_prior_bytes(setup, prior, monkeypatch):
    from openflight.camera import sync_resume

    client, controller, jobs, released = setup
    controller.root.mkdir()
    saved = controller.root / prior.name
    shutil.copytree(prior, saved)
    controller.session = prior.name
    monkeypatch.setattr(sync_resume, "MANIFEST_SHA256", hashlib.sha256(b"{}").hexdigest())
    before = {path.name: path.read_bytes() for path in saved.iterdir()}
    state = client.get("/api/tester/internal-sync").json
    assert state["resume_available"] is True
    response = client.post("/api/tester/internal-sync/resume", json={"phase_token": "old-token"})
    assert response.status_code == 202
    assert response.json["phase"] == "resuming"
    assert response.json["session_id"] != prior.name
    assert response.json["normal_boot_restore_needed"] is True
    assert response.json["attempts"] == []
    command = jobs.started[0][1][0]
    assert "--resume-zero-attempt" in command
    assert released == [True]
    assert before == {path.name: path.read_bytes() for path in saved.iterdir()}
    proof = json.loads((controller.root / controller.session / "resume.json").read_text())
    assert proof["prior_session_id"] == prior.name and proof["runtime"] == RUNTIME
    assert (
        client.post(
            "/api/tester/internal-sync/resume", json={"phase_token": "old-token"}
        ).status_code
        == 409
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"phase_token": "stale"},
        {"phase_token": "old-token", "runtime": RUNTIME},
        {"phase_token": "old-token", "path": "/tmp/x"},
    ],
)
def test_resume_refuses_stale_or_user_supplied_inputs(setup, prior, monkeypatch, payload):
    from openflight.camera import sync_resume

    client, controller, jobs, released = setup
    controller.root.mkdir()
    shutil.copytree(prior, controller.root / prior.name)
    controller.session = prior.name
    monkeypatch.setattr(sync_resume, "MANIFEST_SHA256", hashlib.sha256(b"{}").hexdigest())
    assert client.post("/api/tester/internal-sync/resume", json=payload).status_code == 409
    assert not jobs.started and not released


def test_running_status_stays_readable_and_resume_never_opens_another_job(setup):
    client, _, jobs, _ = setup
    assert client.post("/api/tester/internal-sync/start").status_code == 202
    state = client.get("/api/tester/internal-sync")
    assert state.status_code == 200 and state.json["resume_available"] is False
    assert len(jobs.started) == 1


@pytest.mark.parametrize(
    "outcome",
    [
        "clean",
        "close_error",
        "close_unknown",
        "close_key_error",
        "attach_error",
        "attach_unknown",
        "cleanup_owned",
    ],
)
def test_worker_resume_is_read_only_waits_next_and_retains_close_errors(
    prior, monkeypatch, collector_type, outcome
):
    from openflight.camera import sync_resume

    tools = importlib.import_module("tester_sync_session")
    package = prior.parent / "package"
    package.mkdir()
    (package / "MANIFEST.json").write_text("{}")
    digest = hashlib.sha256(b"{}").hexdigest()
    monkeypatch.setattr(sync_resume, "MANIFEST_SHA256", digest)
    output = prior.parent / ("b" * 32)
    output.mkdir()
    proof = sync_resume.inspect_prior(prior, manifest_sha256=digest, capture_plan="default")
    (output / "resume.json").write_text(json.dumps(proof))
    events, port = [], Port()
    old_error = {"mono_us": "123", "message": "prior cp210x purge timeout"}
    kernel_calls = []

    def kernel_errors():
        kernel_calls.append(True)
        if (outcome == "close_unknown" and len(kernel_calls) == 3) or (
            outcome == "attach_unknown" and len(kernel_calls) == 2
        ):
            raise RuntimeError("journal unavailable")
        if outcome == "close_key_error" and len(kernel_calls) == 3:
            raise KeyError("__MONOTONIC_TIMESTAMP")
        return [old_error] + (
            [{"mono_us": "999", "message": "new close purge timeout"}]
            if (
                (outcome == "close_error" and len(kernel_calls) == 3)
                or (outcome == "attach_error" and len(kernel_calls) >= 2)
            )
            else []
        )

    @contextmanager
    def locked(_port):
        events.append("lock")
        try:
            yield
        finally:
            events.append("unlock")

    @contextmanager
    def opened(_port):
        events.append("open existing")
        try:
            yield port
        finally:
            events.append("close existing")

    helper = SimpleNamespace(
        require_unowned=lambda: events.append("require unowned"),
        enhanced_port=lambda: "test-port",
        IWR6843DeviceLock=locked,
        verify_package=lambda: {"source_fingerprint": BUILD},
        boot_identity=lambda: "host-boot",
        kernel_errors=kernel_errors,
    )
    monkeypatch.setattr(tools, "load_package", lambda *_args, **_kwargs: helper)
    monkeypatch.setattr(tools, "open_existing_serial", opened)
    ownership_calls = []

    def ownership():
        ownership_calls.append(True)
        return {
            "resources_released": not (outcome == "cleanup_owned" and len(ownership_calls) == 2)
        }

    monkeypatch.setattr(tools, "resource_ownership", ownership)

    def wait(files, expected):
        assert expected == "next" and files.state["phase"] == "next"
        assert files.state["normal_boot_restore_needed"] is True
        assert port.writes and all(
            cmd.startswith("triggerLog") or cmd == "stats" for cmd in port.writes
        )
        events.append("wait Next")
        raise InterruptedError("operator stopped before Next")

    monkeypatch.setattr(tools.SessionFiles, "wait", wait)
    if outcome in {"attach_error", "attach_unknown"}:
        with pytest.raises(RuntimeError, match="attachment kernel|journal unavailable"):
            tools.run(package, output, resume=True)
    else:
        tools.run(package, output, resume=True)
    status = json.loads((output / "status.json").read_text())
    assert status["phase"] == ("stopped" if outcome == "clean" else "failed")
    assert status["normal_boot_restore_needed"] is True
    assert status["attempts"] == [] and status["iwr_lifecycle"]["rf_start_attempted"] is False
    assert not list(output.glob("attempt-*")) and not (output / "startup.bin").exists()
    assert events == [
        "require unowned",
        "lock",
        "open existing",
        *([] if outcome in {"attach_error", "attach_unknown"} else ["wait Next"]),
        "close existing",
        "unlock",
    ]
    attach = json.loads((output / "resume-attachment.json").read_text())
    assert attach["prior"] == proof and attach["context"]["boot"] == RUNTIME
    assert attach["read_only"] is True and attach["reused_clock_mapping"] is False
    kernel = json.loads((output / "resume-kernel.json").read_text())
    assert kernel["before"] == [old_error]
    if outcome in {"close_unknown", "close_key_error"}:
        assert kernel["new_errors"] is None and kernel["after"] is None
        assert (
            "journal unavailable" if outcome == "close_unknown" else "__MONOTONIC_TIMESTAMP"
        ) in kernel["collection_error"]
    else:
        assert len(kernel["new_errors"]) == int(outcome in {"close_error", "attach_error"})
    assert not any(cmd in {"sensorStart", "sensorStop", "flushCfg"} for cmd in port.writes)


def test_worker_revalidates_linked_evidence_before_any_ownership_or_uart_action(
    prior, collector_type, monkeypatch
):
    from openflight.camera import sync_resume

    tools = importlib.import_module("tester_sync_session")
    package = prior.parent / "package"
    package.mkdir()
    (package / "MANIFEST.json").write_text("{}")
    digest = hashlib.sha256(b"{}").hexdigest()
    monkeypatch.setattr(sync_resume, "MANIFEST_SHA256", digest)
    output = prior.parent / ("b" * 32)
    output.mkdir()
    proof = sync_resume.inspect_prior(prior, manifest_sha256=digest, capture_plan="default")
    (output / "resume.json").write_text(json.dumps(proof))
    (prior / "startup.bin").write_bytes((prior / "startup.bin").read_bytes() + b"\n")
    events = []
    helper = SimpleNamespace(
        require_unowned=lambda: events.append("ownership"), kernel_errors=lambda: []
    )
    monkeypatch.setattr(tools, "load_package", lambda *_args, **_kwargs: helper)
    monkeypatch.setattr(tools, "resource_ownership", lambda: {"resources_released": True})
    with pytest.raises(ValueError, match="evidence changed"):
        tools.run(package, output, resume=True)
    assert not events
    assert json.loads((output / "status.json").read_text())["phase"] == "failed"

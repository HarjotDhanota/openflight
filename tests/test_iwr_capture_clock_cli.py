"""CLI evidence preservation on synthetic captures; never opens hardware."""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from openflight.iwr6843.capture_clock import record_capture_clock
from tests.test_iwr6843_capture_clock import BUILD, Radar, collect

SPEC = importlib.util.spec_from_file_location(
    "iwr_capture_clock", Path(__file__).parents[1] / "scripts/hardware-test/iwr_capture_clock.py"
)
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)


def saved(tmp_path):
    rows, raw, _ = collect()
    folder = tmp_path / "capture"
    folder.mkdir()
    (folder / "capture-clock.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    (folder / "capture.l3dump").write_bytes(raw)
    return folder


def audit_args(folder, output):
    return [
        "audit",
        str(folder),
        "--output",
        str(output),
        "--rate-min-hz",
        "200000000",
        "--rate-max-hz",
        "200000000",
        "--max-distance-ns",
        "1000000000",
    ]


@pytest.mark.parametrize("uart", [False, True])
def test_offline_audit_hashes_original_evidence_and_never_opens_a_port(tmp_path, monkeypatch, uart):
    def no_hardware(**_kwargs):
        pytest.fail("offline audit attempted hardware access")

    monkeypatch.setattr(cli, "IWR6843Radar", no_hardware)
    folder = saved(tmp_path)
    original = {p.name: p.read_bytes() for p in folder.iterdir()}
    output = tmp_path / "audit.json"
    args = audit_args(folder, output)
    if uart:
        args += [
            "--reviewed-build",
            BUILD,
            "--request-max-baud",
            "1000000",
            "--reply-max-baud",
            "1000000",
            "--uart-reference",
            "synthetic",
        ]
    assert cli.main(args) == 0
    result = json.loads(output.read_text())
    assert result["status"] == "conditional_capture_counter_link"
    assert result["recording_sha256"] == hashlib.sha256(original["capture-clock.jsonl"]).hexdigest()
    assert result["physical_timing_qualified"] is False
    assert len(result["frames"]) == 4
    assert {p.name: p.read_bytes() for p in folder.iterdir()} == original
    assert cli.main(args) == 2


def test_audit_rejects_changed_payload_and_reports_unavailability(tmp_path):
    folder = saved(tmp_path)
    with (folder / "capture.l3dump").open("ab") as handle:
        handle.write(b"changed")
    output = tmp_path / "audit.json"
    assert cli.main(audit_args(folder, output)) == 1
    result = json.loads(output.read_text())
    assert result["status"] == "unavailable"
    assert result["frames"] == []


def test_partial_uart_assumptions_are_not_filled_in(tmp_path):
    folder = saved(tmp_path)
    output = tmp_path / "audit.json"
    assert cli.main(audit_args(folder, output) + ["--reviewed-build", BUILD]) == 2
    assert not output.exists()


class ManagedRadar(Radar):
    def __init__(self, fail_capture=False, fail_stop=False):
        super().__init__()
        self.fail_capture, self.fail_stop = fail_capture, fail_stop
        self.stopped, self.closed = False, False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True

    def read_dump(self, *, timeout_s):
        assert timeout_s > 0
        if self.fail_capture:
            error = RuntimeError("synthetic capture failure")
            error.raw = b"partial"
            raise error
        return self.acquire()

    def stop_sensor(self):
        self.stopped = True
        if self.fail_stop:
            raise RuntimeError("synthetic stop failure")


def runner(monkeypatch, radar):
    monkeypatch.setattr(cli, "IWR6843Radar", lambda **_kwargs: radar)
    monkeypatch.setattr(cli.time, "sleep", lambda _seconds: None)

    def record(*args):
        return record_capture_clock(*args, now=radar.now, boot=lambda: "synthetic-host")

    monkeypatch.setattr(cli, "record_capture_clock", record)


def record_args(folder):
    return ["record", "--port", "synthetic", "--build", BUILD, "--directory", str(folder)]


def test_recorder_saves_one_capture_checks_identity_and_verifies_stop(tmp_path, monkeypatch):
    radar = ManagedRadar()
    runner(monkeypatch, radar)
    folder = tmp_path / "recording"
    assert cli.main(record_args(folder)) == 0
    assert radar.stopped and radar.closed
    assert json.loads((folder / "cleanup.json").read_text())["rf_idle_verified"] is True
    rows = [json.loads(line) for line in (folder / "capture-clock.jsonl").read_text().splitlines()]
    assert rows[-1]["error"] is None
    assert not any("sensorStart" in command or "Cfg" in command for command in radar.commands)
    assert cli.main(audit_args(folder, tmp_path / "audit.json")) == 0
    previous_commands = radar.commands[:]
    assert cli.main(record_args(folder)) == 2
    assert radar.commands == previous_commands


def test_wrong_reviewed_build_stops_before_capture(tmp_path, monkeypatch):
    radar = ManagedRadar()
    runner(monkeypatch, radar)
    folder = tmp_path / "wrong-build"
    args = record_args(folder)
    args[args.index(BUILD)] = "b" * 64
    assert cli.main(args) == 2
    assert radar.closed and not radar.stopped
    assert not (folder / "capture.l3dump").exists()
    assert len(radar.commands) == 1


def test_collection_failure_keeps_partial_payload_without_unsafe_uart_retries(
    tmp_path, monkeypatch
):
    radar = ManagedRadar(fail_capture=True)
    runner(monkeypatch, radar)
    folder = tmp_path / "partial"
    assert cli.main(record_args(folder)) == 2
    assert radar.closed and not radar.stopped
    assert len(radar.commands) == 2
    assert (folder / "capture.l3dump").read_bytes() == b"partial"
    rows = [json.loads(line) for line in (folder / "capture-clock.jsonl").read_text().splitlines()]
    assert rows[-1]["error"] == "RuntimeError"
    assert cli.main(audit_args(folder, tmp_path / "audit.json")) == 1


def test_cleanup_failure_does_not_claim_rf_idle_or_discard_saved_payload(tmp_path, monkeypatch):
    radar = ManagedRadar(fail_stop=True)
    runner(monkeypatch, radar)
    folder = tmp_path / "stop-failed"
    assert cli.main(record_args(folder)) == 2
    assert radar.closed
    assert (folder / "capture.l3dump").exists()
    assert json.loads((folder / "cleanup.json").read_text())["rf_idle_verified"] is False

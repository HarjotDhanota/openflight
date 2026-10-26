"""Recorder ownership and failures, with synthetic kernel events and raw I/Q."""

import base64
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_ops_sync import capture, recording

SCRIPT = Path(__file__).parents[1] / "scripts" / "hardware-test" / "record_ops_sync.py"
SPEC = importlib.util.spec_from_file_location("record_ops_sync", SCRIPT)
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)


def write_recording(path):
    raw = "".join(json.dumps(row) + "\n" for row in recording()).encode()
    path.write_bytes(raw)
    return raw


def write_manifest(folder, raw=None, **changes):
    if raw is None:
        raw = "\n".join(
            json.dumps(row)
            for row in [
                {"sample_time": "1.000"},
                {"trigger_time": "1.050"},
                {"I": [1, 2, 3, 4]},
                {"Q": [5, 6, 7, 8]},
            ]
        )
    (folder / "capture.txt").write_text(raw, encoding="utf-8")
    row = capture(raw_capture="capture.txt", **changes)
    manifest = folder / "captures.json"
    manifest.write_text(
        json.dumps({"schema": "openflight.ops-sync-captures.v1", "captures": [row]}),
        encoding="utf-8",
    )
    return manifest


def test_audit_hashes_original_evidence_and_validates_raw_arrays(tmp_path):
    events = tmp_path / "edges.jsonl"
    raw = write_recording(events)
    manifest = write_manifest(tmp_path)
    result = cli.audit(events, manifest)
    assert result["recording_sha256"] == hashlib.sha256(raw).hexdigest()
    assert result["capture_manifest_sha256"] == hashlib.sha256(manifest.read_bytes()).hexdigest()
    row = result["captures"][0]
    assert row["status"] == "associated_unqualified"
    assert row["raw_sha256"] == hashlib.sha256((tmp_path / "capture.txt").read_bytes()).hexdigest()


def test_manifest_cannot_claim_that_a_truncated_dump_is_complete(tmp_path):
    events = tmp_path / "edges.jsonl"
    write_recording(events)
    manifest = write_manifest(tmp_path, raw='{"I":[1,2],"Q":[')
    assert cli.audit(events, manifest)["captures"][0]["reason"] == "capture_incomplete"


def test_raw_paths_cannot_escape_manifest_directory(tmp_path):
    events = tmp_path / "edges.jsonl"
    write_recording(events)
    manifest = write_manifest(tmp_path)
    data = json.loads(manifest.read_text())
    data["captures"][0]["raw_capture"] = "../outside.txt"
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="within"):
        cli.audit(events, manifest)


def test_cli_preserves_existing_output_without_reading_gpio(tmp_path):
    output = tmp_path / "keep.jsonl"
    output.write_text("original")
    assert (
        cli.main(
            [
                "record",
                "--chip",
                "/dev/unused",
                "--line",
                "27",
                "--duration-s",
                "1",
                "--output",
                str(output),
            ]
        )
        == 2
    )
    assert output.read_text() == "original"


def test_audit_cli_returns_failure_for_missing_pulse_and_keeps_report(tmp_path):
    events = tmp_path / "edges.jsonl"
    events.write_text("".join(json.dumps(row) + "\n" for row in recording([])))
    manifest = write_manifest(tmp_path)
    output = tmp_path / "report.json"
    assert (
        cli.main(["audit", str(events), "--captures", str(manifest), "--output", str(output)]) == 1
    )
    assert json.loads(output.read_text())["captures"][0]["reason"] == "pulse_unobserved"


class Context:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True


class FakeRequest(Context):
    def __init__(self, failure=False):
        self.closed = False
        self.failure = failure

    def get_value(self, _line):
        return 0

    def wait_edge_events(self, timeout):
        return timeout.total_seconds() > 0

    def read_edge_events(self, max_events):
        assert max_events == 64
        if self.failure:
            raise OSError("read failed")
        return [
            SimpleNamespace(
                timestamp_ns=115, event_type=1, line_offset=27, global_seqno=1, line_seqno=1
            ),
            SimpleNamespace(
                timestamp_ns=130, event_type=2, line_offset=27, global_seqno=2, line_seqno=2
            ),
        ]


def fake_api(used=False, failure=False):
    request = FakeRequest(failure)

    class Chip(Context):
        def __init__(self, _path):
            pass

        def get_line_info(self, _line):
            return SimpleNamespace(used=used, name="GPIO27")

        def get_info(self):
            return SimpleNamespace(label="test-chip")

    settings = []
    api = SimpleNamespace(
        Chip=Chip,
        __version__="test",
        line=SimpleNamespace(
            Direction=SimpleNamespace(INPUT="input"),
            Edge=SimpleNamespace(BOTH="both"),
            Bias=SimpleNamespace(AS_IS="unchanged"),
            Clock=SimpleNamespace(MONOTONIC="monotonic"),
            Value=SimpleNamespace(ACTIVE=1),
        ),
        EdgeEvent=SimpleNamespace(Type=SimpleNamespace(RISING_EDGE=1)),
    )
    api.LineSettings = lambda **values: settings.append(values) or values
    api.request_lines = lambda *_args, **_kwargs: request
    return api, request, settings


def test_recorder_preserves_kernel_time_sequence_and_releases_input():
    api, request, settings = fake_api()
    rows = []
    clock = iter([100, 110, 120, 140, 1_000_000_001, 1_000_000_010])
    cli.record_gpio(
        "/dev/test", 27, 0.5, rows.append, api=api, now=lambda: next(clock), boot_id="test-boot"
    )
    assert request.closed
    assert settings[0] == {
        "direction": "input",
        "edge_detection": "both",
        "bias": "unchanged",
        "event_clock": "monotonic",
    }
    assert rows[1]["timestamp_ns"] == 115
    assert rows[1]["received_mono_ns"] == 120
    assert rows[2]["line_seqno"] == rows[2]["global_seqno"] == 2
    assert rows[-1]["edge_count"] == 2
    assert cli.analyze_recording(rows)["stream_issues"] == []


def test_gpio_read_failure_preserves_error_and_releases_line():
    api, request, _settings = fake_api(failure=True)
    rows = []
    with pytest.raises(OSError, match="read failed"):
        cli.record_gpio(
            "/dev/test", 27, 1, rows.append, api=api, now=lambda: 100, boot_id="test-boot"
        )
    assert request.closed
    assert rows[-1]["error"] == "OSError"


def test_busy_line_is_never_requested():
    api, _request, settings = fake_api(used=True)
    with pytest.raises(ValueError, match="already in use"):
        cli.record_gpio("/dev/test", 27, 1, lambda _row: None, api=api)
    assert not settings


@pytest.mark.parametrize("duration", [0, -1, float("nan"), float("inf")])
def test_bad_duration_fails_before_hardware_access(duration):
    with pytest.raises(ValueError):
        cli.record_gpio("/dev/test", 27, duration, lambda _row: None, api=object())


def live_evidence(**changes):
    raw = b'{"sample_time":"1.000"}\r\n{"trigger_time":"1.050"}\r\n{"I":[1,2]}\r\n{"Q":[3,4]}\r\n'
    return {
        "type": "ops_capture_evidence",
        "schema": "openflight.ops-capture-evidence.v1",
        "capture_id": "a" * 32,
        "boot_id": "test-boot",
        "clock_domain": "host_monotonic",
        "armed_mono_ns": 150,
        "arm_command_completed_mono_ns": 160,
        "wait_started_mono_ns": 170,
        "dump_start_mono_ns": 400,
        "response_completed_mono_ns": 500,
        "arm_command_error": None,
        "arm_observation": "host_command_bounds_not_device_acknowledgement",
        "outcome": "capture",
        "error": None,
        "evidence_issues": [],
        "raw_capture_base64": base64.b64encode(raw).decode("ascii"),
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "raw_bytes_received": len(raw),
        **changes,
    }


def export_live(tmp_path, evidence):
    session = tmp_path / "session.jsonl"
    session.write_text(json.dumps(evidence) + "\n")
    output = tmp_path / "extracted"
    result = cli.export_captures(session, output)
    return output, result


def test_export_round_trip_keeps_exact_bytes_and_associates_only_as_unqualified(tmp_path):
    evidence = live_evidence()
    output, result = export_live(tmp_path, evidence)
    raw_path = output / result["captures"][0]["raw_capture"]
    assert raw_path.read_bytes() == base64.b64decode(evidence["raw_capture_base64"])
    assert (
        result["source_session_sha256"]
        == hashlib.sha256((tmp_path / "session.jsonl").read_bytes()).hexdigest()
    )
    events = tmp_path / "edges.jsonl"
    events.write_text("".join(json.dumps(row) + "\n" for row in recording()))
    audit = cli.audit(events, output / "captures.json")
    assert audit["captures"][0]["status"] == "associated_unqualified"
    assert audit["captures"][0]["sample_mapping"] is None
    assert audit["physical_timing_qualified"] is False


@pytest.mark.parametrize(
    "changes",
    [
        {"armed_mono_ns": None},
        {"arm_command_completed_mono_ns": None},
        {"arm_command_error": "arm_commands_incomplete"},
        {"boot_id": None},
        {"wait_started_mono_ns": 100},
        {"response_completed_mono_ns": 300},
        {"dump_start_mono_ns": True},
        {"clock_domain": "wall"},
        {"raw_bytes_received": True},
        {"raw_bytes_received": 1},
        {"outcome": "cancelled"},
        {"error": "SerialException"},
        {"evidence_issues": ["preamble_truncated"]},
        {"arm_observation": "assumed"},
    ],
)
def test_exported_incomplete_context_cannot_associate(tmp_path, changes):
    output, manifest = export_live(tmp_path, live_evidence(**changes))
    assert manifest["captures"][0]["evidence_issues"]
    events = tmp_path / "edges.jsonl"
    events.write_text("".join(json.dumps(row) + "\n" for row in recording()))
    assert (
        cli.audit(events, output / "captures.json")["captures"][0]["reason"]
        == "capture_evidence_invalid"
    )


def test_export_cli_preserves_incomplete_evidence_with_failure_status(tmp_path):
    session = tmp_path / "session.jsonl"
    session.write_text(json.dumps(live_evidence(armed_mono_ns=None)) + "\n")
    output = tmp_path / "exported"
    assert cli.main(["export", str(session), "--output-dir", str(output)]) == 1
    assert (output / "captures.json").exists()


@pytest.mark.parametrize(
    "changes",
    [
        {"raw_sha256": "0" * 64},
        {"raw_capture_base64": "invalid*"},
        {"schema": "unknown"},
        {"capture_id": "../unsafe"},
    ],
)
def test_invalid_export_never_creates_output(tmp_path, changes):
    with pytest.raises(ValueError):
        export_live(tmp_path, live_evidence(**changes))
    assert not (tmp_path / "extracted").exists()


def test_export_refuses_duplicate_capture_ids(tmp_path):
    session = tmp_path / "session.jsonl"
    session.write_text((json.dumps(live_evidence()) + "\n") * 2)
    with pytest.raises(ValueError, match="duplicate"):
        cli.export_captures(session, tmp_path / "extracted")


def test_export_never_overwrites_existing_directory(tmp_path):
    output, _manifest = export_live(tmp_path, live_evidence())
    manifest = output / "captures.json"
    original = manifest.read_bytes()
    with pytest.raises(FileExistsError):
        cli.export_captures(tmp_path / "session.jsonl", output)
    assert manifest.read_bytes() == original


def test_concatenated_raw_dumps_are_not_mistaken_for_one_complete_capture(tmp_path):
    evidence = live_evidence()
    raw = base64.b64decode(evidence["raw_capture_base64"]) * 2
    evidence.update(
        raw_capture_base64=base64.b64encode(raw).decode("ascii"),
        raw_sha256=hashlib.sha256(raw).hexdigest(),
        raw_bytes_received=len(raw),
    )
    output, _manifest = export_live(tmp_path, evidence)
    events = tmp_path / "edges.jsonl"
    events.write_text("".join(json.dumps(row) + "\n" for row in recording()))
    assert (
        cli.audit(events, output / "captures.json")["captures"][0]["reason"] == "capture_incomplete"
    )

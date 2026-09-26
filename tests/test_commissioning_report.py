"""Offline commissioning evidence report."""

from __future__ import annotations

import importlib.util
import json
import sys
from copy import deepcopy
from pathlib import Path

from openflight.commissioning_report import build_commissioning_report
from openflight.session_logger import SessionLogger
from openflight.timing import measured_stage, new_stage_timing

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "analysis" / "commissioning_report.py"
SPEC = importlib.util.spec_from_file_location("commissioning_report_cli", SCRIPT)
CLI = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = CLI
SPEC.loader.exec_module(CLI)


def test_report_exposes_recorded_failures_gaps_and_stage_timing():
    source = {
        "path": "session.jsonl",
        "sha256": "a" * 64,
        "byte_count": 10,
        "complete": True,
        "warnings": [],
        "records": [
            {"type": "session_start", "session_uuid": "s1"},
            {"type": "trigger_event", "accepted": False, "latency_ms": 4.0},
            {
                "type": "shot_detected",
                "shot_number": 1,
                "pipeline_ms": {"iwr6843": 12.0, "camera_capture": 8.0},
            },
            {
                "type": "rolling_buffer_capture",
                "shot_number": 1,
                "sample_count": 4096,
                "trigger_latency_ms": 3.5,
                "impact_transition_gap_ms": 1.2,
            },
            {
                "type": "camera_capture",
                "shot_number": 1,
                "capture_error": None,
                "_artifact": {"available": True},
                "metadata": {"frame_count": 90, "delivered_fps": 450.0, "gap_count": 2},
            },
            {
                "type": "iwr6843_capture",
                "shot_number": 1,
                "capture_error": "timeout",
                "_artifact": {"available": False},
            },
            {"type": "session_end"},
        ],
    }
    report = build_commissioning_report([source])
    assert report["status"] == "complete"
    assert report["counts"]["recorded_sensor_shots"] == 1
    assert report["counts"]["rejected_trigger_events"] == 1
    assert report["counts"]["acquisition_states"]["camera_recorded_frame_gaps"] == 2
    assert report["counts"]["acquisition_states"]["iwr6843_capture_failed"] == 1
    assert report["timing"]["pipeline_iwr6843_ms"]["median"] == 12.0
    assert report["interpretation"]["physical_attempt_coverage"].startswith("unknown")


def test_old_commissioning_timing_is_unchanged_and_new_contract_is_additive():
    old_source = {
        "records": [
            {"type": "session_start", "session_uuid": "old"},
            {
                "type": "shot_detected",
                "shot_number": 1,
                "pipeline_ms": {"initial_ui": 12.5, "iwr6843": 8000.0},
            },
            {"type": "trigger_event", "latency_ms": 3.5},
            {"type": "session_end"},
        ]
    }
    old_report = build_commissioning_report([old_source])

    new_source = deepcopy(old_source)
    new_source["records"][0]["session_uuid"] = "new"
    timing = new_stage_timing()
    timing["iwr6843"]["uart_transport"] = measured_stage(
        1,
        7_550_000_001,
        "iwr_uart_read_started",
        "iwr_uart_read_completed",
    )
    new_source["records"][1]["stage_timing"] = timing
    new_report = build_commissioning_report([new_source])

    for field in (
        "pipeline_initial_ui_ms",
        "pipeline_iwr6843_ms",
        "trigger_event_latency_ms",
    ):
        assert new_report["timing"][field] == old_report["timing"][field]
    assert new_report["timing"]["stage_iwr6843_uart_transport_ms"]["median"] == 7550.0


def test_cli_hashes_evidence_verifies_artifacts_and_marks_unfinished_session_partial(tmp_path):
    camera = tmp_path / "camera_1"
    camera.mkdir()
    (camera / "frames.npz").write_bytes(b"npz")
    (camera / "metadata.json").write_text("{}", encoding="utf-8")
    session = tmp_path / "session.jsonl"
    rows = [
        {"type": "session_start", "session_uuid": "s1"},
        {"type": "shot_detected", "shot_number": 1},
        {
            "type": "camera_capture",
            "shot_number": 1,
            "capture_path": str(camera),
            "capture_error": None,
        },
    ]
    session.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    output = tmp_path / "report.json"
    assert CLI.main([str(session), "--output", str(output)]) == 3
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "partial"
    assert report["sessions"][0]["parse_warnings"] == [
        "unfinished final JSONL record was not consumed"
    ]
    assert report["sessions"][0]["sha256"]


def test_cli_complete_session_and_missing_saved_artifact_are_visible(tmp_path):
    session = tmp_path / "session.jsonl"
    rows = [
        {"type": "session_start", "session_uuid": "s1"},
        {"type": "shot_detected", "shot_number": 1},
        {
            "type": "iwr6843_capture",
            "shot_number": 1,
            "capture_path": str(tmp_path / "missing.l3dump"),
            "capture_error": None,
        },
        {"type": "session_end"},
    ]
    session.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    output = tmp_path / "report.json"
    assert CLI.main([str(session), "--output", str(output)]) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    shot = report["sessions"][0]["shots"][0]
    assert shot["iwr6843_capture"]["status"] == "artifact_missing"


def test_cli_consumes_real_session_logger_capture_contract(tmp_path):
    camera = tmp_path / "camera_actual"
    camera.mkdir()
    (camera / "frames.npz").write_bytes(b"npz")
    (camera / "metadata.json").write_text("{}", encoding="utf-8")
    dump = tmp_path / "capture.l3dump"
    dump.write_bytes(b"raw")
    logger = SessionLogger(tmp_path, provenance_collector=lambda *_args: {})
    logger.start_session(camera_enabled=True)
    logger.log_trigger_event("sound", True, latency_ms=2.5)
    logger.log_camera_capture(
        shot_number=1,
        shot_timestamp=10.0,
        trigger_timestamp=10.002,
        capture_path=str(camera),
        metadata={"frame_count": 90, "delivered_fps": 449.5, "gap_count": 1},
    )
    logger.log_iwr6843_capture(
        shot_number=1,
        shot_timestamp=10.0,
        trigger_timestamp=10.003,
        capture_path=str(dump),
        capture_bytes=3,
        dump_duration_s=0.04,
        capture_error=None,
        ball_speed_mph=100.0,
    )
    logger.end_session()
    session = next(tmp_path.glob("session_*.jsonl"))
    output = tmp_path / "commissioning.json"
    assert CLI.main([str(session), "--output", str(output)]) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["timing"]["camera_capture_trigger_delta_ms"]["n"] == 1
    assert report["timing"]["iwr6843_dump_duration_s"]["median"] == 0.04
    assert report["counts"]["acquisition_states"]["camera_capture_recorded"] == 1


def test_duplicate_session_or_capture_identity_is_not_silently_joined():
    source = {
        "path": "session.jsonl",
        "sha256": "a" * 64,
        "byte_count": 10,
        "complete": True,
        "warnings": [],
        "records": [
            {"type": "session_start", "session_uuid": "s1"},
            {"type": "session_start", "session_uuid": "s2"},
        ],
    }
    try:
        build_commissioning_report([source])
    except ValueError as exc:
        assert "exactly one session_start" in str(exc)
    else:
        raise AssertionError("duplicate session identity was accepted")


def test_multisession_report_rejects_duplicate_session_identity():
    source = {
        "path": "session-a.jsonl",
        "sha256": "a" * 64,
        "byte_count": 10,
        "complete": True,
        "warnings": [],
        "records": [{"type": "session_start", "session_uuid": "s1"}, {"type": "session_end"}],
    }
    duplicate = {**source, "path": "session-b.jsonl", "sha256": "b" * 64}
    try:
        build_commissioning_report([source, duplicate])
    except ValueError as exc:
        assert "UUIDs must be unique" in str(exc)
    else:
        raise AssertionError("duplicate session UUID was accepted across sources")


def test_report_rejects_a_shot_event_with_a_conflicting_session_uuid():
    source = {
        "path": "session.jsonl",
        "sha256": "a" * 64,
        "byte_count": 10,
        "complete": True,
        "warnings": [],
        "records": [
            {"type": "session_start", "session_uuid": "s1"},
            {"type": "shot_detected", "session_uuid": "other", "shot_number": 1},
            {"type": "session_end"},
        ],
    }
    try:
        build_commissioning_report([source])
    except ValueError as exc:
        assert "must not mix session UUIDs" in str(exc)
    else:
        raise AssertionError("conflicting event session identity was joined")


def test_cli_cannot_overwrite_relative_recorded_artifact(tmp_path):
    dump = tmp_path / "capture.l3dump"
    dump.write_bytes(b"raw")
    session = tmp_path / "session.jsonl"
    rows = [
        {"type": "session_start", "session_uuid": "s1"},
        {
            "type": "iwr6843_capture",
            "shot_number": 1,
            "capture_path": "capture.l3dump",
            "capture_error": None,
        },
        {"type": "session_end"},
    ]
    session.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    assert CLI.main([str(session), "--output", str(dump), "--overwrite"]) == 2
    assert dump.read_bytes() == b"raw"

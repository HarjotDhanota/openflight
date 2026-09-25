"""Tests for persisted live-fusion diagnostic snapshots and replay."""

from __future__ import annotations

import json
import math
from datetime import datetime

from openflight.camera import fusion_diagnostics as fd, tester_server as ts
from openflight.launch_monitor import Shot
from openflight.session_logger import SessionLogger


def _shot(**overrides):
    shot = Shot(ball_speed_mph=0.0, club_speed_mph=101.0, timestamp=datetime.now())
    shot.shot_number = 1
    for name, value in overrides.items():
        setattr(shot, name, value)
    return shot


def test_snapshot_preserves_zero_rejects_nonfinite_and_labels_sources():
    snapshot = fd.build_snapshot(
        _shot(
            ball_speed_contract="radial",
            launch_angle_vertical=math.nan,
            launch_angle_vertical_source="estimated",
            experimental_club_path_deg=-2.5,
            experimental_club_path_status="rejected_low_confidence",
        ),
        session_uuid="session-a",
        revision=2,
        phase="terminal",
        outcome="complete",
    )
    metrics = {item["key"]: item for item in snapshot["metrics"]}

    assert metrics["ball_speed_mph"]["value"] == 0.0
    assert metrics["ball_speed_mph"]["source"] == "ops_radial"
    assert metrics["launch_vertical_deg"]["value"] is None
    assert metrics["launch_vertical_deg"]["validation"] == "estimated"
    assert metrics["iwr_club_path_candidate_deg"]["value"] == -2.5
    assert metrics["iwr_club_path_candidate_deg"]["status"] == "rejected"
    assert snapshot["accuracy_qualified"] is False
    json.dumps(snapshot, allow_nan=False)


def test_mock_snapshot_labels_all_metrics_mock():
    snapshot = fd.build_snapshot(
        _shot(mode="mock", launch_angle_horizontal=0.0),
        session_uuid="mock-session",
        revision=2,
        phase="terminal",
        outcome="complete",
    )
    ordinary = [
        metric
        for metric in snapshot["metrics"]
        if metric["key"] != "experimental_ball_speed_total_mph"
    ]
    assert all(metric["source"] == "mock" for metric in ordinary)
    assert all(metric["validation"] == "mock" for metric in ordinary)


def test_snapshot_exposes_unvalidated_total_speed_candidate_separately():
    snapshot = fd.build_snapshot(
        _shot(
            ball_speed_mph=108.0,
            ball_speed_contract="radial",
            experimental_ball_speed_total={
                "status": "available",
                "value_mph": 110.25,
                "reason": None,
            },
        ),
        session_uuid="candidate-session",
        revision=2,
        phase="terminal",
        outcome="complete",
    )
    metrics = {item["key"]: item for item in snapshot["metrics"]}
    assert metrics["ball_speed_mph"]["value"] == 108.0
    assert metrics["ball_speed_mph"]["source"] == "ops_radial"
    candidate = metrics["experimental_ball_speed_total_mph"]
    assert candidate["value"] == 110.25
    assert candidate["validation"] == "unvalidated"


def test_only_recorded_accepted_camera_iwr_delta_is_exposed():
    accepted = fd.build_snapshot(
        _shot(
            experimental_camera_horizontal_status="camera_assisted_high",
            experimental_camera_iwr_delta_deg=0.0,
            experimental_camera_horizontal_deg=1.0,
            iwr6843_horizontal_deg=1.0,
        ),
        session_uuid="s",
        revision=2,
        phase="terminal",
        outcome="complete",
    )["comparisons"][0]
    rejected = fd.build_snapshot(
        _shot(
            experimental_camera_horizontal_status="camera_withheld_iwr_implausible",
            experimental_camera_iwr_delta_deg=1.25,
        ),
        session_uuid="s",
        revision=2,
        phase="terminal",
        outcome="complete",
    )["comparisons"][0]
    assert accepted["value"] == 0.0
    assert accepted["status"] == "available"
    assert "iwr_range_evidence" in accepted["shared_inputs"]
    assert rejected["value"] is None
    assert rejected["status"] == "withheld"


def test_snapshot_exposes_bounded_reference_detector_disagreement():
    processing = {
        "ball_estimate": {
            "reference_ball_diagnostics": {
                "agreement": False,
                "scene": {"candidate": {"x": 10.0, "y": 20.0, "diameter_px": 14.0}},
                "impact": {"candidate": {"x": 13.0, "y": 24.0, "diameter_px": 15.0}},
                "selected_source": "session_anchor_disagreement",
                "untrusted_extra": {"pixels": [1, 2, 3]},
            }
        }
    }
    comparisons = fd.build_snapshot(
        _shot(camera_fusion_processing=processing),
        session_uuid="s",
        revision=2,
        phase="terminal",
        outcome="complete",
    )["comparisons"]
    detector = next(item for item in comparisons if item["key"].startswith("reference_ball"))

    assert detector["status"] == "available"
    assert detector["value"] == 5.0
    assert detector["reason"] == "scene and impact detectors disagreed"
    assert set(detector) == {"key", "label", "value", "unit", "status", "reason", "shared_inputs"}


def test_snapshot_withholds_absent_or_malformed_reference_detector_data():
    snapshots = [
        fd.build_snapshot(
            _shot(), session_uuid="s", revision=2, phase="terminal", outcome="complete"
        ),
        fd.build_snapshot(
            _shot(
                camera_fusion_processing={
                    "ball_estimate": {
                        "reference_ball_diagnostics": {
                            "agreement": "yes",
                            "scene": {"candidate": {"x": 1.0, "y": 2.0}},
                            "impact": {"candidate": {"x": float("nan"), "y": 3.0}},
                        }
                    }
                }
            ),
            session_uuid="s",
            revision=2,
            phase="terminal",
            outcome="complete",
        ),
    ]

    detector_rows = [
        next(item for item in snapshot["comparisons"] if item["key"].startswith("reference_ball"))
        for snapshot in snapshots
    ]
    assert all(item["status"] == "withheld" and item["value"] is None for item in detector_rows)
    assert "not recorded" in detector_rows[0]["reason"]
    assert "malformed" in detector_rows[1]["reason"]
    for snapshot in snapshots:
        json.dumps(snapshot, allow_nan=False)


def test_session_logger_rejects_late_snapshot_after_session_reset(tmp_path):
    logger = SessionLogger(tmp_path, provenance_collector=lambda *_args: {})
    logger.start_session(mode="mock")
    first_uuid = logger.active_session_uuid
    assert first_uuid
    snapshot = fd.build_snapshot(
        _shot(),
        session_uuid=first_uuid,
        revision=1,
        phase="pending",
        outcome="processing",
    )
    assert logger.log_fusion_diagnostic(first_uuid, snapshot) is True
    first_path = logger.session_path
    logger.end_session()
    first_entries = [json.loads(line) for line in first_path.read_text().splitlines()]
    logger.start_session(mode="mock")

    assert logger.log_fusion_diagnostic(first_uuid, snapshot) is False
    logger.end_session()
    assert [
        entry["revision"] for entry in first_entries if entry["type"] == "fusion_diagnostic"
    ] == [1]


def _entry(entry_type, **values):
    return json.dumps({"ts": "now", "type": entry_type, **values}).encode() + b"\n"


def test_reader_retains_partial_line_latest_revision_and_distinct_sessions(tmp_path):
    first = tmp_path / "session_1.jsonl"
    second = tmp_path / "session_2.jsonl"
    pending = fd.build_snapshot(
        _shot(), session_uuid="one", revision=1, phase="pending", outcome="processing"
    )
    terminal = fd.build_snapshot(
        _shot(),
        session_uuid="one",
        revision=2,
        phase="terminal",
        outcome="partial",
        reason="deadline",
    )
    first.write_bytes(
        _entry("session_start", session_uuid="one")
        + _entry("fusion_diagnostic", **pending)
        + _entry("shot_detected", readings=[1] * 100)
        + json.dumps({"ts": "now", "type": "fusion_diagnostic", **terminal}).encode()[:-5]
    )
    second.write_bytes(_entry("session_start", session_uuid="two") + _entry("session_end"))
    reader = fd.DiagnosticReader()

    initial = reader.read(tmp_path)
    first_session = next(item for item in initial["sessions"] if item["session_uuid"] == "one")
    assert first_session["shots"][0]["revision"] == 1
    with first.open("ab") as handle:
        encoded = json.dumps({"ts": "now", "type": "fusion_diagnostic", **terminal}).encode()
        existing = json.dumps({"ts": "now", "type": "fusion_diagnostic", **terminal}).encode()[:-5]
        handle.write(encoded[len(existing) :] + b"\n")
    updated = reader.read(tmp_path)
    first_session = next(item for item in updated["sessions"] if item["session_uuid"] == "one")
    assert first_session["shots"][0]["revision"] == 2
    legacy = next(item for item in updated["sessions"] if item["session_uuid"] == "two")
    assert legacy["diagnostics_available"] is False
    assert legacy["ended"] is True


def test_reader_ignores_wrong_uuid_and_detects_same_path_larger_rewrite(tmp_path):
    path = tmp_path / "session_same.jsonl"
    stale = fd.build_snapshot(
        _shot(), session_uuid="wrong", revision=2, phase="terminal", outcome="complete"
    )
    path.write_bytes(
        _entry("session_start", session_uuid="first") + _entry("fusion_diagnostic", **stale)
    )
    reader = fd.DiagnosticReader()
    first = reader.read(tmp_path)["sessions"][0]
    assert first["shots"] == []
    assert any("ignored 1" in reason for reason in first["reasons"])

    current = fd.build_snapshot(
        _shot(shot_number=2),
        session_uuid="second",
        revision=2,
        phase="terminal",
        outcome="complete",
    )
    path.write_bytes(
        _entry("session_start", session_uuid="second")
        + _entry("shot_detected", padding="x" * 1000)
        + _entry("fusion_diagnostic", **current)
    )
    rewritten = reader.read(tmp_path)["sessions"][0]
    assert rewritten["session_uuid"] == "second"
    assert [item["shot_number"] for item in rewritten["shots"]] == [2]


def test_reader_rejects_invalid_revision_and_metric_shapes(tmp_path):
    path = tmp_path / "session_invalid.jsonl"
    invalid = fd.build_snapshot(
        _shot(), session_uuid="session", revision=2, phase="terminal", outcome="complete"
    )
    invalid["revision"] = 3
    invalid["metrics"] = ["not a metric"]
    path.write_bytes(
        _entry("session_start", session_uuid="session") + _entry("fusion_diagnostic", **invalid)
    )
    session = fd.DiagnosticReader().read(tmp_path)["sessions"][0]
    assert session["shots"] == []
    assert any("ignored 1" in reason for reason in session["reasons"])


def test_session_file_starts_with_header_before_diagnostic_write(tmp_path):
    logger = SessionLogger(tmp_path, provenance_collector=lambda *_args: {})
    logger.start_session(mode="mock")
    session_uuid = logger.active_session_uuid
    snapshot = fd.build_snapshot(
        _shot(),
        session_uuid=session_uuid,
        revision=2,
        phase="terminal",
        outcome="complete",
    )
    assert logger.log_fusion_diagnostic(session_uuid, snapshot)
    logger.end_session()
    entries = [json.loads(line) for line in logger.session_path.read_text().splitlines()]
    assert [entry["type"] for entry in entries[:2]] == ["session_start", "fusion_diagnostic"]
    assert entries[0]["session_uuid"] == entries[1]["session_uuid"]


def test_tester_route_is_scoped_no_store_and_reports_legacy(tmp_path):
    run = tmp_path / "tester" / "arm1" / "paired" / "run-01"
    run.mkdir(parents=True)
    (run / "session_legacy.jsonl").write_bytes(
        _entry("session_start", session_uuid="legacy") + _entry("session_end")
    )
    client = ts.create_app(
        sessions_root=tmp_path, rig_geometry=ts.DEFAULT_RIG_GEOMETRY
    ).test_client()
    response = client.get(
        "/api/tester/diagnostics",
        query_string={"tester_id": "tester", "arm_id": "arm1", "run": "run-01"},
    )
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    body = response.get_json()
    assert body["scope"] == {"tester_id": "tester", "arm_id": "arm1", "run": "run-01"}
    assert body["sessions"][0]["diagnostics_available"] is False


def test_reader_bounds_cached_session_files_and_reports_truncation(tmp_path):
    for index in range(fd.MAX_CACHED_FILES + 2):
        (tmp_path / f"session_{index:03d}.jsonl").write_bytes(
            _entry("session_start", session_uuid=f"session-{index}")
        )
    reader = fd.DiagnosticReader()
    result = reader.read(tmp_path)
    assert len(result["sessions"]) == fd.MAX_CACHED_FILES
    assert len(reader._states) == fd.MAX_CACHED_FILES
    assert result["retention"]["truncated"] is True
    assert any("bounded reader window" in reason for reason in result["reasons"])

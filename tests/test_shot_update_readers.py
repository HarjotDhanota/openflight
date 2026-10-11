"""Every session-log reader shows a shot's latest values and counts it once.

A shot published before its IWR or camera enrichment finished is logged as
``shot_detected`` and later ``shot_updated`` (same shot number). These tests
pin each reader to the late values, with one shot per shot number.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from openflight.camera import tester_server
from openflight.cloud import filtering
from openflight.commissioning_report import build_commissioning_report
from openflight.raw_radar_replay import load_session_events

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts" / "analysis"))

# pylint: disable=wrong-import-position,import-error
import analyze_trackman_angles  # noqa: E402
import compare_trackman  # noqa: E402
import diagnose_horizontal_angle  # noqa: E402
import experiment_spin_windows  # noqa: E402
import export_session  # noqa: E402
import plot_session  # noqa: E402
import replay_camera_fusion  # noqa: E402
import replay_spin_candidates  # noqa: E402

START = {"ts": "2026-10-09T10:00:00", "type": "session_start", "session_uuid": "s1"}
FIRST = {
    "ts": "2026-10-09T10:00:05",
    "type": "shot_detected",
    "shot_number": 1,
    "ball_speed_mph": 150.0,
    "club_speed_mph": 100.0,
    "club": "driver",
    "launch_angle_vertical": None,
    "launch_angle_horizontal": None,
    "spin_rpm": None,
    "pipeline_ms": {"initial_ui": 900.0, "iwr6843": None},
}
SECOND = {**FIRST, "ts": "2026-10-09T10:00:30", "shot_number": 2, "ball_speed_mph": 140.0}
UPDATE = {
    **FIRST,
    "ts": "2026-10-09T10:00:27",
    "type": "shot_updated",
    "launch_angle_vertical": 14.0,
    "launch_angle_horizontal": -1.5,
    "pipeline_ms": {"initial_ui": 900.0, "iwr6843": 21000.0},
    "update": {"reason": "late_enrichment", "late_by_ms": 21000.0, "simulators": "not_resent"},
}
EVENTS = [START, FIRST, SECOND, UPDATE, {"type": "session_end"}]


def _write(path: Path, events=EVENTS) -> Path:
    path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    return path


def _assert_latest_once(shots, key="launch_angle_vertical"):
    by_number = {}
    for shot in shots:
        by_number.setdefault(shot["shot_number"], []).append(shot)
    assert sorted(by_number) == [1, 2]
    assert all(len(rows) == 1 for rows in by_number.values()), "each shot counted once"
    assert by_number[1][0][key] == 14.0


def test_replay_and_review_loader_folds_updates(tmp_path):
    _hash, _start, events = load_session_events(_write(tmp_path / "session_1.jsonl"))

    assert [e["type"] for e in events].count("shot_updated") == 0
    _assert_latest_once([e for e in events if e["type"] == "shot_detected"])


def test_commissioning_report_counts_the_shot_once_with_its_late_timing():
    report = build_commissioning_report(
        [
            {
                "path": "session.jsonl",
                "sha256": "a" * 64,
                "byte_count": 10,
                "complete": True,
                "warnings": [],
                "records": EVENTS,
            }
        ]
    )

    assert report["counts"]["recorded_sensor_shots"] == 2
    assert report["timing"]["pipeline_iwr6843_ms"]["n"] == 1
    assert report["timing"]["pipeline_iwr6843_ms"]["median"] == 21000.0


def test_tester_server_shot_events_fold_updates(tmp_path):
    _write(tmp_path / "session_1.jsonl")

    events = tester_server._shot_events(tmp_path)

    _assert_latest_once([e for e in events if e.get("type") == "shot_detected"])
    assert tester_server._logged_sensor_shot_count(events) == 2


def test_cloud_upload_sends_the_final_shot_in_the_existing_format():
    lines = [json.dumps(event) for event in EVENTS]

    result = filtering.filter_session_lines(lines, device_id="dev-1")

    kept = [json.loads(line) for line in result.kept_lines]
    assert [e["type"] for e in kept] == [
        "session_start",
        "shot_detected",
        "shot_detected",
        "session_end",
    ]
    _assert_latest_once([e for e in kept if e["type"] == "shot_detected"])
    assert "update" not in kept[1], "no new field: the shot_detected contract is unchanged"
    assert kept[1]["ts"] == FIRST["ts"]
    assert result.kept_type_counts == {"session_start": 1, "shot_detected": 2, "session_end": 1}
    assert result.manifest["kept_entry_types"] == ["session_end", "session_start", "shot_detected"]


def test_cloud_upload_keeps_the_first_result_if_the_update_line_is_too_large(monkeypatch):
    monkeypatch.setattr(filtering, "MAX_LINE_BYTES", len(json.dumps(FIRST)))
    lines = [json.dumps(event) for event in EVENTS]

    result = filtering.filter_session_lines(lines, device_id="dev-1")

    kept = [json.loads(line) for line in result.kept_lines]
    assert kept[1]["launch_angle_vertical"] is None
    assert result.dropped_oversize == 1


def test_export_session_reads_folded_events(tmp_path):
    _write(tmp_path / "session_1.jsonl")

    _assert_latest_once(
        [e for e in export_session.read_events(tmp_path) if e["type"] == "shot_detected"]
    )
    _assert_latest_once(
        [
            e
            for e in export_session.read_events_file(tmp_path / "session_1.jsonl")
            if e["type"] == "shot_detected"
        ]
    )


def test_compare_trackman_uses_the_latest_shot(tmp_path):
    shots = compare_trackman.load_openflight(_write(tmp_path / "session.jsonl"))

    assert [shot.shot_number for shot in shots] == [1, 2]
    assert shots[0].launch_angle_vertical == 14.0


def test_plot_session_uses_the_latest_shot(tmp_path):
    _assert_latest_once(plot_session.load_session(_write(tmp_path / "session.jsonl")))


def test_analyze_trackman_angles_uses_the_latest_shot(tmp_path):
    shots = analyze_trackman_angles.load_openflight_details(_write(tmp_path / "session.jsonl"))

    _assert_latest_once(list(shots.values()))


def test_diagnose_horizontal_angle_uses_the_latest_shot(tmp_path):
    rows = diagnose_horizontal_angle.load_sessions([_write(tmp_path / "session.jsonl")])

    assert [row.shot_number for row in rows] == [1, 2]
    assert rows[0].v_angle == 14.0
    assert rows[0].h_angle == -1.5


@pytest.mark.parametrize("module", [experiment_spin_windows, replay_spin_candidates])
def test_spin_scripts_use_the_latest_shot(tmp_path, module):
    shots, _captures = module._load_session_entries(_write(tmp_path / "session.jsonl"))

    _assert_latest_once(shots)


def test_replay_camera_fusion_reads_folded_events(tmp_path):
    events = replay_camera_fusion._read_events(_write(tmp_path / "session.jsonl"))

    _assert_latest_once([e for e in events if e["type"] == "shot_detected"])

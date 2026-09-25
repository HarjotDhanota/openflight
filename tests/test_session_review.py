"""The session review must show every recorded result with an honest status and reason."""

from __future__ import annotations

import csv
import io
import json
import os
from pathlib import Path

import pytest

from openflight.review_metrics import STATUSES, review_replay
from openflight.session_review import (
    attempts_csv,
    build_session_review,
    replay_report_path,
    report_markdown,
)

PI_TESTER = "/home/pi/openflight_sessions/tester_pilot/t1"
# Sanitized shapes of the first Pi session: values are the published experimental
# spin candidates; identities and paths are synthetic.
SESSION_ONE = (
    # shot, spin rpm, spin confidence, IWR status, club status, scene candidate (x, y, d)
    (1, 3076.17, 0.249, "accepted_ops_guided", "rejected_no_club_track", (714.0, 245.3, 23.0)),
    (
        2,
        9008.79,
        0.258,
        "accepted_ops_guided_single_channel",
        "rejected_no_club_track",
        (1058.1, 193.0, 15.2),
    ),
    (
        3,
        8789.06,
        0.203,
        "accepted_ops_guided_single_channel",
        "rejected_no_club_track",
        (725.2, 245.9, 31.0),
    ),
    (
        4,
        3735.35,
        0.590,
        "accepted_track_speed_warning",
        "rejected_no_impact_time",
        (583.7, 214.8, 21.5),
    ),
    (
        5,
        2252.20,
        0.406,
        "accepted_ops_guided_single_channel",
        "rejected_no_club_track",
        (618.8, 222.5, 8.7),
    ),
)


def _camera_result(scene):
    x, y, diameter = scene
    return {
        "ball_estimate": {
            "status": "rejected_reference_ball_not_found",
            "confidence_tier": "withheld",
            "horizontal_deg": None,
            "vertical_deg": None,
            "reference_ball_diagnostics": {
                "scene": {
                    "status": "rejected",
                    "reason": "candidate failed size or hitting-zone geometry",
                    "candidate": {"x": x, "y": y, "diameter_px": diameter, "area_px": 200},
                },
                "impact": {
                    "status": "rejected",
                    "reason": "ValueError: no persistent tee-ball departure found",
                    "candidate": None,
                },
                "agreement": None,
                "selected_candidate": None,
            },
        },
        "horizontal_decision": {
            "selected_deg": None,
            "status": "camera_withheld_fallback_iwr",
            "iwr_horizontal_deg": None,
            "camera_horizontal_deg": None,
        },
        "club_delivery": {
            "status": "rejected_no_ball",
            "club_path_deg": None,
            "attack_angle_deg": None,
            "path_confidence_tier": "withheld",
            "attack_confidence_tier": "withheld",
        },
    }


def _report(shot, spin, confidence, iwr_status, club_status, scene):
    camera = _camera_result(scene)
    return {
        "schema_version": 1,
        "session_uuid": "session-one",
        "session_sha256": "a" * 64,
        "shot_number": shot,
        "source_identity": {"arm_id": "arm5", "capture_exposure_us": 300.0},
        "source_identity_evidence": {
            "arm_id": "session_start.config.camera_capture.output_dir location"
        },
        "stages": {
            "ops": {
                "status": "ok",
                "equivalence_status": "config_match_source_revision_not_proven",
                "result": {
                    "ball_speed_mph": 72.1,
                    "club_speed_mph": 49.5,
                    "spin": {
                        "spin_rpm": spin,
                        "confidence": confidence,
                        "snr": 1.66,
                        "quality": "experimental",
                        "method": "multitaper_ungated",
                        "candidates": [{"rpm": spin}],
                        "rejection_reason": None,
                    },
                },
            },
            "measured_total_speed_candidate": {"status": "not_requested"},
            "iwr6843": {
                "status": iwr_status,
                "launch_angle_deg": 38.0,
                "horizontal_deg": None,
                "horizontal_confidence": 0.637,
                "horizontal_status": "hlcmf_v1_low_coherence",
                "track_speed_mph": 81.3,
                "tracker_quality": "low",
                "club_path": {"status": club_status, "path_deg": None, "candidate_path_deg": None},
            },
            "camera": {
                "recorded_context": {
                    "replay": camera,
                    "recorded": camera,
                    "matches_recorded": True,
                    "comparison": {"status": "compared", "mismatches": []},
                },
                "recomputed_radar_context": {"status": "replayed", "result": camera},
            },
        },
    }


def _by_key(metrics):
    return {metric["key"]: metric for metric in metrics}


@pytest.mark.parametrize("shape", SESSION_ONE, ids=[f"shot-{row[0]}" for row in SESSION_ONE])
def test_session_one_shapes_render_every_status_with_its_reason(shape):
    shot, spin, confidence, iwr_status, club_status, scene = shape
    reviewed = review_replay(_report(*shape), {"ball_speed_mph": 72.1})
    metrics = _by_key(reviewed["metrics"])
    assert all(metric["status"] in STATUSES for metric in reviewed["metrics"])

    assert metrics["ball_speed_mph"]["status"] == "accepted"
    assert metrics["club_speed_mph"]["status"] == "accepted"
    spin_metric = metrics["spin_rpm"]
    assert spin_metric["status"] == "experimental"
    assert spin_metric["value"] == pytest.approx(spin)
    assert spin_metric["confidence"] == pytest.approx(confidence)
    assert "candidate only" in spin_metric["reason"]

    assert metrics["iwr_launch_vertical_deg"]["status"] == "accepted"
    assert metrics["iwr_launch_vertical_deg"]["recorded_status"] == iwr_status
    assert "tracker quality low" in metrics["iwr_launch_vertical_deg"]["reason"]
    if "warning" in iwr_status:
        assert iwr_status in metrics["iwr_launch_vertical_deg"]["reason"]
    horizontal = metrics["iwr_launch_horizontal_deg"]
    assert horizontal["status"] == "rejected"
    assert "hlcmf_v1_low_coherence" in horizontal["reason"]
    assert horizontal["confidence"] == pytest.approx(0.637)
    assert metrics["iwr_club_path_deg"]["status"] == "rejected"
    assert club_status in metrics["iwr_club_path_deg"]["reason"]

    camera = metrics["camera_launch_horizontal_deg"]
    assert camera["status"] == "rejected"
    assert "rejected_reference_ball_not_found" in camera["reason"]
    assert f"({scene[0]:.0f}, {scene[1]:.0f})" in camera["reason"]
    club = metrics["camera_club_path_deg"]
    assert club["status"] == "rejected"
    assert "needs the camera reference ball" in club["reason"]
    assert metrics["ball_speed_total_mph"]["status"] == "not_requested"

    overlay = reviewed["overlay"]
    assert overlay["expected_region"]["y_fraction"] == [0.4, 0.95]
    assert overlay["candidates"][0]["detector"] == "scene"
    assert overlay["candidates"][0]["y"] == pytest.approx(scene[1])

    agreements = {row["key"]: row for row in reviewed["agreements"]}
    assert agreements["live_vs_replay_ball_speed"]["difference"] == pytest.approx(0.0)
    assert agreements["ops_ball_vs_iwr_track_speed"]["difference"] == pytest.approx(9.2)
    assert agreements["camera_replay_vs_live"]["matches"] is True


def test_reliable_spin_follows_the_processor_rule_and_missing_spin_is_rejected():
    report = _report(*SESSION_ONE[0])
    spin = report["stages"]["ops"]["result"]["spin"]
    spin.update(quality="high", confidence=0.8)
    assert _by_key(review_replay(report, {})["metrics"])["spin_rpm"]["status"] == "accepted"
    spin.update(spin_rpm=None, rejection_reason="lower rail", quality="low")
    metric = _by_key(review_replay(report, {})["metrics"])["spin_rpm"]
    assert (metric["status"], metric["value"], metric["reason"]) == ("rejected", None, "lower rail")


def test_stage_errors_distinguish_absent_input_from_processing_failure():
    report = _report(*SESSION_ONE[0])
    report["stages"]["iwr6843"] = {
        "status": "error",
        "error": "ValueError: expected exactly one iwr6843_capture for the shot, found 0",
    }
    report["stages"]["camera"] = {"status": "error", "error": "ZeroDivisionError: division by zero"}
    metrics = _by_key(review_replay(report, {})["metrics"])
    assert metrics["iwr_launch_vertical_deg"]["status"] == "unavailable"
    assert "found 0" in metrics["iwr_launch_vertical_deg"]["reason"]
    assert metrics["camera_club_path_deg"]["status"] == "processing_failed"
    assert metrics["camera_club_path_deg"]["value"] is None


def test_unanalysed_shot_lists_every_metric_as_unavailable():
    reviewed = review_replay(None, {})
    assert reviewed["metrics"]
    assert {metric["status"] for metric in reviewed["metrics"]} == {"unavailable"}
    assert {metric["reason"] for metric in reviewed["metrics"]} == {
        "this shot has not been analysed yet"
    }


def _pgm(path: Path, width=8, height=4) -> None:
    path.write_bytes(f"P5\n{width} {height}\n255\n".encode("ascii") + bytes(width * height))


def _tester_tree(tmp_path: Path) -> Path:
    root = tmp_path / "sessions"
    tester = root / "t1"
    run = tester / "arm5" / "paired" / "run-01"
    (run / "iwr6843").mkdir(parents=True)
    events = [
        {"type": "session_start", "session_uuid": "session-one"},
        {"type": "trigger_event", "accepted": False, "reason": "no ball speed", "ts": "t"},
    ]
    for shot, *_rest in SESSION_ONE[:2]:
        capture = run / "arm5" / "camera" / f"camera_00{shot}"
        capture.mkdir(parents=True)
        for label in ("first", "trigger", "last"):
            _pgm(capture / f"{label}.pgm")
        (run / "iwr6843" / f"iwr_00{shot}.l3dump").write_bytes(b"raw")
        events += [
            {
                "type": "shot_detected",
                "shot_number": shot,
                "ball_speed_mph": 72.1,
                "ts": f"shot-{shot}",
            },
            {
                "type": "camera_capture",
                "shot_number": shot,
                "capture_path": f"{PI_TESTER}/arm5/paired/run-01/arm5/camera/camera_00{shot}",
            },
            {
                "type": "iwr6843_capture",
                "shot_number": shot,
                "capture_path": f"{PI_TESTER}/arm5/paired/run-01/iwr6843/iwr_00{shot}.l3dump",
            },
            {
                "type": "fusion_diagnostic",
                "shot_number": shot,
                "revision": 2,
                "outcome": "complete",
            },
        ]
    stray = run / "arm5" / "camera" / "camera_009"
    stray.mkdir(parents=True)
    _pgm(stray / "first.pgm")
    (run / "session_x_arm5.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )
    (tester / "impact").mkdir()
    _pgm(tester / "impact" / "camera_001.pgm")
    (tester / "ladder.json").write_text(
        json.dumps(
            {
                "rungs": {
                    "full-300": {
                        "swings": [
                            {"capture": "camera_001", "color": "green", "reasons": []},
                            {"capture": "camera_002", "color": "amber", "reasons": ["ball moved"]},
                        ]
                    }
                },
                "photos": {"camera_001": f"{PI_TESTER}/impact/camera_001.pgm"},
                "ineligible_captures": [
                    {
                        "capture": "camera_009",
                        "reason": "runtime setup readiness blocked at capture review",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    annotation = tester / "annotations" / "arm5" / "run-01" / "camera_001.tracks.json"
    annotation.parent.mkdir(parents=True)
    annotation.write_text('{"schema": "openflight.track_annotation.v1"}', encoding="utf-8")
    report = replay_report_path(tester / "analysis", "arm5", "run-01", 1)
    report.parent.mkdir(parents=True)
    report.write_text(json.dumps(_report(*SESSION_ONE[0])), encoding="utf-8")
    return root


def test_session_review_covers_shots_stray_triggers_photos_and_verdicts(tmp_path):
    root = _tester_tree(tmp_path)
    review = build_session_review(root, "t1", analysis={"status": "complete"})
    assert review["schema"] == "openflight.session_review.v1"
    attempts = {attempt["attempt_id"]: attempt for attempt in review["attempts"]}
    assert set(attempts) == {"session-one:1", "session-one:2", "run-01:camera_009"}

    first = attempts["session-one:1"]
    assert first["evidence"]["impact_photo"] == "t1/impact/camera_001.pgm"
    assert first["evidence"]["preview_frames"]["trigger"] == (
        "t1/arm5/paired/run-01/arm5/camera/camera_001/trigger.pgm"
    )
    assert first["evidence"]["iwr_dump"] == "t1/arm5/paired/run-01/iwr6843/iwr_001.l3dump"
    assert first["evidence"]["replay_report"] == "t1/analysis/replay/arm5/run-01/shot-001.json"
    assert first["evidence"]["track_annotation"] == (
        "t1/annotations/arm5/run-01/camera_001.tracks.json"
    )
    assert attempts["session-one:2"]["evidence"]["track_annotation"] is None
    assert first["picture_verdict"]["color"] == "green"
    assert "not a fused-metric verdict" in first["picture_verdict"]["meaning"]
    assert first["live_processing"]["label"] == "processing finished"
    assert _by_key(first["metrics"])["spin_rpm"]["status"] == "experimental"

    unanalysed = attempts["session-one:2"]
    assert {metric["status"] for metric in unanalysed["metrics"]} == {"unavailable"}
    assert unanalysed["picture_verdict"]["reasons"] == ["ball moved"]

    stray = attempts["run-01:camera_009"]
    assert stray["kind"] == "camera_trigger_without_shot"
    assert "setup readiness" in stray["rejection"]["reason"]
    assert stray["evidence"]["preview_frames"]["first"].endswith("camera_009/first.pgm")

    run = review["runs"][0]
    assert run["rejected_trigger_count"] == 1
    assert run["session_file"] == "t1/arm5/paired/run-01/session_x_arm5.jsonl"
    every_path = json.dumps(review)
    assert "/home/pi" not in every_path and str(tmp_path) not in every_path


def test_review_outputs_are_readable_without_a_viewer(tmp_path):
    review = build_session_review(_tester_tree(tmp_path), "t1", analysis={})
    rows = list(csv.DictReader(io.StringIO(attempts_csv(review))))
    spin = next(
        row for row in rows if row["attempt_id"] == "session-one:1" and row["metric"] == "spin_rpm"
    )
    assert spin["status"] == "experimental"
    assert float(spin["value"]) == pytest.approx(3076.17)
    assert any(row["kind"] == "camera_trigger_without_shot" for row in rows)
    markdown = report_markdown(review)
    assert "| Spin (OPS) | experimental | 3076.2 rpm | 0.25 |" in markdown
    assert "Impact photo: t1/impact/camera_001.pgm" in markdown


def test_a_corrupt_session_log_is_reported_not_hidden(tmp_path):
    root = _tester_tree(tmp_path)
    session = next((root / "t1" / "arm5" / "paired" / "run-01").glob("session_*.jsonl"))
    session.write_bytes(session.read_bytes() + b'{"type": "shot_det')
    review = build_session_review(root, "t1", analysis={})
    assert "session log unreadable" in review["runs"][0]["errors"][0]
    assert any(a["kind"] == "camera_trigger_without_shot" for a in review["attempts"])


LOCAL_REPLAYS = os.environ.get("OPENFLIGHT_LOCAL_REPLAYS")


@pytest.mark.skipif(
    not LOCAL_REPLAYS, reason="set OPENFLIGHT_LOCAL_REPLAYS to a folder of replay reports"
)
def test_local_replay_reports_render_with_experimental_spin_and_reasons():
    reports = sorted(Path(LOCAL_REPLAYS).glob("replay-shot-*.json"))
    assert reports
    for path in reports:
        metrics = _by_key(
            review_replay(json.loads(path.read_text(encoding="utf-8")), {})["metrics"]
        )
        assert metrics["spin_rpm"]["status"] in {"experimental", "accepted"}
        assert metrics["spin_rpm"]["value"] is not None
        for metric in metrics.values():
            assert metric["status"] in STATUSES
            if metric["status"] not in ("accepted",):
                assert metric["reason"], metric


def test_refused_camera_triggers_are_counted_by_reason_even_without_a_shot(tmp_path):
    root = _tester_tree(tmp_path)
    session = next((root / "t1" / "arm5" / "paired" / "run-01").glob("session_*.jsonl"))
    with session.open("a", encoding="utf-8") as handle:
        for reason in ("ring_busy", "ring_busy", "save_backlog_full"):
            event = {"type": "camera_trigger_rejected", "reason": reason, "trigger_timestamp": 1.0}
            handle.write(json.dumps(event) + "\n")
    review = build_session_review(root, "t1", analysis={})
    rejections = review["runs"][0]["camera_trigger_rejections"]
    assert rejections["counts"] == {"ring_busy": 2, "save_backlog_full": 1}
    assert len(rejections["recent"]) == 3
    attempt = next(a for a in review["attempts"] if a["attempt_id"] == "session-one:1")
    assert attempt["evidence"]["camera_outcome"]["category"] == "captured"
    assert "camera refused triggers: 2 ring_busy, 1 save_backlog_full" in report_markdown(review)

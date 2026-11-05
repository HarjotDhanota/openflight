"""Synthetic known-answer signals through the normal saved-shot analysis action."""

import json

import pytest

from openflight import shot_metrics
from scripts.analysis import analyze_tester_session as runner, replay_raw_fusion
from tests import session_fixtures as signals


def prepared_signals(tmp_path):
    root = signals.capture_tree(tmp_path / "signals", shots=(1,), tester_setup=True)
    session = next((root / signals.TESTER).rglob("session_*.jsonl"))
    events = [json.loads(line) for line in session.read_text().splitlines()]
    events[0]["config"].pop("rig_geometry")
    for event in events:
        if event["type"] == "shot_detected":
            event.update(timestamp="2026-09-25T01:50:37+00:00", handed="RH")
        if event["type"] == "rolling_buffer_capture":
            event.update(
                trigger_timestamp=1790301037.0,
                first_byte_timestamp=1790301037.01,
                trigger_type="sound",
                capture_id="synthetic-own-ops-1",
            )
    session.write_text("".join(json.dumps(event) + "\n" for event in events))
    return root, session


def test_normal_action_recomputes_full_records_and_keeps_saved_metrics_separate(tmp_path):
    root, _ = prepared_signals(tmp_path)
    assert runner.analyze(root, signals.TESTER, package=True, viewer=None) == 0
    analysis = root / signals.TESTER / "analysis"
    report = json.loads(next((analysis / "replay").rglob("shot-001.json")).read_text())
    fresh = report["stages"]["full_shot"]
    metrics = fresh["metrics"]
    assert len(metrics) == 37
    assert all(record["status"] in shot_metrics.LABELS for record in metrics.values())
    assert metrics["ball_speed"]["value"] == pytest.approx(100.7, abs=0.3)
    assert metrics["launch_vertical"]["value"] == pytest.approx(18, abs=1)
    assert metrics["launch_horizontal"]["value"] == pytest.approx(
        signals.FLIGHT_HORIZONTAL_DEG, abs=0.2
    )
    assert fresh["status"] == "replayed" and fresh["errors"] == {}
    assert fresh["camera_fusion_context"]["camera_pose"]["status"] == "uncalibrated"
    assert fresh["session_uuid"] == signals.SESSION_UUID
    assert fresh["shot_number"] == 1 and fresh["source_published"] is True
    assert metrics["carry"]["value"] is None and metrics["carry"]["reason"]
    assert fresh["impact"]["status"] == "rejected"
    assert fresh["impact"]["reason_code"] == "no_ball_range_for_this_shot"
    review = json.loads((analysis / "session_review.json").read_text())
    attempt = next(row for row in review["attempts"] if row["shot_number"] == 1)
    reviewed = {row["key"]: row for row in attempt["replayed_kiosk_metrics"]}
    assert len(reviewed) == 37
    for key, record in metrics.items():
        assert reviewed[key]["value"] == record["value"]
        assert reviewed[key]["status"] == record["status"]
        assert reviewed[key]["reason"] == record["reason"]
    assert attempt["replayed_impact"] == fresh["impact"]
    assert "replayed.ball_speed" in (analysis / "attempts.csv").read_text()
    markdown = (analysis / "report.md").read_text()
    assert "Freshly recomputed metric records:" in markdown
    job = json.loads((analysis / "job.json").read_text())
    reproduced = runner.verify(root / job["bundle"]["path"], tmp_path)
    assert reproduced["status"] == "reproduced", reproduced["mismatches"]


def test_explicit_camera_disable_is_respected_by_full_chain(tmp_path):
    root, session = prepared_signals(tmp_path)
    args = runner._replay_args(session, 1, root)
    args.camera = False
    report = replay_raw_fusion.replay(args)
    fresh = report["stages"]["full_shot"]
    assert fresh["metrics"]["ball_speed"]["value"] > 100
    assert fresh["errors"]["camera"] == "camera replay not requested"
    assert fresh["impact"]["status"] == "rejected"


def test_raw_benchmark_call_does_not_implicitly_run_full_pipeline(tmp_path):
    root, session = prepared_signals(tmp_path)
    args = runner._replay_args(session, 1, root)
    args.full_shot = False
    assert "full_shot" not in replay_raw_fusion.replay(args)["stages"]

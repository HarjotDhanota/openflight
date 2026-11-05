"""Raw full-shot replay must retain supported observations across independent failures."""

import io
import json
from types import SimpleNamespace

import pytest

from openflight import kiosk_extensions, server, shot_metrics
from openflight.camera.impact_live import LiveAnalyser
from openflight.full_shot_replay import _fresh_ops
from scripts.analysis import analyze_tester_session as analyzer
from scripts.analysis.replay_raw_fusion import replay
from tests.test_full_shot_replay import prepared_signals


@pytest.fixture
def recording(tmp_path):
    root, session = prepared_signals(tmp_path)
    rows = [json.loads(line) for line in session.read_text(encoding="utf-8").splitlines()]
    return root, session, rows


def _write(session, rows):
    session.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _full(recording):
    root, session, _rows = recording
    args = analyzer._replay_args(session, 1, root)
    args.full_shot = True
    return replay(args)["stages"]["full_shot"]


def _assert_owned_ops(result):
    assert result["shot_number"] == 1
    assert result["session_uuid"] == "session-bundle"
    assert set(result["metrics"]) == set(shot_metrics.METRIC_IDS)
    assert len(result["metrics"]) == 37
    ball = result["metrics"]["ball_speed"]
    assert ball["status"] == "measured"
    assert ball["value"] == pytest.approx(100.7, abs=0.2)


def test_poisoned_saved_metrics_and_impact_cannot_seed_the_raw_full_chain(recording):
    baseline = _full(recording)
    _assert_owned_ops(baseline)
    _root, session, rows = recording
    saved = next(row for row in rows if row["type"] == "shot_detected")
    saved.update(
        metrics={
            key: {"status": "measured", "value": 9999, "source": "poisoned saved output"}
            for key in shot_metrics.METRIC_IDS
        },
        ball_speed_mph=9999,
        club_speed_mph=9999,
        spin_rpm=9999,
        spin_confidence=1.0,
        launch_angle_vertical=89,
        launch_angle_vertical_source="radar",
        launch_angle_horizontal=89,
        launch_angle_horizontal_source="radar",
        ball_range_m=99,
        ball_range_source="camera_resting_ball",
        impact_location={"status": "ok", "impact_offset_mm": 9999, "impact_height_mm": 9999},
        collision_model={"status": "measured", "spin_axis_deg": 89},
        ball_speed_full={"status": "measured", "value_mph": 9999},
    )
    _write(session, rows)
    fresh = _full(recording)
    _assert_owned_ops(fresh)
    assert fresh["metrics"] == baseline["metrics"]
    assert fresh["impact"] == baseline["impact"]


def test_missing_ops_raw_cannot_promote_saved_derived_outputs(recording):
    _root, session, rows = recording
    saved = next(row for row in rows if row["type"] == "shot_detected")
    saved["metrics"] = {
        key: {"status": "measured", "value": 9999} for key in shot_metrics.METRIC_IDS
    }
    saved["impact_location"] = {"status": "ok", "impact_offset_mm": 9999}
    _write(session, [row for row in rows if row["type"] != "rolling_buffer_capture"])
    result = _full(recording)
    assert result["session_uuid"] == "session-bundle" and result["shot_number"] == 1
    assert result["source_published"] is True
    assert len(result["metrics"]) == 37
    assert result["status"] == "partial"
    assert all(record["value"] is None for record in result["metrics"].values())
    assert all(record["status"] == "not_measured" for record in result["metrics"].values())
    assert result["impact"]["status"] == "rejected"


def test_missing_iwr_cannot_promote_the_saved_radar_launch(recording):
    _root, session, rows = recording
    saved = next(row for row in rows if row["type"] == "shot_detected")
    saved.update(
        launch_angle_vertical=89,
        launch_angle_vertical_source="radar",
        launch_angle_horizontal=89,
        launch_angle_horizontal_source="radar",
        iwr6843_horizontal_deg=89,
    )
    _write(session, [row for row in rows if row["type"] != "iwr6843_capture"])
    result = _full(recording)
    _assert_owned_ops(result)
    assert "iwr6843" in result["errors"]
    for key in ("launch_vertical", "launch_horizontal"):
        metric = result["metrics"][key]
        assert metric["value"] != 89
        assert metric["source"] not in ("radar", "radar_low_coherence")
        if metric["value"] is None:
            assert metric["status"] == "not_measured"
        else:
            assert "camera" in metric["source"].lower()


def test_camera_context_identity_mismatch_keeps_ops_and_refuses_camera(recording):
    _root, session, rows = recording
    saved = next(row for row in rows if row["type"] == "shot_detected")
    saved["camera_fusion_context"]["shot_number"] = 999
    _write(session, rows)
    result = _full(recording)
    _assert_owned_ops(result)
    assert result["status"] == "partial"
    assert "camera context" in result["errors"]["camera"]
    assert result["camera_fusion_context"]["available"] is False
    assert result["impact"]["status"] == "rejected"
    assert result["metrics"]["impact_offset"]["value"] is None


def test_missing_camera_raw_keeps_ops_and_own_iwr_without_saved_camera_output(recording):
    _root, session, rows = recording
    saved = next(row for row in rows if row["type"] == "shot_detected")
    saved["impact_location"] = {"status": "ok", "impact_offset_mm": 9999}
    _write(session, rows)
    next(session.parent.rglob("frames.npz")).unlink()
    result = _full(recording)
    _assert_owned_ops(result)
    assert "camera" in result["errors"]
    assert "iwr6843" not in result["errors"]
    assert result["metrics"]["launch_vertical"]["source"] == "radar"
    assert result["metrics"]["launch_vertical"]["value"] == pytest.approx(18, abs=1)
    assert result["impact"]["status"] == "rejected"
    assert result["metrics"]["impact_offset"]["value"] is None


def test_missing_original_ops_epochs_keep_signal_speed_but_no_contact_epoch(recording):
    _root, _session, rows = recording
    entry = next(row for row in rows if row["type"] == "rolling_buffer_capture")
    entry.pop("trigger_timestamp")
    entry.pop("first_byte_timestamp")
    fresh = _fresh_ops(entry, rows[0], 1, 30000, "7-iron")
    assert fresh.ball_speed_mph == pytest.approx(100.7, abs=0.2)
    assert fresh.impact_timestamp is None and fresh.impact_timestamp_kld7 is None


def test_impact_exception_preserves_fresh_metrics_and_restores_external_state(
    recording, monkeypatch
):
    calls = []

    def failing_impact(_self, _job):
        calls.append("impact")
        raise RuntimeError("known-answer impact failure")

    def forbidden_external_read():
        calls.append("external state read")
        raise AssertionError("offline replay borrowed current operator state")

    monkeypatch.setattr(LiveAnalyser, "analyse", failing_impact)
    camera_config = {"external": "camera"}
    profile = SimpleNamespace(get_active=forbidden_external_read)
    air = SimpleNamespace(conditions=forbidden_external_read)
    debug = io.StringIO()
    logger = SimpleNamespace(log_event=forbidden_external_read)
    monkeypatch.setattr(server, "camera_capture_config", camera_config)
    monkeypatch.setattr(server, "profile_store", profile)
    monkeypatch.setattr(server, "air_sensor_service", air)
    monkeypatch.setattr(server, "debug_mode", True)
    monkeypatch.setattr(server, "debug_log_file", debug)
    monkeypatch.setattr(server, "get_session_logger", lambda: logger)
    monkeypatch.setattr(server, "log_session_error", forbidden_external_read)
    extensions, socket = kiosk_extensions._extensions, server.socketio
    result = _full(recording)
    _assert_owned_ops(result)
    assert calls == ["impact"]
    assert result["status"] == "partial"
    assert "impact" in result["errors"]
    assert result["impact"]["status"] == "rejected"
    assert result["metrics"]["impact_offset"]["value"] is None
    assert result["metrics"]["impact_height"]["value"] is None
    assert server.camera_capture_config is camera_config
    assert server.profile_store is profile and server.air_sensor_service is air
    assert server.debug_mode is True and server.debug_log_file is debug
    assert server.get_session_logger() is logger
    assert server.log_session_error is forbidden_external_read
    assert kiosk_extensions._extensions is extensions and server.socketio is socket
    assert debug.getvalue() == ""


@pytest.mark.parametrize("unsupported", ["calibrated_camera", "invalid_rig"])
def test_unsupported_camera_geometry_refuses_camera_and_impact_but_keeps_ops(
    recording, unsupported
):
    _root, session, rows = recording
    config = rows[0]["config"]
    if unsupported == "calibrated_camera":
        config["camera_capture"]["lens"] = {"lens_id": "6mm", "intrinsics": "calibrated"}
        error_key, reason = "camera", "calibrated"
    else:
        config["rig_geometry"] = {
            "snapshot": {"parameters": {"camera_mount_height_m": -1}, "sha256": "0" * 64}
        }
        error_key, reason = "rig", "rig"
    _write(session, rows)
    saved_bytes = session.read_bytes()
    result = _full(recording)
    _assert_owned_ops(result)
    assert result["status"] == "partial"
    assert reason in result["errors"][error_key]
    assert result["impact"]["status"] == "rejected"
    assert result["camera_fusion_context"]["available"] is False
    for key in ("launch_vertical", "launch_horizontal"):
        assert "camera" not in str(result["metrics"][key]["source"]).lower()
    for key in ("impact_offset", "impact_height", "dynamic_lie"):
        assert result["metrics"][key]["status"] == "not_measured"
        assert result["metrics"][key]["value"] is None
    assert session.read_bytes() == saved_bytes

import hashlib
import json
from datetime import datetime

import numpy as np
import pytest

from openflight.camera.club_delivery import ReferenceBallTracker
from openflight.camera.fusion_processing import build_context
from openflight.camera.geometry_contract import EffectiveCameraGeometryInputs
from openflight.clubs import ClubType
from openflight.launch_monitor import Shot
from openflight.session_logger import SessionLogger
from scripts.analysis.replay_camera_fusion import (
    _comparison_mismatches,
    _write_output,
    main,
    replay_recorded_shot,
)


def _write_replay_fixture(tmp_path):
    capture_dir = tmp_path / "camera" / "camera_000001"
    capture_dir.mkdir(parents=True)
    capture_path = capture_dir / "frames.npz"
    np.savez(
        capture_path,
        frames=np.zeros((20, 6, 8), dtype=np.uint8),
        host_timestamp_ns=np.arange(20, dtype=np.int64),
        trigger_host_timestamp_ns=np.int64(10),
        pre_trigger_count=np.int32(10),
    )
    capture_hash = hashlib.sha256(capture_path.read_bytes()).hexdigest()
    geometry = EffectiveCameraGeometryInputs(
        camera_height_m=0.095,
        radar_height_m=0.051,
        tee_slant_range_m=1.5,
        ball_height_m=0.021,
        camera_lateral_offset_m=0.0,
        camera_forward_offset_m=0.03,
        image_width_px=8,
        image_height_px=6,
        horizontal_pixel_sign=1.0,
        roll_correction_deg=0.0,
        ball_horizontal_output_offset_deg=0.0,
        ball_diameter_m=0.04267,
    )
    context = build_context(
        geometry=geometry,
        lighting_eligible=True,
        ball_tracker=ReferenceBallTracker(),
        club_tracker=ReferenceBallTracker(),
        ball_range_evidence=None,
        club_range_evidence=None,
        ops_ball_speed_mph=100.0,
        ops_club_speed_mph=75.0,
        iwr_vertical_deg=18.0,
        iwr_horizontal_deg=1.0,
        iwr_horizontal_confidence=0.8,
        club=ClubType.IRON_7,
        capture_npz_sha256=capture_hash,
        session_uuid="session-a",
        shot_number=3,
    )
    events = [
        {"type": "session_start", "session_uuid": "session-a"},
        {
            "type": "camera_capture",
            "shot_number": 3,
            "capture_path": str(capture_dir),
            "capture_error": None,
        },
        {"type": "shot_detected", "shot_number": 3, "camera_fusion_context": context},
    ]
    session_file = tmp_path / "session_test.jsonl"
    session_file.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    return session_file, capture_path


def test_replay_entry_reads_frozen_capture_and_reports_identity(tmp_path):
    session_file, _ = _write_replay_fixture(tmp_path)
    result = replay_recorded_shot(session_file, 3)
    assert result["session_uuid"] == "session-a"
    assert result["shot_number"] == 3
    assert result["replay"]["ball_estimate"]["status"]
    assert result["matches_recorded"] is None


def test_replay_entry_rejects_capture_changed_after_recording(tmp_path):
    session_file, capture_path = _write_replay_fixture(tmp_path)
    capture_path.write_bytes(capture_path.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="hash"):
        replay_recorded_shot(session_file, 3)


def test_output_cannot_overwrite_an_input(tmp_path):
    protected = tmp_path / "frames.npz"
    protected.write_bytes(b"capture")
    with pytest.raises(ValueError, match="overwrite"):
        _write_output(protected, {"ok": True}, {protected.resolve()})
    assert protected.read_bytes() == b"capture"


def test_replay_comparison_keeps_integer_identity_exact_and_float_tolerance_explicit():
    assert _comparison_mismatches({"count": 1}, {"count": True}) == ["$.count"]
    assert _comparison_mismatches({"count": 1}, {"count": 1.0}) == ["$.count"]
    assert _comparison_mismatches({"value": 2.0}, {"value": 2.0 + 5e-10}) == []
    assert _comparison_mismatches({"value": 1e9}, {"value": 1e9 + 1e-3}) == ["$.value"]


def test_cli_reports_expected_input_error_without_traceback(tmp_path, monkeypatch, capsys):
    missing = tmp_path / "missing.jsonl"
    monkeypatch.setattr("sys.argv", ["replay_camera_fusion.py", str(missing), "1"])
    assert main() == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("camera fusion replay failed:")
    assert "Traceback" not in captured.err


def test_replay_entry_reads_actual_session_logger_capture_contract(tmp_path):
    logger = SessionLogger(log_dir=tmp_path, enabled=True)
    logger.start_session(mode="rolling-buffer", trigger_type="sound")
    capture_dir = tmp_path / "camera" / "camera_000001"
    capture_dir.mkdir(parents=True)
    capture_path = capture_dir / "frames.npz"
    np.savez(
        capture_path,
        frames=np.zeros((20, 6, 8), dtype=np.uint8),
        host_timestamp_ns=np.arange(20, dtype=np.int64),
        trigger_host_timestamp_ns=np.int64(10),
        pre_trigger_count=np.int32(10),
    )
    capture_hash = hashlib.sha256(capture_path.read_bytes()).hexdigest()
    context = build_context(
        geometry=EffectiveCameraGeometryInputs(
            camera_height_m=0.095,
            radar_height_m=0.051,
            tee_slant_range_m=1.5,
            ball_height_m=0.021,
            camera_lateral_offset_m=0.0,
            camera_forward_offset_m=0.03,
            image_width_px=8,
            image_height_px=6,
            horizontal_pixel_sign=1.0,
            roll_correction_deg=0.0,
            ball_horizontal_output_offset_deg=0.0,
            ball_diameter_m=0.04267,
        ),
        lighting_eligible=True,
        ball_tracker=ReferenceBallTracker(),
        club_tracker=ReferenceBallTracker(),
        ball_range_evidence=None,
        club_range_evidence=None,
        ops_ball_speed_mph=100.0,
        ops_club_speed_mph=75.0,
        iwr_vertical_deg=18.0,
        iwr_horizontal_deg=1.0,
        iwr_horizontal_confidence=0.8,
        club=ClubType.IRON_7,
        capture_npz_sha256=capture_hash,
        session_uuid=logger.active_session_uuid,
        shot_number=1,
    )
    shot = Shot(
        ball_speed_mph=100.0,
        timestamp=datetime.now(),
        shot_number=1,
        camera_fusion_context=context,
    )
    logger.log_camera_capture(
        shot_number=1,
        shot_timestamp=None,
        trigger_timestamp=None,
        capture_path=str(capture_dir),
    )
    logger.log_shot(shot)
    result = replay_recorded_shot(logger.session_path, 1)
    assert result["capture_npz_sha256"] == capture_hash
    assert result["session_uuid"] == logger.active_session_uuid

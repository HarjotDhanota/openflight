"""A reviewed attempt carries the camera facts needed to read its frames correctly."""

from __future__ import annotations

import json

import numpy as np

from openflight.capture_facts import capture_facts
from tests.session_fixtures import TESTER, capture_tree


def _capture(tmp_path):
    root = capture_tree(tmp_path)
    run = root / TESTER / "arm5" / "paired" / "run-01"
    session = json.loads(
        next(run.glob("session_*.jsonl")).read_text(encoding="utf-8").splitlines()[0]
    )
    return run / "arm5" / "camera" / "camera_001", session


def test_facts_come_from_the_clip_and_the_session(tmp_path):
    folder, session = _capture(tmp_path)
    facts = capture_facts(folder, {"trigger_delta_ms": 1.5}, session)
    assert facts["saved_dimensions_px"] == [320, 200]
    assert facts["orientation"] == {
        "rotate_180": False,
        "mirror_horizontal": False,
        "roll_correction_deg": 0.0,
    }
    assert facts["orientation_matches_session"] is True
    assert (facts["delivered_fps"], facts["gap_count"]) == (119.6, 0)
    assert (
        facts["pre_trigger_frames"],
        facts["post_trigger_frames"],
        facts["trigger_frame_index"],
    ) == (18, 6, 17)
    assert facts["sensor_timestamp_ns"]["trigger"] == str(17 * 8_333_333)
    assert (facts["applied_exposure_us_median"], facts["applied_gain_median"]) == (300.0, 4.0)
    assert facts["setup_config_hash"] == "5e" * 32
    assert facts["rig_geometry_sha256"] == session["config"]["rig_geometry"]["snapshot"]["sha256"]
    assert facts["strip_y_offset_px"] == 0
    assert facts["ball_gate_rows_px"] == [80.0, 190.0]


def test_a_full_resolution_clip_states_the_rows_the_ball_gate_accepts(tmp_path):
    folder, session = _capture(tmp_path)
    (folder / "first.pgm").write_bytes(b"P5\n1280 800\n255\n" + bytes(1280 * 800))
    assert capture_facts(folder, {}, session)["ball_gate_rows_px"] == [320.0, 760.0]


def test_an_orientation_that_differs_from_the_session_is_flagged(tmp_path):
    folder, session = _capture(tmp_path)
    session["config"]["camera_capture"]["rotate_180"] = True
    facts = capture_facts(folder, {}, session)
    assert facts["orientation_matches_session"] is False
    assert facts["session_orientation"]["rotate_180"] is True


def test_missing_records_stay_null_rather_than_guessed(tmp_path):
    folder, _session = _capture(tmp_path)
    (folder / "metadata.json").unlink()
    np.savez(folder / "frames.npz", frames=np.zeros((2, 4, 8), np.uint8))
    facts = capture_facts(folder, {}, {})
    assert facts["delivered_fps"] is None and facts["applied_exposure_us_median"] is None
    assert facts["orientation"]["rotate_180"] is None
    assert facts["rig_geometry_sha256"] is None
    assert capture_facts(None, {}, {}) is None

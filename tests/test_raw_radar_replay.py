from __future__ import annotations

import json
import math

import pytest

from openflight.clubs import ClubType
from openflight.raw_radar_replay import replay_ops_capture, session_shot_events
from openflight.rolling_buffer.processor import RollingBufferProcessor


def synthetic_entry():
    frequency_hz = 4_000.0
    samples = range(4096)
    return {
        "type": "rolling_buffer_capture",
        "shot_number": 7,
        "sample_time": 10.0,
        "trigger_time": 10.1,
        "i_samples": [
            2048 + int(500 * math.cos(2 * math.pi * frequency_hz * index / 30_000))
            for index in samples
        ],
        "q_samples": [
            2048 + int(500 * math.sin(2 * math.pi * frequency_hz * index / 30_000))
            for index in samples
        ],
    }


def test_raw_ops_replay_uses_production_processor_deterministically():
    entry = synthetic_entry()
    first = replay_ops_capture(entry, sample_rate_hz=30_000, club_type=ClubType.IRON_7)
    second = replay_ops_capture(entry, sample_rate_hz=30_000, club_type=ClubType.IRON_7)
    assert first == second
    assert first["overlapping_readings"]
    assert first["canonical_capture_payload_sha256"]
    assert first["equivalence_status"] == "unverified_missing_or_different_recorded_config"
    assert "Per-window" in first["note"]


def test_recorded_config_hash_enables_bounded_ops_equivalence():
    entry = synthetic_entry()
    first = replay_ops_capture(entry, sample_rate_hz=30_000, club_type=ClubType.IRON_7)
    entry["processor_config"] = first["processor_config"]
    entry["processor_config_sha256"] = first["processor_config"]["sha256"]
    replay = replay_ops_capture(entry, sample_rate_hz=30_000, club_type=ClubType.IRON_7)
    assert replay["equivalence_status"] == "config_match_source_revision_not_proven"


def test_processor_config_freezes_primitive_inputs_before_spin_callback():
    entry = synthetic_entry()
    original = RollingBufferProcessor.BALL_SPEED_MATCH_TOLERANCE_MPH
    processor = RollingBufferProcessor(sample_rate=30_000)
    from openflight.rolling_buffer.types import IQCapture

    capture = IQCapture(
        sample_time=entry["sample_time"],
        trigger_time=entry["trigger_time"],
        i_samples=entry["i_samples"],
        q_samples=entry["q_samples"],
    )

    def mutate_after_ball_detection(_speed):
        RollingBufferProcessor.BALL_SPEED_MATCH_TOLERANCE_MPH = original + 10.0
        return 5000.0

    try:
        processed = processor.process_capture(
            capture,
            expected_spin_for_ball_speed=mutate_after_ball_detection,
            club_type=ClubType.IRON_7,
        )
    finally:
        RollingBufferProcessor.BALL_SPEED_MATCH_TOLERANCE_MPH = original
    assert processed is not None
    assert processed.processor_config["constants"]["BALL_SPEED_MATCH_TOLERANCE_MPH"] == original
    assert processed.processor_config["resolved_spin_prior_rpm"] == 5000.0


def test_session_reader_hashes_exact_bytes_and_selects_shot(tmp_path):
    entry = synthetic_entry()
    other = {**entry, "shot_number": 8}
    path = tmp_path / "session.jsonl"
    start = {"type": "session_start", "session_uuid": "session-a"}
    path.write_text(
        json.dumps(start) + "\n" + json.dumps(entry) + "\n" + json.dumps(other) + "\n",
        encoding="utf-8",
    )
    source_hash, events = session_shot_events(path, 7)
    assert len(source_hash) == 64
    assert [event.get("shot_number") for event in events] == [None, 7]


def test_session_reader_retains_unavailable_camera_context_without_identity(tmp_path):
    path = tmp_path / "session.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(event)
            for event in (
                {"type": "session_start", "session_uuid": "session-a"},
                {
                    "type": "shot_detected",
                    "shot_number": 1,
                    "camera_fusion_context": {"available": False, "reason": "capture failed"},
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )
    _hash, events = session_shot_events(path, 1)
    assert events[1]["camera_fusion_context"]["available"] is False


def test_session_reader_rejects_cross_session_event_identity(tmp_path):
    path = tmp_path / "session.jsonl"
    path.write_text(
        json.dumps({"type": "session_start", "session_uuid": "session-a"})
        + "\n"
        + json.dumps({"type": "shot_detected", "shot_number": 1, "session_uuid": "session-b"})
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="event session_uuid"):
        session_shot_events(path, 1)


@pytest.mark.parametrize("invalid", [True, 7.0, 0])
def test_session_reader_rejects_noncanonical_shot_identity(tmp_path, invalid):
    path = tmp_path / "session.jsonl"
    path.write_text(
        json.dumps({"type": "session_start", "session_uuid": "session-a"})
        + "\n"
        + json.dumps({"type": "shot_detected", "shot_number": invalid})
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="shot_number identity"):
        session_shot_events(path, 7)

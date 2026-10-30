"""Capture events must not silently become sound-derived impact observations."""

import json

import pytest

from openflight.rolling_buffer.monitor import RollingBufferMonitor
from openflight.rolling_buffer.processor import RollingBufferProcessor
from openflight.rolling_buffer.types import (
    ImpactEstimate,
    IQCapture,
    ProcessedCapture,
    SpeedReading,
    SpeedTimeline,
)


def capture_with_mode(mode):
    capture = IQCapture(
        sample_time=100,
        trigger_time=100.068,
        i_samples=[2048] * 4096,
        q_samples=[2048] * 4096,
        trigger_timestamp=1000,
    )
    capture.trigger_type = mode
    return capture


@pytest.mark.parametrize("mode", ["hardware", "speed", None])
def test_non_sound_capture_does_not_supply_a_sound_impact_fallback(mode):
    capture = capture_with_mode(mode)
    timeline = SpeedTimeline([SpeedReading(67, 100, 80)], 937.5, capture)
    impact = RollingBufferProcessor().estimate_impact(timeline, 67, capture=capture)
    assert impact.source == "unavailable"
    assert impact.timestamp_ms is None
    assert capture.trigger_offset_ms == pytest.approx(68)


def test_known_sound_capture_keeps_its_separate_sound_fallback():
    capture = capture_with_mode("sound")
    timeline = SpeedTimeline([SpeedReading(67, 100, 80)], 937.5, capture)
    impact = RollingBufferProcessor().estimate_impact(timeline, 67, capture=capture)
    assert impact.source == "sound_trigger"
    assert impact.timestamp_ms == pytest.approx(68)


@pytest.mark.parametrize("mode", ["hardware", "speed"])
def test_monitor_does_not_restore_an_internal_trigger_as_an_impact_epoch(mode):
    capture = capture_with_mode(mode)
    processed = ProcessedCapture(
        timeline=SpeedTimeline([], 937.5, capture),
        ball_speed_mph=67,
        ball_timestamp_ms=80,
        club_speed_mph=78,
        capture=capture,
        impact=ImpactEstimate(None, "unavailable"),
    )
    monitor = RollingBufferMonitor(port=None, trigger_type=mode)
    shot = monitor._create_shot(processed)
    assert shot is not None
    assert shot.impact_timestamp is None
    assert shot.impact_timestamp_kld7 is None
    assert capture.trigger_timestamp == 1000


@pytest.mark.parametrize("mode", ["hardware", "speed", "sound", None])
def test_trigger_mode_survives_session_logging_and_raw_replay(tmp_path, mode):
    from openflight.clubs import ClubType
    from openflight.raw_radar_replay import replay_ops_capture
    from openflight.session_logger import SessionLogger
    from tests.test_raw_radar_replay import synthetic_entry

    entry = synthetic_entry()
    logger = SessionLogger(log_dir=tmp_path, provenance_collector=lambda *_args: {})
    logger.start_session(trigger_type=mode)
    try:
        logger.log_rolling_buffer_capture(
            shot_number=entry["shot_number"],
            sample_time=entry["sample_time"],
            trigger_time=entry["trigger_time"],
            i_samples=entry["i_samples"],
            q_samples=entry["q_samples"],
            trigger_type=mode,
        )
    finally:
        logger.end_session()
    rows = [
        json.loads(line) for line in next(tmp_path.glob("session_*.jsonl")).read_text().splitlines()
    ]
    recorded = next(row for row in rows if row["type"] == "rolling_buffer_capture")
    assert recorded["trigger_type"] == mode
    replay = replay_ops_capture(recorded, sample_rate_hz=30000, club_type=ClubType.IRON_7)
    assert replay["result"]["capture"]["trigger_type"] == mode
    assert replay["result"]["impact"]["source"] == (
        "sound_trigger" if mode == "sound" else "unavailable"
    )


def test_stale_sound_impact_cannot_anchor_an_explicit_internal_capture():
    from openflight.ops_speed_track import ops_speed_track

    capture = capture_with_mode("hardware")
    processed = ProcessedCapture(
        timeline=SpeedTimeline([], 937.5, capture),
        ball_speed_mph=67,
        ball_timestamp_ms=80,
        capture=capture,
        impact=ImpactEstimate(68, "sound_trigger"),
    )
    assert RollingBufferMonitor._impact_epoch_from_processed(processed) is None
    assert ops_speed_track(processed) is None

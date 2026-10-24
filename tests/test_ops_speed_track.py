"""The OPS readings around impact kept on the shot, on the OPS's own clock."""

import pytest

from openflight.ops_speed_track import SOUND_TRIGGER_IMPACT_TOLERANCE_MS, ops_speed_track

WINDOW_MS = 128 / 30.0  # one 128-sample FFT window at 30 ksps


def _processed(impact_source="ops_transition", impact_ms=40.0, gap=2.0):
    from openflight.rolling_buffer.types import (
        ImpactEstimate,
        ProcessedCapture,
        SpeedReading,
        SpeedTimeline,
    )

    readings = [
        SpeedReading(speed_mph=80.0, magnitude=5.0, timestamp_ms=30.0),  # club, long before
        SpeedReading(speed_mph=82.0, magnitude=5.0, timestamp_ms=37.0),  # club, straddles
        SpeedReading(speed_mph=115.0, magnitude=9.0, timestamp_ms=41.0),
        SpeedReading(speed_mph=60.0, magnitude=3.0, timestamp_ms=41.0),
        SpeedReading(speed_mph=40.0, magnitude=3.0, timestamp_ms=45.0, direction="inbound"),
        SpeedReading(speed_mph=118.0, magnitude=9.0, timestamp_ms=80.0),  # past 30 ms
    ]
    return ProcessedCapture(
        timeline=SpeedTimeline(readings=readings, sample_rate_hz=937.5),
        ball_speed_mph=118.0,
        ball_timestamp_ms=80.0,
        impact=ImpactEstimate(timestamp_ms=impact_ms, source=impact_source, transition_gap_ms=gap),
    )


def test_ops_speed_track_keeps_outbound_readings_around_impact_on_the_ops_clock():
    track = ops_speed_track(_processed())

    assert track["clock"] == "ops_capture"
    assert track["impact_ms"] == 40.0
    assert track["impact_source"] == "ops_transition"
    # half the club-to-ball transition gap
    assert track["impact_uncertainty_ms"] == pytest.approx(1.0)
    assert track["window_ms"] == pytest.approx(WINDOW_MS)
    assert track["radial_resolution_mph"] == pytest.approx(0.0509, abs=1e-3)
    assert track["canonical_radial_mph"] == 118.0
    assert track["outbound"] == [[37.0, 82.0], [41.0, 115.0], [41.0, 60.0]]


def test_ops_speed_track_states_the_sound_trigger_tolerance():
    track = ops_speed_track(_processed(impact_source="sound_trigger"))
    assert track["impact_uncertainty_ms"] == SOUND_TRIGGER_IMPACT_TOLERANCE_MS


def test_ops_speed_track_is_none_without_an_impact():
    assert ops_speed_track(_processed(impact_ms=None)) is None


def test_the_monitor_attaches_the_ops_track_to_the_shot():
    from openflight.rolling_buffer import RollingBufferMonitor

    monitor = RollingBufferMonitor(port=None, trigger_type="sound")
    shot = monitor._create_shot(_processed())  # pylint: disable=protected-access

    assert shot.ops_speed_track == ops_speed_track(_processed())
    assert "ops_speed_track" not in shot.to_dict()

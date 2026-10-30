"""The speed handoff retains actual club-path observations and exact support."""

from dataclasses import asdict
from types import SimpleNamespace

import pytest

from openflight.ops_speed_track import ops_speed_track
from openflight.rolling_buffer.motion_tracks import MotionTrack, Peak
from tests.test_ops_speed_track import _processed


@pytest.mark.parametrize("sample_rate,window", [(30000.0, 256), (40000.0, 256), (30000.0, 128)])
def test_the_whole_club_path_survives_without_replacing_its_observations(sample_rate, window):
    processed = _processed()
    processed.processor_config = {"sample_rate_hz": sample_rate}
    processed.club_speed_mph, processed.club_timestamp_ms = 81.0, 29.0
    peaks = (
        Peak(3, 12.0, 70.1, 18.0),
        Peak(4, 13.1, 72.3, 16.0),
        Peak(7, 16.3, 75.2, 21.0),
        Peak(8, 17.4, 73.8, 13.0),
    )
    club = MotionTrack(74.0, peaks, 2)
    processed.motion = SimpleNamespace(club=club, window_samples=window)
    before = asdict(club)

    result = ops_speed_track(processed)

    assert result["club_readings_source"] == "coherent_spectral_path"
    assert len(result["club_readings"]) == len(peaks)
    for observed, saved in zip(peaks, result["club_readings"], strict=True):
        assert saved == dict(
            frame=observed.frame,
            window_start_ms=pytest.approx(observed.time_ms - window / sample_rate * 500),
            window_ms=pytest.approx(window / sample_rate * 1000),
            radial_mph=observed.speed_mph,
            snr=observed.snr,
        )
    assert result["club_reading"]["radial_mph"] == peaks[2].speed_mph
    assert result["club_reading"]["radial_mph"] != club.speed_mph
    assert asdict(club) == before


def test_no_path_does_not_fill_support_from_unassociated_timeline_peaks():
    processed = _processed()
    processed.club_speed_mph, processed.club_timestamp_ms = 80.0, 30.0
    result = ops_speed_track(processed)
    assert result["club_readings"] == []
    assert result["club_readings_source"] is None
    assert result["club_reading"] == {"window_start_ms": 30.0, "radial_mph": 80.0}

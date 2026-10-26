"""Known-answer checks for the offline observation audit, never a hardware grade."""

from datetime import datetime

import numpy as np
import pytest

from openflight.launch_monitor import Shot
from scripts.analysis.audit_fusion_observations import capture_diagnostics, current_speed_inputs


def test_current_speed_inputs_use_one_replay_and_recompute_radial_smash():
    shot = Shot(ball_speed_mph=90, club_speed_mph=30, timestamp=datetime(2026, 10, 25))
    current_speed_inputs(shot, {"canonical_radial_mph": 120, "club_reading": {"radial_mph": 80}})
    assert shot.ball_speed_mph == 120
    assert shot.club_speed_mph == 80
    assert shot.smash_factor == 1.5


def test_missing_current_club_reading_does_not_keep_the_historical_club_speed():
    shot = Shot(ball_speed_mph=90, club_speed_mph=30, timestamp=datetime(2026, 10, 25))
    current_speed_inputs(shot, {"canonical_radial_mph": 120, "club_reading": None})
    assert shot.club_speed_mph is None
    assert shot.smash_factor is None


def test_failed_raw_speed_replay_cannot_silently_keep_historical_inputs():
    shot = Shot(ball_speed_mph=90, timestamp=datetime(2026, 10, 25))
    with pytest.raises(ValueError, match="successfully replayed"):
        current_speed_inputs(shot, None)


def test_capture_audit_counts_exposures_on_the_sensor_clock_and_retains_delivery_age():
    archive = {
        "sensor_timestamp_ns": np.array([0, 10, 20, 30]) * 1_000_000,
        "host_timestamp_ns": np.array([20, 30, 40, 50]) * 1_000_000,
        "trigger_boottime_ns": np.array(15_000_000),
        "exposure_us": np.zeros(4),
    }
    metadata = {"trigger_clocks": {"boottime_minus_monotonic_ns": 0}}
    result = capture_diagnostics(archive, metadata)
    assert result["post_callback_exposures"] == 2
    assert result["fully_pre_contact_model"] == 1
    assert result["fully_post_contact_model"] == 2
    assert result["median_delivery_age_ms"] == 20
    assert result["lighting_analysis_eligible"] is None

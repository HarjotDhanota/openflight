"""Out-of-domain perturbations and unknown tolerances cannot give finite ranges."""

import json

import pytest

from openflight import full_speed
from tests.test_camera_head_track import Swing
from tests.test_full_speed import _evaluate, _track
from tests.test_full_speed_routes import CLUB_RIG, _club_track, _head_record


def _ball(*, uncertainty=0.6, start=42.0, window=None):
    track = _track(120, 16, 2, starts=[start], impact_uncertainty_ms=uncertainty)
    if window is not None:
        track["window_ms"] = window
    return _evaluate(track)


def _club(*, uncertainty=0.6, before=8.0, straight=False, sigma=None, window=None):
    swing = Swing()
    head = _head_record(swing)
    if straight:
        # A declared straight mathematical model for the time-domain test only.
        head["turn"]["omega_rad_s"] = 0.0
    if sigma is not None:
        head[sigma[0]] = sigma[1]
    track = _club_track(swing, before, impact_uncertainty_ms=uncertainty)
    if window is not None:
        track["club_reading"]["window_ms"] = window
    return full_speed.evaluate_full_club_speed_head_track(
        ops_track=track, head_track=head, rig=CLUB_RIG
    )


@pytest.mark.parametrize("uncertainty", [1.0, 3.0])
def test_ball_time_perturbation_cannot_cross_launch_or_its_fft_support(uncertainty):
    result = _ball(uncertainty=uncertainty, start=40.5)
    assert result["status"] == "calculated"
    assert result["range_mph"] is None
    assert result["uncertainty_mph"]["impact_time"] is None


def test_ball_time_perturbation_cannot_exceed_the_declared_referral_window():
    result = _ball(uncertainty=1.0, start=69.5)
    assert result["status"] == "calculated"
    assert result["range_mph"] is None
    assert result["uncertainty_mph"]["impact_time"] is None


def test_original_half_ms_offset_does_not_bound_a_six_tenths_ms_tolerance():
    result = _evaluate(_track(120, 16, 2))
    assert result["status"] == "calculated"
    assert result["range_mph"] is None
    assert result["uncertainty_mph"]["impact_time"] is None


@pytest.mark.parametrize("uncertainty", [7.0, 10.0])
def test_club_time_perturbation_cannot_cross_contact_or_its_fft_support(uncertainty):
    result = _club(uncertainty=uncertainty)
    assert result["status"] == "experimental", result["reason"]
    assert result["range_mph"] is None
    assert result["uncertainty_mph"]["reading_time"] is None


@pytest.mark.parametrize("uncertainty", [20.5, 25.0])
def test_club_time_perturbation_cannot_exceed_the_declared_arc_support(uncertainty):
    result = _club(uncertainty=uncertainty, straight=True)
    assert result["status"] == "experimental", result["reason"]
    assert result["range_mph"] is None
    assert result["uncertainty_mph"]["reading_time"] is None


@pytest.mark.parametrize("uncertainty", [None, float("nan"), -1.0, "bad", False])
@pytest.mark.parametrize("kind", ["ball", "club"])
def test_invalid_timing_tolerance_cannot_become_zero_or_a_finite_range(kind, uncertainty):
    result = _ball(uncertainty=uncertainty) if kind == "ball" else _club(uncertainty=uncertainty)
    assert result["value_mph"] > 0
    assert result["range_mph"] is None
    key = "impact_time_ms" if kind == "ball" else "reading_time_ms"
    assert result["tolerances"][key] is None
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("name", ["club_path_sigma_deg", "attack_angle_sigma_deg"])
@pytest.mark.parametrize("sigma", [None, -1.0])
def test_unknown_or_invalid_head_sigma_does_not_become_an_exact_direction(name, sigma):
    result = _club(sigma=(name, sigma))
    assert result["status"] == "experimental", result["reason"]
    assert result["range_mph"] is None
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("window", [0.0, -1.0, float("nan")])
@pytest.mark.parametrize("kind", ["ball", "club"])
def test_invalid_explicit_fft_duration_cannot_be_used_or_silently_defaulted(kind, window):
    result = _ball(window=window) if kind == "ball" else _club(window=window)
    assert result["status"] == "not_measured"
    assert "window" in result["reason"]


@pytest.mark.parametrize("kind", ["ball", "club"])
def test_supported_time_uncertainty_stays_bounded_including_explicit_zero(kind):
    for tolerance in (0.0, 0.4):
        result = _ball(uncertainty=tolerance) if kind == "ball" else _club(uncertainty=tolerance)
        assert result["value_mph"] > 0
        assert result["range_mph"] is not None
        json.dumps(result, allow_nan=False)

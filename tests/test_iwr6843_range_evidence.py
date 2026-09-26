"""Truth-free IWR range evidence stays diagnostic until hardware validation."""

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from openflight.iwr6843.dump import SAMPLE_RANGE_FFT_IQ16, pack_dump
from openflight.iwr6843.music import LAM
from openflight.iwr6843.range_evidence import (
    IndependentImpactTime,
    StaticRangeProfile,
    StaticRangeProfileV2,
    build_moving_track_candidate,
    build_static_profile_candidate,
    compare_static_range_profiles,
    extract_moving_ball_range_track,
    static_range_profile,
)
from openflight.iwr6843.tracking import RANGE_SPAN_M

PROFILE_SHA = "a" * 64
RIG_SHA = "b" * 64
CALIBRATION_SHA = "c" * 64
STATIC_REGRESSION_ROOT = Path(__file__).parent / "fixtures" / "iwr6843_static_range"


def _moving_ball_dump(*, start_range_m=1.25, speed_ms=42.0):
    n_frames, n_loops, n_tx, n_rx, n_samples = 12, 16, 2, 4, 128
    frame_period_s = 0.006
    loop_period_s = 90e-6
    trigger_frame = 3
    resolution_m = RANGE_SPAN_M / n_samples
    samples = np.arange(n_samples)
    cube = np.zeros((n_frames, n_loops * n_tx, n_rx, n_samples), dtype=complex)
    for slot in range(n_frames):
        frame_time = ((slot - trigger_frame) % n_frames) * frame_period_s
        for loop in range(n_loops):
            time_s = frame_time + loop * loop_period_s
            range_bin = (start_range_m + speed_ms * time_s) / resolution_m
            tone = np.exp(2j * np.pi * range_bin * samples / n_samples)
            doppler = np.exp(4j * np.pi * speed_ms * time_s / LAM)
            for tx in range(n_tx):
                for rx in range(n_rx):
                    cube[slot, loop * n_tx + tx, rx] = 500.0 * doppler * tone
    return pack_dump(
        cube,
        n_tx=n_tx,
        trigger_frame=trigger_frame,
        version=3,
        frame_period_us=round(frame_period_s * 1e6),
    )


def _static_dump(targets):
    n_frames, chirps, n_rx, n_samples = 4, 8, 4, 128
    samples = np.arange(n_samples)
    cube = np.zeros((n_frames, chirps, n_rx, n_samples), dtype=complex)
    for range_bin, amplitude in targets:
        tone = amplitude * np.exp(2j * np.pi * range_bin * samples / n_samples)
        cube += tone[None, None, None, :]
    return pack_dump(cube, n_tx=2, version=3, frame_period_us=6000)


def _impact(
    time_s=0.004,
    *,
    uncertainty_s=0.0005,
    qualified=True,
    independent_of_iwr_range=True,
):
    return IndependentImpactTime(
        time_s=time_s,
        uncertainty_s=uncertainty_s,
        source="ops_hardware_edge",
        qualified=qualified,
        independent_of_iwr_range=independent_of_iwr_range,
        provenance={"clock": "host_monotonic", "event_id": "ops-17"},
    )


def _profile(
    power,
    *,
    capture="d",
    profile=PROFILE_SHA,
    profile_qualified=True,
    rig=RIG_SHA,
    config="e",
):
    return StaticRangeProfile(
        capture_sha256=capture * 64,
        radar_profile_sha256=profile,
        radar_profile_qualified=profile_qualified,
        rig_geometry_sha256=rig,
        capture_config_sha256=config * 64,
        range_bin_start=0,
        range_bin_count=len(power),
        range_resolution_m=0.04,
        power=tuple(float(value) for value in power),
    )


def _regression_fixture(epoch_id):
    return json.loads((STATIC_REGRESSION_ROOT / f"{epoch_id}.json").read_text(encoding="utf-8"))


def _recorded_profile(fixture, capture):
    return StaticRangeProfile(**fixture[capture]["profile"])


def _v2_profile(fixture, capture):
    recorded = fixture[capture]["profile"]
    frames = np.asarray(fixture[capture]["frame_power"], dtype=float)
    center = np.median(frames, axis=0)
    spread = np.median(np.abs(frames - center), axis=0) / np.maximum(center, 1e-12)
    return StaticRangeProfileV2(
        **{key: recorded[key] for key in recorded if key != "power"},
        power=tuple(center),
        frame_mad_fraction=tuple(spread),
        frame_count=len(frames),
    )


def test_moving_track_extraction_does_not_require_a_configured_tee(monkeypatch):
    monkeypatch.setattr(
        "openflight.iwr6843.shot.impact_time_s",
        lambda *_args, **_kwargs: pytest.fail("tee-intersection timing was used"),
    )

    result = extract_moving_ball_range_track(_moving_ball_dump(), club="7-iron")

    assert result.status == "selected"
    assert result.track is not None
    assert result.track.speed_ms == pytest.approx(42.0, abs=1.0)
    assert result.scope in {"burst", "window"}
    assert result.diagnostics["configured_tee_required"] is False


def test_moving_candidate_uses_only_the_supplied_independent_impact_time():
    result = extract_moving_ball_range_track(_moving_ball_dump(), club="7-iron")

    candidate = build_moving_track_candidate(
        result,
        _impact(0.004),
        range_bias_m=0.03,
        range_bias_uncertainty_m=0.01,
        calibration_sha256=CALIBRATION_SHA,
    )

    expected = result.track.range_at(0.004, result.geometry.range_res_m) - 0.03
    assert candidate.radar_slant_range_m == pytest.approx(expected)
    assert candidate.source_group == "iwr"
    assert candidate.selectable is False
    assert candidate.evidence["selection_policy"] == "diagnostic_only_unvalidated"
    assert candidate.evidence["impact_time"]["derivation"] == "independent_external"
    assert candidate.evidence["configured_tee_used"] is False
    assert candidate.evidence["track"]["intercept_bins"] == pytest.approx(
        result.track.intercept_bins
    )


@pytest.mark.parametrize(
    ("impact", "message"),
    [
        (None, "independent impact time is required"),
        (_impact(qualified=False), "impact time is not qualified"),
        (_impact(independent_of_iwr_range=False), "impact time depends on IWR range"),
    ],
)
def test_moving_candidate_refuses_missing_or_unqualified_timing(impact, message):
    result = extract_moving_ball_range_track(_moving_ball_dump(), club="7-iron")

    with pytest.raises(ValueError, match=message):
        build_moving_track_candidate(
            result,
            impact,
            range_bias_m=0.0,
            range_bias_uncertainty_m=0.01,
            calibration_sha256=CALIBRATION_SHA,
        )


def test_timing_uncertainty_is_propagated_into_moving_range_uncertainty():
    result = extract_moving_ball_range_track(_moving_ball_dump(), club="7-iron")
    narrow = build_moving_track_candidate(
        result,
        _impact(uncertainty_s=0.0001),
        range_bias_m=0.0,
        range_bias_uncertainty_m=0.005,
        calibration_sha256=CALIBRATION_SHA,
    )
    wide = build_moving_track_candidate(
        result,
        _impact(uncertainty_s=0.003),
        range_bias_m=0.0,
        range_bias_uncertainty_m=0.005,
        calibration_sha256=CALIBRATION_SHA,
    )

    assert wide.uncertainty_m > narrow.uncertainty_m
    assert wide.evidence["uncertainty_m"]["timing"] > narrow.evidence["uncertainty_m"]["timing"]


def test_pre_mti_static_difference_finds_a_localized_added_reflector():
    empty = np.ones(96)
    present = empty.copy()
    present[37:39] += (35.0, 20.0)

    result = compare_static_range_profiles(
        _profile(empty, capture="d"),
        _profile(present, capture="f"),
        plausible_apparent_range_m=(0.5, 3.5),
    )

    assert result.status == "accepted"
    assert result.apparent_range_m == pytest.approx(37.0 * 0.04, abs=0.04)
    assert result.range_bin_uncertainty_m >= 0.02
    candidate = build_static_profile_candidate(
        result,
        range_bias_m=0.03,
        range_bias_uncertainty_m=0.01,
        calibration_sha256=CALIBRATION_SHA,
    )
    assert candidate.source_group == "iwr"
    assert candidate.selectable is False
    assert candidate.radar_slant_range_m == pytest.approx(result.apparent_range_m - 0.03)
    assert candidate.evidence["method"] == "pre_mti_empty_vs_ball_present"


@pytest.mark.parametrize(
    ("epoch_id", "status", "peak_bin"),
    [
        ("setup-20260925-fff56186f155", "rejected_no_ball", 23.0),
        ("setup-20260926-153ffb8aa4be", "accepted", 12.0),
        ("setup-20260926-b9f4dd8b3a75", "accepted", 36.0),
    ],
)
def test_pi_static_range_fixtures_reproduce_the_recorded_v1_results(epoch_id, status, peak_bin):
    fixture = _regression_fixture(epoch_id)
    empty = _recorded_profile(fixture, "empty")
    present = _recorded_profile(fixture, "present")

    assert fixture["schema"] == "openflight.iwr6843.static_range_regression.v1"
    assert fixture["empty"]["raw_sha256"] == empty.capture_sha256
    assert fixture["present"]["raw_sha256"] == present.capture_sha256
    assert np.mean(fixture["empty"]["frame_power"], axis=0) == pytest.approx(empty.power)
    assert np.mean(fixture["present"]["frame_power"], axis=0) == pytest.approx(present.power)

    result = compare_static_range_profiles(empty, present)

    assert result.status == status
    assert result.peak_bin == pytest.approx(peak_bin)
    assert result.peak_score == pytest.approx(fixture["recorded_v1_difference"]["peak_score"])


@pytest.mark.parametrize(
    ("epoch_id", "forbidden_peak_bin"),
    [
        ("setup-20260926-153ffb8aa4be", 12.0),
        ("setup-20260926-b9f4dd8b3a75", 36.0),
    ],
)
def test_pi_static_range_regressions_do_not_confidently_accept_observed_false_peaks(
    epoch_id, forbidden_peak_bin
):
    fixture = _regression_fixture(epoch_id)

    result = compare_static_range_profiles(
        _v2_profile(fixture, "empty"),
        _v2_profile(fixture, "present"),
    )

    assert not (
        result.status == "accepted" and result.peak_bin == pytest.approx(forbidden_peak_bin)
    )


@pytest.mark.parametrize(
    ("empty", "present", "status"),
    [
        (np.ones(96), np.ones(96), "rejected_no_ball"),
        (
            np.ones(96),
            np.ones(96) + np.where(np.isin(np.arange(96), (30, 50)), 30.0, 0.0),
            "rejected_ambiguous",
        ),
        (
            np.ones(96),
            np.ones(96) + np.where((np.arange(96) >= 25) & (np.arange(96) < 45), 20.0, 0.0),
            "rejected_clutter",
        ),
    ],
)
def test_static_difference_rejects_no_ball_ambiguity_and_clutter(empty, present, status):
    result = compare_static_range_profiles(
        _profile(empty, capture="d"),
        _profile(present, capture="f"),
        plausible_apparent_range_m=(0.5, 3.5),
    )

    assert result.status == status
    assert result.apparent_range_m is not None
    assert result.peak_bin is not None


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"radar_profile_sha256": "1" * 64}, "radar profile"),
        ({"rig_geometry_sha256": "2" * 64}, "rig geometry"),
        ({"capture_config_sha256": "3" * 64}, "capture configuration"),
        ({"range_resolution_m": 0.05}, "range grid"),
    ],
)
def test_static_difference_refuses_mismatched_evidence(change, message):
    empty = _profile(np.ones(96), capture="d")
    present = replace(_profile(np.ones(96), capture="f"), **change)

    with pytest.raises(ValueError, match=message):
        compare_static_range_profiles(empty, present)


def test_static_profile_factory_records_raw_capture_and_configuration_hashes():
    cube = np.ones((3, 4, 4, 128), dtype=complex)
    raw = pack_dump(cube, n_tx=2, version=3, frame_period_us=6000)

    profile = static_range_profile(
        raw,
        radar_profile_sha256=PROFILE_SHA,
        rig_geometry_sha256=RIG_SHA,
    )

    assert len(profile.capture_sha256) == 64
    assert len(profile.capture_config_sha256) == 64
    assert profile.radar_profile_qualified is False
    assert profile.range_bin_count == 128
    assert profile.range_resolution_m == pytest.approx(RANGE_SPAN_M / 128)
    assert len(profile.power) == 128


def test_static_profile_preserves_absolute_bins_for_a_stored_range_window():
    n_frames, chirps, n_rx, count = 3, 4, 4, 16
    empty_cube = np.ones((n_frames, chirps, n_rx, count), dtype=complex)
    present_cube = empty_cube.copy()
    present_cube[..., 3] = 20.0
    empty_raw = pack_dump(
        empty_cube,
        n_tx=2,
        version=3,
        sample_fmt=SAMPLE_RANGE_FFT_IQ16,
        range_bin_start=29,
    )
    present_raw = pack_dump(
        present_cube,
        n_tx=2,
        version=3,
        sample_fmt=SAMPLE_RANGE_FFT_IQ16,
        range_bin_start=29,
    )
    empty = static_range_profile(
        empty_raw,
        radar_profile_sha256=PROFILE_SHA,
        rig_geometry_sha256=RIG_SHA,
    )
    present = static_range_profile(
        present_raw,
        radar_profile_sha256=PROFILE_SHA,
        rig_geometry_sha256=RIG_SHA,
    )
    result = compare_static_range_profiles(
        empty,
        present,
        plausible_apparent_range_m=(1.0, 2.2),
    )

    assert present.range_bin_start == 29
    assert present.range_bin_count == count
    assert np.argmax(present.power) == 3
    assert result.status == "accepted"
    assert result.peak_bin == pytest.approx(32.0)
    assert result.apparent_range_m == pytest.approx(32 * RANGE_SPAN_M / 128)


def test_raw_pre_mti_capture_pair_produces_only_diagnostic_iwr_evidence():
    empty = static_range_profile(
        _static_dump(((12, 300.0), (72, 500.0))),
        radar_profile_sha256=PROFILE_SHA,
        radar_profile_qualified=True,
        rig_geometry_sha256=RIG_SHA,
    )
    present = static_range_profile(
        _static_dump(((12, 300.0), (37, 180.0), (72, 500.0))),
        radar_profile_sha256=PROFILE_SHA,
        radar_profile_qualified=True,
        rig_geometry_sha256=RIG_SHA,
    )

    result = compare_static_range_profiles(
        empty,
        present,
        plausible_apparent_range_m=(0.5, 3.5),
    )
    candidate = build_static_profile_candidate(
        result,
        range_bias_m=0.0,
        range_bias_uncertainty_m=0.01,
        calibration_sha256=CALIBRATION_SHA,
    )

    assert result.status == "accepted"
    assert result.apparent_range_m == pytest.approx(37 * RANGE_SPAN_M / 128, abs=0.02)
    assert candidate.source == "iwr_static_profile_difference"
    assert candidate.selectable is False
    assert candidate.evidence["selection_policy"] == "diagnostic_only_unvalidated"
    assert candidate.evidence["empty_capture_sha256"] == empty.capture_sha256
    assert candidate.evidence["present_capture_sha256"] == present.capture_sha256


def test_static_candidate_refuses_an_unqualified_radar_profile():
    empty = np.ones(96)
    present = empty.copy()
    present[37] += 35.0
    result = compare_static_range_profiles(
        _profile(empty, capture="d", profile_qualified=False),
        _profile(present, capture="f", profile_qualified=False),
    )

    with pytest.raises(ValueError, match="not independently qualified"):
        build_static_profile_candidate(
            result,
            range_bias_m=0.0,
            range_bias_uncertainty_m=0.01,
            calibration_sha256=CALIBRATION_SHA,
        )

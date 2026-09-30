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
    static_range_estimator_policy,
    static_range_estimator_sha256,
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


def test_pi_static_range_regressions_do_not_confidently_accept_observed_false_peaks():
    near_field = _regression_fixture("setup-20260926-153ffb8aa4be")
    door = _regression_fixture("setup-20260926-b9f4dd8b3a75")

    shifted = compare_static_range_profiles(
        _v2_profile(near_field, "empty"), _v2_profile(near_field, "present")
    )
    behind = compare_static_range_profiles(_v2_profile(door, "empty"), _v2_profile(door, "present"))

    assert shifted.status != "accepted"
    assert not (behind.status == "accepted" and abs(behind.peak_bin - 36.0) < 2.0)


@pytest.mark.parametrize(
    ("epoch_id", "status", "peak_bin", "width"),
    [
        ("setup-20260925-fff56186f155", "accepted", 23.5007, 2),
        ("setup-20260926-153ffb8aa4be", "rejected_ambiguous", 29.0, 1),
        ("setup-20260926-b9f4dd8b3a75", "accepted", 23.9376, 3),
    ],
)
def test_provisional_v2_selector_outcomes_on_the_captured_epochs_are_pinned(
    epoch_id, status, peak_bin, width
):
    """Characterization only: no epoch carries tape truth, so none is an accuracy result."""
    fixture = _regression_fixture(epoch_id)

    result = compare_static_range_profiles(
        _v2_profile(fixture, "empty"), _v2_profile(fixture, "present")
    )

    assert result.status == status
    assert result.peak_bin == pytest.approx(peak_bin, abs=1e-3)
    assert result.peak_width_bins == width
    assert result.estimator_sha256 == static_range_estimator_sha256()


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


SYNTHETIC_WINDOW_M = (0.5, 2.8)


def _synthetic_v2(power, *, capture, frame_mad=None, frame_count=24):
    power = np.asarray(power, dtype=float)
    return StaticRangeProfileV2(
        capture_sha256=capture * 64,
        radar_profile_sha256=PROFILE_SHA,
        radar_profile_qualified=True,
        rig_geometry_sha256=RIG_SHA,
        capture_config_sha256="e" * 64,
        range_bin_start=0,
        range_bin_count=len(power),
        range_resolution_m=0.05,
        power=tuple(power),
        frame_mad_fraction=tuple(np.full(len(power), 0.01) if frame_mad is None else frame_mad),
        frame_count=frame_count,
    )


def _static_scene(*, scale=1.0, seed=7):
    rng = np.random.default_rng(seed)
    empty = 1e6 * np.exp(rng.normal(0.0, 0.6, 60))
    present = empty * scale * (1.0 + rng.normal(0.0, 0.02, 60))
    return empty, present


def _added(present, empty, scale, bins, fraction=1.3):
    changed = present.copy()
    for index in bins:
        changed[index] = empty[index] * scale * (1.0 + fraction)
    return changed


def _compare(empty, present, *, window=SYNTHETIC_WINDOW_M, candidate_window_m=None, **options):
    return compare_static_range_profiles(
        _synthetic_v2(empty, capture="d"),
        _synthetic_v2(present, capture="f", **options),
        plausible_apparent_range_m=window,
        **({"candidate_window_m": candidate_window_m} if candidate_window_m else {}),
    )


def test_v2_accepts_one_added_reflector_despite_global_gain_drift():
    empty, present = _static_scene(scale=1.58)

    result = _compare(empty, _added(present, empty, 1.58, [25]))

    assert result.status == "accepted"
    assert result.peak_bin == pytest.approx(25.0)
    assert result.normalization_scale == pytest.approx(1.58, rel=0.02)


@pytest.mark.parametrize("scale", [0.66, 1.0, 1.58])
def test_v2_rejects_global_gain_drift_without_an_added_reflector(scale):
    empty, present = _static_scene(scale=scale)

    assert _compare(empty, present).status == "rejected_no_ball"


def test_v2_ignores_a_much_stronger_static_reflector_with_a_small_fractional_change():
    empty, present = _static_scene()
    empty[45] *= 250.0
    present[45] = empty[45] * 1.01

    result = _compare(empty, _added(present, empty, 1.0, [25]))

    assert result.status == "accepted"
    assert result.peak_bin == pytest.approx(25.0)
    door_only = _compare(empty, present)
    assert door_only.status == "rejected_no_ball"


def test_v2_rejects_a_weak_bin_whose_large_percentage_change_is_below_absolute_evidence():
    empty, present = _static_scene()
    empty[30] = 1e3
    present[30] = 5e3

    assert _compare(empty, present).status == "rejected_no_ball"


@pytest.mark.parametrize("edge_bin", [10, 55])
def test_v2_rejects_a_change_at_either_search_boundary(edge_bin):
    empty, present = _static_scene()

    result = _compare(empty, _added(present, empty, 1.0, [edge_bin]))

    assert result.status == "rejected_boundary"


def test_v2_rejects_two_comparable_added_reflectors():
    empty, present = _static_scene()

    result = _compare(empty, _added(present, empty, 1.0, [20, 40]))

    assert result.status == "rejected_ambiguous"
    assert len(result.alternate_peaks) == 2


def test_v2_rejects_a_broad_multipath_change():
    empty, present = _static_scene()

    result = _compare(empty, _added(present, empty, 1.0, range(25, 30)))

    assert result.status == "rejected_clutter"
    assert result.peak_width_bins == 5


def test_v2_treats_adjacent_changed_bins_as_one_reflector_with_honest_width():
    empty, present = _static_scene()
    present = _added(present, empty, 1.0, [24, 26])
    present[25] = empty[25] * 1.6

    result = _compare(empty, present)

    assert result.status == "accepted"
    assert result.peak_width_bins == 3
    assert result.range_bin_uncertainty_m == pytest.approx(1.5 * 0.05)


def test_v2_rejects_a_reflector_that_moved_during_the_capture():
    empty, present = _static_scene()
    unstable = np.full(60, 0.01)
    unstable[25] = 0.2

    result = _compare(empty, _added(present, empty, 1.0, [25]), frame_mad=unstable)

    assert result.status == "rejected_unstable"


def test_v2_rejects_captures_with_too_few_frames():
    empty, present = _static_scene()

    result = _compare(empty, _added(present, empty, 1.0, [25]), frame_count=6)

    assert result.status == "rejected_insufficient_frames"
    assert result.peak_bin is None


def test_v2_rejects_a_reflector_that_disappeared_next_to_the_ball():
    empty, present = _static_scene()
    empty[27] *= 10.0
    present[27] = empty[27] * 0.3
    present = _added(present, empty, 1.0, [25])

    result = _compare(empty, present)

    assert result.status == "rejected_scene_changed"
    assert result.peak_bin == pytest.approx(27.0)


def test_v2_ignores_a_reflector_that_disappeared_too_far_away_to_move_the_ball():
    # a door or net moving 15 bins (0.7 m) behind the ball: its range-FFT leakage
    # into the ball's bins is a fraction of a percent of the ball's own change
    empty, present = _static_scene()
    empty[40] *= 10.0
    present[40] = empty[40] * 0.3
    present = _added(present, empty, 1.0, [25])

    result = _compare(empty, present)

    assert result.status == "accepted"
    assert result.peak_bin == pytest.approx(25.0)
    assert [loss["bin"] for loss in result.ignored_losses] == [40.0]
    assert result.ignored_losses[0]["leak_fraction_of_ball"] < 0.01


def test_v2_still_rejects_a_distant_loss_strong_enough_to_leak_into_the_ball():
    empty, present = _static_scene()
    empty[35] *= 3000.0
    present[35] = empty[35] * 0.3
    present = _added(present, empty, 1.0, [25])

    result = _compare(empty, present)

    assert result.status == "rejected_scene_changed"
    assert result.peak_bin == pytest.approx(35.0)


def _field_profiles(epoch_fixture):
    fixture = _regression_fixture(epoch_fixture)

    def profile(capture):
        recorded = dict(fixture[capture]["profile_v2"])
        recorded["power"] = tuple(recorded["power"])
        recorded["frame_mad_fraction"] = tuple(recorded["frame_mad_fraction"])
        return StaticRangeProfileV2(**recorded)

    return fixture, profile("empty"), profile("present")


def test_a_ball_beside_a_strong_edge_is_rejected_not_read_short():
    """Outdoors, 29 Sept: bins 3 either side of the ball lost half their echo when it
    was placed (interference with a raised mat edge). The leakage-only rule accepted
    1.903 m against a 2.02 m tape; a loss that close to the ball must reject."""
    fixture, empty, present = _field_profiles("field-20260929-outdoor-mat-edge-2m")
    bias = fixture["range_bias_const_m"]

    result = compare_static_range_profiles(
        empty, present, plausible_apparent_range_m=(0.5 + bias, 4.0 + bias)
    )

    assert result.status == "rejected_scene_changed"


def test_the_29_sept_door_setup_is_accepted_at_its_tape_range():
    """Pi field capture: a ball 1.00 m out (tape) in front of a closed door whose
    reflector 0.7 m behind the ball lost half its power between captures."""
    fixture = _regression_fixture("field-20260929-door-1m")

    def profile(capture):
        recorded = dict(fixture[capture]["profile_v2"])
        recorded["power"] = tuple(recorded["power"])
        recorded["frame_mad_fraction"] = tuple(recorded["frame_mad_fraction"])
        return StaticRangeProfileV2(**recorded)

    bias = fixture["range_bias_const_m"]
    result = compare_static_range_profiles(
        profile("empty"), profile("present"), plausible_apparent_range_m=(0.5 + bias, 4.0 + bias)
    )

    assert result.status == "accepted"
    assert result.apparent_range_m - bias == pytest.approx(
        fixture["tape_ball_center_to_rx_m"], abs=0.05
    )
    assert any(abs(loss["range_m"] - 1.734) < 0.03 for loss in result.ignored_losses)


def test_v2_searches_only_the_supplied_placement_envelope():
    empty, present = _static_scene()
    present = _added(present, empty, 1.0, [25])

    inside = _compare(empty, present)
    outside = _compare(empty, present, window=(1.5, 2.8))

    assert inside.status == "accepted"
    assert outside.status == "rejected_no_ball"


QUALIFICATION_WINDOW_M = (0.5, 4.0)
# a camera range of 1.0 m, +-2 sigma at the 20 % floor (tester_server.camera_radar_window)
CAMERA_WINDOW_1M = (0.6, 1.4)


def _qualification_scene(seed=7):
    """90 bins at 5 cm, so the 0.5-4.0 m qualification window holds 71 of them."""
    rng = np.random.default_rng(seed)
    empty = 1e6 * np.exp(rng.normal(0.0, 0.6, 90))
    present = empty * (1.0 + rng.normal(0.0, 0.02, 90))
    return empty, present


def test_a_camera_window_picks_the_cluster_but_statistics_use_the_full_window():
    """Wiring audit S1: a 1.0 m ball's three changed bins are 3 of 16 inside a camera
    window of 0.6-1.4 m, over the 12 % clutter limit, but 3 of 71 in the
    qualification window the limit was set for."""
    empty, present = _qualification_scene()
    present = _added(present, empty, 1.0, [19, 20, 21])

    full = _compare(empty, present, window=QUALIFICATION_WINDOW_M)
    narrowed = _compare(empty, present, window=CAMERA_WINDOW_1M)
    windowed = _compare(
        empty, present, window=QUALIFICATION_WINDOW_M, candidate_window_m=CAMERA_WINDOW_1M
    )

    # narrowing the search itself is what rejected the real ball
    assert narrowed.status == "rejected_clutter"
    assert windowed.status == "accepted"
    assert windowed.apparent_range_m == pytest.approx(1.0, abs=0.03)
    assert windowed.peak_width_bins == 3
    assert windowed.changed_fraction == pytest.approx(full.changed_fraction)
    assert windowed.normalization_scale == pytest.approx(full.normalization_scale)
    assert windowed.candidate_window_m == CAMERA_WINDOW_1M
    assert full.candidate_window_m is None


def test_a_camera_window_excludes_a_person_behind_the_ball():
    empty, present = _qualification_scene()
    present = _added(present, empty, 1.0, [19, 20, 21])
    # a still person 2.8 m out, changing far more than the ball
    present = _added(present, empty, 1.0, [56], fraction=12.0)

    full = _compare(empty, present, window=QUALIFICATION_WINDOW_M)
    windowed = _compare(
        empty, present, window=QUALIFICATION_WINDOW_M, candidate_window_m=CAMERA_WINDOW_1M
    )

    assert full.status == "accepted"
    assert full.apparent_range_m == pytest.approx(2.8, abs=0.03)
    assert windowed.status == "accepted"
    assert windowed.apparent_range_m == pytest.approx(1.0, abs=0.03)
    assert all(abs(peak["apparent_range_m"] - 2.8) > 0.1 for peak in windowed.alternate_peaks)


def test_a_camera_window_with_no_change_inside_it_finds_no_ball():
    empty, present = _qualification_scene()
    present = _added(present, empty, 1.0, [56], fraction=12.0)

    windowed = _compare(
        empty, present, window=QUALIFICATION_WINDOW_M, candidate_window_m=CAMERA_WINDOW_1M
    )

    assert windowed.status == "rejected_no_ball"
    assert CAMERA_WINDOW_1M[0] <= windowed.apparent_range_m <= CAMERA_WINDOW_1M[1]


def test_a_legacy_profile_honours_the_camera_window_too():
    power = np.ones(100)
    empty = _profile(power, capture="d")
    present = power.copy()
    present[25] += 30.0  # the ball, 1.0 m
    present[70] += 300.0  # a person, 2.8 m
    present = _profile(present, capture="f")

    full = compare_static_range_profiles(
        empty, present, plausible_apparent_range_m=QUALIFICATION_WINDOW_M
    )
    windowed = compare_static_range_profiles(
        empty,
        present,
        plausible_apparent_range_m=QUALIFICATION_WINDOW_M,
        candidate_window_m=CAMERA_WINDOW_1M,
    )

    assert full.apparent_range_m == pytest.approx(2.8)
    assert windowed.status == "accepted"
    assert windowed.apparent_range_m == pytest.approx(1.0)
    assert windowed.changed_fraction == pytest.approx(full.changed_fraction)


@pytest.mark.parametrize(
    ("epoch_id", "status"),
    [
        ("field-20260929-door-1m", "accepted"),
        ("field-20260929-outdoor-mat-edge-2m", "rejected_scene_changed"),
    ],
)
def test_the_29_sept_field_setups_keep_their_outcomes_inside_a_camera_window(epoch_id, status):
    fixture, empty, present = _field_profiles(epoch_id)
    bias = fixture["range_bias_const_m"]
    tape = fixture["tape_ball_center_to_rx_m"]

    full = compare_static_range_profiles(
        empty, present, plausible_apparent_range_m=(0.5 + bias, 4.0 + bias)
    )
    windowed = compare_static_range_profiles(
        empty,
        present,
        plausible_apparent_range_m=(0.5 + bias, 4.0 + bias),
        candidate_window_m=(0.6 * tape + bias, 1.4 * tape + bias),
    )

    assert full.status == windowed.status == status
    assert windowed.changed_fraction == pytest.approx(full.changed_fraction)
    if status == "accepted":
        assert windowed.apparent_range_m == pytest.approx(full.apparent_range_m)


def test_v2_refuses_to_compare_a_legacy_profile_with_a_v2_profile():
    empty, present = _static_scene()

    with pytest.raises(ValueError, match="schemas do not match"):
        compare_static_range_profiles(
            _profile(empty, capture="d"),
            _synthetic_v2(present, capture="f"),
            plausible_apparent_range_m=SYNTHETIC_WINDOW_M,
        )


def test_v2_candidate_carries_alternates_only_as_diagnostics():
    empty, present = _static_scene()
    present = _added(present, empty, 1.0, [25])
    present = _added(present, empty, 1.0, [40], fraction=0.6)

    result = _compare(empty, present)
    candidate = build_static_profile_candidate(
        result, range_bias_m=0.05, range_bias_uncertainty_m=0.01, calibration_sha256="c" * 64
    )

    assert result.status == "accepted"
    assert [peak["peak_bin"] for peak in result.alternate_peaks] == [25.0, 40.0]
    assert candidate.radar_slant_range_m == pytest.approx(result.apparent_range_m - 0.05)
    assert all(
        "radar_slant_range_m" not in peak and "candidate_id" not in peak
        for peak in candidate.evidence["alternate_peaks"]
    )


def test_static_range_estimator_identity_is_pinned():
    """Any selector constant change must be a deliberate, reviewed identity change."""
    policy = static_range_estimator_policy()

    assert policy["profile_schema"] == "openflight.iwr6843.static_range_profile.v2"
    assert static_range_estimator_sha256() == (
        "c7051ff0142e38817655799841c10eb0fa2f450df644f7454e858c30e2a9d30d"
    )


def test_v2_ranks_each_change_by_its_gate_passing_bins_only():
    """A filled null next to a marginal change must not outrank the real reflector."""
    rng = np.random.default_rng(1)
    empty = 1000.0 + rng.normal(0.0, 15.0, 80)
    empty[50] = 40.0
    present = empty.copy()
    present[30] = empty[30] * 1.6
    present[49] = empty[49] * 1.55
    present[50] = 100.0

    result = _compare(empty, present, window=(0.5, 3.9))

    assert not (result.status == "accepted" and abs(result.peak_bin - 49.0) < 1.5)
    passing_peaks = {round(peak["peak_bin"]) for peak in result.alternate_peaks}
    assert passing_peaks <= {30, 49}

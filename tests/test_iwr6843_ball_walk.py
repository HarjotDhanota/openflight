"""The ball's own range walk on Oct 27 shot 1, under a golfer band 11-21 dB stronger.

The production MTI keeps the ball (bins 57.5 -> 83.3 over frames 12-23, 36.5 m/s, OPS
36.52 m/s); a search that keeps one peak per loop never sees it, because the golfer band
(bins 44-56) is the argmax of every loop (gap-closure/alias-clutter/WALK-SELECTION-TRACE.md).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openflight.iwr6843 import launch_range as lr, shot, tracking
from openflight.iwr6843.lcmf import prepare_lcmf_capture

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "iwr6843_oct27_shot1"
# The OPS ball speed projected on the radar's line of sight (36.06-36.23 m/s, REPORT.md):
# the walk's range rate must lie within 1 m/s of 36.1-36.3 m/s.
LOS_SPEED_MS = (36.1, 36.3)
RATE_TOLERANCE_MS = 1.0


@pytest.fixture(scope="module")
def inputs():
    return json.loads((FIXTURE / "shot1_inputs.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def vertical(inputs):
    return prepare_lcmf_capture((FIXTURE / inputs["dump"]).read_bytes()).vertical


def _frames(track, geometry):
    first = int(track.t_first // geometry.frame_period_s)
    last = int(track.t_last // geometry.frame_period_s)
    return last - first + 1


def _assert_is_the_ball(track, geometry):
    assert track is not None
    low, high = LOS_SPEED_MS
    assert low - RATE_TOLERANCE_MS <= track.speed_ms <= high + RATE_TOLERANCE_MS
    assert _frames(track, geometry) >= 10
    assert track.bin_at(track.t_last) > 80.0


@pytest.mark.parametrize("prior_source", ["recorded", "located_tee"])
def test_the_launch_range_search_selects_the_ball_s_walk(inputs, vertical, prior_source):
    recorded = inputs["range_prior"]
    prior = (
        lr.LaunchRangePrior(recorded["range_m"], recorded["uncertainty_m"], recorded["source"])
        if prior_source == "recorded"
        else lr.LaunchRangePrior(1.814, 0.03, lr.LOCATED_TEE_SOURCE)
    )
    estimate = lr.estimate_launch_range(
        vertical,
        ball_speed_mph=inputs["ball_speed_mph"],
        club_speed_mph=inputs["club_speed_mph"],
        prior=prior,
        net_range_m=inputs["net_range_m"],
        range_bias_m=inputs["range_bias_m"],
    )

    _assert_is_the_ball(estimate.track, vertical.geometry)


def test_the_ops_blind_search_keeps_one_peak_per_loop():
    """More peaks help only a search bounded by the OPS speed (``shot`` module note)."""
    assert shot.LCMF_SEARCH_MAX_PEAKS_PER_LOOP == 1
    assert tracking.BALL_MAX_PEAKS_PER_LOOP > shot.LCMF_SEARCH_MAX_PEAKS_PER_LOOP


def test_one_peak_per_loop_never_sees_the_ball(vertical):
    """The defect, kept visible: the golfer band is every loop's strongest peak."""
    track = tracking.find_ball(
        vertical.mti("burst"),
        vertical.geometry,
        gates_m=((0.5, 4.35),),
        speed_bounds_ms=(33.6, 40.2),
        min_ball_ms=33.6,
        max_peaks_per_loop=1,
    )

    assert track is None or track.bin_at(track.t_last) < 60.0


def test_the_ball_search_keeps_a_slot_per_golfer_band_cell():
    """The band (bins 44-56, 0.56 m) resolves into band_width / peak separation cells."""
    assert tracking.BALL_MAX_PEAKS_PER_LOOP == round(
        tracking.GOLFER_BAND_WIDTH_BINS / tracking.PEAK_SEPARATION_BINS
    )


@pytest.mark.parametrize("peaks", [1, tracking.BALL_MAX_PEAKS_PER_LOOP])
@pytest.mark.parametrize("scope", ["burst", "window"])
def test_the_club_s_approach_walk_at_one_and_at_the_ball_s_peak_count(
    inputs, vertical, scope, peaks
):
    """The club decides for itself: its approach (OPS club 30.7 m/s) crosses the tee bin
    (bin 40) in frame 5 (15-18 ms). One peak per loop loses it in the window MTI."""
    club_ms = inputs["club_speed_mph"] / tracking.MPH_PER_MS
    walk = tracking.find_ball(
        vertical.mti(scope),
        vertical.geometry,
        gates_m=((0.35, 2.8),),
        speed_bounds_ms=(0.7 * club_ms, 1.2 * club_ms),
        min_ball_ms=0.7 * club_ms,
        time_window_s=(0.0, 0.030),
        max_peaks_per_loop=peaks,
    )
    found = (
        walk is not None
        and abs(walk.speed_ms - club_ms) < 2.0
        and 0.015 <= (40.0 - walk.intercept_bins) / walk.slope_bins <= 0.018
    )

    assert found == (scope == "burst" or peaks > 1)
    assert lr.CLUB_WALK_MAX_PEAKS_PER_LOOP == tracking.BALL_MAX_PEAKS_PER_LOOP


def test_shot_1_s_launch_point_is_the_ball_walk_read_back_to_contact(inputs, vertical):
    """The located tee (1.814 m): the ball's walk read back at the club walk's arrival at
    the tee bin lands within 0.10 m of it, labelled estimated with the reason (no camera
    contact time on the v7 dump's ring clock). Never the club-ball intersection."""
    estimate = lr.estimate_launch_range(
        vertical,
        ball_speed_mph=inputs["ball_speed_mph"],
        club_speed_mph=inputs["club_speed_mph"],
        prior=lr.LaunchRangePrior(1.814, 0.03, lr.LOCATED_TEE_SOURCE),
        net_range_m=inputs["net_range_m"],
        range_bias_m=inputs["range_bias_m"],
    )

    assert estimate.method == "back_extrapolation"
    assert estimate.range_m == pytest.approx(1.814, abs=0.10)
    assert estimate.label == "estimated"
    assert estimate.contact["source"] == "club_walk_at_tee"
    assert estimate.contact["label"] == "estimated"
    # the contact time lies inside the pre-request frames, as the walks say (~13-16 ms)
    assert 0.010 <= estimate.impact_t_s <= 0.018
    # sigma = range rate x contact-time sigma, with the line's own and the model floor
    assert estimate.uncertainty_m >= estimate.ball_track["speed_ms"] * estimate.contact["sigma_s"]

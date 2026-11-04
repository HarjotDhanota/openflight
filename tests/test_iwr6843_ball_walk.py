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


def _shot_1_on_the_located_tee(inputs, vertical):
    return lr.estimate_launch_range(
        vertical,
        ball_speed_mph=inputs["ball_speed_mph"],
        club_speed_mph=inputs["club_speed_mph"],
        prior=lr.LaunchRangePrior(1.814, 0.03, lr.LOCATED_TEE_SOURCE),
        net_range_m=inputs["net_range_m"],
        range_bias_m=inputs["range_bias_m"],
    )


def _shot_1_calibration(inputs):
    """The Oct 27 session's recorded LCMF inputs, on the camera's located tee."""
    from dataclasses import replace  # noqa: PLC0415

    from openflight.iwr6843.replay import build_replay_calibration  # noqa: PLC0415

    calibration = build_replay_calibration(
        Path(__file__).resolve().parents[1] / "config" / "iwr6843_calibration_reference.json",
        tee_range_m=1.814,
        tilt_deg=9.946,
        radar_height_m=0.05224817918569254,
        ball_height_m=0.02135,
        board_rotation_deg=90.0,
        roll_deg=0.149,
    )
    return replace(calibration, range_bias_m=inputs["range_bias_m"])


def _walk_from_contact(estimate):
    """The launch range's walk from its contact time on: before it, its line carries the
    club's approach."""
    from dataclasses import replace  # noqa: PLC0415

    track = estimate.track
    return replace(track, t_first=max(track.t_first, estimate.impact_t_s), quad_bins=None)


def test_the_recovered_walk_is_good_enough_for_the_angle_stage(inputs, vertical):
    """What the LCMF needs of a walk (WALK-SELECTION-TRACE.md): the ball's bin within half a
    bin in frames 12-23 (it reads round(bin_at(t))) and a range rate good to ~1 m/s (13 deg
    of TDM phase per m/s; the LCMF turns the phase with the OPS speed in any case)."""
    estimate = _shot_1_on_the_located_tee(inputs, vertical)
    walk = _walk_from_contact(estimate)
    # the ball's own line (REPORT.md fit, 12 frames, 0.5 cm rms): 28.51 + 788.6 t bins.
    # Measured: 0.61 and 0.52 bins off in frames 12-13 (the walk's 35.6 m/s against the
    # ball's 36.5), within 0.43 from frame 14: the half-bin holds from frame 14 on.
    geometry = vertical.geometry
    for frame in range(12, 24):
        t_s = geometry.loop_time(frame, geometry.n_loops // 2)
        error = abs(walk.bin_at(t_s) - (28.51 + 788.6 * t_s))
        assert error <= (0.5 if frame >= 14 else 0.65), frame
    assert abs(walk.speed_ms - 36.2) <= 1.0
    assert walk.t_first >= estimate.impact_t_s


def test_the_snapshot_gate_drops_the_ball_s_own_frames(inputs, vertical):
    """Measured, not changed (G4 C3): on the walk from contact on, the LCMF keeps snapshots
    at MIN_SNR (8x the cube noise, 9 dB). In frames 16-23 (the ball alone, bins 66-83) the
    walk's snapshots read 3.4-7.0 dB (frame medians) and none pass; the frames that pass are
    where the walk runs through the golfer band (5-12). Lowering the gate needs the
    August noise check first."""
    import numpy as np  # noqa: PLC0415

    from openflight.iwr6843 import lcmf  # noqa: PLC0415
    from openflight.iwr6843.shot import ShotMeasurement  # noqa: PLC0415

    raw = (FIXTURE / inputs["dump"]).read_bytes()
    prepared = lcmf.prepare_lcmf_capture(raw)
    estimate = _shot_1_on_the_located_tee(inputs, vertical)
    shot_track = ShotMeasurement(
        geometry=vertical.geometry,
        ball_found=True,
        track=_walk_from_contact(estimate),
        club=inputs["club"],
        tx_order="normal",
        tdm_sign_policy="positive",
        notch_recovered=estimate.scope == "window",
        tdm_sign_used=1,
    )
    cache, _geometry, _cube = lcmf._snapshot_cache(  # pylint: disable=protected-access
        raw,
        shot_track,
        _shot_1_calibration(inputs),
        "normal",
        1,
        prepared.tdm_tau_s,
        prepared.loop_period_s,
        phase_velocity_ms=inputs["ball_speed_mph"] / tracking.MPH_PER_MS,
        prepared=vertical,
    )
    passing = cache["snr"] >= lcmf.MIN_SNR
    clear_of_band = cache["frame"] >= 16

    assert passing[~clear_of_band].mean() > 0.7
    assert passing[clear_of_band].mean() < 0.05
    assert np.median(10.0 * np.log10(cache["snr"][clear_of_band])) < 9.0


def test_the_shot_record_names_the_walk_and_how_its_contact_was_timed(inputs, vertical):
    record = _shot_1_on_the_located_tee(inputs, vertical).to_dict()

    walk = record["ball_track"]
    assert walk["speed_ms"] == pytest.approx(36.2, abs=1.1)
    assert walk["n_frames"] >= 10
    assert walk["bin_last"] > 80.0
    assert walk["frames"][1] == 23
    # the ball runs 2.5-10 dB over its gate's median (REPORT.md); the band-crossing frames more
    assert 2.0 < walk["snr_db_median"] < 15.0
    assert record["contact"]["source"] == "club_walk_at_tee"
    assert record["label"] == "estimated"
    assert "no camera contact time on the IWR ring clock" in record["reason"]


def test_an_estimated_iwr_launch_point_confirms_no_located_tee_and_says_why(inputs, vertical):
    """The located tee's cross-check (iwr_confirmed / iwr_disagrees / iwr_not_measured)
    takes only a measured IWR launch point: one read back at the IWR's own contact time
    is estimated and confirms nothing, with the reason."""
    from openflight.camera.fallback_chain import iwr_cross_check  # noqa: PLC0415

    launch = _shot_1_on_the_located_tee(inputs, vertical).to_dict()
    check = iwr_cross_check({"range_m": 1.814, "uncertainty_m": 0.03}, launch)

    assert check["status"] == "iwr_not_measured"
    assert "club walk reaching the tee bin" in check["text"]
    assert check["iwr_range_m"] == pytest.approx(1.814, abs=0.10)
    shot_range = lr.shot_ball_range(
        _shot_1_on_the_located_tee(inputs, vertical),
        lr.LaunchRangePrior(1.814, 0.03, lr.LOCATED_TEE_SOURCE),
    )
    assert shot_range.source == lr.LOCATED_TEE_SOURCE
    assert "IWR launch point 1.9" in shot_range.reason and "(estimated)" in shot_range.reason

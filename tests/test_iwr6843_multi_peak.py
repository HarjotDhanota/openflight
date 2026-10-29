"""Keep weaker moving returns available when brighter clutter shares a gate."""

import numpy as np
import pytest

from openflight.iwr6843 import club, tracking
from openflight.iwr6843.calibration import Calibration
from openflight.iwr6843.dump import SAMPLE_RANGE_FFT_IQ16, pack_dump


def scene(*, clutter=True, second_mover=False):
    geo = tracking.Geometry(7, 24, 2, 4, 128, 0.003, 0)
    mti = np.ones((7, 2, 12, 4, 128), dtype=complex)
    bins = np.arange(128)
    for frame in range(7):
        for loop in range(12):
            time_s = geo.loop_time(frame, loop)
            profile = 1 + 20 * np.exp(
                -0.5 * ((bins - (1.35 + 35 * time_s) / geo.range_res_m) / 0.45) ** 2
            )
            if clutter:
                profile += 100 * np.exp(-0.5 * ((bins - 2.45 / geo.range_res_m) / 0.45) ** 2)
            if second_mover:
                profile += 20 * np.exp(
                    -0.5 * ((bins - (1.75 + 35 * time_s) / geo.range_res_m) / 0.45) ** 2
                )
            mti[frame, :, loop, :, :] = np.sqrt(profile)
    return geo, mti


def test_club_finds_weaker_approach_without_narrowing_the_range_gate():
    geo, mti = scene()
    selection = club.find_club(
        mti,
        geo,
        tee_range_m=2.085,
        tee_uncertainty_m=0.45,
        window_s=(0, 0.021),
        impact_t_s=0.021,
        ops_club_speed_mph=35 * tracking.MPH_PER_MS,
    )
    assert selection is not None
    assert selection.track.speed_ms == pytest.approx(35, abs=1)
    assert selection.impact_error_m < 0.05


def test_default_detection_still_keeps_only_the_strongest_peak():
    geo, mti = scene()
    loops, bins = tracking._detections(tracking.loop_power(mti), geo, gates_m=((1.0, 2.7),))
    assert len(loops) == geo.n_frames * geo.n_loops
    assert np.allclose(bins * geo.range_res_m, 2.45, atol=0.02)


def test_multiple_peaks_keep_competing_trajectories_ambiguous():
    geo, mti = scene(clutter=False, second_mover=True)
    search = tracking.search_ball(
        mti,
        geo,
        gates_m=((1.0, 2.7),),
        speed_bounds_ms=(25, 42),
        min_ball_ms=25,
        max_peaks_per_loop=3,
    )
    assert search.track is not None
    assert search.ambiguous
    assert search.runner_up is not None


def test_ambiguous_club_tracks_cannot_supply_camera_depth_or_angles(monkeypatch):
    geo, mti = scene(clutter=False, second_mover=True)
    selection = club.find_club(
        mti,
        geo,
        tee_range_m=2.3,
        tee_uncertainty_m=0.5,
        window_s=(0, 0.021),
        impact_t_s=0.021,
        ops_club_speed_mph=35 * tracking.MPH_PER_MS,
    )
    assert selection.track.ambiguity is not None
    monkeypatch.setattr(club, "find_club", lambda *args, **kwargs: selection)
    raw = pack_dump(
        np.zeros((7, 36, 4, 128), dtype=complex),
        n_tx=3,
        version=3,
        frame_period_us=3000,
        sample_fmt=SAMPLE_RANGE_FFT_IQ16,
    )
    result = club.estimate_club_path(
        raw,
        Calibration(tee_range_m=2.3, elem_correction=np.ones(8), tilt_rad=0, range_bias_m=0),
        ops_club_speed_mph=35 * tracking.MPH_PER_MS,
        impact_t_s=0.021,
    )
    assert result.status == "rejected_ambiguous_club_track"
    assert result.path_deg is None
    assert result.candidate_attack_angle_deg is None
    assert tracking.accepted_range_evidence(result.range_evidence) is None


def test_stationary_clutter_does_not_become_a_multi_peak_club_track():
    geo, mti = scene()
    mti[:] = mti[0, :, 0, :, :][:, None, :, :]
    assert (
        tracking.find_ball(
            mti,
            geo,
            gates_m=((1.0, 2.7),),
            speed_bounds_ms=(25, 42),
            min_ball_ms=25,
            max_peaks_per_loop=3,
        )
        is None
    )


def test_adjacent_peaks_cannot_give_one_loop_multiple_votes_for_one_track():
    geo = tracking.Geometry(1, 2, 2, 4, 128, 0.003, 0)
    power = np.ones((1, 128))
    power[0, [32, 34, 42]] = [100, 80, 60]
    loops, bins = tracking._detections(
        power,
        geo,
        gates_m=((1, 3),),
        max_peaks_per_loop=3,
    )
    assert loops.tolist() == [0, 0]
    assert bins.tolist() == [32, 42]


def test_nonpositive_peak_budget_is_rejected():
    geo, mti = scene()
    with pytest.raises(ValueError, match="positive"):
        tracking._detections(tracking.loop_power(mti), geo, max_peaks_per_loop=0)


@pytest.mark.parametrize("windowed", [False, True])
def test_multi_peak_detections_respect_snr_and_stored_range_window(windowed):
    geo, mti = scene()
    if windowed:
        geo.range_bin_starts = (20,) * geo.n_frames
        geo.range_bin_counts = (40,) * geo.n_frames
        geo.range_fft_size = 128
        geo.n_samples = 40
        mti = mti[..., 20:60]
    power = tracking.loop_power(mti)
    loops, bins = tracking._detections(power, geo, gates_m=((1.0, 2.7),), max_peaks_per_loop=3)
    assert len(loops) == 2 * geo.n_frames * geo.n_loops
    assert np.all((bins * geo.range_res_m >= 1.0) & (bins * geo.range_res_m <= 2.7))
    empty_loops, _ = tracking._detections(
        np.ones_like(power), geo, gates_m=((1.0, 2.7),), max_peaks_per_loop=3
    )
    assert len(empty_loops) == 0

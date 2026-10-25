"""The ball tracker's exhaustive, deterministic range-walk search and its ambiguity flag.

``find_ball`` used to RANSAC 2500 random point pairs from a fixed seed: when two walks
competed, which pairs were drawn decided the winner (field dumps flipped launch range
1.51 / 2.18 m between seeds). The search now enumerates every pair, ranks the consensus
lines by documented rules, and says ``ambiguous`` -- with both walks and their evidence --
when the strongest distinct competitor's support is not significantly below the pick's.
"""

from __future__ import annotations

import inspect

import numpy as np
import pytest

from openflight.iwr6843 import tracking
from openflight.iwr6843.dump import SAMPLE_RANGE_FFT_IQ16, is_range_snapshot, pack_dump, parse_dump
from openflight.iwr6843.shot import geometry_from_header

RES_M = 6.0 / 128
FRAME_S = 0.003
LOOP_S = 90e-6
N_LOOPS = 12
TOL = 0.8
BOUNDS = (20.0, 90.0)


def _walk(range_m, speed_ms, frames, *, loops=range(N_LOOPS), t0_s=0.0, jitter=0.0, rng=None):
    """Detections of one receding walk: (time s, fractional bin, frame) per loop."""
    rows = []
    for frame in frames:
        for loop in loops:
            t = frame * FRAME_S + loop * LOOP_S
            bin_at = (range_m + speed_ms * (t - t0_s)) / RES_M
            if rng is not None and jitter:
                bin_at += rng.uniform(-jitter, jitter)
            rows.append((t, bin_at, frame))
    return rows


def _static(range_m, frames, loops=range(N_LOOPS)):
    return [(f * FRAME_S + lp * LOOP_S, range_m / RES_M, f) for f in frames for lp in loops]


def _arrays(*groups):
    rows = sorted(row for group in groups for row in group)
    data = np.asarray(rows, dtype=float)
    return data[:, 0], data[:, 1], data[:, 2].astype(int)


def _search(times, bins, frames, **kwargs):
    kwargs.setdefault("range_res_m", RES_M)
    kwargs.setdefault("tol_bins", TOL)
    kwargs.setdefault("speed_bounds_ms", BOUNDS)
    kwargs.setdefault("min_ball_ms", BOUNDS[0])
    return tracking.search_range_walks(times, bins, frames, **kwargs)


def test_a_strong_walk_beats_a_weak_distinct_one_without_ambiguity():
    times, bins, frames = _arrays(
        _walk(1.60, 40.0, range(2, 12)),  # 120 detections over 10 frames
        _walk(2.60, 30.0, range(14, 17), loops=range(0, 12, 2)),  # 18 over 3 frames
    )

    search = _search(times, bins, frames)

    assert search.selected is not None
    assert search.selected.track.speed_ms == pytest.approx(40.0, abs=0.5)
    assert not search.ambiguous
    assert search.track.ambiguity is None


def test_two_walks_of_equal_support_are_flagged_ambiguous_with_both_candidates():
    times, bins, frames = _arrays(
        _walk(1.50, 30.0, range(2, 6)),  # 48 detections from 1.68 m
        _walk(2.10, 31.0, range(9, 13)),  # 48 detections from 2.94 m, a distinct line
    )

    search = _search(times, bins, frames, ops_speed_ms=30.5)

    assert search.ambiguous
    assert search.runner_up is not None
    speeds = sorted(c.track.speed_ms for c in (search.selected, search.runner_up))
    assert speeds == pytest.approx([30.0, 31.0], abs=0.3)
    ambiguity = search.track.ambiguity
    assert ambiguity is not None
    assert ambiguity.reason == "two IWR tracks compete"
    assert ambiguity.support_z < tracking.SUPPORT_SIGNIFICANCE_Z
    assert ambiguity.separation_bins > 2 * TOL
    for record in (ambiguity.selected, ambiguity.runner_up):
        for key in (
            "speed_ms",
            "range_at_first_m",
            "t_first_s",
            "t_last_s",
            "n_inliers",
            "n_frames",
            "rms_bins",
            "check",
            "ops_speed_ratio",
        ):
            assert key in record
    assert ambiguity.runner_up_track.speed_ms == pytest.approx(search.runner_up.track.speed_ms)
    payload = search.to_dict()
    assert payload["ambiguous"] is True
    assert payload["runner_up"]["n_inliers"] == search.runner_up.track.n_inliers


def test_the_same_detections_give_the_same_answer_in_any_order():
    rng = np.random.default_rng(3)
    times, bins, frames = _arrays(
        _walk(1.50, 30.0, range(2, 7), jitter=0.4, rng=rng),
        _walk(2.10, 31.0, range(9, 14), jitter=0.4, rng=rng),
        [(f * FRAME_S + lp * LOOP_S, rng.uniform(30, 90), f) for f in range(24) for lp in (3, 9)],
    )
    first = _search(times, bins, frames)
    second = _search(times, bins, frames)
    order = np.random.default_rng(11).permutation(times.size)
    shuffled = _search(times[order], bins[order], frames[order])

    assert first.to_dict() == second.to_dict() == shuffled.to_dict()


def test_find_ball_has_no_seed_and_ignores_the_global_random_state():
    params = inspect.signature(tracking.find_ball).parameters
    assert "seed" not in params and "iterations" not in params

    raw = _two_movers()
    meta, cube = parse_dump(raw)
    geo = geometry_from_header(meta)
    mti = tracking.mti_filter(
        cube, scope="window", range_domain=is_range_snapshot(meta), geometry=geo
    )
    answers = set()
    for state in range(6):  # nothing random is left to move the answer
        np.random.seed(state)
        track = tracking.find_ball(mti, geo, gates_m=((1.0, 4.4),))
        answers.add((round(track.speed_ms, 6), round(track.intercept_bins, 6), track.n_inliers))
    assert len(answers) == 1


def test_the_exhaustive_search_finds_the_maximum_consensus_line():
    rng = np.random.default_rng(5)
    clutter = [
        (f * FRAME_S + lp * LOOP_S, rng.uniform(25, 95), f) for f in range(24) for lp in range(12)
    ]
    # a sparse true walk: three detections in each of 10 frames, among 288 clutter points
    walk = _walk(1.40, 35.0, range(3, 13), loops=(1, 5, 9))
    times, bins, frames = _arrays(clutter, walk)

    search = _search(times, bins, frames)

    assert search.selected.track.speed_ms == pytest.approx(35.0, abs=1.0)
    assert search.selected.track.n_inliers >= len(walk)
    assert search.n_pairs > 10_000  # every pair inside the speed bounds was tried


def _check(times, bins, frames, min_speed_ms=20.0):
    return tracking.walk_check(times, bins, frames, range_res_m=RES_M, min_speed_ms=min_speed_ms)


def test_a_clean_walk_passes_the_consistency_check():
    times, bins, frames = _arrays(_walk(1.60, 36.0, range(2, 10)))

    check, halves = _check(times, bins, frames)

    assert check == "ok"
    assert halves == pytest.approx((36.0, 36.0), abs=0.5)


def test_a_line_through_two_objects_is_refused_as_a_mix():
    # a ball at 36 m/s whose later detections belong to a slower object (16 m/s): the
    # halves' speeds differ far beyond what drag or the radial projection allow
    kink_s = 6 * FRAME_S
    times, bins, frames = _arrays(
        _walk(1.60, 36.0, range(2, 6)),
        _walk(1.60 + 36.0 * kink_s, 16.0, range(6, 10), t0_s=kink_s),
    )

    check, halves = _check(times, bins, frames, min_speed_ms=15.0)

    assert check.startswith("decelerates"), check
    assert halves[0] > halves[1] + 15.0


def test_a_walk_that_stops_is_refused_as_stalled():
    # a return walking at 25 m/s that then stays put (a tee, a club at rest)
    stop_s = 6 * FRAME_S
    times, bins, frames = _arrays(
        _walk(1.50, 25.0, range(2, 6)), _static(1.50 + 25.0 * stop_s, range(6, 10))
    )

    check, _halves = _check(times, bins, frames)

    assert check.startswith("stalled"), check


def test_three_frames_cannot_test_a_walk_s_consistency():
    times, bins, frames = _arrays(_walk(1.60, 36.0, range(2, 5)))

    check, halves = _check(times, bins, frames)

    assert check == "untestable"
    assert halves is None


def test_near_duplicate_lines_of_one_noisy_walk_are_not_competitors():
    rng = np.random.default_rng(9)
    times, bins, frames = _arrays(_walk(1.70, 33.0, range(1, 15), jitter=0.6, rng=rng))

    search = _search(times, bins, frames)

    assert search.n_pairs > 1000  # many seed lines, one walk
    assert not search.ambiguous
    assert search.runner_up is None


def test_a_slow_walk_with_more_support_loses_to_a_credible_fast_one_by_rule():
    # the existing fastest-credible contract: a fast walk with >= FAST_SUPPORT_FRAC of the
    # most-inliers (slow) walk's support wins, and the slow one is not an ambiguity
    times, bins, frames = _arrays(
        _walk(1.30, 22.0, range(0, 12)),  # 144, slow (club / tee)
        _walk(1.90, 45.0, range(4, 11)),  # 84, fast
    )

    search = _search(times, bins, frames, min_ball_ms=34.0)

    assert search.selected.track.speed_ms == pytest.approx(45.0, abs=0.5)
    assert not search.ambiguous


def test_a_range_anchor_refuses_a_walk_that_starts_short_of_the_resting_ball():
    times, bins, frames = _arrays(
        _walk(1.00, 30.0, range(2, 6)),
        _walk(2.10, 31.0, range(9, 13)),
    )
    unanchored = _search(times, bins, frames)
    anchored = _search(
        times, bins, frames, anchor=tracking.TrackAnchor(range_m=2.3, range_sd_m=0.05)
    )

    assert unanchored.ambiguous
    assert not anchored.ambiguous
    assert anchored.selected.range_at_first_m > 2.2
    assert any(c.check.startswith("corridor") for c in anchored.rejected)


def test_an_impact_anchor_refuses_a_walk_seen_before_impact():
    times, bins, frames = _arrays(
        _walk(1.50, 30.0, range(2, 6)),
        _walk(2.10, 31.0, range(9, 13)),
    )

    search = _search(
        times, bins, frames, anchor=tracking.TrackAnchor(impact_t_s=0.026, impact_sd_s=0.001)
    )

    assert not search.ambiguous
    assert search.selected.track.t_first >= 0.026


def test_the_ops_speed_is_recorded_as_evidence_not_required():
    times, bins, frames = _arrays(_walk(1.60, 40.0, range(2, 12)))

    without = _search(times, bins, frames)
    with_ops = _search(times, bins, frames, ops_speed_ms=42.0)

    assert without.selected.ops_speed_ratio is None
    assert with_ops.selected.ops_speed_ratio == pytest.approx(40.0 / 42.0, abs=0.01)
    assert with_ops.selected.track.speed_ms == without.selected.track.speed_ms


def test_too_few_detections_find_nothing():
    times, bins, frames = _arrays(_walk(1.60, 40.0, (3,), loops=range(4)))

    assert _search(times, bins, frames).selected is None


def _two_movers(n_frames=18, loops=12, n_rx=4, n_samples=128):
    """Two receding movers with equal echoes, alternating as the per-loop peak."""
    cube = np.zeros((n_frames, loops * 2, n_rx, n_samples), dtype=complex)
    for frame in range(n_frames):
        for loop in range(loops * 2):
            t = frame * 4e-3 + (loop // 2) * 90e-6
            mover = (frame + loop // 2) % 2
            range_m = (1.5 + 30.0 * t) if mover == 0 else (2.3 + 31.0 * t)
            bin_at = range_m / (6.0 / n_samples)
            lo = int(bin_at)
            if 0 <= lo < n_samples - 1:
                frac = bin_at - lo
                cube[frame, loop, :, lo] = 1000.0 * (1 - frac)
                cube[frame, loop, :, lo + 1] = 1000.0 * frac
    return pack_dump(
        cube, n_tx=2, version=3, frame_period_us=4000, sample_fmt=SAMPLE_RANGE_FFT_IQ16
    )


def test_find_ball_returns_the_ambiguity_on_the_track_it_picks():
    raw = _two_movers()
    meta, cube = parse_dump(raw)
    geo = geometry_from_header(meta)
    mti = tracking.mti_filter(
        cube, scope="window", range_domain=is_range_snapshot(meta), geometry=geo
    )

    search = tracking.search_ball(mti, geo, gates_m=((1.0, 4.4),))
    track = tracking.find_ball(mti, geo, gates_m=((1.0, 4.4),))

    assert search.ambiguous
    assert track.ambiguity is not None
    assert track.speed_ms == pytest.approx(search.selected.track.speed_ms)
    assert track.quad_bins is not None  # the picked walk keeps its quadratic refit

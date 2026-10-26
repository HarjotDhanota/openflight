"""Known-answer checks for the isolated spin recall study, not hardware accuracy."""

import numpy as np
import pytest

from scripts.analysis.spin_fusion_study import (
    carrier_diagnostic,
    classify,
    local_candidates,
    recall_summary,
    spectra,
    supporting_rank,
)
from scripts.analysis.spin_signal_windows import onset_sample, summarize
from scripts.analysis.spin_study_report import stats


def test_all_local_peaks_are_retained_and_ranked_without_reference():
    frequencies = np.arange(300.0)
    power = np.ones(300)
    peak_indices = list(range(20, 230, 20))
    for rank, index in enumerate(peak_indices, 1):
        power[index] = 20 - rank
    candidates = local_candidates(frequencies, power, power)
    assert len(candidates) == 11
    assert [c["rpm"] for c in candidates] == [i * 60 for i in peak_indices]
    assert candidates[-1]["rank"] == 11
    assert candidates[0]["relative_power"] == 1
    assert candidates[-1]["multitaper_peak_to_band_median"] == 9
    assert supporting_rank(candidates, 13_200, 0.01) == 11


def test_monotone_spectrum_is_marked_as_boundary_not_unlabelled_tone():
    frequencies = np.arange(300.0)
    candidates = local_candidates(frequencies, frequencies + 1, frequencies + 1)
    assert len(candidates) == 1
    assert candidates[0]["band_boundary"]


def test_widened_spectrum_recovers_known_amplitude_modulation_above_old_rail():
    time = np.arange(3000) / 30_000
    envelope = 10 + 3 * np.cos(2 * np.pi * 35 * time) + np.cos(2 * np.pi * 200 * time)
    original = envelope.copy()
    result = spectra(envelope)
    assert result["candidates"][0]["rpm"] == pytest.approx(12_000, abs=60)
    assert result["fade_hz"] == pytest.approx(35, abs=1)
    np.testing.assert_array_equal(envelope, original)


def test_recall_denominator_excludes_estimated_or_bad_pairs():
    rows = [
        {
            "eligible": True,
            "dataset": "may",
            "reference_club": "driver",
            "rank_10": 2,
            "rank_20": 1,
        },
        {
            "eligible": True,
            "dataset": "may",
            "reference_club": "driver",
            "rank_10": None,
            "rank_20": 6,
        },
        {
            "eligible": False,
            "dataset": "may",
            "reference_club": "driver",
            "rank_10": 1,
            "rank_20": 1,
        },
    ]
    result = recall_summary(rows)[0]
    assert result["n"] == 2
    assert result["recall_10_top_1"] == 0
    assert result["recall_10_top_5"] == 0.5
    assert result["recall_20_top_all"] == 1


def test_harmonic_class_requires_a_nearby_multiple_of_pick():
    assert classify(2000, 4000, []) == "harmonic_only"
    assert classify(2000, 7000, []) == "no_supported_peak"
    assert classify(2000, 2050, []) == "current_pick_ok"


def test_no_carrier_is_zero_duration_not_full_buffer():
    result = carrier_diagnostic(np.zeros(4096, dtype=complex), 8000, 60)
    assert result[-1] == 0


def test_carrier_onset_requires_sustained_post_impact_evidence():
    times = np.arange(100) * 0.001
    ratios = np.zeros(100)
    ratios[:20] = 50
    ratios[35:46] = 50
    assert onset_sample(times, ratios, 25) is None
    ratios[60:72] = 50
    assert onset_sample(times, ratios, 25) == 1800


def test_window_summary_keeps_a_fixed_cohort_for_length_comparison():
    rows = []
    for shot, lengths in [("may-01", (20, 60)), ("may-02", (20,))]:
        for length in lengths:
            rows.append(
                {
                    "shot": shot,
                    "dataset": "may",
                    "anchor": "carrier_onset",
                    "eligible": True,
                    "requested_ms": length,
                    "available": True,
                    "pick_rpm": 1000 if shot == "may-01" else 9000,
                    "truth_rpm": 1000,
                    "rank_10": 1 if shot == "may-01" else None,
                }
            )
    results = summarize(rows)
    fixed = [
        r
        for r in results
        if r["dataset"] == "may"
        and r["anchor"] == "carrier_onset"
        and r["cohort"] == "same_shots_available_60ms"
        and r["requested_ms"] in (20, 60)
    ]
    assert len(fixed) == 2
    assert all(r["n"] == 1 and r["median_abs_pct"] == 0 for r in fixed)


def test_error_summary_uses_reference_denominator_and_absolute_errors_before_median():
    result = stats(
        [
            {"ops_pick_rpm": 1500, "reference_spin_rpm": 1000},
            {"ops_pick_rpm": 1600, "reference_spin_rpm": 2000},
        ]
    )
    assert result["mae_rpm"] == 450
    assert result["median_abs_pct"] == 35
    assert result["median_signed_pct"] == 15
    assert result["gross_error_rate"] == 0.5

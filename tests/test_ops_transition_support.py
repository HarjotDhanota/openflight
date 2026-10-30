"""Temporal order and finite FFT support constrain the OPS impact heuristic."""

import numpy as np
import pytest

from openflight.ops_speed_track import ops_speed_track
from openflight.rolling_buffer.processor import RollingBufferProcessor
from openflight.rolling_buffer.types import IQCapture, ProcessedCapture, SpeedReading, SpeedTimeline
from tests.test_ops_speed_track import _processed


def _capture(mode):
    return IQCapture(0, 0.068, [2048] * 4096, [2048] * 4096, trigger_type=mode)


@pytest.mark.parametrize("mode", ["sound", "hardware", "speed", None])
def test_simultaneous_peaks_do_not_supply_a_temporal_transition(mode):
    processor = RollingBufferProcessor()
    timeline = SpeedTimeline([SpeedReading(60, 200, 10), SpeedReading(80, 300, 10)], 937.5)
    result = processor.estimate_impact(timeline, 80, 60, _capture(mode))
    assert result.source == ("sound_trigger" if mode == "sound" else "unavailable")
    assert result.timestamp_ms == (pytest.approx(68) if mode == "sound" else None)


def test_a_cotimed_peak_cannot_replace_the_actual_earlier_observation():
    processor = RollingBufferProcessor()
    timeline = SpeedTimeline(
        [SpeedReading(60, 200, 8), SpeedReading(55, 400, 10), SpeedReading(80, 300, 10)], 937.5
    )
    result = processor.estimate_impact(timeline, 80, 60)
    assert result.source == "ops_transition"
    assert result.last_club_timestamp_ms == 8
    assert result.transition_gap_ms == pytest.approx(2)


def test_constant_simultaneous_raw_tones_have_no_observed_impact_order():
    processor = RollingBufferProcessor()
    t = np.arange(4096) / processor.SAMPLE_RATE
    factor = 2 / processor.MPS_TO_MPH / processor.WAVELENGTH_M
    signal = 500 * np.exp(2j * np.pi * factor * 80 * t) + 400 * np.exp(
        2j * np.pi * factor * 60 * t + 0.5j
    )
    noise = np.random.default_rng(301030954).normal(0, 2, (2, len(t)))
    capture = IQCapture(
        0,
        0.068,
        np.round(2048 + signal.real + noise[0]).astype(int).tolist(),
        np.round(2048 + signal.imag + noise[1]).astype(int).tolist(),
        trigger_type="hardware",
    )
    result = processor.process_capture(capture)
    assert result is not None
    assert result.impact.source == "unavailable"
    assert result.impact.timestamp_ms is None


@pytest.mark.parametrize(
    "rate,window,gap",
    [
        (30000, 128, 1),
        (30000, 128, 2),
        (30000, 128, 8),
        (24000, 256, 2),
        (40000, 256, 6),
        (48000, 128, 10),
    ],
)
def test_transition_tolerance_covers_both_observed_fft_windows(rate, window, gap):
    processor = RollingBufferProcessor()
    processor.SAMPLE_RATE, processor.WINDOW_SIZE = rate, window
    club_start, ball_start = 10.0, 10.0 + gap
    timeline = SpeedTimeline(
        [SpeedReading(60, 200, club_start), SpeedReading(80, 300, ball_start)], rate / 32
    )
    impact = processor.estimate_impact(timeline, 80, 60)
    assert impact.source == "ops_transition"
    capture = ProcessedCapture(
        timeline,
        80,
        ball_start,
        impact=impact,
        processor_config={
            "sample_rate_hz": rate,
            "constants": {"WINDOW_SIZE": window, "STEP_SIZE_OVERLAP": 32},
        },
    )
    result = ops_speed_track(capture)
    lower = result["impact_ms"] - result["impact_uncertainty_ms"]
    upper = result["impact_ms"] + result["impact_uncertainty_ms"]
    assert lower <= club_start + 1e-9
    assert upper >= ball_start + window / rate * 1000 - 1e-9
    assert result["impact_physical_timing_qualified"] is False
    assert result["impact_uncertainty_basis"] == "conditional union of the transition FFT supports"


@pytest.mark.parametrize("gap", [None, float("nan"), -1.0, 0.0])
def test_a_missing_or_invalid_gap_cannot_become_a_precise_transition_track(gap):
    assert ops_speed_track(_processed(gap=gap)) is None


def test_positive_overlapping_observations_keep_the_ordered_heuristic():
    processor = RollingBufferProcessor()
    timeline = SpeedTimeline([SpeedReading(60, 200, 10), SpeedReading(80, 300, 11)], 937.5)
    result = processor.estimate_impact(timeline, 80, 60)
    assert result.source == "ops_transition"
    assert result.transition_gap_ms == pytest.approx(1)
    assert result.timestamp_ms == pytest.approx(10.5 + 128 / 30 / 2)

"""Unsupported ADC intervals cannot supply measured speeds."""

from pathlib import Path

import numpy as np
import pytest

from openflight.clubs import ClubType
from openflight.rolling_buffer.processor import RollingBufferProcessor
from openflight.rolling_buffer.types import IQCapture

FIXTURE = Path(__file__).parent / "fixtures/ops_club_pick/outdoor-test-1-arm5.npz"


def field_capture(shot):
    with np.load(FIXTURE, allow_pickle=False) as data:
        index = int(np.flatnonzero(data["shot"] == shot)[0])
        return IQCapture(
            sample_time=float(data["sample_time"][index]),
            trigger_time=float(data["trigger_time"][index]),
            i_samples=data["i_samples"][index].tolist(),
            q_samples=data["q_samples"][index].tolist(),
        )


def tone_capture(speed_mph=90, amplitude=600):
    processor = RollingBufferProcessor()
    time_s = np.arange(4096) / processor.SAMPLE_RATE
    frequency = 2 * speed_mph / processor.MPS_TO_MPH / processor.WAVELENGTH_M
    phase = 2 * np.pi * frequency * time_s
    return IQCapture(
        sample_time=0,
        trigger_time=0.068,
        i_samples=np.round(2048 + amplitude * np.cos(phase)).astype(int).tolist(),
        q_samples=np.round(2048 + amplitude * np.sin(phase)).astype(int).tolist(),
    )


@pytest.mark.parametrize("shot", [5, 8])
def test_railed_transient_does_not_establish_a_ball(shot):
    result = RollingBufferProcessor().process_capture(
        field_capture(shot), club_type=ClubType.IRON_7
    )
    assert result is None, "A transient in unusable ADC input is not measured ball flight"


def test_windows_straddling_unfilled_prehistory_do_not_supply_peaks():
    capture = tone_capture()
    prefix = 1280
    capture.i_samples[:prefix] = [0] * prefix
    capture.q_samples[:prefix] = [0] * prefix
    timeline = RollingBufferProcessor().process_overlapping(capture)
    assert timeline.readings, "The usable tail must remain measurable"
    assert min(row.timestamp_ms for row in timeline.readings) >= prefix / 30


def test_single_near_rail_sample_does_not_discard_clean_ball_return():
    capture = tone_capture()
    capture.i_samples[2300] = 4095
    result = RollingBufferProcessor().process_capture(capture)
    assert result is not None
    assert result.ball_speed_mph == pytest.approx(90, abs=1)


def test_zero_filled_prefix_does_not_discard_valid_later_ball():
    result = RollingBufferProcessor().process_capture(field_capture(23), club_type=ClubType.IRON_7)
    assert result is not None
    assert result.ball_speed_mph == pytest.approx(69, abs=2)


def test_noisy_clipped_tone_does_not_produce_a_valid_speed():
    capture = tone_capture(amplitude=9000)
    capture.i_samples = np.clip(capture.i_samples, 0, 4095).tolist()
    capture.q_samples = np.clip(capture.q_samples, 0, 4095).tolist()
    assert RollingBufferProcessor().process_capture(capture) is None


def test_spectral_onset_does_not_use_a_clipped_prefix():
    capture = tone_capture(speed_mph=76)
    clipped = tone_capture(speed_mph=76, amplitude=9000)
    capture.i_samples[:1800] = np.clip(clipped.i_samples[:1800], 0, 4095).tolist()
    capture.q_samples[:1800] = np.clip(clipped.q_samples[:1800], 0, 4095).tolist()
    onset = RollingBufferProcessor()._ball_line_onset_ms(capture, 76)
    assert onset is not None, "The usable later line must still be found"
    assert onset >= 60, "A clipped prefix cannot date the usable line's onset"


def test_zero_prefix_is_identified_separately_and_raw_samples_are_unchanged():
    capture = field_capture(23)
    original_i = capture.i_samples.copy()
    original_q = capture.q_samples.copy()
    processor = RollingBufferProcessor()
    quality = processor.capture_quality(capture)
    assert [
        (item.start_sample, item.end_sample, item.reason) for item in quality.invalid_intervals
    ] == [(0, 1280, "zero_filled")]
    assert not (quality.near_rail & ~quality.invalid).any()
    processor.process_capture(capture, club_type=ClubType.IRON_7)
    assert capture.i_samples == original_i
    assert capture.q_samples == original_q


def test_invalid_gap_cannot_be_bridged_by_an_fft_window():
    capture = tone_capture()
    capture.i_samples[1500:1756] = [0] * 256
    capture.q_samples[1500:1756] = [0] * 256
    processor = RollingBufferProcessor()
    timeline = processor.process_overlapping(capture)
    assert any(row.timestamp_ms < 40 for row in timeline.readings)
    assert any(row.timestamp_ms > 70 for row in timeline.readings)
    for row in timeline.readings:
        start = round(row.timestamp_ms * 30)
        assert start + processor.WINDOW_SIZE <= 1500 or start >= 1756


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), -1, 5000])
def test_invalid_adc_values_cannot_supply_fft_peaks(invalid):
    capture = tone_capture()
    capture.i_samples[1800] = invalid
    processor = RollingBufferProcessor()
    quality = processor.capture_quality(capture)
    assert any(item.reason == "invalid_adc" for item in quality.invalid_intervals)
    timeline = processor.process_overlapping(capture)
    assert timeline.readings
    assert all(
        not (round(row.timestamp_ms * 30) <= 1800 < round(row.timestamp_ms * 30) + 128)
        for row in timeline.readings
    )

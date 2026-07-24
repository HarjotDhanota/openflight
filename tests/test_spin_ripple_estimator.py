"""Tests for the offline speed-track ripple spin estimator."""

import sys
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "analysis"))

import spin_ripple_estimator as ripple  # noqa: E402
from spin_synth import synth_capture  # noqa: E402

BALL_MPH = 145.0
BALL_HZ = 2 * (BALL_MPH / 2.23694) / 0.01243


class TestExtractRippleTrack:
    def test_track_follows_doppler_tone(self):
        i_samples, q_samples = synth_capture(
            rpm=3000, ball_speed_mph=BALL_MPH, decel_mph_per_s=0.0, fm_dev_hz=0.0
        )
        track = ripple.extract_ripple_track(i_samples, q_samples, BALL_MPH, hop=32)
        assert track.n_windows == (4096 - 128) // 32 + 1
        active = track.times_ms > 12.0
        assert np.all(np.abs(track.freq_hz[active] - BALL_HZ) < 120.0)

    def test_track_follows_deceleration_chirp(self):
        i_samples, q_samples = synth_capture(
            rpm=3000, ball_speed_mph=BALL_MPH, decel_mph_per_s=60.0, fm_dev_hz=0.0
        )
        track = ripple.extract_ripple_track(i_samples, q_samples, BALL_MPH, hop=32)
        active = track.times_ms > 12.0
        first = track.freq_hz[active][0]
        last = track.freq_hz[active][-1]
        # 60 mph/s over ~120 ms of visible flight = ~7 mph = ~520 Hz drop
        assert first - last > 300.0

    def test_hop_16_doubles_track_density(self):
        i_samples, q_samples = synth_capture(rpm=3000, ball_speed_mph=BALL_MPH)
        track32 = ripple.extract_ripple_track(i_samples, q_samples, BALL_MPH, hop=32)
        track16 = ripple.extract_ripple_track(i_samples, q_samples, BALL_MPH, hop=16)
        assert track16.n_windows > 1.9 * track32.n_windows

    def test_magnitude_track_is_positive_during_flight(self):
        i_samples, q_samples = synth_capture(rpm=3000, ball_speed_mph=BALL_MPH)
        track = ripple.extract_ripple_track(i_samples, q_samples, BALL_MPH, hop=32)
        active = track.times_ms > 12.0
        assert np.all(track.magnitude[active] > 0)

    def test_extreme_high_ball_speed_returns_empty_track(self):
        i_samples, q_samples = synth_capture(rpm=3000, ball_speed_mph=BALL_MPH)
        track = ripple.extract_ripple_track(i_samples, q_samples, 230.0, hop=32)
        assert track.n_windows == 0

    def test_negative_ball_speed_returns_empty_track(self):
        i_samples, q_samples = synth_capture(rpm=3000, ball_speed_mph=BALL_MPH)
        track = ripple.extract_ripple_track(i_samples, q_samples, -5.0, hop=32)
        assert track.n_windows == 0


class TestTrimToBallWindow:
    def _track(self, rpm=3000, onset_ms=8.0, visible_ms=None):
        i_samples, q_samples = synth_capture(
            rpm=rpm, ball_speed_mph=BALL_MPH, onset_ms=onset_ms, visible_ms=visible_ms
        )
        return ripple.extract_ripple_track(i_samples, q_samples, BALL_MPH, hop=32)

    def test_trims_windows_before_onset(self):
        track = self._track(onset_ms=20.0)
        trimmed = ripple.trim_to_ball_window(track, ball_timestamp_ms=20.0, hop=32)
        assert trimmed.n_windows < track.n_windows
        assert trimmed.times_ms[0] >= 20.0

    def test_trims_after_signal_collapse(self):
        track = self._track(onset_ms=8.0, visible_ms=50.0)
        trimmed = ripple.trim_to_ball_window(track, ball_timestamp_ms=8.0, hop=32)
        # Signal dies at ~58 ms; the trimmed track must not extend far past it.
        assert trimmed.times_ms[-1] < 75.0

    def test_keeps_full_track_when_signal_persists(self):
        track = self._track(onset_ms=8.0, visible_ms=None)
        trimmed = ripple.trim_to_ball_window(track, ball_timestamp_ms=8.0, hop=32)
        kept = track.times_ms >= 8.0
        assert trimmed.n_windows == int(np.sum(kept))

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

    def test_onset_beyond_capture_returns_empty(self):
        track = self._track(onset_ms=8.0)
        trimmed = ripple.trim_to_ball_window(track, ball_timestamp_ms=99999.0, hop=32)
        assert trimmed.n_windows == 0

    def test_short_track_returned_untrimmed(self):
        # Build a short track (~5 windows) shorter than ref_n (~15 windows at hop=32)
        short_track = ripple.RippleTrack(
            times_ms=np.arange(5.0),
            freq_hz=np.full(5, 10000.0),
            magnitude=np.ones(5),
        )
        trimmed = ripple.trim_to_ball_window(short_track, ball_timestamp_ms=0.0, hop=32)
        assert trimmed.n_windows == 5

    def test_zero_reference_returns_full_track(self):
        # Build a track with all-zero magnitude; zero reference → returned as-is
        zero_track = ripple.RippleTrack(
            times_ms=np.arange(30.0),
            freq_hz=np.full(30, 10000.0),
            magnitude=np.zeros(30),
        )
        trimmed = ripple.trim_to_ball_window(zero_track, ball_timestamp_ms=0.0, hop=32)
        assert trimmed.n_windows == 30


TRACK_RATE = 30000 / 32  # 937.5 Hz


def _sine_track(freq_hz, n=110, rate=TRACK_RATE, amp=1.0, noise=0.02, seed=3):
    rng = np.random.default_rng(seed)
    t = np.arange(n) / rate
    return amp * np.sin(2 * np.pi * freq_hz * t) + rng.normal(0, noise, n)


class TestDetectRippleSpin:
    def test_recovers_clean_seam_tone(self):
        result = ripple.detect_ripple_spin(_sine_track(60.0), TRACK_RATE)
        assert result.detected
        assert abs(result.spin_rpm - 3600.0) < 150.0
        assert result.snr >= ripple.SNR_MIN
        assert result.persistent

    def test_rejects_track_too_short(self):
        result = ripple.detect_ripple_spin(_sine_track(60.0, n=12), TRACK_RATE)
        assert not result.detected
        assert "short" in result.rejection_reason

    def test_rejects_flat_track(self):
        result = ripple.detect_ripple_spin(np.ones(110), TRACK_RATE)
        assert not result.detected

    def test_rejects_pure_noise(self):
        rng = np.random.default_rng(7)
        result = ripple.detect_ripple_spin(rng.normal(0, 1, 110), TRACK_RATE)
        assert not result.detected

    def test_cubic_drift_alone_is_not_spin(self):
        # Deceleration-style smooth drift, no seam tone.
        t = np.linspace(0, 1, 110)
        drift = 500.0 * t - 180.0 * t**2 + 40.0 * t**3
        result = ripple.detect_ripple_spin(drift, TRACK_RATE)
        assert not result.detected

    def test_persistence_rejects_frequency_shift(self):
        # Known blind spot (found while implementing this test): a transient
        # tone whose remaining track is near-silent can spuriously pass this
        # gate, because the full-track polynomial detrend leaves residue
        # that leaks into the tolerance window around the picked peak — a
        # structural property shared with the production envelope check.
        # Real-data impact is judged by the experiment results, not this
        # suite. This test instead exercises the gate's actual mechanism
        # deterministically: a real tone stays pinned at the same frequency
        # in every half, so a track whose frequency shifts partway through
        # must be rejected regardless of which half's peak the full-track
        # FFT locks onto.
        half = 55
        n = 110
        t_first = np.arange(half) / TRACK_RATE
        t_second = np.arange(half, n) / TRACK_RATE
        track = np.concatenate(
            [np.sin(2 * np.pi * 60.0 * t_first), np.sin(2 * np.pi * 120.0 * t_second)]
        )
        rng = np.random.default_rng(9)
        result = ripple.detect_ripple_spin(track + rng.normal(0, 0.02, n), TRACK_RATE)
        assert not result.detected

    def test_prior_recovers_fundamental_over_stronger_harmonic(self):
        fundamental = _sine_track(55.0, amp=0.8, noise=0.0)
        harmonic = _sine_track(110.0, amp=1.0, noise=0.0, seed=4)
        rng = np.random.default_rng(5)
        track = fundamental + harmonic + rng.normal(0, 0.02, 110)
        without_prior = ripple.detect_ripple_spin(track, TRACK_RATE)
        with_prior = ripple.detect_ripple_spin(
            track, TRACK_RATE, expected_spin_rpm=3300.0
        )
        assert abs(without_prior.spin_rpm - 6600.0) < 200.0
        assert abs(with_prior.spin_rpm - 3300.0) < 200.0

#!/usr/bin/env python3
"""Spin estimation from overlapped-STFT speed-track ripple (offline).

The golf ball seam modulates the radar return once per revolution: in
amplitude (AM — what production detect_spin sees via the bandpass envelope)
and in apparent Doppler frequency (FM — the "ripple in the speed reports"
described by OmniPreSense when the main speed FFT runs with overlapped
128-sample windows). This module extracts both tracks from an overlapped
STFT and recovers the seam tone from either, mirroring the production
detect_spin gates (seam band, SNR floor, split-half persistence, rails,
minimum cycles) so results are comparable.

Offline/experimental only — nothing in src/ imports this.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Radar/STFT constants — must match RollingBufferProcessor.
SAMPLE_RATE = 30000
WINDOW_SIZE = 128
FFT_SIZE = 4096
WAVELENGTH_M = 0.01243
MPS_TO_MPH = 2.23694

# Ball-tone tracking band around the detected ball speed.
TOLERANCE_MPH = 8.0

# Seam-tone gates — values mirror production detect_spin.
MIN_SEAM_HZ = 33.0            # ~2000 RPM
MAX_SEAM_HZ = 200.0           # 12000 RPM
TRACK_FFT_SIZE = 8192         # zero-padded ripple FFT
DETREND_POLY_ORDER = 3
SNR_MIN = 2.5
MIN_CYCLES = 2.0
MIN_TRACK_DURATION_MS = 20.0  # mirrors SPIN_MIN_SAMPLES (600 samples @ 30 ksps)
RAIL_GUARD_NATURAL_BINS = 2   # rail margin in natural-resolution bins

# Ball-signal-loss trim — production constants, expressed in raw samples;
# converted to track windows by hop at use time.
SIGNAL_LOSS_SMOOTH_SAMPLES = 90
SIGNAL_LOSS_REF_SAMPLES = 450
SIGNAL_LOSS_THRESHOLD = 0.15
SIGNAL_LOSS_HOLD_SAMPLES = 150

# Expected-spin prior disambiguation — production thresholds.
PRIOR_MIN_RELATIVE_MAG = 0.40
PRIOR_MAX_RELATIVE_ERROR = 0.55
PRIOR_STRONGEST_FAR_ERROR = 0.45


def _mph_to_hz(mph: float) -> float:
    return 2 * (mph / MPS_TO_MPH) / WAVELENGTH_M


@dataclass
class RippleTrack:
    """Per-window ball-tone measurements from the overlapped STFT."""

    times_ms: np.ndarray   # window-center timestamps
    freq_hz: np.ndarray    # interpolated ball-peak frequency (FM ripple)
    magnitude: np.ndarray  # ball-peak magnitude (AM ripple)

    @property
    def n_windows(self) -> int:
        return len(self.times_ms)


def extract_ripple_track(
    i_samples,
    q_samples,
    ball_speed_mph: float,
    hop: int,
) -> RippleTrack:
    """Track the ball tone through every overlapped STFT window.

    Unlike process_overlapping, no CFAR/threshold gates are applied: the
    peak inside a ±TOLERANCE_MPH band around the expected ball frequency is
    taken in every window so the track is continuous. Peak frequency is
    refined by parabolic interpolation on the zero-padded spectrum.
    """
    i_data = np.asarray(i_samples, dtype=np.float64)
    q_data = np.asarray(q_samples, dtype=np.float64)
    hann = np.hanning(WINDOW_SIZE)

    # Guard against invalid or out-of-range ball speeds.
    if ball_speed_mph <= 0:
        return RippleTrack(
            times_ms=np.array([]),
            freq_hz=np.array([]),
            magnitude=np.array([]),
        )

    bin_hz = SAMPLE_RATE / FFT_SIZE
    center_hz = _mph_to_hz(ball_speed_mph)
    tol_hz = _mph_to_hz(TOLERANCE_MPH)
    lo_bin = max(1, int(np.floor((center_hz - tol_hz) / bin_hz)))
    hi_bin = min(FFT_SIZE // 2 - 2, int(np.ceil((center_hz + tol_hz) / bin_hz)))

    # Guard against out-of-range ball speeds that result in empty search band.
    if hi_bin < lo_bin:
        return RippleTrack(
            times_ms=np.array([]),
            freq_hz=np.array([]),
            magnitude=np.array([]),
        )

    times, freqs, mags = [], [], []
    for start in range(0, len(i_data) - WINDOW_SIZE + 1, hop):
        i_block = i_data[start : start + WINDOW_SIZE]
        q_block = q_data[start : start + WINDOW_SIZE]
        block = (i_block - i_block.mean()) + 1j * (q_block - q_block.mean())
        spectrum = np.abs(np.fft.fft(block * hann, FFT_SIZE))

        band = spectrum[lo_bin : hi_bin + 1]
        peak_bin = int(np.argmax(band)) + lo_bin

        # Parabolic interpolation on the peak and its neighbors.
        y0, y1, y2 = spectrum[peak_bin - 1], spectrum[peak_bin], spectrum[peak_bin + 1]
        denom = y0 - 2 * y1 + y2
        delta = 0.5 * (y0 - y2) / denom if denom != 0 else 0.0
        delta = float(np.clip(delta, -0.5, 0.5))

        times.append((start + WINDOW_SIZE / 2) / SAMPLE_RATE * 1000)
        freqs.append((peak_bin + delta) * bin_hz)
        mags.append(float(y1))

    return RippleTrack(
        times_ms=np.array(times),
        freq_hz=np.array(freqs),
        magnitude=np.array(mags),
    )

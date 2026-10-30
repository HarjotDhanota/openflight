"""The OPS243 readings around impact, kept on the shot as raw evidence.

Outbound readings from impact to ``MAX_REFERRAL_MS`` after it, on the OPS's own clock,
with the impact time, its source and uncertainty, and the club pick: the one FFT window
the processor read the club speed from (its start and radial speed). The coherent
club path's observed peaks retain their individual FFT support, without filling
gaps or claiming that overlapping windows are independent. Holds no model of its own.
"""

from __future__ import annotations

import math
from typing import Any, Optional

from .ballistics import MPH_TO_MPS
from .numeric import finite as _finite

# Later readings are not used: the referral error grows with time after impact.
MAX_REFERRAL_MS = 30.0

# Impact tolerance for a sound trigger: its jitter and latency are unmeasured, so wide.
SOUND_TRIGGER_IMPACT_TOLERANCE_MS = 3.0


def _club_window(processed: Any) -> tuple[Optional[dict], Optional[str]]:
    """Copy exact sample support for the selected coherent spectral observation."""
    motion = getattr(processed, "motion", None)
    if motion is None or motion.club is None:
        return None, "no coherent spectral club observation"
    capture = getattr(processed, "capture", None)
    if capture is None:
        return None, "the selected club observation has no raw capture"
    config = getattr(processed, "processor_config", None) or {}
    policy = config.get("motion_policy") or {}
    constants = config.get("constants") or {}
    rate = config.get("sample_rate_hz")
    wavelength = constants.get("WAVELENGTH_M")
    size, hop = policy.get("window_samples"), policy.get("hop_samples")
    fft_size = constants.get("FFT_SIZE")
    if (
        not _finite(rate)
        or rate <= 0
        or not _finite(wavelength)
        or wavelength <= 0
        or not isinstance(size, int)
        or size != motion.window_samples
        or size <= 0
        or not isinstance(hop, int)
        or hop <= 0
        or not isinstance(fft_size, int)
        or fft_size < size
        or policy.get("window_function") != "hamming"
    ):
        return None, "the selected club observation has no matching FFT processing metadata"
    peak = motion.club.peaks[motion.club.reference_index]
    start = int(peak.frame) * hop
    expected = (peak.time_ms * rate / 1000.0) - size / 2
    if start < 0 or not math.isclose(start, expected, rel_tol=0.0, abs_tol=1e-6):
        return None, "the selected club frame and timestamp disagree on its sample support"
    stop = start + size
    i_values, q_values = capture.i_samples, capture.q_samples
    if len(i_values) != len(q_values) or stop > len(i_values):
        return None, "the selected club window is not fully present in the raw capture"
    i_samples, q_samples = list(i_values[start:stop]), list(q_values[start:stop])
    if not all(_finite(value) for value in (*i_samples, *q_samples)):
        return None, "the selected club window contains nonfinite samples"
    return {
        "clock": "ops_capture",
        "capture_id": getattr(capture, "capture_id", None),
        "frame": int(peak.frame),
        "sample_start": start,
        "sample_stop": stop,
        "sample_rate_hz": float(rate),
        "wavelength_m": float(wavelength),
        "fft_size": fft_size,
        "window_function": policy["window_function"],
        "i_samples": i_samples,
        "q_samples": q_samples,
        "point_identity": "unqualified_spectral_scatterer",
    }, None


def ops_speed_track(processed: Any, *, max_referral_ms: float = MAX_REFERRAL_MS) -> Optional[dict]:
    """The OPS readings around impact from a processed capture, or None without an impact.

    Timestamps are FFT window starts, milliseconds from the capture start.
    """
    impact = getattr(processed, "impact", None)
    impact_ms = getattr(impact, "timestamp_ms", None)
    timeline = getattr(processed, "timeline", None)
    if impact is None or not _finite(impact_ms) or timeline is None:
        return None
    capture = getattr(processed, "capture", None)
    if impact.source == "sound_trigger" and getattr(capture, "trigger_type", None) in (
        "hardware",
        "speed",
    ):
        return None
    config = getattr(processed, "processor_config", None) or {}
    constants = config.get("constants") or {}
    sample_rate = float(config.get("sample_rate_hz") or 30000.0)
    window = int(constants.get("WINDOW_SIZE") or 128)
    fft_size = int(constants.get("FFT_SIZE") or 4096)
    step = int(constants.get("STEP_SIZE_OVERLAP") or 32)
    wavelength = float(constants.get("WAVELENGTH_M") or 0.01243)
    window_ms = 1000.0 * window / sample_rate
    if impact.source == "ops_transition":
        gap = impact.transition_gap_ms if _finite(impact.transition_gap_ms) else 0.0
        uncertainty = max(0.5 * gap, 500.0 * step / sample_rate)
    else:
        uncertainty = SOUND_TRIGGER_IMPACT_TOLERANCE_MS
    outbound = [
        [float(reading.timestamp_ms), float(reading.speed_mph)]
        for reading in timeline.readings
        if reading.is_outbound
        and impact_ms - window_ms <= reading.timestamp_ms <= impact_ms + max_referral_ms
    ]
    club_ms = getattr(processed, "club_timestamp_ms", None)
    club_mph = getattr(processed, "club_speed_mph", None)
    club_reading = (
        {"window_start_ms": float(club_ms), "radial_mph": float(club_mph)}
        if _finite(club_ms) and _finite(club_mph)
        else None
    )
    motion = getattr(processed, "motion", None)
    club_readings = []
    if motion is not None and motion.club is not None:
        club_window_ms = 1000.0 * motion.window_samples / sample_rate
        club_readings = [
            {
                "frame": int(peak.frame),
                "window_start_ms": float(peak.time_ms - 0.5 * club_window_ms),
                "window_ms": club_window_ms,
                "radial_mph": float(peak.speed_mph),
                "snr": float(peak.snr),
            }
            for peak in motion.club.peaks
        ]
    if club_reading is not None and motion is not None and motion.club is not None:
        observation = motion.club.peaks[motion.club.reference_index]
        club_reading = {
            "window_start_ms": float(
                observation.time_ms - 500 * motion.window_samples / sample_rate
            ),
            "window_ms": float(1000 * motion.window_samples / sample_rate),
            "radial_mph": float(observation.speed_mph),
        }
    club_window, club_window_reason = _club_window(processed)
    return {
        "clock": "ops_capture",
        "timestamps": "fft_window_start_ms_from_capture_start",
        "impact_ms": float(impact_ms),
        "impact_source": impact.source,
        "impact_uncertainty_ms": uncertainty,
        "window_ms": window_ms,
        "radial_resolution_mph": 0.5 * (sample_rate / fft_size) * (wavelength / 2.0) / MPH_TO_MPS,
        "canonical_radial_mph": float(processed.ball_speed_mph),
        "outbound": outbound,
        "club_reading": club_reading,
        "club_readings": club_readings,
        "club_readings_source": "coherent_spectral_path" if club_readings else None,
        "club_window": club_window,
        "club_window_reason": club_window_reason,
    }

"""The OPS243 readings around impact, kept on the shot as raw evidence.

Outbound readings from impact to ``MAX_REFERRAL_MS`` after it, on the OPS's own clock,
with the impact time, its source and uncertainty, and the club pick: the one FFT window
the processor read the club speed from (its start and radial speed). Holds no model of
its own.
"""

from __future__ import annotations

from typing import Any, Optional

from .ballistics import MPH_TO_MPS
from .numeric import finite as _finite

# Later readings are not used: the referral error grows with time after impact.
MAX_REFERRAL_MS = 30.0

# Impact tolerance for a sound trigger: its jitter and latency are unmeasured, so wide.
SOUND_TRIGGER_IMPACT_TOLERANCE_MS = 3.0


def ops_speed_track(processed: Any, *, max_referral_ms: float = MAX_REFERRAL_MS) -> Optional[dict]:
    """The OPS readings around impact from a processed capture, or None without an impact.

    Timestamps are FFT window starts, milliseconds from the capture start.
    """
    impact = getattr(processed, "impact", None)
    impact_ms = getattr(impact, "timestamp_ms", None)
    timeline = getattr(processed, "timeline", None)
    if impact is None or not _finite(impact_ms) or timeline is None:
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
    }

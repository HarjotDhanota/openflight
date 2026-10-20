"""The OPS243 readings around impact, kept on the shot as raw evidence.

A processed rolling-buffer capture is reduced to its outbound readings from the
window that starts at impact up to ``MAX_REFERRAL_MS`` after it, all on the OPS's
own clock (window starts, milliseconds from the capture start), with the impact
time, its source and how sure it is. Models that project the radial speed read
this track; it holds no model of its own.
"""

from __future__ import annotations

import math
from typing import Any, Optional

MPH_TO_MPS = 0.44704

# Readings later than this after impact are not used: the referral and the
# ball's straight-line position both grow with time, and an early reading is
# what "referred to separation" means.
MAX_REFERRAL_MS = 30.0

# A sound-trigger impact (no OPS club-to-ball transition) after the acoustic
# walk-back: the trigger's own jitter and detector latency are not measured on
# this rig, so the tolerance is stated wide.
SOUND_TRIGGER_IMPACT_TOLERANCE_MS = 3.0


def _finite(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def ops_speed_track(processed: Any, *, max_referral_ms: float = MAX_REFERRAL_MS) -> Optional[dict]:
    """The OPS readings around impact from a processed capture, all on the OPS's own clock.

    Every outbound reading from a window that starts within a window of the
    impact up to ``max_referral_ms`` after it; timestamps are window starts,
    milliseconds from the capture start. None when the capture has no impact.
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
    }

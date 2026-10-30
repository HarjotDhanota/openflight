"""Raw ADC support qualification; invalid regions are never interpolated here."""

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class InvalidInterval:
    """Half-open raw-sample interval unavailable for measurement."""

    start_sample: int
    end_sample: int
    reason: str


@dataclass
class CaptureQuality:
    """Per-sample validity and a declared spectral-window eligibility policy."""

    invalid_intervals: tuple[InvalidInterval, ...]
    near_rail: np.ndarray
    invalid: np.ndarray
    max_rail_fraction: float

    def supports_window(self, start: int, end: int) -> bool:
        """Require valid samples and limit near-rail exposure in the whole window."""
        return (
            0 <= start < end <= len(self.invalid)
            and not self.invalid[start:end].any()
            and np.count_nonzero(self.near_rail[start:end])
            <= (end - start) * self.max_rail_fraction
        )


def _runs(mask):
    edges = np.diff(np.r_[False, mask, False].astype(np.int8))
    return zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1))


def assess_capture(i_samples, q_samples, *, rail_margin, max_rail_fraction, zero_run_samples):
    """Separate zero-filled/invalid samples from near-rail ADC observations."""
    i_values = np.asarray(i_samples, dtype=float)
    q_values = np.asarray(q_samples, dtype=float)
    if i_values.ndim != 1 or i_values.shape != q_values.shape:
        raise ValueError("I/Q samples must be matching one-dimensional arrays")
    invalid = (
        ~np.isfinite(i_values)
        | ~np.isfinite(q_values)
        | (i_values < 0)
        | (q_values < 0)
        | (i_values > 4095)
        | (q_values > 4095)
    )
    intervals = [
        InvalidInterval(int(start), int(end), "invalid_adc") for start, end in _runs(invalid)
    ]
    for start, end in _runs((i_values == 0) & (q_values == 0)):
        if end - start >= zero_run_samples:
            invalid[start:end] = True
            intervals.append(InvalidInterval(int(start), int(end), "zero_filled"))
    near_rail = (
        (i_values <= rail_margin)
        | (q_values <= rail_margin)
        | (i_values >= 4095 - rail_margin)
        | (q_values >= 4095 - rail_margin)
    )
    return CaptureQuality(
        tuple(sorted(intervals, key=lambda interval: interval.start_sample)),
        near_rail,
        invalid,
        max_rail_fraction,
    )

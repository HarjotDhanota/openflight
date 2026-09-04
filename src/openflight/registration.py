"""In-situ inter-sensor registration from live shots.

The camera and the IWR6843 each measure the ball's launch direction. Their
difference on any one shot is noise plus a constant: the angular offset
between the two sensors' datums (mount pitch, array phase, lens boresight).
Across a session the noise averages out and the constant remains -- that
constant is the registration, and it is measured from the golfer's own shots,
never from a calibration gesture. TrackMan runs the same idea continuously
(US 11,619,708), so the estimate tracks a bumped unit as well as a fresh one.

What this module does NOT do: apply the offset. The registration is the
observed camera-minus-radar difference; which sensor holds the error is not
knowable from the pair alone, and the LCMF's elevation moves ~2x per degree of
physical tilt, so mapping the observation onto either sensor's calibration
needs the absolute reference (inclinometer, surveyed boresight) the 2026-09-01
audit called for. Until then the offset is reported with its spread, and a
consumer that chooses to correct one sensor must say so in its provenance.

Sign conventions are the pipeline's own: elevation is the camera vertical
launch minus the LCMF vertical (`ball_flight.VerticalLaunchDecision.
lcmf_delta_deg`), azimuth is the camera horizontal minus the IWR horizontal
(`ball_flight.HorizontalFusionDecision.camera_iwr_delta_deg`).
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np

# Fewer shots than this and one bad detection can own the median.
MIN_SHOTS = 8
# A robust spread wider than this is not a datum: the two sensors disagree
# shot by shot, and a single offset would hide that rather than fix it.
MAX_SPREAD_DEG = 2.0
# Robust sd from a MAD assumes a normal core; the median's standard error is
# sqrt(pi/2) times the mean's.
_MAD_TO_SD = 1.4826
_MEDIAN_SE_FACTOR = 1.2533

ELEVATION = "elevation"
AZIMUTH = "azimuth"


@dataclass(frozen=True)
class RegistrationSample:
    """One shot's camera-minus-radar differences; either axis may be missing."""

    shot: int | str
    elevation_delta_deg: float | None = None
    azimuth_delta_deg: float | None = None

    def delta(self, axis: str) -> float | None:
        """The difference on ``axis``, or None when that axis was not measured."""
        if axis == ELEVATION:
            return self.elevation_delta_deg
        if axis == AZIMUTH:
            return self.azimuth_delta_deg
        raise ValueError(f"unknown axis {axis!r}")


@dataclass(frozen=True)
class AxisRegistration:
    """The registered offset on one axis, or the named reason there is none.

    ``offset_deg`` is populated only when ``status`` is ``"registered"``.
    ``spread_deg`` and ``n`` are reported whenever at least two shots
    contributed, so a withheld axis still says how far it was from passing.
    """

    axis: str
    status: str
    n: int
    offset_deg: float | None = None
    spread_deg: float | None = None
    standard_error_deg: float | None = None
    reason: str = ""


@dataclass(frozen=True)
class Registration:
    """Both axes, from one set of samples."""

    elevation: AxisRegistration
    azimuth: AxisRegistration
    n_samples: int

    def axis(self, axis: str) -> AxisRegistration:
        """Look one axis up by name."""
        if axis == ELEVATION:
            return self.elevation
        if axis == AZIMUTH:
            return self.azimuth
        raise ValueError(f"unknown axis {axis!r}")


def _finite(values: Iterable[float | None]) -> np.ndarray:
    return np.asarray(
        [float(v) for v in values if v is not None and math.isfinite(float(v))],
        dtype=float,
    )


def register_axis(
    values: Iterable[float | None],
    axis: str,
    *,
    min_shots: int = MIN_SHOTS,
    max_spread_deg: float = MAX_SPREAD_DEG,
) -> AxisRegistration:
    """Register one axis from per-shot differences.

    The offset is the median; the spread is the MAD scaled to a standard
    deviation, which one wild shot cannot inflate. Refusal is by name.
    """
    usable = _finite(values)
    n = int(len(usable))
    if n == 0:
        return AxisRegistration(axis, "withheld_insufficient_shots", 0, reason="no shots")
    median = float(np.median(usable))
    spread = float(_MAD_TO_SD * np.median(np.abs(usable - median))) if n >= 2 else None
    standard_error = (
        float(_MEDIAN_SE_FACTOR * spread / math.sqrt(n)) if spread is not None else None
    )
    if n < min_shots:
        return AxisRegistration(
            axis,
            "withheld_insufficient_shots",
            n,
            spread_deg=spread,
            standard_error_deg=standard_error,
            reason=f"{n} shots, need {min_shots}",
        )
    if spread is not None and spread > max_spread_deg:
        return AxisRegistration(
            axis,
            "withheld_unstable",
            n,
            spread_deg=spread,
            standard_error_deg=standard_error,
            reason=f"spread {spread:.2f} deg exceeds {max_spread_deg:.2f}: not a constant datum",
        )
    return AxisRegistration(
        axis,
        "registered",
        n,
        offset_deg=median,
        spread_deg=spread,
        standard_error_deg=standard_error,
    )


def register(
    samples: Sequence[RegistrationSample],
    *,
    min_shots: int = MIN_SHOTS,
    max_spread_deg: float = MAX_SPREAD_DEG,
) -> Registration:
    """Register both axes from a batch of shots."""
    return Registration(
        elevation=register_axis(
            (s.elevation_delta_deg for s in samples),
            ELEVATION,
            min_shots=min_shots,
            max_spread_deg=max_spread_deg,
        ),
        azimuth=register_axis(
            (s.azimuth_delta_deg for s in samples),
            AZIMUTH,
            min_shots=min_shots,
            max_spread_deg=max_spread_deg,
        ),
        n_samples=len(samples),
    )


# A bump moves the datum by degrees at once; this many trailing shots must
# agree before the tracker calls it, so one stray detection is not a bump.
JUMP_SHOTS = 5
JUMP_DEG = 2.0


class RegistrationTracker:
    """Always-on registration over the most recent shots.

    Every shot refreshes the estimate over a sliding window. A ``jump`` on an
    axis means the trailing `JUMP_SHOTS` shots sit more than `JUMP_DEG` from
    the offset registered on the shots before them: the unit was moved, or
    something else changed the datum. The tracker reports it; deciding what
    to do about it belongs to the setup UI.
    """

    def __init__(
        self,
        *,
        window: int = 30,
        min_shots: int = MIN_SHOTS,
        max_spread_deg: float = MAX_SPREAD_DEG,
        jump_shots: int = JUMP_SHOTS,
        jump_deg: float = JUMP_DEG,
    ) -> None:
        if window < min_shots + jump_shots:
            raise ValueError("window must hold the registration base plus the jump test")
        self._samples: deque[RegistrationSample] = deque(maxlen=window)
        self._min_shots = min_shots
        self._max_spread_deg = max_spread_deg
        self._jump_shots = jump_shots
        self._jump_deg = jump_deg

    def add(self, sample: RegistrationSample) -> Registration:
        """Ingest one shot and return the refreshed registration."""
        self._samples.append(sample)
        return self.current()

    def current(self) -> Registration:
        """The registration over the window as it stands."""
        return register(
            list(self._samples),
            min_shots=self._min_shots,
            max_spread_deg=self._max_spread_deg,
        )

    def jump(self, axis: str) -> bool:
        """Whether the newest shots have left the datum the earlier ones set."""
        samples = list(self._samples)
        if len(samples) < self._min_shots + self._jump_shots:
            return False
        base = register_axis(
            (s.delta(axis) for s in samples[: -self._jump_shots]),
            axis,
            min_shots=self._min_shots,
            max_spread_deg=self._max_spread_deg,
        )
        if base.status != "registered":
            return False
        trailing = _finite(s.delta(axis) for s in samples[-self._jump_shots :])
        if len(trailing) < self._jump_shots:
            return False
        return abs(float(np.median(trailing)) - float(base.offset_deg)) > self._jump_deg

    def __len__(self) -> int:
        return len(self._samples)


__all__ = [
    "AZIMUTH",
    "ELEVATION",
    "JUMP_DEG",
    "JUMP_SHOTS",
    "MAX_SPREAD_DEG",
    "MIN_SHOTS",
    "AxisRegistration",
    "Registration",
    "RegistrationSample",
    "RegistrationTracker",
    "register",
    "register_axis",
]

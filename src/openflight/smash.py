"""Smash factor: ball speed divided by club speed."""

from __future__ import annotations

import math
from typing import Optional

# Generous ceiling above the ~1.5 a conforming head can reach under the USGA/R&A COR
# limit of 0.830, with margin for radial-vs-total readings: a smash above it means the
# OPS picked the wrong club reading. Below MIN_PLAUSIBLE_SMASH the ball never left the
# face at speed, or the club reading is not the club's.
MAX_PLAUSIBLE_SMASH = 1.70
MIN_PLAUSIBLE_SMASH = 0.5


def smash_factor(ball_speed: Optional[float], club_speed: Optional[float]) -> Optional[float]:
    """Smash factor: ball speed divided by club speed (dimensionless).

    https://www.trackman.com/blog/smash-factor. Both speeds must be full speeds in the same
    unit (a radial ratio is labelled ``launch_monitor.SMASH_RADIAL_RATIO``); None for a
    missing speed, a non-positive club speed or a negative ball speed.
    """
    if ball_speed is None or club_speed is None:
        return None
    if not math.isfinite(club_speed) or club_speed <= 0.0:
        return None
    if ball_speed < 0.0:
        return None
    return ball_speed / club_speed


def implausible_smash_reason(
    ball_speed: Optional[float], club_speed: Optional[float]
) -> Optional[str]:
    """Why this pair's smash cannot be a golf shot's, or None (plausible or undefined).

    The ball speed is the validated reading, so the club reading takes the blame
    (``implausible_club_speed_reason``); a non-finite ratio is left to the callers'
    own non-finite handling.
    """
    value = smash_factor(ball_speed, club_speed)
    if value is None or not math.isfinite(value):
        return None
    if value > MAX_PLAUSIBLE_SMASH:
        return (
            f"smash {value:.2f} is above the plausible ceiling {MAX_PLAUSIBLE_SMASH:.2f}: "
            "the club reading is not the club's"
        )
    if value < MIN_PLAUSIBLE_SMASH:
        return (
            f"smash {value:.2f} is below the plausible floor {MIN_PLAUSIBLE_SMASH:.2f}: "
            "the club reading is not the club's"
        )
    return None


def implausible_club_speed_reason(
    ball_speed: Optional[float], club_speed: Optional[float]
) -> Optional[str]:
    """Why a club speed is experimental: its smash against the ball speed, or None."""
    if implausible_smash_reason(ball_speed, club_speed) is None:
        return None
    value = smash_factor(ball_speed, club_speed)
    return f"implausible against the ball speed (smash {value:.2f})"

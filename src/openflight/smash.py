"""Smash factor: ball speed divided by club speed (D37)."""

from __future__ import annotations

import math
from typing import Optional


def smash_factor(ball_speed: Optional[float], club_speed: Optional[float]) -> Optional[float]:
    """Smash factor: ball speed divided by club speed (dimensionless).

    Public definition: "Smash Factor is ball speed divided by club speed"
    (https://www.trackman.com/blog/smash-factor); "The ratio between the
    measured Ball Speed and Club Speed"
    (https://support.trackmangolf.com/hc/en-us/articles/39726314021915-Parameters-Smash-Factor-Tee-to-Green).

    Both speeds must be in the same unit, and must be the same kind of speed
    for the ratio to be TrackMan's smash factor: TrackMan's ball speed is the
    ball's full speed and its club speed the head's full speed. A ratio of two
    radar line-of-sight (radial) speeds is a different quantity, and callers
    must label it as such (``launch_monitor.SMASH_RADIAL_RATIO``; D21).

    Returns None when either speed is missing, when the club speed is not a
    positive finite number (no ratio is defined), or when the ball speed is
    negative. A non-finite ball speed gives a non-finite ratio.
    """
    if ball_speed is None or club_speed is None:
        return None
    if not math.isfinite(club_speed) or club_speed <= 0.0:
        return None
    if ball_speed < 0.0:
        return None
    return ball_speed / club_speed

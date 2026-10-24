"""Smash factor: ball speed divided by club speed."""

from __future__ import annotations

import math
from typing import Optional


def smash_factor(ball_speed: Optional[float], club_speed: Optional[float]) -> Optional[float]:
    """Smash factor: ball speed divided by club speed (dimensionless).

    Public definition: "Smash Factor is ball speed divided by club speed"
    (https://www.trackman.com/blog/smash-factor); "The ratio between the
    measured Ball Speed and Club Speed"
    (https://support.trackmangolf.com/hc/en-us/articles/39726314021915-Parameters-Smash-Factor-Tee-to-Green).

    Both speeds must be full speeds in the same unit; a ratio of radial speeds
    must be labelled as such (``launch_monitor.SMASH_RADIAL_RATIO``). Returns
    None for a missing speed, a non-positive club speed or a negative ball speed.
    """
    if ball_speed is None or club_speed is None:
        return None
    if not math.isfinite(club_speed) or club_speed <= 0.0:
        return None
    if ball_speed < 0.0:
        return None
    return ball_speed / club_speed

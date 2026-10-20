"""Bounce and roll on fairway turf: the distance from the landing point to rest.

The ball lands on turf that dents under it. Penner (2002), "The run of a golf
ball", Can. J. Phys. 80, 931-940 (doi:10.1139/p02-022), models the dent as a
local tilt of the impact plane toward the incoming ball; here the tilt grows with
the landing speed and peaks at a fitted landing angle. Each impact is a rigid
sphere bouncing with grip-slip friction (Cross 2002, "Grip-slip behavior of a
bouncing ball", Am. J. Phys. 70, 1093-1102, doi:10.1119/1.1507792): it slides
with friction mu unless friction can bring the contact point to rest, in which
case it leaves rolling. The normal restitution falls with the normal impact
speed, and the turf caps the speed the ball keeps along the surface. Hops are
ballistic; once a hop is under 1 cm the ball slides on the ground until it rolls,
then rolls out against a constant rolling resistance plus a speed-squared turf
drag (deceleration g c0 + k v^2), which integrates in closed form.

The constants were fitted by the maintainer (flight-fit-v2); see
``TURF_ROLL_V1.calibration``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from .numeric import finite as _finite

GRAVITY = 9.81
BALL_RADIUS_M = 0.02135
MPH_TO_MPS = 0.44704
M_TO_YD = 1.0 / 0.9144
MIN_HOP_M = 0.01
MAX_BOUNCES = 25


@dataclass(frozen=True)
class RollCalibration:
    """Provenance of a fitted bounce-and-roll model."""

    version: str
    source: str
    fitted_on: str  # ISO date
    domain_landing_angle_deg: tuple[float, float]
    domain: str
    method: str
    metrics: tuple[tuple[str, float], ...]  # (description, yd)


@dataclass(frozen=True)
class TurfRollModel:
    """A dented-turf bounce, grip-slip impacts and a closed-form roll-out."""

    version: str
    surface: str
    citation: str
    landing_angle_range_deg: tuple[float, float]
    tilt_max_rad: float
    tilt_speed_mps: float
    tilt_peak_rad: float
    tilt_width_rad: float
    restitution: float
    restitution_speed_mps: float
    friction: float
    tangential_cap_mps: float
    tangential_cap_normal_mps: float
    sliding_friction: float
    rolling_resistance: float
    turf_drag_per_m: float
    calibration: Optional[RollCalibration] = None

    def roll_yards(
        self, landing_angle_deg: float, landing_speed_mph: float, backspin_rpm: float
    ) -> Optional[float]:
        """Signed distance (yd) from landing to rest along the landing heading, or None.

        None for a non-finite input or a landing angle outside the fitted range.
        Negative when backspin pulls the ball back past its landing point.
        """
        if not all(_finite(x) for x in (landing_angle_deg, landing_speed_mph, backspin_rpm)):
            return None
        low, high = self.landing_angle_range_deg
        if not low <= landing_angle_deg <= high:
            return None
        return _rest_distance_m(self, landing_speed_mph, landing_angle_deg, backspin_rpm) * M_TO_YD


def _impact(m: TurfRollModel, vx: float, vy: float, om: float) -> tuple[float, float, float]:
    """One impact: incoming (vx, vy<0, forward-roll rate om) to outgoing."""
    speed = math.hypot(vx, vy)
    angle = math.atan2(-vy, abs(vx))
    tilt = (
        m.tilt_max_rad
        * (1.0 - math.exp(-speed / m.tilt_speed_mps))
        * math.exp(-(((angle - m.tilt_peak_rad) / m.tilt_width_rad) ** 2))
    )
    c, s = math.cos(tilt), math.sin(tilt)
    vt = vx * c + vy * s
    vn = vx * s - vy * c
    e = m.restitution / (1.0 + vn / m.restitution_speed_mps)
    slip = vt - BALL_RADIUS_M * om
    limit = m.friction * (1.0 + e) * vn
    if (2.0 / 7.0) * abs(slip) <= limit:
        impulse = (2.0 / 7.0) * slip
    else:
        impulse = math.copysign(limit, slip)
    vt -= impulse
    om += 2.5 * impulse / BALL_RADIUS_M
    cap = m.tangential_cap_mps / (1.0 + vn / m.tangential_cap_normal_mps)
    vt /= math.sqrt(1.0 + (vt / cap) ** 2)
    vn *= e
    return vt * c - vn * s, max(vt * s + vn * c, 0.0), om


def _rest_distance_m(
    m: TurfRollModel, speed_mph: float, angle_deg: float, spin_rpm: float
) -> float:
    a = math.radians(angle_deg)
    speed = speed_mph * MPH_TO_MPS
    vx, vy = speed * math.cos(a), -speed * math.sin(a)
    om = -spin_rpm * math.pi / 30.0
    x = 0.0
    for _ in range(MAX_BOUNCES):
        vx, up, om = _impact(m, vx, vy, om)
        if up * up / (2.0 * GRAVITY) < MIN_HOP_M:
            break
        x += vx * 2.0 * up / GRAVITY
        vy = -up
    slip = vx - BALL_RADIUS_M * om
    t_slide = abs(slip) / (3.5 * m.sliding_friction * GRAVITY)
    x += vx * t_slide - 0.5 * m.sliding_friction * GRAVITY * math.copysign(t_slide**2, slip)
    v_roll = (5.0 * vx + 2.0 * BALL_RADIUS_M * om) / 7.0
    k = m.turf_drag_per_m
    roll = math.log1p(k * v_roll * v_roll / (GRAVITY * m.rolling_resistance)) / (2.0 * k)
    return x + math.copysign(roll, v_roll)


TURF_ROLL_V1 = TurfRollModel(
    version="turf-roll-v1",
    surface="fairway, normal firmness",
    citation=(
        "turf-roll-v1: Penner 2002 dented-turf bounce (Can. J. Phys. 80, 931) with "
        "Cross 2002 grip-slip impacts (Am. J. Phys. 70, 1093), sliding then rolling "
        "out; constants fitted by the maintainer (flight-fit-v2)"
    ),
    landing_angle_range_deg=(5.0, 65.0),
    tilt_max_rad=0.602,
    tilt_speed_mps=25.2,
    tilt_peak_rad=0.768,
    tilt_width_rad=0.337,
    restitution=0.445,
    restitution_speed_mps=59.6,
    friction=0.135,
    tangential_cap_mps=22.3,
    tangential_cap_normal_mps=24.6,
    sliding_friction=0.353,
    rolling_resistance=0.0979,
    turf_drag_per_m=0.0407,
    calibration=RollCalibration(
        version="flight-fit-v2",
        source="fitted by the maintainer (flight-fit-v2) on fairway turf, normal firmness",
        fitted_on="2026-10-20",
        domain_landing_angle_deg=(5.0, 65.0),
        domain=(
            "landing speed 18-100 mph, landing angle 5-65 deg, backspin 0-11,000 rpm; "
            "4096 landing states, a random 20 % held out"
        ),
        method=(
            "scipy least_squares (soft L1, 2 yd) on the distance to rest, twelve "
            "constants, rounded to three significant figures"
        ),
        metrics=(("held-out landing states roll RMSE yd", 1.77),),
    ),
)

ROLL_MODELS: dict[str, TurfRollModel] = {TURF_ROLL_V1.version: TURF_ROLL_V1}

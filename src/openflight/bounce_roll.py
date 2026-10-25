"""Bounce and roll on fairway turf: the distance from the landing point to rest.

Dented-turf impacts (Penner 2002, Can. J. Phys. 80, 931) with grip-slip friction (Cross 2002,
Am. J. Phys. 70, 1093), then a closed-form roll-out at deceleration g c0 + k v^2. turf-roll-v2
flies the hops through the flight model; its constants are fitted (see ``calibration``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Optional

from .ballistics import BALL_RADIUS_M, GRAVITY, MPH_TO_MPS
from .numeric import finite as _finite

M_TO_YD = 1.0 / 0.9144
MIN_HOP_M = 0.01
MAX_BOUNCES = 25

HopFlight = Callable[[float, float, float], tuple[float, float, float, float]]
"""(vx, vz, om) leaving the turf to (distance m, vx, vz, om) at the next touchdown.

vx is along the landing heading, vz up (m/s); om is the spin about the horizontal
axis, positive rolling forward (rad/s).
"""


def vacuum_hop(vx: float, vz: float, om: float) -> tuple[float, float, float, float]:
    """A ballistic hop without air: it lands as it left, mirrored."""
    return vx * 2.0 * vz / GRAVITY, vx, -vz, om


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
    aerodynamic_hops: bool = False

    def roll_yards(
        self,
        landing_angle_deg: float,
        landing_speed_mph: float,
        backspin_rpm: float,
        fly_hop: Optional[HopFlight] = None,
    ) -> Optional[float]:
        """Signed distance (yd) from landing to rest along the landing heading, or None.

        None for a non-finite input or a landing angle outside the fitted range; negative
        when backspin pulls the ball back. Aerodynamic hops fly with ``fly_hop``.
        """
        if self.aerodynamic_hops and fly_hop is None:
            raise ValueError(f"{self.version} flies its hops through the air: pass a hop flight")
        if not all(_finite(x) for x in (landing_angle_deg, landing_speed_mph, backspin_rpm)):
            return None
        low, high = self.landing_angle_range_deg
        if not low <= landing_angle_deg <= high:
            return None
        hop = fly_hop if self.aerodynamic_hops else vacuum_hop
        return (
            _rest_distance_m(self, landing_speed_mph, landing_angle_deg, backspin_rpm, hop)
            * M_TO_YD
        )


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
    m: TurfRollModel, speed_mph: float, angle_deg: float, spin_rpm: float, hop: HopFlight
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
        dx, vx, vy, om = hop(vx, up, om)
        x += dx
    slip = vx - BALL_RADIUS_M * om
    t_slide = abs(slip) / (3.5 * m.sliding_friction * GRAVITY)
    x += vx * t_slide - 0.5 * m.sliding_friction * GRAVITY * math.copysign(t_slide**2, slip)
    v_roll = (5.0 * vx + 2.0 * BALL_RADIUS_M * om) / 7.0
    k = m.turf_drag_per_m
    roll = math.log1p(k * v_roll * v_roll / (GRAVITY * m.rolling_resistance)) / (2.0 * k)
    return x + math.copysign(roll, v_roll)


TURF_ROLL_V2 = TurfRollModel(
    version="turf-roll-v2",
    surface="fairway, normal firmness",
    citation=(
        "turf-roll-v2: Penner 2002 dented-turf bounce (Can. J. Phys. 80, 931) with "
        "Cross 2002 grip-slip impacts (Am. J. Phys. 70, 1093), hops flown through the "
        "flight model, sliding then rolling out; constants fitted by the maintainer "
        "(turf-roll-v2)"
    ),
    landing_angle_range_deg=(5.0, 65.0),
    tilt_max_rad=0.749,
    tilt_speed_mps=34.6,
    tilt_peak_rad=0.776,
    tilt_width_rad=0.321,
    restitution=0.533,
    restitution_speed_mps=28.5,
    friction=0.140,
    tangential_cap_mps=21.6,
    tangential_cap_normal_mps=33.3,
    sliding_friction=0.328,
    rolling_resistance=0.104,
    turf_drag_per_m=0.0305,
    aerodynamic_hops=True,
    calibration=RollCalibration(
        version="turf-roll-v2",
        source="fitted by the maintainer (turf-roll-v2) on fairway turf, normal firmness",
        fitted_on="2026-10-20",
        domain_landing_angle_deg=(5.0, 65.0),
        domain=(
            "landing speed 18-100 mph, landing angle 5-65 deg, backspin 0-11,000 rpm: "
            "4096 landing states, plus the 3072 landings of club-typical launches "
            "(driver/wood, mid iron, wedge); a random 20 % of each held out"
        ),
        method=(
            "scipy least_squares (soft L1, 2 yd) on the distance to rest, twelve "
            "constants, multistart; hops flown by flight-fit-v1 at 1.1769 kg/m3 (RK4, "
            "10 ms); rounded to three significant figures"
        ),
        metrics=(
            ("held-out landing states roll RMSE yd", 1.76),
            ("held-out club-typical landings roll RMSE yd", 1.08),
            ("turf-roll-v1 on the held-out club-typical landings roll RMSE yd", 1.23),
        ),
    ),
)

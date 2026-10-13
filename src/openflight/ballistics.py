"""
Physics-based golf ball flight simulator.

Integrates the drag + Magnus ODE using RK4 to produce a deterministic
trajectory from launch conditions. On an OPS-only install, when measured spin is
low-confidence or missing, `resolve_launch` substitutes the club-typical spin
estimate (clubs.physics.CLUB_TYPICAL_SPIN_RPM); a fusion unit never does (D17).

Coordinate system (world frame):
    x — downrange, along the horizontal-launch zero (the target line once D34's
        target line is set; the unit's boresight otherwise)
    y — lateral, +right
    z — height, up

W7 (E4) frames:
    carry      — the horizontal straight-line distance from the launch point to
                 the landing point at launch height, hypot(x, y): TrackMan's public
                 definition (metric_definitions.curve cites it). Its downrange
                 component is `downrange_yards`, its side `carry_side_yards`.
    spin axis  — a tilt against the launch direction: 0 is pure backspin about the
                 horizontal axis perpendicular to the horizontal launch direction,
                 so the spin vector turns with the horizontal launch angle and a
                 pure-backspin shot launched right flies the straight shot, rotated.

Total (carry plus bounce and roll) uses the USGA / R&A 2021 linear bounce model
(`bounce_and_roll_yards`) on assumed fairway turf; see BOUNCE_MODELS.

Aerodynamic model: Cd and Cl are functions of the spin parameter
Sp = r*omega / v. Fits are consistent with Bearman & Harvey (1976) and
Kensrud & Smith (2018) measurements for dimpled golf balls in the
post-drag-crisis regime (Re ~ 5e4 to 2e5). Spin decay follows
Kiratidis & Leinweber (2018) at ~4%/s.
"""

import math
from dataclasses import dataclass
from typing import Literal, Optional

from .clubs import ClubType
from .clubs.physics import CLUB_TYPICAL_SPIN_RPM
from .launch_monitor import SPIN_CONFIDENCE_HIGH, Shot
from .pi_trace import _finite

MPH_TO_MPS = 0.44704
MPS_TO_MPH = 1.0 / MPH_TO_MPS
M_TO_YD = 1.09361

# USGA maximum-conforming ball (45.93 g, 42.7 mm diameter).
# Using the max — not an average — so carry estimates are upper-bounded by
# the rules rather than by a guess at the specific ball in play.
BALL_MASS_KG = 0.04593
BALL_RADIUS_M = 0.02135
BALL_AREA_M2 = math.pi * BALL_RADIUS_M**2
AIR_DENSITY_STD = 1.225  # kg/m³ at sea level, 15 °C ISA

# Cd = CD_POLY[0] + CD_POLY[1]*Sp + CD_POLY[2]*Sp^2
# Cl = CL_POLY[0] + CL_POLY[1]*Sp + CL_POLY[2]*Sp^2, clamped at >= 0
#   Second-order polynomials in the spin parameter Sp = r*omega/v, the form
#   Ferguson, McNally & McPhee (2022, ISEA 14, doi:10.5703/1288284317493)
#   fitted to N=1040 shots (GCQuad launch conditions, FlightScope X3
#   carry/apex/offline, Pro V1, Sp 0.02-0.75). The coefficients below are
#   their published values, not re-fitted here.
#   Lift peaks near Sp ~ 0.52 and turns over, as in Bearman & Harvey (1976)
#   and Lyu, Kensrud & Smith (2018). The previous Hill-type saturating form
#   was monotonic by construction, so no choice of its two constants could
#   represent that peak, and it sat ~4 yd low on apex across every club.
#   Beyond Sp = SP_FIT_MAX both curves are held at their end value rather
#   than extrapolating a parabola into a region the fit never saw.
#
# Validation, TrackMan measured launch conditions in, simulated flight out,
# Hill form (as re-fit in #229) -> this form:
#   committed 24-shot capture:            carry RMSE 3.97 -> 2.90 yd
#   independent 5-session 279-shot set:   carry RMSE 3.48 -> 1.98 yd,
#                                         apex RMSE 4.41 -> 0.97 yd
CD_POLY = (0.1304, 0.9287, -0.8259)
CL_POLY = (0.0504, 1.2031, -1.1490)
SP_FIT_MAX = 0.75

# Exponential spin decay: ω(t) = ω₀·exp(-rate·t).
# ~4%/s per Kiratidis & Leinweber (2018); small but matters over ~6 s flights.
SPIN_DECAY_RATE = 0.04

GRAVITY = 9.81
# 500 Hz integration. RK4 error is O(dt⁵) so this is effectively exact for
# the timescales involved; larger dt starts to visibly shorten long drives.
DT_SECONDS = 0.002
# Safety cap — real shots terminate in 5–9 s; anything longer implies the
# solver went unstable or physical inputs are pathological.
MAX_FLIGHT_SECONDS = 15.0
# Trajectory sample cadence for the returned point list. Integration still
# runs at DT_SECONDS; this only controls how many points the caller sees,
# keeping payload size reasonable for UI/log consumers.
SAMPLE_INTERVAL_S = 0.05

# The one club-typical spin table lives in clubs.physics (CLUB_TYPICAL_SPIN_RPM,
# labelled by CLUB_TYPICAL_SPIN_LABEL) and is re-exported here. It is the OPS-only
# estimate path's spin; a fusion unit never uses it (D17).


@dataclass(frozen=True)
class BounceModel:
    """A published bounce-and-roll model: distance from landing to rest.

    ``roll = intercept_yards + slope_yards_per_deg * landing_angle_deg`` over the
    landing angles its data covered; outside them it gives no value.
    """

    surface: str
    citation: str
    intercept_yards: float
    slope_yards_per_deg: float
    landing_angle_range_deg: tuple[float, float]


# USGA & R&A Rules Ltd (9 March 2021), "Proposed Bounce Model for Use in Evaluating
# Optimum Overall Distance", Eq. 1: d_bounce = 79.1 - 1.6 * alpha_f (yards; alpha_f
# the terminal trajectory angle in degrees). Built for fairways from USGA/R&A
# ball-launcher tests (USGA range, Pinehurst No. 2, Philadelphia CC, Rutgers plots,
# Lehigh CC) and 2016-2018 PGA TOUR ShotLink/TrackMan bounce-and-roll data: the
# slope -1.6 yd/deg sits with the USGA range (-1.8 to -1.6) and the tour (-1.5); the
# intercept was set so the 2020 ODS test balls' mean total did not change (the
# model equals the USGA 2003 model at their mean terminal angle, 37.6 deg). The
# terminal angles in its Table 1 data span 10-50 deg; the model is not applied
# outside them. Published with the R&A's "Notice to Golf Ball Manufacturers -
# Publication of Technical Documents" (16 March 2021).
USGA_RA_2021_BOUNCE_MODEL = BounceModel(
    surface="fairway, normal firmness",
    citation=(
        "USGA & R&A Rules Ltd (2021), Proposed Bounce Model for Use in Evaluating "
        "Optimum Overall Distance, Eq. 1: roll = 79.1 - 1.6 x landing angle (yd)"
    ),
    intercept_yards=79.1,
    slope_yards_per_deg=-1.6,
    landing_angle_range_deg=(10.0, 50.0),
)

# Eq. 1 reaches zero at 79.1 / 1.6 = 49.44 deg and is negative from there to the top
# of its data (50 deg). A ball does not roll backwards on a fairway: the roll is
# clamped at 0 there, and the total says so (ROLL_CLAMP_NOTE).
ROLL_CLAMP_NOTE = "roll clamped at 0: Eq. 1 is negative above 49.4°"

FAIRWAY_NORMAL = "fairway_normal"
DEFAULT_SURFACE = FAIRWAY_NORMAL
BOUNCE_MODELS: dict[str, BounceModel] = {FAIRWAY_NORMAL: USGA_RA_2021_BOUNCE_MODEL}


def bounce_and_roll_yards(
    landing_angle_deg: float, surface: str = DEFAULT_SURFACE
) -> Optional[float]:
    """Distance from landing to rest (yards) by the surface's published model, or None.

    None for a surface with no published model, a non-finite angle, or a landing
    angle outside the model's data range (no extrapolation). Inside the range, a
    negative value of the linear model is clamped at 0 (``ROLL_CLAMP_NOTE``).
    """
    model = BOUNCE_MODELS.get(surface)
    if model is None or not _finite(landing_angle_deg):
        return None
    low, high = model.landing_angle_range_deg
    if not low <= landing_angle_deg <= high:
        return None
    return max(0.0, model.intercept_yards + model.slope_yards_per_deg * landing_angle_deg)


def roll_clamped(landing_angle_deg: float, surface: str = DEFAULT_SURFACE) -> bool:
    """Whether the surface's model gave a negative roll here that was clamped at 0."""
    model = BOUNCE_MODELS.get(surface)
    if model is None or bounce_and_roll_yards(landing_angle_deg, surface) is None:
        return False
    return model.intercept_yards + model.slope_yards_per_deg * landing_angle_deg < 0.0


@dataclass
class LaunchConditions:
    """Deterministic launch parameters for the physics model or simulator export."""

    ball_speed_mph: float
    launch_angle_v: float
    launch_angle_h: float
    spin_rpm: float
    spin_axis_deg: float
    spin_source: Literal["measured", "calculated", "model_geometry", "club_typical"]


@dataclass
class TrajectoryPoint:
    t: float
    x: float
    y: float
    z: float
    speed_mph: float
    spin_rpm: float


@dataclass
class Trajectory:
    """A simulated flight from launch to the landing point at launch height.

    carry_yards is the straight-line horizontal distance to the landing point
    (W7); lateral_yards (alias carry_side_yards) is its signed side from the x
    axis and downrange_yards its x component. landing_heading_deg is the
    horizontal direction of travel at landing, positive right.
    """

    points: list[TrajectoryPoint]
    carry_yards: float
    apex_yards: float
    lateral_yards: float
    flight_time_s: float
    landing_speed_mph: float
    landing_angle_deg: float
    downrange_yards: float = 0.0
    landing_heading_deg: float = 0.0

    @property
    def carry_side_yards(self) -> float:
        """Signed side of the landing point from the x axis, positive right."""
        return self.lateral_yards

    def rest_point(self, surface: str = DEFAULT_SURFACE) -> Optional[tuple[float, float]]:
        """(x, y) yards where the ball stops, or None where the model gives no value.

        The landing point plus the surface's bounce-and-roll distance along the
        landing heading. Rolling on along the heading is an assumption: the model
        gives a distance, not a direction, and no sideways kick is modelled.
        """
        roll = bounce_and_roll_yards(self.landing_angle_deg, surface)
        if roll is None:
            return None
        heading = math.radians(self.landing_heading_deg)
        return (
            self.downrange_yards + roll * math.cos(heading),
            self.lateral_yards + roll * math.sin(heading),
        )

    @property
    def total_yards(self) -> Optional[float]:
        """Straight-line distance from launch to rest on the default surface, or None."""
        rest = self.rest_point()
        return None if rest is None else math.hypot(*rest)

    @property
    def total_side_yards(self) -> Optional[float]:
        """Signed side of the rest point on the default surface, or None."""
        rest = self.rest_point()
        return None if rest is None else rest[1]


FULL_BALL_SPEED_CONTRACTS = ("full",)


# Shot.spin_source values a model wrote, each kept as the flight's spin label
MODEL_SPIN_SOURCES = ("calculated", "model_geometry")


def ball_speed_for_flight(shot: Shot) -> tuple[Optional[float], str]:
    """(ball speed mph, label): the full ball speed when the shot has one, else the radial.

    TrackMan's ball speed is the ball's full 3D speed; the OPS243 reads its
    line-of-sight (radial) component. A full speed is read from
    ``ball_speed_full_mph`` when present (E3), or from ``ball_speed_mph`` when the
    shot's ``ball_speed_contract`` says it is full. The label records which was used.
    None when the shot has no positive finite ball speed.
    """
    full = getattr(shot, "ball_speed_full_mph", None)
    if _finite(full) and full > 0:
        return float(full), "full ball speed (ball_speed_full_mph)"
    speed = shot.ball_speed_mph
    if not _finite(speed) or speed <= 0:
        return None, "ball speed not measured"
    contract = shot.ball_speed_contract
    if contract in FULL_BALL_SPEED_CONTRACTS:
        return float(speed), f"full ball speed (ball_speed_contract={contract})"
    return float(speed), (
        f"radial ball speed (OPS243 line of sight, ball_speed_contract={contract}); "
        "no full ball speed on the shot"
    )


def resolve_launch(shot: Shot, *, substitutes: bool = True) -> Optional[LaunchConditions]:
    """
    Produce committed launch conditions from a shot.

    Returns None if the vertical launch angle is unavailable (no physics
    simulation possible without it). Spin is the measurement when its
    confidence >= SPIN_CONFIDENCE_HIGH, else a model's spin under its own label:
    "calculated", or "model_geometry" when built on an assumed swing geometry (D39).

    ``substitutes`` lets an OPS-only install, which has nothing to measure the
    rest with, fill a club-typical spin and a straight (0) horizontal launch and
    spin axis. A fusion unit passes False: without a measured spin, horizontal
    launch and spin axis there is no carry (D17).

    A non-finite input (NaN, inf) is a missing input and is handled exactly so;
    without a finite ball speed there is nothing to simulate. The ball speed is the
    full ball speed when the shot has one (ball_speed_for_flight), else the radial.
    """
    ball_speed, _ = ball_speed_for_flight(shot)
    if ball_speed is None or not _finite(shot.launch_angle_vertical):
        return None

    source: Literal["measured", "calculated", "model_geometry", "club_typical"]
    if (
        _finite(shot.spin_rpm)
        and _finite(shot.spin_confidence)
        and shot.spin_confidence >= SPIN_CONFIDENCE_HIGH
    ):
        spin_rpm, source = float(shot.spin_rpm), "measured"
    elif shot.spin_source in MODEL_SPIN_SOURCES and _finite(shot.spin_rpm) and shot.spin_rpm:
        spin_rpm, source = float(shot.spin_rpm), shot.spin_source
    elif substitutes:
        spin_rpm = CLUB_TYPICAL_SPIN_RPM.get(shot.club, CLUB_TYPICAL_SPIN_RPM[ClubType.UNKNOWN])
        source = "club_typical"
    else:
        return None
    horizontal = shot.launch_angle_horizontal if _finite(shot.launch_angle_horizontal) else None
    spin_axis = shot.spin_axis_deg if _finite(shot.spin_axis_deg) else None
    if not substitutes and (horizontal is None or spin_axis is None):
        return None

    return LaunchConditions(
        ball_speed_mph=ball_speed,
        launch_angle_v=shot.launch_angle_vertical,
        launch_angle_h=horizontal or 0.0,
        spin_rpm=spin_rpm,
        spin_axis_deg=spin_axis or 0.0,
        spin_source=source,
    )


def _poly(coeffs: tuple, sp: float) -> float:
    return coeffs[0] + coeffs[1] * sp + coeffs[2] * sp * sp


def _cd(sp: float) -> float:
    return _poly(CD_POLY, min(sp, SP_FIT_MAX))


def _cl(sp: float) -> float:
    if sp <= 0:
        return 0.0
    return max(0.0, _poly(CL_POLY, min(sp, SP_FIT_MAX)))


def _derivatives(
    state: tuple,
    omega: float,
    axis: tuple,
    air_density: float,
) -> tuple:
    """d/dt of (x, y, z, vx, vy, vz) under gravity + drag + Magnus."""
    _, _, _, vx, vy, vz = state
    v = math.sqrt(vx * vx + vy * vy + vz * vz)
    # At v ≈ 0 both drag and Magnus vanish (F ∝ v²) and division below
    # would be unsafe. Should only happen at the instantaneous apex of a
    # pathological straight-up launch.
    if v < 1e-6:
        return (vx, vy, vz, 0.0, 0.0, -GRAVITY)

    # Spin parameter Sp drives both Cd and Cl. Note ω is tracked separately
    # (not in the state tuple) because its decay is exponential, not an ODE
    # we want to couple into RK4.
    sp = BALL_RADIUS_M * omega / v
    cd = _cd(sp)
    cl = _cl(sp)

    # q: dynamic-pressure × reference-area / mass = acceleration per unit C.
    # Pre-dividing by mass here lets us return accelerations directly.
    q = 0.5 * air_density * v * v * BALL_AREA_M2 / BALL_MASS_KG
    drag_a = cd * q
    lift_a = cl * q

    # Drag opposes velocity: -v̂ · |F_drag|.
    ax = -drag_a * vx / v
    ay = -drag_a * vy / v
    az = -drag_a * vz / v

    # Magnus direction is (ω̂ × v̂). We compute axis × velocity and normalize
    # by the cross-product magnitude rather than |ω|·|v|·sin(θ), which avoids
    # an extra sin() and gracefully degrades when axis ∥ v (spin does no work).
    ox, oy, oz = axis
    cx = oy * vz - oz * vy
    cy = oz * vx - ox * vz
    cz = ox * vy - oy * vx
    c_mag = math.sqrt(cx * cx + cy * cy + cz * cz)
    if c_mag > 1e-6:
        ax += lift_a * cx / c_mag
        ay += lift_a * cy / c_mag
        az += lift_a * cz / c_mag

    az -= GRAVITY

    return (vx, vy, vz, ax, ay, az)


def _rk4_step(
    state: tuple,
    omega: float,
    axis: tuple,
    air_density: float,
    dt: float,
) -> tuple:
    k1 = _derivatives(state, omega, axis, air_density)
    s2 = tuple(state[i] + 0.5 * dt * k1[i] for i in range(6))
    k2 = _derivatives(s2, omega, axis, air_density)
    s3 = tuple(state[i] + 0.5 * dt * k2[i] for i in range(6))
    k3 = _derivatives(s3, omega, axis, air_density)
    s4 = tuple(state[i] + dt * k3[i] for i in range(6))
    k4 = _derivatives(s4, omega, axis, air_density)
    return tuple(state[i] + (dt / 6.0) * (k1[i] + 2 * k2[i] + 2 * k3[i] + k4[i]) for i in range(6))


def launch_relative_spin_axis(spin_axis_deg: float, launch_angle_h_deg: float) -> tuple:
    """Unit spin vector for a spin axis tilted against the launch direction (W7).

    spin_axis_deg = 0 is pure backspin: the axis is horizontal, perpendicular to
    the horizontal launch direction (cos h, sin h, 0) and pointing to its left, so
    omega x v lifts. A positive tilt raises the axis's top toward the right of the
    launch line (fade/slice); a negative one toward the left (draw/hook). The axis
    turns with the horizontal launch angle h: a spin axis is a tilt of the ball's
    own rotation, not of a fixed world axis.
    """
    axis_rad = math.radians(spin_axis_deg)
    h = math.radians(launch_angle_h_deg)
    tilt = math.cos(axis_rad)
    return (tilt * math.sin(h), -tilt * math.cos(h), math.sin(axis_rad))


def simulate(
    conditions: LaunchConditions,
    air_density: float = AIR_DENSITY_STD,
    dt: float = DT_SECONDS,
) -> Trajectory:
    """
    Integrate flight from launch to first ground contact (z = 0).
    """
    v0 = conditions.ball_speed_mph * MPH_TO_MPS
    la_v = math.radians(conditions.launch_angle_v)
    la_h = math.radians(conditions.launch_angle_h)

    # Initial velocity in world frame (x downrange, y right, z up).
    vx = v0 * math.cos(la_v) * math.cos(la_h)
    vy = v0 * math.cos(la_v) * math.sin(la_h)
    vz = v0 * math.sin(la_v)

    # Spin axis against the launch direction (W7): see launch_relative_spin_axis.
    axis = launch_relative_spin_axis(conditions.spin_axis_deg, conditions.launch_angle_h)

    # rpm → rad/s. Decays exponentially each step; see loop below.
    omega = conditions.spin_rpm * 2 * math.pi / 60.0

    points: list[TrajectoryPoint] = [
        TrajectoryPoint(0.0, 0.0, 0.0, 0.0, conditions.ball_speed_mph, conditions.spin_rpm)
    ]

    state = (0.0, 0.0, 0.0, vx, vy, vz)
    t = 0.0
    max_z = 0.0
    last_sample_t = 0.0

    while t < MAX_FLIGHT_SECONDS:
        new_state = _rk4_step(state, omega, axis, air_density, dt)
        t += dt
        omega *= math.exp(-SPIN_DECAY_RATE * dt)

        _, _, z, _, _, _ = new_state
        if z > max_z:
            max_z = z

        # Ground contact. Linear interpolation between prev and new state
        # is sufficient because dt (2 ms) is tiny relative to the timescale
        # of the descent — the state is effectively linear across one step
        # even though the full trajectory is not.
        if z <= 0.0 and t > dt:
            prev_z = state[2]
            denom = prev_z - z
            frac = prev_z / denom if abs(denom) > 1e-9 else 0.0
            t_hit = (t - dt) + frac * dt
            final = tuple(state[i] + frac * (new_state[i] - state[i]) for i in range(6))
            fx, fy, fz, fvx, fvy, fvz = final
            v_final = math.sqrt(fvx * fvx + fvy * fvy + fvz * fvz)
            points.append(
                TrajectoryPoint(
                    t_hit,
                    fx * M_TO_YD,
                    fy * M_TO_YD,
                    max(fz, 0.0) * M_TO_YD,
                    v_final * MPS_TO_MPH,
                    omega * 60 / (2 * math.pi),
                )
            )
            return _trajectory(points, final, max_z, t_hit)

        state = new_state

        if t - last_sample_t >= SAMPLE_INTERVAL_S:
            sx_, sy_, sz_, svx, svy, svz = state
            v = math.sqrt(svx * svx + svy * svy + svz * svz)
            points.append(
                TrajectoryPoint(
                    t,
                    sx_ * M_TO_YD,
                    sy_ * M_TO_YD,
                    sz_ * M_TO_YD,
                    v * MPS_TO_MPH,
                    omega * 60 / (2 * math.pi),
                )
            )
            last_sample_t = t

    # Flight did not terminate — return current state as best-effort
    return _trajectory(points, state, max_z, t)


def _trajectory(points: list, final: tuple, max_z: float, t_end: float) -> Trajectory:
    """The Trajectory ending at state ``final`` (metres), carry to the landing point (W7)."""
    fx, fy, _, fvx, fvy, fvz = final
    v_final = math.sqrt(fvx * fvx + fvy * fvy + fvz * fvz)
    horizontal = math.sqrt(fvx * fvx + fvy * fvy)
    return Trajectory(
        points=points,
        carry_yards=math.hypot(fx, fy) * M_TO_YD,
        apex_yards=max_z * M_TO_YD,
        lateral_yards=fy * M_TO_YD,
        flight_time_s=t_end,
        landing_speed_mph=v_final * MPS_TO_MPH,
        landing_angle_deg=math.degrees(math.atan2(-fvz, horizontal)),
        downrange_yards=fx * M_TO_YD,
        landing_heading_deg=math.degrees(math.atan2(fvy, fvx)),
    )

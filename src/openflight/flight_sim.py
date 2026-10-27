"""Physics-based golf ball flight: RK4 over drag, Magnus lift and spin decay.

Integrates the drag + Magnus ODE using RK4 to produce a deterministic
trajectory from launch conditions. `resolve_launch` flies only the shot's own
inputs: without a spin (measured, or a model's under its label), a horizontal launch
and a spin axis there is no flight, on any install (D17; owner decision 1, 25 Oct 2026).

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

Total (carry plus bounce and roll) uses the turf bounce-and-roll model
(``bounce_roll.TURF_ROLL_V2``, its hops flown in the flight's own air and model)
from the landing angle, speed and backspin on assumed fairway turf (BOUNCE_MODELS).

Aerodynamic model: Cd, Cl and the spin-down moment C_M are functions of the spin
parameter Sp = r*omega / v in the form of Ferguson, McNally & McPhee (2022) Eqs.
(1)-(4). The active constants are flight-fit-v1 (``FLIGHT_FIT_V1``): that form
refitted to TM4's normalized exports on carry, apex, landing angle and hang time,
with its provenance in ``FLIGHT_FIT_V1.calibration``.
"""

import math
from dataclasses import dataclass
from typing import Literal, Optional

from .ballistics import (
    AIR_DENSITY_STD,
    BALL_AREA_M2,
    BALL_MASS_KG,
    BALL_RADIUS_M,
    GRAVITY,
    M_TO_YD,
    MPH_TO_MPS,
    MPS_TO_MPH,
)
from .bounce_roll import TURF_ROLL_V2, HopFlight, TurfRollModel
from .launch_monitor import SPIN_CONFIDENCE_HIGH, Shot
from .numeric import finite as _finite

# Aerodynamic model: the form of Ferguson, McNally & McPhee (2022), ISEA Engineering of
# Sport 14, doi:10.5703/1288284317493:
#   Eq. (1)  Sp  = r * Omega / v
#   Eq. (2)  Cd  = a + b*Sp + c*Sp^2        (CD_POLY = (a, b, c))
#   Eq. (3)  Cl  = d + e*Sp + f*Sp^2        (CL_POLY = (d, e, f)), clamped at >= 0
#   Eq. (4)  C_M = g*Sp                     (CM_PER_SP = g), g bounded 0.010-0.015
# FLIGHT_FIT_V1 refits its constants to TM4 exports (scripts/analysis/fit_flight_model.py).
# Beyond SP_FIT_MAX both curves are held at their end value, never extrapolated.
SP_FIT_MAX = 0.75

# Spin decay, Eq. (4), with Smits & Smith's torque T = C_M (1/2) rho v^2 A d and
# I = (2/5) m r^2; g = 0.01 matches Penner 2001 Eq. (16) (lambda = 2.0e-5) to 0.2 %.
SPIN_DECAY_G_BOUNDS = (0.010, 0.015)
_BALL_INERTIA = 0.4 * BALL_MASS_KG * BALL_RADIUS_M**2


@dataclass(frozen=True)
class CalibrationData:
    """One TM4 export a calibration was fitted to, identified by its hash."""

    name: str
    path: str  # repo-relative for an in-repo file; where it was read from otherwise
    sha256: str
    shots: int
    fit_shots: int  # rows inside the fit's domain (FlightCalibration.domain)
    in_repo: bool
    origin: str


@dataclass(frozen=True)
class FlightCalibration:
    """Provenance of a fitted aerodynamic model: what, from what, how well."""

    version: str
    source: str
    form: str
    fitted_on: str  # ISO date
    data: tuple[CalibrationData, ...]
    domain: str
    reference_air_density_kg_m3: float
    residual_scales: tuple[tuple[str, str, float], ...]  # (output, unit, 1-sigma scale)
    method: str
    # (description, value): held-out and in-sample errors, in each output's unit
    metrics: tuple[tuple[str, float], ...]


@dataclass(frozen=True)
class AeroModel:
    """Cd, Cl and spin-down moment vs Sp; ``descent_*`` scale Cd/Cl while descending (0 = off)."""

    version: str
    label: str
    cd_poly: tuple[float, ...]
    cl_poly: tuple[float, ...]
    cm_per_sp: float
    sp_fit_max: float = SP_FIT_MAX
    calibration: Optional[FlightCalibration] = None
    descent_drag: float = 0.0
    descent_lift: float = 0.0
    descent_power: float = 1.0

    def cd(self, sp: float) -> float:
        """Drag coefficient, held at its SP_FIT_MAX value beyond the fit."""
        return _poly(self.cd_poly, min(sp, self.sp_fit_max))

    def cl(self, sp: float) -> float:
        """Lift coefficient: 0 without spin, never negative, held beyond the fit."""
        if sp <= 0:
            return 0.0
        return max(0.0, _poly(self.cl_poly, min(sp, self.sp_fit_max)))

    def descent_factors(self, sin_descent: float) -> tuple[float, float]:
        """(Cd, Cl) multipliers at a descent angle with this sine; 1 when climbing."""
        if sin_descent <= 0.0 or not (self.descent_drag or self.descent_lift):
            return 1.0, 1.0
        x = sin_descent**self.descent_power
        return math.exp(-self.descent_drag * x), math.exp(-self.descent_lift * x)


def _poly(coeffs: tuple, sp: float) -> float:
    value = 0.0
    for c in reversed(coeffs):
        value = value * sp + c
    return value


FERGUSON_FORM = (
    "Ferguson, McNally & McPhee 2022, Predicting the Flight of a Golf Ball (ISEA 14, "
    "doi:10.5703/1288284317493), Eqs. (1)-(4): Cd and Cl quadratic in Sp = r Omega / v, "
    "C_M = g Sp"
)

FLIGHT_FIT_V1_CALIBRATION = FlightCalibration(
    version="flight-fit-v1",
    source=(
        "fitted to TM4 normalized exports: a TM4 normalized export, 6 May 2026 (in the "
        "repo), and a second, public TM4 session, 12 Oct 2026; TM4's own launch numbers "
        "in, its carry, apex, landing angle and hang time out"
    ),
    form=FERGUSON_FORM,
    fitted_on="2026-10-13",
    data=(
        CalibrationData(
            name="OpenFlight-Test.Normalized.csv",
            path="session_logs/OpenFlight-Test.Normalized.csv",
            sha256="df84641f883a5c7afedca24c009efad896a3843d00e5908d89215f8f3726e072",
            shots=24,
            fit_shots=24,
            in_repo=True,
            origin="a TM4 normalized export, 6 May 2026: 9 7-iron, 8 driver, 7 PW",
        ),
        CalibrationData(
            name="tm4-public-swingsync-2026-10-12.csv",
            path="../tm-exports/tm4-public-swingsync-2026-10-12.csv (beside the checkout)",
            sha256="d4dbf8e6056feed8d4ae3947536c0e8a2607663b245ff49ee16b26bcb000ee60",
            shots=43,
            fit_shots=36,
            in_repo=False,
            origin=(
                "a second, public TM4 session, 12 Oct 2026, SwingSync export, public GitHub "
                "release trackman_session_10_12_26 (someone else's data: not copied into "
                "the repo): 22 PW, 15 7-iron, 6 6-iron"
            ),
        ),
    ),
    domain=(
        "inputs only: ball speed >= 50 mph, vertical launch >= 5 deg, no topspin; "
        "60 of the 67 shots (the 7 out are tops and grounders). Sp at launch 0.05-0.63, "
        "ball speed 56-163 mph; driver from 8 shots in one source"
    ),
    reference_air_density_kg_m3=1.176931,  # flight.AIR_DENSITY_NORMALIZED (25 C, sea level, RH 50 %)
    residual_scales=(
        ("carry", "yd", 1.0),
        ("apex", "ft", 1.0),
        ("landing_angle", "deg", 0.5),
        ("hang_time", "s", 0.1),
    ),
    method=(
        "scipy least_squares on the four outputs divided by their scales, seven "
        "constants, g bounded 0.010-0.015, from Ferguson's constants; RK4 at 5 ms "
        "(production 2 ms), constants rounded to 4 decimals "
        "(scripts/analysis/fit_flight_model.py)"
    ),
    metrics=(
        # RMSE over the 60 in-domain shots, model minus TM4
        ("pooled 10-fold held-out carry RMSE yd", 0.93),
        ("pooled 10-fold held-out apex RMSE ft", 0.79),
        ("pooled 10-fold held-out landing_angle RMSE deg", 0.65),
        ("pooled 10-fold held-out hang_time RMSE s", 0.06),
        ("pooled leave-one-out held-out carry RMSE yd", 0.94),
        ("pooled leave-one-out held-out apex RMSE ft", 0.78),
        ("pooled leave-one-out held-out landing_angle RMSE deg", 0.65),
        ("pooled leave-one-out held-out hang_time RMSE s", 0.06),
        ("6-May-fit on 12-Oct held-out carry RMSE yd", 0.73),
        ("6-May-fit on 12-Oct held-out apex RMSE ft", 0.62),
        ("6-May-fit on 12-Oct held-out landing_angle RMSE deg", 0.46),
        ("6-May-fit on 12-Oct held-out hang_time RMSE s", 0.05),
        # the driver is extrapolated here (12 Oct has no Sp below 0.13): +13 yd carry
        ("12-Oct-fit on 6-May held-out carry RMSE yd", 10.13),
        ("12-Oct-fit on 6-May held-out apex RMSE ft", 15.34),
        ("12-Oct-fit on 6-May held-out landing_angle RMSE deg", 4.55),
        ("12-Oct-fit on 6-May held-out hang_time RMSE s", 0.57),
        ("6-May drives leave-one-out held-out carry RMSE yd", 1.46),
        ("6-May drives leave-one-out held-out apex RMSE ft", 0.92),
        ("ferguson-2022 carry RMSE yd", 1.73),
        ("ferguson-2022 apex RMSE ft", 2.09),
        ("ferguson-2022 landing_angle RMSE deg", 2.20),
        ("ferguson-2022 hang_time RMSE s", 0.14),
    ),
)

FLIGHT_FIT_V1 = AeroModel(
    version="flight-fit-v1",
    label=(
        "flight-fit-v1: Ferguson, McNally & McPhee 2022 Eqs. (1)-(4) quadratic Cd/Cl "
        "form (ISEA 14, doi:10.5703/1288284317493) refitted to TM4 normalized exports "
        "(carry, apex, landing angle, hang time), spin decay C_M = 0.010 S"
    ),
    cd_poly=(0.1459, 0.8331, -0.5549),
    cl_poly=(0.0646, 1.1261, -0.8869),
    cm_per_sp=0.0100,
    calibration=FLIGHT_FIT_V1_CALIBRATION,
)

ACTIVE_AERO_MODEL = FLIGHT_FIT_V1

# The active model's constants under their long-standing names (full_speed reads CD_POLY).
CD_POLY = ACTIVE_AERO_MODEL.cd_poly
CL_POLY = ACTIVE_AERO_MODEL.cl_poly
CM_PER_SP = ACTIVE_AERO_MODEL.cm_per_sp


def spin_acceleration(
    omega: float, speed_mps: float, air_density: float, cm_per_sp: float = CM_PER_SP
) -> float:
    """d(omega)/dt in rad/s^2: the aerodynamic spin-down moment over the ball's inertia."""
    if speed_mps <= 0.0:
        return 0.0
    c_m = cm_per_sp * BALL_RADIUS_M * omega / speed_mps
    torque = c_m * 0.5 * air_density * speed_mps * speed_mps * BALL_AREA_M2 * 2.0 * BALL_RADIUS_M
    return -torque / _BALL_INERTIA


# 500 Hz integration. RK4 error is O(dt⁵) so this is effectively exact for
# the timescales involved; larger dt starts to visibly shorten long drives.
DT_SECONDS = 0.002
# A hop between bounces lasts a second or less: at 10 ms RK4 lands it within 2 mm of
# the 2 ms step, at a fifth of the cost.
HOP_DT_SECONDS = 0.01
# Safety cap — real shots terminate in 5–9 s; anything longer implies the
# solver went unstable or physical inputs are pathological.
MAX_FLIGHT_SECONDS = 15.0
# Trajectory sample cadence for the returned point list. Integration still
# runs at DT_SECONDS; this only controls how many points the caller sees,
# keeping payload size reasonable for UI/log consumers.
SAMPLE_INTERVAL_S = 0.05

FAIRWAY_NORMAL = "fairway_normal"
DEFAULT_SURFACE = FAIRWAY_NORMAL
RollModel = TurfRollModel
ACTIVE_ROLL_MODEL: RollModel = TURF_ROLL_V2
BOUNCE_MODELS: dict[str, RollModel] = {FAIRWAY_NORMAL: ACTIVE_ROLL_MODEL}


def bounce_and_roll_yards(
    landing_angle_deg: float,
    surface: str = DEFAULT_SURFACE,
    landing_speed_mph: Optional[float] = None,
    backspin_rpm: Optional[float] = None,
    model: Optional[RollModel] = None,
    fly_hop: Optional[HopFlight] = None,
) -> Optional[float]:
    """Distance from landing to rest (yards) by the surface's model (or ``model``), or None.

    None without a model, without a needed finite input, or outside the model's landing
    angles; turf hops fly with ``fly_hop`` (default ``hop_flight()``, ISA sea-level air).
    """
    model = BOUNCE_MODELS.get(surface) if model is None else model
    if model is None:
        return None
    if isinstance(model, TurfRollModel) and (landing_speed_mph is None or backspin_rpm is None):
        return None
    if fly_hop is None and isinstance(model, TurfRollModel) and model.aerodynamic_hops:
        fly_hop = hop_flight()
    return model.roll_yards(landing_angle_deg, landing_speed_mph, backspin_rpm, fly_hop)


@dataclass
class LaunchConditions:
    """Deterministic launch parameters for the physics model or simulator export."""

    ball_speed_mph: float
    launch_angle_v: float
    launch_angle_h: float
    spin_rpm: float
    spin_axis_deg: float
    spin_source: Literal["measured", "calculated", "model_geometry", "estimated"]


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

    carry_yards is the horizontal straight-line distance; lateral_yards its signed side
    from the x axis and landing_heading_deg the direction of travel, both positive right.
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
    landing_backspin_rpm: float = 0.0
    air_density_kg_m3: float = AIR_DENSITY_STD
    aero: Optional[AeroModel] = None

    @property
    def carry_side_yards(self) -> float:
        """Signed side of the landing point from the x axis, positive right."""
        return self.lateral_yards

    def rest_point(
        self, surface: str = DEFAULT_SURFACE, model: Optional[RollModel] = None
    ) -> Optional[tuple[float, float]]:
        """(x, y) yards where the ball stops, or None where the model gives no value.

        It rolls on along the landing heading: an assumption, no sideways kick is modelled.
        """
        roll = bounce_and_roll_yards(
            self.landing_angle_deg,
            surface,
            self.landing_speed_mph,
            self.landing_backspin_rpm,
            model,
            hop_flight(self.air_density_kg_m3, self.aero),
        )
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
MODEL_SPIN_SOURCES = ("calculated", "model_geometry", "estimated")


def ball_speed_for_flight(shot: Shot) -> tuple[Optional[float], str]:
    """(ball speed mph, label): the full 3-D ball speed when the shot has one, else the radial.

    Full is ``ball_speed_full_mph``, or ``ball_speed_mph`` when ``ball_speed_contract``
    says full. None without a positive finite ball speed.
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


def resolve_launch(shot: Shot) -> Optional[LaunchConditions]:
    """
    Produce committed launch conditions from a shot, or None.

    Every input is the shot's own: the vertical and horizontal launch, the spin
    axis, and a spin that is the measurement when its confidence >=
    SPIN_CONFIDENCE_HIGH, else a model's spin under its own label ("calculated",
    "model_geometry" when built on an assumed swing geometry (D39), "estimated").
    Without any one of them there is no flight; nothing is filled in on any install
    (D17; owner decision 1, 25 Oct 2026).

    A non-finite input (NaN, inf) is a missing input and is handled exactly so;
    without a finite ball speed there is nothing to simulate. The ball speed is the
    full ball speed when the shot has one (ball_speed_for_flight), else the radial.
    """
    ball_speed, _ = ball_speed_for_flight(shot)
    if ball_speed is None or not _finite(shot.launch_angle_vertical):
        return None

    source: Literal["measured", "calculated", "model_geometry", "estimated"]
    if (
        _finite(shot.spin_rpm)
        and _finite(shot.spin_confidence)
        and shot.spin_confidence >= SPIN_CONFIDENCE_HIGH
    ):
        spin_rpm, source = float(shot.spin_rpm), "measured"
    elif shot.spin_source in MODEL_SPIN_SOURCES and _finite(shot.spin_rpm) and shot.spin_rpm:
        spin_rpm, source = float(shot.spin_rpm), shot.spin_source
    else:
        return None
    if not _finite(shot.launch_angle_horizontal) or not _finite(shot.spin_axis_deg):
        return None

    return LaunchConditions(
        ball_speed_mph=ball_speed,
        launch_angle_v=shot.launch_angle_vertical,
        launch_angle_h=shot.launch_angle_horizontal,
        spin_rpm=spin_rpm,
        spin_axis_deg=shot.spin_axis_deg,
        spin_source=source,
    )


def _cd(sp: float) -> float:
    return ACTIVE_AERO_MODEL.cd(sp)


def _cl(sp: float) -> float:
    return ACTIVE_AERO_MODEL.cl(sp)


def _derivatives(
    state: tuple,
    axis: tuple,
    air_density: float,
    aero: AeroModel,
) -> tuple:
    """d/dt of (x, y, z, vx, vy, vz, omega) under gravity + drag + Magnus + spin-down."""
    _, _, _, vx, vy, vz, omega = state
    v = math.sqrt(vx * vx + vy * vy + vz * vz)
    # At v ≈ 0 drag, Magnus and the spin-down moment vanish (all ∝ v²) and the
    # division below would be unsafe. Should only happen at the instantaneous
    # apex of a pathological straight-up launch.
    if v < 1e-6:
        return (vx, vy, vz, 0.0, 0.0, -GRAVITY, 0.0)

    # Spin parameter Sp drives Cd, Cl and C_M (Ferguson 2022 Eq. 1).
    sp = BALL_RADIUS_M * omega / v
    drag_scale, lift_scale = aero.descent_factors(-vz / v)
    cd = aero.cd(sp) * drag_scale
    cl = aero.cl(sp) * lift_scale

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

    return (vx, vy, vz, ax, ay, az, spin_acceleration(omega, v, air_density, aero.cm_per_sp))


def _rk4_step(
    state: tuple,
    axis: tuple,
    air_density: float,
    dt: float,
    aero: AeroModel,
) -> tuple:
    n = len(state)
    k1 = _derivatives(state, axis, air_density, aero)
    s2 = tuple(state[i] + 0.5 * dt * k1[i] for i in range(n))
    k2 = _derivatives(s2, axis, air_density, aero)
    s3 = tuple(state[i] + 0.5 * dt * k2[i] for i in range(n))
    k3 = _derivatives(s3, axis, air_density, aero)
    s4 = tuple(state[i] + dt * k3[i] for i in range(n))
    k4 = _derivatives(s4, axis, air_density, aero)
    return tuple(state[i] + (dt / 6.0) * (k1[i] + 2 * k2[i] + 2 * k3[i] + k4[i]) for i in range(n))


def launch_relative_spin_axis(
    spin_axis_deg: float, launch_angle_h_deg: float, launch_angle_v_deg: float = 0.0
) -> tuple:
    """Unit spin vector for a spin axis tilted against the launch direction.

    0 is pure backspin: horizontal, perpendicular to the horizontal launch direction and
    pointing to its left, so omega x v lifts. A positive tilt leans the top right (fade).
    The tilt rotates about the launch velocity, so it adds no rifle spin.
    """
    axis_rad = math.radians(spin_axis_deg)
    h = math.radians(launch_angle_h_deg)
    v = math.radians(launch_angle_v_deg)
    c, s = math.cos(axis_rad), math.sin(axis_rad)
    return (
        c * math.sin(h) - s * math.sin(v) * math.cos(h),
        -c * math.cos(h) - s * math.sin(v) * math.sin(h),
        s * math.cos(v),
    )


def simulate(
    conditions: LaunchConditions,
    air_density: float = AIR_DENSITY_STD,
    dt: float = DT_SECONDS,
    aero: Optional[AeroModel] = None,
) -> Trajectory:
    """
    Integrate flight from launch to first ground contact (z = 0).
    """
    aero = ACTIVE_AERO_MODEL if aero is None else aero
    v0 = conditions.ball_speed_mph * MPH_TO_MPS
    la_v = math.radians(conditions.launch_angle_v)
    la_h = math.radians(conditions.launch_angle_h)

    # Initial velocity in world frame (x downrange, y right, z up).
    vx = v0 * math.cos(la_v) * math.cos(la_h)
    vy = v0 * math.cos(la_v) * math.sin(la_h)
    vz = v0 * math.sin(la_v)

    # Spin axis against the launch direction: see launch_relative_spin_axis.
    axis = launch_relative_spin_axis(
        conditions.spin_axis_deg, conditions.launch_angle_h, conditions.launch_angle_v
    )

    # rpm → rad/s; integrated with the flight (Ferguson 2022's Eq. 4 spin-down moment).
    omega = conditions.spin_rpm * 2 * math.pi / 60.0
    backspin_share = math.cos(math.radians(conditions.spin_axis_deg))

    points: list[TrajectoryPoint] = [
        TrajectoryPoint(0.0, 0.0, 0.0, 0.0, conditions.ball_speed_mph, conditions.spin_rpm)
    ]

    state = (0.0, 0.0, 0.0, vx, vy, vz, omega)
    t = 0.0
    max_z = 0.0
    last_sample_t = 0.0

    while t < MAX_FLIGHT_SECONDS:
        new_state = _rk4_step(state, axis, air_density, dt, aero)
        t += dt
        omega = new_state[6]

        z = new_state[2]
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
            final = tuple(state[i] + frac * (new_state[i] - state[i]) for i in range(7))
            fx, fy, fz, fvx, fvy, fvz, omega = final
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
            return _trajectory(points, final, max_z, t_hit, backspin_share, air_density, aero)

        state = new_state

        if t - last_sample_t >= SAMPLE_INTERVAL_S:
            sx_, sy_, sz_, svx, svy, svz, _ = state
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
    return _trajectory(points, state, max_z, t, backspin_share, air_density, aero)


def _trajectory(
    points: list,
    final: tuple,
    max_z: float,
    t_end: float,
    backspin_share: float = 1.0,
    air_density: float = AIR_DENSITY_STD,
    aero: Optional[AeroModel] = None,
) -> Trajectory:
    """The Trajectory ending at state ``final`` (metres).

    ``backspin_share`` is cos(spin axis tilt): the share of the spin that bounces and rolls.
    """
    fx, fy, _, fvx, fvy, fvz = final[:6]
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
        landing_backspin_rpm=final[6] * backspin_share * 60 / (2 * math.pi),
        air_density_kg_m3=air_density,
        aero=aero,
    )


def hop_flight(
    air_density: float = AIR_DENSITY_STD,
    aero: Optional[AeroModel] = None,
    dt: float = HOP_DT_SECONDS,
) -> HopFlight:
    """A bounce_roll.HopFlight that flies each hop through ``simulate``.

    ``om``: rad/s about the horizontal axis, positive rolling forward. Backspin flies as
    a spin axis tilt of 0, topspin as 180 deg.
    """

    def fly(vx: float, vz: float, om: float) -> tuple[float, float, float, float]:
        traj = simulate(
            LaunchConditions(
                ball_speed_mph=math.hypot(vx, vz) * MPS_TO_MPH,
                launch_angle_v=math.degrees(math.atan2(vz, vx)),
                launch_angle_h=0.0,
                spin_rpm=abs(om) * 30.0 / math.pi,
                spin_axis_deg=180.0 if om > 0.0 else 0.0,
                spin_source="calculated",
            ),
            air_density=air_density,
            dt=dt,
            aero=aero,
        )
        speed = traj.landing_speed_mph * MPH_TO_MPS
        angle = math.radians(traj.landing_angle_deg)
        heading = math.radians(traj.landing_heading_deg)
        return (
            traj.downrange_yards / M_TO_YD,
            speed * math.cos(angle) * math.cos(heading),
            -speed * math.sin(angle),
            -traj.landing_backspin_rpm * math.pi / 30.0,
        )

    return fly

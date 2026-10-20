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

Total (carry plus bounce and roll) uses the turf bounce-and-roll model
(``bounce_roll.TURF_ROLL_V1``) from the landing angle, speed and backspin on
assumed fairway turf; the USGA / R&A 2021 linear model stays selectable. See
BOUNCE_MODELS and ROLL_MODELS.

Aerodynamic model: Cd, Cl and the spin-down moment C_M are functions of the spin
parameter Sp = r*omega / v in the form of Ferguson, McNally & McPhee (2022) Eqs.
(1)-(4). The active constants are flight-fit-v1 (``FLIGHT_FIT_V1``): that form
refitted to TM4's normalized exports on carry, apex, landing angle and hang time,
with its D38 provenance in ``FLIGHT_FIT_V1.calibration``. Ferguson's published
constants stay selectable (``FERGUSON_2022``, ``simulate(..., aero=...)``).
"""

import math
from dataclasses import dataclass
from typing import Literal, Optional

from .bounce_roll import TURF_ROLL_V1, TurfRollModel
from .clubs import ClubType
from .clubs.physics import CLUB_TYPICAL_SPIN_RPM
from .launch_monitor import SPIN_CONFIDENCE_HIGH, Shot
from .numeric import finite as _finite

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

# Aerodynamic model (E8a). The form is Ferguson, McNally & McPhee (2022), "Predicting
# the Flight of a Golf Ball: Comparing a Physics-Based Aerodynamic Model to a Neural
# Network", ISEA Engineering of Sport 14, doi:10.5703/1288284317493:
#   Eq. (1)  Sp  = r * Omega / v            (the spin ratio)
#   Eq. (2)  Cd  = a + b*Sp + c*Sp^2        (CD_POLY = (a, b, c))
#   Eq. (3)  Cl  = d + e*Sp + f*Sp^2        (CL_POLY = (d, e, f)), clamped at >= 0
#   Eq. (4)  C_M = g*Sp                     (CM_PER_SP = g), g bounded 0.010-0.015
#            "to reflect realistic spin decay [Smits & Smith 1994]"
# Their constants, fitted to N=1040 shots (GCQuad launch conditions, FlightScope X3
# carry/apex/offline, 2021 Pro V1, 190 m above sea level), are FERGUSON_2022.
# FLIGHT_FIT_V1 keeps the form and refits the seven constants to TM4's normalized
# exports on carry, apex, landing angle and hang time jointly
# (scripts/analysis/fit_flight_model.py; docs/how-it-works/ballistics.md). Ferguson's
# constants were fitted to FlightScope carry, offline and apex, never landing angle or
# hang time; flown with TM4's launch numbers they leave apex ~2 ft low and the
# landing angle 2-4 deg shallow.
#
# Lift peaks and turns over (near Sp 0.52 in Ferguson's fit, 0.63 in the refit), as
# in Bearman & Harvey (1976) and Lyu, Kensrud & Smith (2018). Beyond SP_FIT_MAX both
# curves are held at their end value rather than extrapolating a parabola into a
# region the fit never saw. No Reynolds-number term: one was tested in the refit
# (Smits & Smith's sinusoid and a linear term) and did not improve held-out error.
#
# History (TrackMan measured launch conditions in, simulated flight out):
#   Hill form (#229) -> Ferguson 2022 published constants:
#     committed 24-shot capture:            carry RMSE 3.97 -> 2.90 yd
#     independent 5-session 279-shot set:   carry RMSE 3.48 -> 1.98 yd,
#                                           apex RMSE 4.41 -> 0.97 yd
#   Ferguson 2022 -> flight-fit-v1: see FLIGHT_FIT_V1.calibration.metrics.
SP_FIT_MAX = 0.75

# Spin decay, Eq. (4): the abstract does not print the torque; it is taken as Smits &
# Smith's convention, T = C_M (1/2) rho v^2 A d (d the diameter), with
# I = (2/5) m r^2. With Ferguson's g = 0.01 that gives d(omega)/dt =
# -lambda omega v / r with lambda = 2.0e-5 at 1.204 kg/m3: Penner 2001's Eq. (16),
# "alpha = -(0.00002)(omega_b v_b / r)", from the same Smits & Smith data, to 0.2 %.
# (The 4 %/s exponential this replaced was attributed to Kiratidis & Leinweber 2018,
# a paper on FIFA footballs with no spin decay in it.)
SPIN_DECAY_G_BOUNDS = (0.010, 0.015)
_BALL_INERTIA = 0.4 * BALL_MASS_KG * BALL_RADIUS_M**2


@dataclass(frozen=True)
class CalibrationData:
    """One TM4 export a calibration was fitted to, identified by its hash."""

    name: str
    path: str  # repo-relative for an in-repo file; where it was read from otherwise
    sha256: str
    shots: int  # rows in the export
    fit_shots: int  # rows inside the fit's domain (FlightCalibration.domain)
    in_repo: bool
    origin: str


@dataclass(frozen=True)
class FlightCalibration:
    """D38 provenance of a fitted aerodynamic model: what, from what, how well."""

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
    """Cd, Cl and the spin-down moment as functions of Sp (Ferguson 2022 Eqs. 1-4)."""

    version: str
    label: str
    cd_poly: tuple[float, float, float]
    cl_poly: tuple[float, float, float]
    cm_per_sp: float
    sp_fit_max: float = SP_FIT_MAX
    calibration: Optional[FlightCalibration] = None

    def cd(self, sp: float) -> float:
        """Drag coefficient, held at its SP_FIT_MAX value beyond the fit."""
        return _poly(self.cd_poly, min(sp, self.sp_fit_max))

    def cl(self, sp: float) -> float:
        """Lift coefficient: 0 without spin, never negative, held beyond the fit."""
        if sp <= 0:
            return 0.0
        return max(0.0, _poly(self.cl_poly, min(sp, self.sp_fit_max)))


def _poly(coeffs: tuple, sp: float) -> float:
    return coeffs[0] + coeffs[1] * sp + coeffs[2] * sp * sp


FERGUSON_FORM = (
    "Ferguson, McNally & McPhee 2022, Predicting the Flight of a Golf Ball (ISEA 14, "
    "doi:10.5703/1288284317493), Eqs. (1)-(4): Cd and Cl quadratic in Sp = r Omega / v, "
    "C_M = g Sp"
)

FERGUSON_2022 = AeroModel(
    version="ferguson-2022",
    label=(
        "Ferguson, McNally & McPhee 2022 quadratic Cd/Cl (ISEA 14, "
        "doi:10.5703/1288284317493), published constants, spin decay C_M = 0.01 S"
    ),
    cd_poly=(0.1304, 0.9287, -0.8259),
    cl_poly=(0.0504, 1.2031, -1.1490),
    cm_per_sp=0.01,
)

FLIGHT_FIT_V1_CALIBRATION = FlightCalibration(
    version="flight-fit-v1",
    source=(
        "fitted to TM4 normalized exports: the owner's TM4 export (in the repo) and "
        "Robinante's public TM4 session; TM4's own launch numbers in, its carry, apex, "
        "landing angle and hang time out"
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
            origin="the owner's TM4 session, 6 May 2026: 9 7-iron, 8 driver, 7 PW",
        ),
        CalibrationData(
            name="robinante-swingsync-2026-10-12.csv",
            path="../tm-exports/robinante-swingsync-2026-10-12.csv (beside the checkout)",
            sha256="d4dbf8e6056feed8d4ae3947536c0e8a2607663b245ff49ee16b26bcb000ee60",
            shots=43,
            fit_shots=36,
            in_repo=False,
            origin=(
                "Robinante's TM4 session, 12 Oct 2026, SwingSync export, public GitHub "
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
        # fitted to the owner's export alone, flown on Robinante's 36 shots
        ("owner-fit on Robinante held-out carry RMSE yd", 0.73),
        ("owner-fit on Robinante held-out apex RMSE ft", 0.62),
        ("owner-fit on Robinante held-out landing_angle RMSE deg", 0.46),
        ("owner-fit on Robinante held-out hang_time RMSE s", 0.05),
        # fitted to Robinante's irons alone, flown on the owner's 24 shots: the
        # driver (Sp 0.05-0.08; Robinante goes no lower than 0.13) is extrapolated,
        # +13 yd carry
        ("Robinante-fit on owner held-out carry RMSE yd", 10.13),
        ("Robinante-fit on owner held-out apex RMSE ft", 15.34),
        ("Robinante-fit on owner held-out landing_angle RMSE deg", 4.55),
        ("Robinante-fit on owner held-out hang_time RMSE s", 0.57),
        # ferguson-2022 on the same 60 shots, for comparison
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

AERO_MODELS: dict[str, AeroModel] = {m.version: m for m in (FERGUSON_2022, FLIGHT_FIT_V1)}
ACTIVE_AERO_MODEL = FLIGHT_FIT_V1

# The active model's constants under their long-standing names (full_speed's drag
# referral reads CD_POLY, so E3's drag stays E4's).
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
    version: str = "usga-ra-2021"

    def roll_yards(
        self,
        landing_angle_deg: float,
        landing_speed_mph: Optional[float] = None,
        backspin_rpm: Optional[float] = None,
    ) -> Optional[float]:
        """The linear model's roll (yd) from the landing angle alone, clamped at 0."""
        if not _finite(landing_angle_deg):
            return None
        low, high = self.landing_angle_range_deg
        if not low <= landing_angle_deg <= high:
            return None
        return max(0.0, self.intercept_yards + self.slope_yards_per_deg * landing_angle_deg)


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
RollModel = BounceModel | TurfRollModel
ROLL_MODELS: dict[str, RollModel] = {
    m.version: m for m in (USGA_RA_2021_BOUNCE_MODEL, TURF_ROLL_V1)
}
ACTIVE_ROLL_MODEL: RollModel = TURF_ROLL_V1
BOUNCE_MODELS: dict[str, RollModel] = {FAIRWAY_NORMAL: ACTIVE_ROLL_MODEL}


def bounce_and_roll_yards(
    landing_angle_deg: float,
    surface: str = DEFAULT_SURFACE,
    landing_speed_mph: Optional[float] = None,
    backspin_rpm: Optional[float] = None,
    model: Optional[RollModel] = None,
) -> Optional[float]:
    """Distance from landing to rest (yards) by the surface's model, or None.

    ``model`` overrides the surface's (``ROLL_MODELS``). None for a surface with
    no model, a missing or non-finite input the model needs, or a landing angle
    outside the model's data range (no extrapolation). The turf model needs the
    landing speed and backspin; the USGA / R&A line clamps a negative roll at 0
    (``ROLL_CLAMP_NOTE``).
    """
    model = BOUNCE_MODELS.get(surface) if model is None else model
    if model is None:
        return None
    if isinstance(model, TurfRollModel) and (landing_speed_mph is None or backspin_rpm is None):
        return None
    return model.roll_yards(landing_angle_deg, landing_speed_mph, backspin_rpm)


def roll_clamped(
    landing_angle_deg: float, surface: str = DEFAULT_SURFACE, model: Optional[RollModel] = None
) -> bool:
    """Whether the USGA / R&A line gave a negative roll here that was clamped at 0."""
    model = BOUNCE_MODELS.get(surface) if model is None else model
    if not isinstance(model, BounceModel) or model.roll_yards(landing_angle_deg) is None:
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
    spin_source: Literal["measured", "calculated", "model_geometry", "estimated", "club_typical"]


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
    landing_backspin_rpm: float = 0.0

    @property
    def carry_side_yards(self) -> float:
        """Signed side of the landing point from the x axis, positive right."""
        return self.lateral_yards

    def rest_point(
        self, surface: str = DEFAULT_SURFACE, model: Optional[RollModel] = None
    ) -> Optional[tuple[float, float]]:
        """(x, y) yards where the ball stops, or None where the model gives no value.

        The landing point plus the surface's (or ``model``'s) bounce-and-roll
        distance along the landing heading. Rolling on along the heading is an
        assumption: the model gives a distance, not a direction, and no sideways
        kick is modelled.
        """
        roll = bounce_and_roll_yards(
            self.landing_angle_deg,
            surface,
            self.landing_speed_mph,
            self.landing_backspin_rpm,
            model,
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

    source: Literal["measured", "calculated", "model_geometry", "estimated", "club_typical"]
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


def _cd(sp: float) -> float:
    """The active model's drag coefficient."""
    return ACTIVE_AERO_MODEL.cd(sp)


def _cl(sp: float) -> float:
    """The active model's lift coefficient."""
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
    cd = aero.cd(sp)
    cl = aero.cl(sp)

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
    """Unit spin vector for a spin axis tilted against the launch direction (W7).

    spin_axis_deg = 0 is pure backspin: the axis is horizontal, perpendicular to
    the horizontal launch direction (cos h, sin h, 0) and pointing to its left, so
    omega x v lifts. A positive tilt turns the axis about the launch direction so
    its top leans toward the right of the launch line (fade/slice); a negative one
    toward the left (draw/hook). The tilt is a rotation about the launch velocity
    itself, so the spin vector stays perpendicular to it: a tilted axis adds no
    rifle spin, whatever the vertical launch angle v.
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

    ``aero`` selects the aerodynamic model (``AERO_MODELS``); the default is
    ``ACTIVE_AERO_MODEL``. Ferguson 2022's published constants stay selectable as
    ``FERGUSON_2022`` for comparison.
    """
    aero = ACTIVE_AERO_MODEL if aero is None else aero
    v0 = conditions.ball_speed_mph * MPH_TO_MPS
    la_v = math.radians(conditions.launch_angle_v)
    la_h = math.radians(conditions.launch_angle_h)

    # Initial velocity in world frame (x downrange, y right, z up).
    vx = v0 * math.cos(la_v) * math.cos(la_h)
    vy = v0 * math.cos(la_v) * math.sin(la_h)
    vz = v0 * math.sin(la_v)

    # Spin axis against the launch direction (W7): see launch_relative_spin_axis.
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
            return _trajectory(points, final, max_z, t_hit, backspin_share)

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
    return _trajectory(points, state, max_z, t, backspin_share)


def _trajectory(
    points: list, final: tuple, max_z: float, t_end: float, backspin_share: float = 1.0
) -> Trajectory:
    """The Trajectory ending at state ``final`` (metres), carry to the landing point (W7).

    ``backspin_share`` is the cosine of the spin axis tilt: the part of the spin
    about the horizontal axis, which is what bounces and rolls.
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
    )

"""Superseded flight and roll models, kept to compare the active ones against (research).

ferguson-2022 (published constants), flight-fit-v2 (a descent term), turf-roll-v1
(vacuum hops) and the USGA / R&A 2021 linear roll; none is used by the kiosk.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .bounce_roll import TURF_ROLL_V2, HopFlight, RollCalibration, TurfRollModel
from .flight_sim import (
    BOUNCE_MODELS,
    DEFAULT_SURFACE,
    FERGUSON_FORM,
    FLIGHT_FIT_V1,
    AeroModel,
    FlightCalibration,
    RollModel,
    _finite,
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

# flight-fit-v2: Ferguson's quadratics on the way up; on the way down both
# coefficients are scaled by exp(-k sin(descent)^n). The descent term is an empirical
# form motivated by the published dependence of a sphere's force coefficients on
# whether the flow around it accelerates (Odar & Hamilton 1964); a falling ball
# speeds up, so it is keyed to the descent angle, whose sine is the share of gravity
# along the path. Not active: with every export shot held out from both models it
# is still behind flight-fit-v1 on the drives' carry and apex (see their metrics).
FLIGHT_FIT_V2_FORM = (
    FERGUSON_FORM + "; times exp(-k_d s^n) on Cd and exp(-k_l s^n) on Cl while descending, "
    "s = sin(descent angle), after Odar & Hamilton 1964, Forces on a sphere accelerating in "
    "a viscous fluid (J. Fluid Mech. 18, 302)"
)

FLIGHT_FIT_V2_CALIBRATION = FlightCalibration(
    version="flight-fit-v2",
    source=(
        "fitted by the maintainer (flight-fit-v2) on carry, apex, apex distance, landing "
        "angle, landing speed, hang time, curve and landing spin over a launch grid"
    ),
    form=FLIGHT_FIT_V2_FORM,
    fitted_on="2026-10-20",
    data=(),
    domain=(
        "ball speed 40-190 mph, launch 0-45 deg, spin 500-12,000 rpm, spin axis -30 to "
        "+30 deg, launch direction -10 to +10 deg; 11,264 launches, a random 20 % held out"
    ),
    reference_air_density_kg_m3=1.176931,
    residual_scales=(
        ("carry", "yd", 1.0),
        ("apex", "ft", 1.0),
        ("apex_distance", "yd", 1.0),
        ("landing_angle", "deg", 0.5),
        ("landing_speed", "mph", 1.0),
        ("hang_time", "s", 0.1),
        ("curve", "yd", 1.0),
        ("landing_spin", "rpm", 100.0),
    ),
    method=(
        "scipy least_squares (soft L1) over 4000 training launches, RK4 at 10 ms, nine "
        "constants with the Cd intercept held at 0.151, then rounded"
    ),
    metrics=(
        # RMSE on the 2187 held-out grid launches, model minus target
        ("grid held-out carry RMSE yd", 1.72),
        ("grid held-out apex RMSE ft", 1.81),
        ("grid held-out landing_angle RMSE deg", 1.10),
        ("grid held-out hang_time RMSE s", 0.06),
        ("grid held-out curve RMSE yd", 0.57),
        ("grid held-out landing_speed RMSE mph", 2.13),
        # the 67 export shots flight-fit-v1 was fitted to, all held out here
        ("export shots held-out carry RMSE yd", 1.06),
        ("export shots held-out apex RMSE ft", 1.40),
        ("export shots held-out landing_angle RMSE deg", 0.46),
        ("export shots held-out hang_time RMSE s", 0.07),
        # flight-fit-v1 on the same grid hold-out
        ("flight-fit-v1 grid carry RMSE yd", 5.75),
        ("flight-fit-v1 grid apex RMSE ft", 2.09),
        ("flight-fit-v1 grid landing_angle RMSE deg", 5.98),
        ("flight-fit-v1 grid hang_time RMSE s", 0.25),
    ),
)

FLIGHT_FIT_V2 = AeroModel(
    version="flight-fit-v2",
    label=(
        "flight-fit-v2: Ferguson, McNally & McPhee 2022 quadratic Cd/Cl with a descent "
        "term after Odar & Hamilton 1964, fitted by the maintainer, spin decay C_M = 0.0117 S"
    ),
    cd_poly=(0.1510, 0.8274, -0.5778),
    cl_poly=(0.0715, 1.1250, -0.9015),
    cm_per_sp=0.0117,
    calibration=FLIGHT_FIT_V2_CALIBRATION,
    descent_drag=4.99,
    descent_lift=4.90,
    descent_power=8.03,
)

TURF_ROLL_V1 = TurfRollModel(
    version="turf-roll-v1",
    surface="fairway, normal firmness",
    citation=(
        "turf-roll-v1: Penner 2002 dented-turf bounce (Can. J. Phys. 80, 931) with "
        "Cross 2002 grip-slip impacts (Am. J. Phys. 70, 1093), sliding then rolling "
        "out; constants fitted by the maintainer (flight-fit-v2)"
    ),
    landing_angle_range_deg=(5.0, 65.0),
    tilt_max_rad=0.595,
    tilt_speed_mps=24.8,
    tilt_peak_rad=0.768,
    tilt_width_rad=0.338,
    restitution=0.437,
    restitution_speed_mps=65.5,
    friction=0.135,
    tangential_cap_mps=22.4,
    tangential_cap_normal_mps=24.5,
    sliding_friction=0.355,
    rolling_resistance=0.0971,
    turf_drag_per_m=0.0414,
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
            "scipy least_squares (soft L1, 2 yd) on the distance to rest, eleven "
            "constants with the tilt maximum held at 0.595 rad, rounded to three "
            "significant figures"
        ),
        metrics=(("held-out landing states roll RMSE yd", 1.76),),
    ),
)


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
        fly_hop: Optional[HopFlight] = None,
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


def roll_clamped(
    landing_angle_deg: float, surface: str = DEFAULT_SURFACE, model: Optional[RollModel] = None
) -> bool:
    """Whether the USGA / R&A line gave a negative roll here that was clamped at 0."""
    model = BOUNCE_MODELS.get(surface) if model is None else model
    if not isinstance(model, BounceModel) or model.roll_yards(landing_angle_deg) is None:
        return False
    return model.intercept_yards + model.slope_yards_per_deg * landing_angle_deg < 0.0


AERO_MODELS: dict[str, AeroModel] = {
    m.version: m for m in (FERGUSON_2022, FLIGHT_FIT_V1, FLIGHT_FIT_V2)
}
ROLL_MODELS: dict[str, object] = {
    m.version: m for m in (USGA_RA_2021_BOUNCE_MODEL, TURF_ROLL_V1, TURF_ROLL_V2)
}

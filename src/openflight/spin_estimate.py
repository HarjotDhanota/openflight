"""Calculated spin from the shot's own inputs: the spin chain's kinematic route (D38).

The OPS243 return carries no usable spin line: golf-ball dimples
(~0.3 mm) are Rayleigh-smooth at 24 GHz (λ = 12.4 mm) and the specular
point does not rotate with the ball, so rotation barely modulates the
echo within our ~65 ms ball dwell. Offline analysis of the 2026-06-08
TrackMan session (131 paired shots, PW-3h) found no spectral line at
the TrackMan spin frequency on any club after dechirp and carrier
removal — and the production envelope estimator's output had ~zero
within-club correlation with TrackMan spin (r ≈ +0.19).

What does work is impact kinematics: spin grows with speed and with the
spin loft. Two fitted models live here, both labelled as fitted (D38):

- ``calculated_spin_rpm``: the first fit, ``170 · v · sin(LA)^1.2``, on the
  2026-06-08 session (one player, range balls, not in the repo). The
  ``--calculated-spin`` flag still uses it.
- spin-refit-v1 (``refit_spin``, ``refit_route``): the spin chain's kinematic
  route, refitted to TM exports of three sessions in tiers by the inputs the
  shot has. Coefficients, data provenance and held-out error are in
  ``spin_refit_v1.json``; the fit and its cross-validation in
  ``scripts/analysis/fit_spin_refit.py``; the method in
  ``docs/how-it-works/spin-refit.md``.

Caveats for both: a few players' sessions. Attack angle shifts spin loft at a
fixed launch or loft, and it is not an input here, so shot-to-shot error is
large (each value carries its held-out 68 % range).
"""

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import numpy as np

from .clubs.physics import get_club_physics
from .clubs.types import ClubType
from .numeric import finite_float
from .source_chain import Candidate, weakest_label
from .source_labels import LABEL_CALCULATED, LABEL_ESTIMATED, SPIN_REFIT_METHOD_MARKER

# Fitted on the 2026-06-08 TrackMan session (131 shots, PW-3h);
# leave-one-club-out stable at (170, 1.2) across all folds.
SPIN_COEFF_RPM_PER_MPH = 170.0
SPIN_LA_EXPONENT = 1.2

# Outside these bounds the kinematic model is extrapolating into
# regimes it was never calibrated on (top-spinned thins, pop-ups).
MIN_LAUNCH_ANGLE_DEG = 2.0
MAX_LAUNCH_ANGLE_DEG = 60.0

# Physical ceiling — beyond fresh-groove lob wedge territory.
MAX_SPIN_RPM = 13000.0


def calculated_spin_rpm(
    ball_speed_mph: float,
    launch_angle_deg: float,
) -> Optional[float]:
    """Kinematic spin estimate from ball speed and vertical launch angle.

    Use the true (cosine-corrected) ball speed when available — the
    model was calibrated against TrackMan ball speed.

    Returns None when inputs are missing or outside the calibrated
    range; callers should fall back to club-typical spin.
    """
    if ball_speed_mph is None or launch_angle_deg is None:
        return None
    if ball_speed_mph <= 0:
        return None
    if not MIN_LAUNCH_ANGLE_DEG <= launch_angle_deg <= MAX_LAUNCH_ANGLE_DEG:
        return None

    sin_la = math.sin(math.radians(launch_angle_deg))
    spin = SPIN_COEFF_RPM_PER_MPH * ball_speed_mph * sin_la**SPIN_LA_EXPONENT
    return float(min(spin, MAX_SPIN_RPM))


# ---------------------------------------------------------------- spin-refit-v1 (D38)
#
# Every tier is one power law, fitted to TM's measured shots:
#
#     spin_rpm = coefficient * speed_mph * sin(angle)^exponent
#
#   club_speed: the full club speed and the club's nominal loft (a calculated full
#               club speed only: the OPS radial club speed reads 3-10 % low);
#   club:       the ball speed and the club's nominal loft;
#   launch:     the ball speed and a sensor's vertical launch, when there is no club
#               (the exponent is kept at 1.2 from the first fit; the coefficient refitted).
#
# With a club the launch is not used: fitted together, its exponent was -0.04, and it
# lowered no held-out error. The fit is on TM's true speeds. A full ball speed is fed
# as is; without one the OPS radial ball speed is fed as is (never corrected by an
# assumed geometry), the value is "estimated", and the range's top is widened by the
# radial reading's largest shortfall.

SPIN_REFIT_PATH = Path(__file__).with_name("spin_refit_v1.json")

TIER_CLUB_SPEED = "club_speed"
TIER_CLUB = "club"
TIER_LAUNCH = "launch"
TIER_ORDER = (TIER_CLUB_SPEED, TIER_CLUB, TIER_LAUNCH)

SPEED_FULL = "full"
SPEED_RADIAL = "radial"

# Launch sources the launch tier stands on: a sensor's (the camera fallback's own
# label reaches the spin through shot_metrics).
SENSOR_LAUNCH_SOURCES = ("radar", "camera", "camera_fallback")

_WEDGES = frozenset({ClubType.PW, ClubType.GW, ClubType.SW, ClubType.LW})
_WOODS = frozenset({ClubType.DRIVER, ClubType.WOOD_3, ClubType.WOOD_5, ClubType.WOOD_7})


def club_category(club: Optional[ClubType]) -> Optional[str]:
    """The club's family for the model's error: wedge, iron (hybrids too) or wood."""
    if not isinstance(club, ClubType) or club is ClubType.UNKNOWN:
        return None
    if club in _WEDGES:
        return "wedge"
    if club in _WOODS:
        return "wood"
    return "iron"


def fit_power_law(
    speeds: Sequence[float],
    sines: Sequence[float],
    spins: Sequence[float],
    *,
    exponent: Optional[float] = None,
) -> tuple[float, float]:
    """(coefficient, exponent) of ``spin = coefficient * speed * sine^exponent``.

    Least squares on log(spin / speed), so the error is relative; with ``exponent``
    given only the coefficient is fitted.
    """
    y = np.log(np.asarray(spins, float) / np.asarray(speeds, float))
    x = np.log(np.asarray(sines, float))
    if exponent is not None:
        return float(math.exp(float(np.mean(y - exponent * x)))), float(exponent)
    design = np.column_stack([np.ones_like(x), x])
    (log_coefficient, slope), *_ = np.linalg.lstsq(design, y, rcond=None)
    return float(math.exp(log_coefficient)), float(slope)


@dataclass(frozen=True)
class RefitTier:  # pylint: disable=too-many-instance-attributes
    """One tier: its inputs, coefficients, domain and held-out error."""

    name: str
    speed: str  # "club_speed" or "ball_speed"
    angle: str  # "loft" (the club's nominal loft) or "launch" (vertical launch)
    coefficient: float
    exponent: float
    speed_range_mph: tuple[float, float]
    angle_range_deg: tuple[float, float]
    range_factors: Mapping[str, tuple[float, float]]
    cv: Mapping[str, Mapping[str, Any]]
    formula: str

    def spin_rpm(self, speed_mph: float, angle_deg: float) -> float:
        """The tier's spin (rpm), uncapped."""
        return self.coefficient * speed_mph * math.sin(math.radians(angle_deg)) ** self.exponent

    def factors(self, category: Optional[str]) -> tuple[float, float]:
        """The 68 % range factors for a club family, else the tier's own."""
        return self.range_factors.get(category or "all") or self.range_factors["all"]


@dataclass(frozen=True)
class SpinRefit:  # pylint: disable=too-many-instance-attributes
    """A versioned spin refit: its tiers and the data it was fitted to."""

    version: str
    name: str
    form: str
    source: str
    fitted_on: str
    data: tuple[Mapping[str, Any], ...]
    radial_speed_shortfall: float
    tiers: Mapping[str, RefitTier]


def load_spin_refit(path: Path = SPIN_REFIT_PATH) -> SpinRefit:
    """The model in a spin refit data file."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    tiers = {
        name: RefitTier(
            name=name,
            speed=entry["speed"],
            angle=entry["angle"],
            coefficient=float(entry["coefficient"]),
            exponent=float(entry["exponent"]),
            speed_range_mph=tuple(entry["speed_range_mph"]),
            angle_range_deg=tuple(entry["angle_range_deg"]),
            range_factors={
                key: (float(low), float(high))
                for key, (low, high) in entry["range_factors"].items()
            },
            cv=entry["cv"],
            formula=entry["formula"],
        )
        for name, entry in raw["tiers"].items()
    }
    return SpinRefit(
        version=raw["version"],
        name=raw["name"],
        form=raw["form"],
        source=raw["source"],
        fitted_on=raw["fitted_on"],
        data=tuple(raw["data"]),
        radial_speed_shortfall=float(raw["radial_speed_shortfall"]),
        tiers=tiers,
    )


SPIN_REFIT = load_spin_refit()


@dataclass(frozen=True)
class RefitSpin:
    """One shot's refit spin, its 68 % range, and the tier and inputs it rests on."""

    spin_rpm: float
    range_rpm: tuple[float, float]
    tier: str
    label: str
    speed_basis: str
    method: str
    note: Optional[str] = None

    @property
    def uncertainty_rpm(self) -> float:
        """Half the 68 % range: the 1-sigma equivalent of an asymmetric range."""
        return (self.range_rpm[1] - self.range_rpm[0]) / 2.0


def _positive(value: Any) -> Optional[float]:
    number = finite_float(value)
    return number if number is not None and number > 0 else None


def _speed_input(
    tier: "RefitTier", full_ball, full_label, radial_ball
) -> tuple[float, str, str] | str:
    """(speed, basis, label) a ball-speed tier stands on, or why there is none."""
    if full_ball is not None:
        label = LABEL_CALCULATED if full_label == LABEL_CALCULATED else LABEL_ESTIMATED
        return full_ball, SPEED_FULL, label
    if radial_ball is not None:
        return radial_ball, SPEED_RADIAL, LABEL_ESTIMATED
    return f"no ball speed for the {tier.name} tier"


_SPEED_TEXT = {
    (TIER_CLUB_SPEED, SPEED_FULL): "the full club speed",
    (TIER_CLUB, SPEED_FULL): "the full ball speed",
    (TIER_LAUNCH, SPEED_FULL): "the full ball speed",
}
_RADIAL_TEXT = "the OPS radial ball speed (no full speed)"


def refit_spin(  # pylint: disable=too-many-arguments,too-many-locals,too-many-return-statements
    *,
    club: Optional[ClubType],
    ball_speed_mph: Optional[float],
    ball_speed_full_mph: Optional[float] = None,
    ball_speed_full_label: Optional[str] = None,
    club_speed_full_mph: Optional[float] = None,
    launch_vertical_deg: Optional[float] = None,
    model: SpinRefit = SPIN_REFIT,
) -> RefitSpin | str:
    """The best tier's spin for the inputs a shot has, or why there is none.

    ``ball_speed_mph`` is the OPS radial ball speed; ``ball_speed_full_mph`` the
    full ball speed with its label (calculated or estimated); ``club_speed_full_mph``
    a calculated full club speed only; ``launch_vertical_deg`` a sensor's launch.
    """
    category = club_category(club)
    full_club = _positive(club_speed_full_mph)
    launch = finite_float(launch_vertical_deg)
    if category is not None and full_club is not None:
        tier = model.tiers[TIER_CLUB_SPEED]
        speed, basis, speed_label = full_club, SPEED_FULL, LABEL_CALCULATED
    elif category is not None or launch is not None:
        tier = model.tiers[TIER_CLUB if category is not None else TIER_LAUNCH]
        chosen = _speed_input(
            tier, _positive(ball_speed_full_mph), ball_speed_full_label, _positive(ball_speed_mph)
        )
        if isinstance(chosen, str):
            return chosen
        speed, basis, speed_label = chosen
    else:
        return "no club and no vertical launch from a sensor"

    low_speed, high_speed = tier.speed_range_mph
    if not low_speed <= speed <= high_speed:
        what = "club speed" if tier.speed == "club_speed" else f"{basis} ball speed"
        return (
            f"{what} {speed:.1f} mph outside the refit's {tier.name} tier "
            f"({low_speed:.0f}-{high_speed:.0f} mph)"
        )
    low_angle, high_angle = tier.angle_range_deg
    note = None
    angle_label = LABEL_CALCULATED
    if category is not None:
        angle = get_club_physics(club).nominal_loft_deg
        if not low_angle <= angle <= high_angle:
            angle_label = LABEL_ESTIMATED
            note = (
                f"{club.value} loft {angle:.1f} deg is outside the fitted clubs' "
                f"{low_angle:.1f}-{high_angle:.1f} deg: extrapolated"
            )
    else:
        angle = launch
        if not low_angle <= angle <= high_angle:
            return (
                f"vertical launch {angle:.1f} deg outside the refit's launch tier "
                f"({low_angle:.0f}-{high_angle:.0f} deg)"
            )

    spin = min(tier.spin_rpm(speed, angle), MAX_SPIN_RPM)
    low, high = tier.factors(category if tier.name != TIER_LAUNCH else None)
    range_rpm = (spin * low, spin * high)
    if basis == SPEED_RADIAL:
        range_rpm = (range_rpm[0], range_rpm[1] / (1.0 - model.radial_speed_shortfall))
    label = weakest_label(LABEL_CALCULATED, speed_label, angle_label)
    on = _SPEED_TEXT.get((tier.name, basis), _RADIAL_TEXT)
    version = model.version.rsplit("-", 1)[-1]
    method = (
        f"{label} {SPIN_REFIT_METHOD_MARKER}{version}, TM exports; "
        f"{tier.name} tier: {tier.formula}; on {on})"
    )
    return RefitSpin(
        spin_rpm=float(spin),
        range_rpm=(float(range_rpm[0]), float(range_rpm[1])),
        tier=tier.name,
        label=label,
        speed_basis=basis,
        method=method,
        note=note,
    )


def _full_value(record: Any) -> tuple[Optional[float], Optional[str]]:
    if not isinstance(record, Mapping):
        return None, None
    return finite_float(record.get("value_mph")), record.get("status")


def refit_route(shot: Any, model: SpinRefit = SPIN_REFIT) -> Candidate | str:
    """The spin chain's kinematic route: the refit's candidate for a shot, or why not."""
    _value, full_ball_status = _full_value(getattr(shot, "ball_speed_full", None))
    club_full, club_full_status = _full_value(getattr(shot, "club_speed_full", None))
    launch = None
    if getattr(shot, "launch_angle_vertical_source", None) in SENSOR_LAUNCH_SOURCES:
        launch = finite_float(getattr(shot, "launch_angle_vertical", None))
    result = refit_spin(
        club=getattr(shot, "club", None),
        ball_speed_mph=getattr(shot, "ball_speed_mph", None),
        ball_speed_full_mph=finite_float(getattr(shot, "ball_speed_full_mph", None)),
        ball_speed_full_label=full_ball_status,
        club_speed_full_mph=club_full if club_full_status == LABEL_CALCULATED else None,
        launch_vertical_deg=launch,
        model=model,
    )
    if isinstance(result, str):
        return result
    return Candidate(
        result.spin_rpm,
        result.label,
        result.method,
        uncertainty=result.uncertainty_rpm,
        note=result.note,
        details={
            "tier": result.tier,
            "speed_basis": result.speed_basis,
            "range_rpm": list(result.range_rpm),
            "model": model.version,
        },
    )

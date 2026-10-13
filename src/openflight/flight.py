"""Flight outputs for one shot, labelled, as measured and normalized (Part E, E4).

The eight TrackMan 4 flight outputs: carry, carry side, total, total side, apex,
landing angle, hang time and curve. ``ballistics.simulate`` already computed most
of them; the server used to keep carry and throw the rest away.

Every metric carries ``value``, ``unit``, ``status``, ``source``, ``range`` and
``reason``:

- ``status`` is ``calculated`` on a fusion unit (computed from the shot's measured
  or calculated inputs by a published model, D38), ``estimated`` on an OPS-only
  install (the club-typical spin and neutral angles stand in), or
  ``not_measured`` with the reason. Nothing is filled in on a fusion unit: without
  spin or spin axis there is no flight (D17).
- ``source`` names the model, the air and, for total, the bounce model and the
  surface assumed.
- ``range`` is a +-1 sigma band from a linearised perturbation sweep of the
  inputs' stated uncertainties (``RANGE_BASIS``).

Two flights from the same launch (TrackMan's "raw" and "normalized"):

- ``as_measured``: in the shot's own air. The density is ``Shot.air_density_kg_m3``
  when supplied (the owner's density hook; this module never measures weather),
  else the ISA sea-level 1.225 kg/m3, labelled "ISA default".
- ``normalized``: sea level, 25 C (77 F), calm, dry air, as in the owner's
  TrackMan export note "normalized to no wind conditions at 0 ft altitude, 77 F".

The launch values (ball speed, launch angles, spin, spin axis) are identical in
both; only the air differs.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, replace
from typing import Optional

from . import metric_definitions
from .ballistics import (
    AIR_DENSITY_STD,
    BOUNCE_MODELS,
    DEFAULT_SURFACE,
    LaunchConditions,
    Trajectory,
    ball_speed_for_flight,
    resolve_launch,
    simulate,
)
from .clubs.physics import CLUB_TYPICAL_SPIN_LABEL
from .launch_monitor import SPIN_CONFIDENCE_HIGH, Shot
from .pi_trace import _finite

FLIGHT_METRICS = (
    "carry",
    "carry_side",
    "total",
    "total_side",
    "apex",
    "landing_angle",
    "hang_time",
    "curve",
)
_UNITS = {
    "carry": "yd",
    "carry_side": "yd",
    "total": "yd",
    "total_side": "yd",
    "apex": "yd",
    "landing_angle": "deg",
    "hang_time": "s",
    "curve": "yd",
}
_TOTAL_METRICS = ("total", "total_side")

STATUS_CALCULATED = "calculated"
STATUS_ESTIMATED = "estimated"
STATUS_NOT_MEASURED = "not_measured"

FLIGHT_MODEL_LABEL = (
    "Ferguson, McNally & McPhee 2022 quadratic Cd/Cl (ISEA 14, doi:10.5703/1288284317493), "
    "RK4 point mass, spin decay 4 %/s"
)

# Normalized air: sea level, 25 C (77 F), calm, dry. Ideal gas, dry-air specific
# gas constant 287.05 J/(kg K). TrackMan's export note gives altitude and
# temperature but not humidity; dry air is our stated assumption.
NORMALIZED_PRESSURE_PA = 101325.0
NORMALIZED_TEMPERATURE_K = 298.15
DRY_AIR_GAS_CONSTANT = 287.05
AIR_DENSITY_NORMALIZED = NORMALIZED_PRESSURE_PA / (DRY_AIR_GAS_CONSTANT * NORMALIZED_TEMPERATURE_K)
NORMALIZED_AIR_LABEL = "normalized: sea level (101,325 Pa), 25 C (77 F), calm, dry air"

AIR_ISA_DEFAULT = "ISA default"
AIR_SUPPLIED = "supplied"
# A supplied density outside this band is a unit or wiring error, not weather: ISA
# gives about 0.74 kg/m3 at 5,000 m and cold sea-level air about 1.4 kg/m3.
AIR_DENSITY_PLAUSIBLE_KG_M3 = (0.6, 1.6)

DENSITY_HOOK = (
    "Shot.air_density_kg_m3 (kg/m3), set per shot before finalization by the "
    "owner's density source; when unset the as-measured flight uses ISA 1.225 kg/m3"
)


@dataclass(frozen=True)
class InputUncertainty:
    """One-sigma input uncertainties for the range sweep.

    The defaults are the D32 goals (ball speed, launch) and the Part E provisional
    goals (calculated spin, spin axis): assumed, not measured per shot.
    """

    ball_speed_mph: float = 1.0
    launch_vertical_deg: float = 0.5
    launch_horizontal_deg: float = 0.5
    spin_fraction: float = 0.10
    spin_axis_deg: float = 3.0


DEFAULT_INPUT_UNCERTAINTY = InputUncertainty()
# Coarser RK4 step for the sweep only: 20 ms moves a driver's carry by < 0.01 yd.
RANGE_DT_SECONDS = 0.02
RANGE_BASIS = (
    "+-1 sigma, linearised: one-sided perturbation of each input by its assumed "
    "uncertainty (ball speed 1 mph, vertical and horizontal launch 0.5 deg each, spin "
    "10 %, spin axis 3 deg; the D32 and Part E goals, not measured per shot), summed "
    "in quadrature; swept once in the as-measured air and applied to both flights; "
    "air density not varied"
)


@dataclass
class FlightResult:
    """The shot's launch conditions, both trajectories, and the labelled payload."""

    conditions: Optional[LaunchConditions]
    as_measured: Optional[Trajectory]
    normalized: Optional[Trajectory]
    payload: dict

    def to_dict(self) -> dict:
        return copy.deepcopy(self.payload)


def _as_measured_air(shot: Shot) -> tuple[Optional[float], str, Optional[str]]:
    """(density, source, reason): the supplied density, or ISA when none was supplied.

    An invalid supplied density is not replaced by ISA: that flight is not measured.
    """
    supplied = getattr(shot, "air_density_kg_m3", None)
    if supplied is None:
        return AIR_DENSITY_STD, AIR_ISA_DEFAULT, None
    low, high = AIR_DENSITY_PLAUSIBLE_KG_M3
    if not _finite(supplied) or not low <= supplied <= high:
        return (
            None,
            AIR_SUPPLIED,
            f"supplied air density invalid ({supplied!r} kg/m3; plausible {low}-{high})",
        )
    return float(supplied), AIR_SUPPLIED, None


def _missing_reason(shot: Shot, *, substitutes: bool) -> str:
    """Why resolve_launch gave no launch conditions, in the shot's own terms."""
    reasons = []
    speed, _ = ball_speed_for_flight(shot)
    if speed is None:
        reasons.append("ball speed not measured")
    if not _finite(shot.launch_angle_vertical):
        reasons.append("vertical launch not measured")
    if not substitutes:
        if not _finite(shot.launch_angle_horizontal):
            reasons.append("horizontal launch not measured")
        measured = (
            _finite(shot.spin_rpm)
            and _finite(shot.spin_confidence)
            and shot.spin_confidence >= SPIN_CONFIDENCE_HIGH
        )
        calculated = shot.spin_source == "calculated" and _finite(shot.spin_rpm) and shot.spin_rpm
        if not (measured or calculated):
            if _finite(shot.spin_rpm) and _finite(shot.spin_confidence):
                reasons.append(
                    f"spin not measured (spin confidence {shot.spin_confidence:.2f} "
                    f"below {SPIN_CONFIDENCE_HIGH})"
                )
            else:
                reasons.append("spin not measured")
        if not _finite(shot.spin_axis_deg):
            reasons.append("spin axis not measured")
    return "; ".join(reasons) or "flight inputs incomplete"


def _angle_label(source: Optional[str], substituted: bool) -> str:
    if substituted:
        return "substituted 0 deg (OPS-only estimate; not measured)"
    if source == "estimated":
        return "estimated (OPS-only club table or neutral estimate)"
    return f"measured ({source})" if source else "measured (source unlabelled on the shot)"


def _inputs(shot: Shot, conditions: Optional[LaunchConditions]) -> dict:
    """The five launch inputs the flight used, each with its value and label."""
    speed, speed_label = ball_speed_for_flight(shot)
    if conditions is None:
        spin_value = shot.spin_rpm if _finite(shot.spin_rpm) else None
        axis_value = shot.spin_axis_deg if _finite(shot.spin_axis_deg) else None
        return {
            "ball_speed": {"value": speed, "label": speed_label},
            "launch_vertical": {
                "value": shot.launch_angle_vertical
                if _finite(shot.launch_angle_vertical)
                else None,
                "label": shot.launch_angle_vertical_source or "not measured",
            },
            "launch_horizontal": {
                "value": (
                    shot.launch_angle_horizontal if _finite(shot.launch_angle_horizontal) else None
                ),
                "label": shot.launch_angle_horizontal_source or "not measured",
            },
            "spin": {"value": spin_value, "label": shot.spin_source or "not measured"},
            "spin_axis": {
                "value": axis_value,
                "label": getattr(shot, "spin_axis_source", None) or "not measured",
            },
        }

    if conditions.spin_source == "measured":
        spin_label = f"measured (OPS243, confidence {shot.spin_confidence:.2f})"
    elif conditions.spin_source == "calculated":
        spin_label = f"calculated ({shot.spin_method or 'model unnamed on the shot'})"
    else:
        spin_label = CLUB_TYPICAL_SPIN_LABEL

    if not _finite(shot.spin_axis_deg):
        axis_label = "substituted 0 deg (OPS-only estimate; not measured)"
    else:
        axis_label = getattr(shot, "spin_axis_source", None) or (
            "unlabelled on the shot (the kiosk's proxy: horizontal launch minus club path)"
        )
    return {
        "ball_speed": {"value": conditions.ball_speed_mph, "label": speed_label},
        "launch_vertical": {
            "value": conditions.launch_angle_v,
            "label": _angle_label(shot.launch_angle_vertical_source, False),
        },
        "launch_horizontal": {
            "value": conditions.launch_angle_h,
            "label": _angle_label(
                shot.launch_angle_horizontal_source, not _finite(shot.launch_angle_horizontal)
            ),
        },
        "spin": {"value": conditions.spin_rpm, "label": spin_label},
        "spin_axis": {"value": conditions.spin_axis_deg, "label": axis_label},
    }


def _metric_values(traj: Trajectory, launch_h: float, surface: str) -> dict:
    rest = traj.rest_point(surface)
    carry, side = traj.carry_yards, traj.carry_side_yards
    curve = metric_definitions.curve(carry, side, launch_h)
    return {
        "carry": carry,
        "carry_side": side,
        "total": math.hypot(*rest) if rest is not None else None,
        "total_side": rest[1] if rest is not None else None,
        "apex": traj.apex_yards,
        "landing_angle": traj.landing_angle_deg,
        "hang_time": traj.flight_time_s,
        "curve": curve if _finite(curve) else None,
    }


def _sigmas(
    conditions: LaunchConditions, density: float, surface: str, unc: InputUncertainty
) -> dict:
    """One-sigma per metric by a linearised one-sided sweep; None where undefined."""
    base = _metric_values(
        simulate(conditions, air_density=density, dt=RANGE_DT_SECONDS),
        conditions.launch_angle_h,
        surface,
    )
    perturbed = (
        replace(conditions, ball_speed_mph=conditions.ball_speed_mph + unc.ball_speed_mph),
        replace(conditions, launch_angle_v=conditions.launch_angle_v + unc.launch_vertical_deg),
        replace(conditions, launch_angle_h=conditions.launch_angle_h + unc.launch_horizontal_deg),
        replace(conditions, spin_rpm=conditions.spin_rpm * (1.0 + unc.spin_fraction)),
        replace(conditions, spin_axis_deg=conditions.spin_axis_deg + unc.spin_axis_deg),
    )
    squares = dict.fromkeys(FLIGHT_METRICS, 0.0)
    for variant in perturbed:
        values = _metric_values(
            simulate(variant, air_density=density, dt=RANGE_DT_SECONDS),
            variant.launch_angle_h,
            surface,
        )
        for name in FLIGHT_METRICS:
            if squares[name] is None:
                continue
            if base[name] is None or values[name] is None:
                squares[name] = None
            else:
                squares[name] += (values[name] - base[name]) ** 2
    return {name: (math.sqrt(sq) if sq is not None else None) for name, sq in squares.items()}


def _not_measured(name: str, reason: str) -> dict:
    return {
        "value": None,
        "unit": _UNITS[name],
        "status": STATUS_NOT_MEASURED,
        "source": None,
        "range": None,
        "reason": reason,
    }


def _source(name: str, status: str, air: str, surface: str, surface_assumed: bool) -> str:
    if name in _TOTAL_METRICS:
        bounce = BOUNCE_MODELS[surface]
        surface_text = (
            f"surface assumed: {bounce.surface}"
            if surface_assumed
            else f"surface: {bounce.surface}"
        )
        return (
            f"{status} (model: {FLIGHT_MODEL_LABEL} + {bounce.citation}; {surface_text}; "
            f"roll along the landing heading assumed; air: {air})"
        )
    if name == "curve":
        return (
            f"{status} (model: D37 curve() of this flight's carry, carry side and launch "
            f"direction; flight: {FLIGHT_MODEL_LABEL}; air: {air})"
        )
    return f"{status} (model: {FLIGHT_MODEL_LABEL}; air: {air})"


def _block(
    *,
    traj: Optional[Trajectory],
    conditions: Optional[LaunchConditions],
    density: Optional[float],
    density_source: str,
    status: str,
    reason: Optional[str],
    sigmas: Optional[dict],
    surface: str,
    surface_assumed: bool,
    launch: Optional[dict],
) -> dict:
    block = {
        "air_density_kg_m3": density,
        "air_density_source": density_source,
        "launch": launch,
    }
    if traj is None or conditions is None:
        block["metrics"] = {
            name: _not_measured(name, reason or "flight inputs incomplete")
            for name in FLIGHT_METRICS
        }
        return block
    air = f"{density_source}, {density:.4f} kg/m3"
    values = _metric_values(traj, conditions.launch_angle_h, surface)
    metrics = {}
    for name in FLIGHT_METRICS:
        value = values[name]
        if value is None:
            if name in _TOTAL_METRICS and surface not in BOUNCE_MODELS:
                why = f"no published bounce model for surface {surface!r}"
            elif name in _TOTAL_METRICS:
                low, high = BOUNCE_MODELS[surface].landing_angle_range_deg
                why = (
                    f"landing angle {traj.landing_angle_deg:.1f} deg outside the bounce "
                    f"model's data range ({low:.0f}-{high:.0f} deg)"
                )
            else:
                why = "curve undefined for this landing point"
            metrics[name] = _not_measured(name, why)
            continue
        sigma = sigmas.get(name) if sigmas else None
        metrics[name] = {
            "value": float(value),
            "unit": _UNITS[name],
            "status": status,
            "source": _source(name, status, air, surface, surface_assumed),
            "range": [float(value) - sigma, float(value) + sigma] if sigma is not None else None,
            "reason": None,
        }
    block["metrics"] = metrics
    return block


def compute_flight(
    shot: Shot,
    *,
    substitutes: bool,
    enabled: bool = True,
    surface: Optional[str] = None,
    uncertainty: InputUncertainty = DEFAULT_INPUT_UNCERTAINTY,
) -> FlightResult:
    """Both flights for ``shot`` from its own inputs, every output labelled.

    ``substitutes`` is True on an OPS-only install (club-typical spin and neutral
    angles stand in, status "estimated") and False on a fusion unit (nothing is
    filled in, D17). ``enabled`` False is ``--no-ballistics``. ``surface`` names a
    BOUNCE_MODELS entry; None assumes fairway, normal firmness.
    """
    surface_assumed = surface is None
    surface = DEFAULT_SURFACE if surface is None else surface
    conditions = resolve_launch(shot, substitutes=substitutes) if enabled else None
    status = STATUS_ESTIMATED if substitutes else STATUS_CALCULATED

    if not enabled:
        reason = "flight model disabled (--no-ballistics)"
    elif conditions is None:
        reason = _missing_reason(shot, substitutes=substitutes)
    else:
        reason = None

    raw_density, raw_source, raw_reason = _as_measured_air(shot)
    raw_traj = norm_traj = None
    sigmas = None
    launch = None
    if conditions is not None:
        launch = {
            "ball_speed_mph": conditions.ball_speed_mph,
            "launch_vertical_deg": conditions.launch_angle_v,
            "launch_horizontal_deg": conditions.launch_angle_h,
            "spin_rpm": conditions.spin_rpm,
            "spin_axis_deg": conditions.spin_axis_deg,
        }
        if raw_density is not None:
            raw_traj = simulate(conditions, air_density=raw_density)
        norm_traj = simulate(conditions, air_density=AIR_DENSITY_NORMALIZED)
        sweep_density = raw_density if raw_density is not None else AIR_DENSITY_NORMALIZED
        sigmas = _sigmas(conditions, sweep_density, surface, uncertainty)

    bounce = BOUNCE_MODELS.get(surface)
    payload = {
        "model": FLIGHT_MODEL_LABEL,
        "frame": shot.launch_angle_horizontal_frame,
        "surface": {
            "name": bounce.surface if bounce else surface,
            "assumed": surface_assumed,
            "model": bounce.citation if bounce else None,
        },
        "inputs": _inputs(shot, conditions),
        "range_basis": RANGE_BASIS,
        "density_hook": DENSITY_HOOK,
        "as_measured": _block(
            traj=raw_traj,
            conditions=conditions,
            density=raw_density,
            density_source=raw_source,
            status=status,
            reason=reason or raw_reason,
            sigmas=sigmas,
            surface=surface,
            surface_assumed=surface_assumed,
            launch=copy.deepcopy(launch),
        ),
        "normalized": _block(
            traj=norm_traj,
            conditions=conditions,
            density=AIR_DENSITY_NORMALIZED,
            density_source=NORMALIZED_AIR_LABEL,
            status=status,
            reason=reason,
            sigmas=sigmas,
            surface=surface,
            surface_assumed=surface_assumed,
            launch=copy.deepcopy(launch),
        ),
    }
    return FlightResult(
        conditions=conditions, as_measured=raw_traj, normalized=norm_traj, payload=payload
    )

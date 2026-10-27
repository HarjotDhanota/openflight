"""Flight outputs for one shot, labelled, as measured and normalized (Part E, E4).

The eight TrackMan 4 flight outputs: carry, carry side, total, total side, apex,
landing angle, hang time and curve. ``ballistics.simulate`` already computed most
of them; the server used to keep carry and throw the rest away.

Every metric carries ``value``, ``unit``, ``status``, ``source``, ``range`` and
``reason``:

- ``status`` is the weakest label of the flight's inputs, in the order measured >
  calculated > model_geometry > estimated > not_measured. A flight is a model's
  output, so at best ``calculated`` (a fusion unit's flight on measured or
  calculated inputs, D38); ``model_geometry`` when its spin or spin axis (or a club
  input under them) rests on an assumed swing geometry (D39, E1's arc prior);
  ``estimated`` when an input is a weak one (the radial ball speed, an estimated spin
  or axis); ``not_measured`` with the reason. Nothing is filled in on any install:
  without spin, horizontal launch or spin axis there is no flight (D17; owner
  decision 1, 25 Oct 2026).
- ``source`` names the model, the air, for total the bounce model and the surface
  assumed, and the model-geometry inputs when there are any.
- ``range`` is a +-1 sigma band from a linearised perturbation sweep of the
  inputs' stated uncertainties (``RANGE_BASIS``).

Two flights from the same launch (TrackMan's "raw" and "normalized"):

- ``as_measured``: in the shot's own air. The density is ``Shot.air_density_kg_m3``
  when supplied (the owner's density hook; this module never measures weather),
  else the ISA sea-level 1.225 kg/m3, labelled "ISA default".
- ``normalized``: TrackMan's reference, 77 F (25 C) and sea level (1013.25 hPa),
  calm ("normalized to no wind conditions at 0 ft altitude, 77 F", the owner's
  export; "Default values are 77°F and sea level", TrackMan's normalization page),
  with a relative humidity TrackMan does not publish, set here as
  ``NORMALIZED_REFERENCE_RH_PCT`` (50 %). The density is computed from the three.

The launch values (ball speed, launch angles, spin, spin axis) are identical in
both; only the air differs.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, replace
from typing import Optional

from . import metric_definitions
from .collision_model import (
    is_estimated_source,
    is_model_geometry_source,
    model_geometry_inputs,
)
from .flight_sim import (
    ACTIVE_AERO_MODEL,
    AIR_DENSITY_STD,
    BOUNCE_MODELS,
    DEFAULT_SURFACE,
    MODEL_SPIN_SOURCES,
    LaunchConditions,
    Trajectory,
    ball_speed_for_flight,
    resolve_launch,
    simulate,
)
from .launch_monitor import SPIN_CONFIDENCE_HIGH, Shot
from .numeric import finite as _finite
from .source_labels import EXPERIMENTAL_LAUNCH_SOURCES

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
STATUS_MODEL_GEOMETRY = "model_geometry"
STATUS_ESTIMATED = "estimated"
STATUS_NOT_MEASURED = "not_measured"
# weakest first: a flight output takes the weakest label among its inputs
STATUS_ORDER = (
    STATUS_NOT_MEASURED,
    STATUS_ESTIMATED,
    STATUS_MODEL_GEOMETRY,
    STATUS_CALCULATED,
    "measured",
)

# The active aerodynamic model's own label (E8a: flight-fit-v1, Ferguson's form
# refitted to TM4), plus the integrator.
FLIGHT_MODEL_LABEL = f"{ACTIVE_AERO_MODEL.label}, RK4 point mass"

# Normalized air: TrackMan's 77 F (25 C) at sea level (1013.25 hPa, 29.92 inHg),
# calm. TrackMan publishes no humidity (its normalization page and help articles
# give altitude and temperature only), so the relative humidity is a named
# assumption: 50 %, as the owner's August research took it (1.1769 kg/m3); dry air
# (0 %) gives 1.1839 kg/m3 and saturated air (100 %) 1.1700.
NORMALIZED_TEMPERATURE_C = 25.0
NORMALIZED_TEMPERATURE_K = NORMALIZED_TEMPERATURE_C + 273.15
NORMALIZED_PRESSURE_PA = 101325.0
NORMALIZED_REFERENCE_RH_PCT = 50.0
DRY_AIR_GAS_CONSTANT = 287.05  # J/(kg K)
WATER_VAPOUR_GAS_CONSTANT = 461.495  # J/(kg K)


def saturation_vapour_pressure_pa(temperature_c: float) -> float:
    """Saturation vapour pressure over water, Pa: Buck (1981).

    A. L. Buck, "New equations for computing vapor pressure and enhancement
    factor", J. Appl. Meteorol. 20, 1527-1532 (1981):
    e_s = 6.1121 exp(17.502 T / (240.97 + T)) hPa, T in deg C. Buck's enhancement
    factor for moist air (about 1.004 at sea level) is left out: it moves the
    density by under 0.003 %.
    """
    return 611.21 * math.exp(17.502 * temperature_c / (240.97 + temperature_c))


def humid_air_density_kg_m3(temperature_c: float, pressure_pa: float, rh_pct: float) -> float:
    """Density of moist air: dry air and water vapour as ideal gases (Dalton's law).

    rho = (p - e) / (R_d T) + e / (R_v T), with e = RH x e_s(T) (Buck 1981).
    """
    temperature_k = temperature_c + 273.15
    vapour = rh_pct / 100.0 * saturation_vapour_pressure_pa(temperature_c)
    return (pressure_pa - vapour) / (DRY_AIR_GAS_CONSTANT * temperature_k) + vapour / (
        WATER_VAPOUR_GAS_CONSTANT * temperature_k
    )


AIR_DENSITY_NORMALIZED = humid_air_density_kg_m3(
    NORMALIZED_TEMPERATURE_C, NORMALIZED_PRESSURE_PA, NORMALIZED_REFERENCE_RH_PCT
)
NORMALIZED_AIR_LABEL = (
    f"normalized: {NORMALIZED_TEMPERATURE_C:.1f} °C (77 °F), "
    f"{NORMALIZED_PRESSURE_PA / 100.0:.2f} hPa (sea level), "
    f"RH {NORMALIZED_REFERENCE_RH_PCT:g} % (assumed: TrackMan publishes none), calm"
)

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
    # the flights' label (STATUS_*): the weakest input label, or not_measured
    status: str = STATUS_NOT_MEASURED

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


def _missing_reason(shot: Shot) -> str:
    """Why resolve_launch gave no launch conditions, in the shot's own terms."""
    reasons = []
    speed, _ = ball_speed_for_flight(shot)
    if speed is None:
        reasons.append("ball speed not measured")
    if not _finite(shot.launch_angle_vertical):
        reasons.append("vertical launch not measured")
    if not _finite(shot.launch_angle_horizontal):
        reasons.append("horizontal launch not measured")
    measured = (
        _finite(shot.spin_rpm)
        and _finite(shot.spin_confidence)
        and shot.spin_confidence >= SPIN_CONFIDENCE_HIGH
    )
    model = shot.spin_source in MODEL_SPIN_SOURCES and _finite(shot.spin_rpm) and shot.spin_rpm
    if not (measured or model):
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


def _angle_label(source: Optional[str]) -> str:
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
    elif conditions.spin_source == STATUS_MODEL_GEOMETRY:
        spin_label = f"model geometry ({shot.spin_method or 'model unnamed on the shot'})"
    else:
        spin_label = f"estimated ({shot.spin_method or 'model unnamed on the shot'})"

    axis_label = getattr(shot, "spin_axis_source", None) or (
        "unlabelled on the shot (the kiosk's proxy: horizontal launch minus club path)"
    )
    return {
        "ball_speed": {"value": conditions.ball_speed_mph, "label": speed_label},
        "launch_vertical": {
            "value": conditions.launch_angle_v,
            "label": _angle_label(shot.launch_angle_vertical_source),
        },
        "launch_horizontal": {
            "value": conditions.launch_angle_h,
            "label": _angle_label(shot.launch_angle_horizontal_source),
        },
        "spin": {"value": conditions.spin_rpm, "label": spin_label},
        "spin_axis": {"value": conditions.spin_axis_deg, "label": axis_label},
    }


def _model_geometry_inputs(shot: Shot, conditions: Optional[LaunchConditions]) -> list[str]:
    """The flight's inputs built on model geometry (D39), named; empty when none are.

    The spin and the spin axis as the flight used them, then the club inputs under
    them (the collision model's model-geometry inputs, e.g. E1's arc prior).
    """
    if conditions is None:
        return []
    named = []
    if conditions.spin_source == STATUS_MODEL_GEOMETRY:
        named.append(f"spin ({shot.spin_method or 'model unnamed on the shot'})")
    if _finite(shot.spin_axis_deg) and is_model_geometry_source(
        getattr(shot, "spin_axis_source", None)
    ):
        named.append("spin axis")
    if named:
        collision = getattr(shot, "collision_model", None) or {}
        named.extend(model_geometry_inputs(collision.get("inputs") or {}))
    return named


RADIAL_SPEED_REASON = (
    "ball speed is the OPS line-of-sight speed (full speed refused: {}), about 3-5% low"
)


def _estimated_inputs(shot: Shot, conditions: Optional[LaunchConditions]) -> list[str]:
    """Why the flight's inputs make it an estimate, one reason each; empty when none do.

    The OPS243's line-of-sight (radial) ball speed stands in for the full speed when
    E3 refused it: a different, lower quantity (V-formulas C1).
    """
    if conditions is None:
        return []
    reasons = []
    _speed, speed_label = ball_speed_for_flight(shot)
    full = getattr(shot, "ball_speed_full", None)
    full = full if isinstance(full, dict) else {}
    if speed_label.startswith("radial"):
        why = full.get("reason")
        reasons.append(RADIAL_SPEED_REASON.format(why or "no full ball speed on the shot"))
    elif full.get("status") in (STATUS_ESTIMATED, "experimental"):
        # an experimental full speed: estimated here, experimental in its records
        reasons.append(
            f"full ball speed {full.get('status')} ({full.get('note') or 'a weak input'})"
        )
    # a camera launch with no validated geometry (source_labels): experimental, so the
    # flight is at best estimated here and shot_metrics labels it experimental
    for name, source in (
        ("vertical launch", shot.launch_angle_vertical_source),
        ("horizontal launch", shot.launch_angle_horizontal_source),
    ):
        if source in EXPERIMENTAL_LAUNCH_SOURCES:
            reasons.append(f"{name} from the {EXPERIMENTAL_LAUNCH_SOURCES[source]} (experimental)")
    # a collision spin or axis built on an estimated input (C2), with that input's reason
    metrics = (getattr(shot, "collision_model", None) or {}).get("metrics") or {}
    estimated_spin = conditions.spin_source == STATUS_ESTIMATED
    estimated_axis = _finite(shot.spin_axis_deg) and is_estimated_source(
        getattr(shot, "spin_axis_source", None)
    )
    for used, name in ((estimated_spin, "spin_rate"), (estimated_axis, "spin_axis")):
        if not used:
            continue
        why = (metrics.get(name) or {}).get("reason") or f"{name.replace('_', ' ')} estimated"
        if why not in reasons:
            reasons.append(why)
    return reasons


def _flight_status(model_geometry: list[str], estimated: list[str] = ()) -> str:
    """The weakest input label: estimated (an estimated input), model geometry, else
    calculated."""
    labels = [STATUS_ESTIMATED if estimated else STATUS_CALCULATED]
    if model_geometry:
        labels.append(STATUS_MODEL_GEOMETRY)
    return min(labels, key=STATUS_ORDER.index)


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


def _source(
    name: str,
    status: str,
    air: str,
    surface: str,
    surface_assumed: bool,
    model_geometry: tuple = (),
) -> str:
    if model_geometry:
        air = f"{air}; model-geometry inputs: {', '.join(model_geometry)}"
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
            f"{status} (model: TrackMan curve, the landing point's distance from the "
            "launch-direction line with carry as the straight-line distance, from this "
            f"flight's carry, carry side and launch direction; flight: {FLIGHT_MODEL_LABEL}; "
            f"air: {air})"
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
    model_geometry: tuple = (),
    estimated: tuple = (),
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
            "source": _source(name, status, air, surface, surface_assumed, model_geometry),
            "range": [float(value) - sigma, float(value) + sigma] if sigma is not None else None,
            # an estimate says why it is one (V-formulas C1, C2)
            "reason": "; ".join(estimated) if status == STATUS_ESTIMATED and estimated else None,
        }
    block["metrics"] = metrics
    return block


def compute_flight(
    shot: Shot,
    *,
    enabled: bool = True,
    surface: Optional[str] = None,
    uncertainty: InputUncertainty = DEFAULT_INPUT_UNCERTAINTY,
) -> FlightResult:
    """Both flights for ``shot`` from its own inputs, every output labelled.

    Nothing is filled in on any install (D17; owner decision 1, 25 Oct 2026). A
    flight is "calculated", "model_geometry" when any input is model geometry (D39),
    or "estimated" on an estimated input; the result's ``status`` names it.
    ``enabled`` False is ``--no-ballistics``. ``surface`` names a BOUNCE_MODELS
    entry; None assumes fairway, normal firmness.
    """
    surface_assumed = surface is None
    surface = DEFAULT_SURFACE if surface is None else surface
    conditions = resolve_launch(shot) if enabled else None
    model_geometry = tuple(_model_geometry_inputs(shot, conditions))
    estimated = tuple(_estimated_inputs(shot, conditions))
    status = _flight_status(list(model_geometry), list(estimated))

    if not enabled:
        reason = "flight model disabled (--no-ballistics)"
    elif conditions is None:
        reason = _missing_reason(shot)
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
        "model_geometry_inputs": list(model_geometry),
        "estimated_inputs": list(estimated),
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
            model_geometry=model_geometry,
            estimated=estimated,
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
            model_geometry=model_geometry,
            estimated=estimated,
        ),
    }
    return FlightResult(
        conditions=conditions,
        as_measured=raw_traj,
        normalized=norm_traj,
        payload=payload,
        status=status if conditions is not None else STATUS_NOT_MEASURED,
    )

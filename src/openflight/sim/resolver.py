"""Translate a Shot into a protocol-neutral ResolvedShot with provenance.

This is the single place the measured-vs-estimated fallback table lives, so
every simulator connector inherits identical fallback behavior. Codecs only
serialize the ResolvedShot into their own wire format.
"""

import math
from typing import Dict, List, Mapping, Optional, Tuple

from openflight.clubs import ClubType
from openflight.clubs.physics import CLUB_TYPICAL_SPIN_RPM, get_club_physics
from openflight.launch_monitor import (
    CARRY_CALCULATED,
    CARRY_ESTIMATED,
    CARRY_MEASURED,
    CARRY_MODEL_GEOMETRY,
    SPIN_CONFIDENCE_HIGH,
    Shot,
)
from openflight.numeric import finite as _finite
from openflight.sim.types import (
    IncompleteShotError,
    PlayerState,
    ResolvedShot,
    UnmeasuredShotError,
)
from openflight.source_labels import (
    horizontal_launch_label,
    is_estimated_source,
    is_model_geometry_source,
)

# OPS-only fallback spin (labelled estimated, as in the kiosk's ballistics); never on fusion.
SPIN_MODEL_RPM: Mapping[ClubType, float] = CLUB_TYPICAL_SPIN_RPM

_DEFAULT_SPIN_RPM = CLUB_TYPICAL_SPIN_RPM[ClubType.UNKNOWN]

# Off: a fusion unit's model-geometry or estimated values are never sent and a shot
# needing one is refused. On: each is sent under its label. OPS-only is unaffected.
SIMULATORS_ACCEPT_MODEL_GEOMETRY = False
SIMULATORS_ACCEPT_ESTIMATED = False

MODEL_GEOMETRY = "model_geometry"
ESTIMATED = "estimated"
# a derived value is as good as its weakest source
_PROVENANCE_ORDER = ("estimated", MODEL_GEOMETRY, "calculated", "measured")


def _model_geometry_spin(shot: Shot) -> bool:
    return shot.spin_source == MODEL_GEOMETRY and _finite(shot.spin_rpm) and bool(shot.spin_rpm)


def _model_geometry_axis(shot: Shot) -> bool:
    return _finite(shot.spin_axis_deg) and is_model_geometry_source(shot.spin_axis_source)


def _estimated_spin(shot: Shot) -> bool:
    return shot.spin_source == ESTIMATED and _finite(shot.spin_rpm) and bool(shot.spin_rpm)


def _estimated_axis(shot: Shot) -> bool:
    return _finite(shot.spin_axis_deg) and is_estimated_source(shot.spin_axis_source)


def _estimated_reason(shot: Shot, metric: str) -> str:
    """The collision model's reason for an estimated spin or axis."""
    metrics = (shot.collision_model or {}).get("metrics") or {}
    return (metrics.get(metric) or {}).get("reason") or "an input estimated"


def _measured_total_spin(shot: Shot) -> Optional[Tuple[float, str]]:
    """Measured spin if high-confidence, else a calculated one under its own label, else None."""
    if (
        _finite(shot.spin_rpm)
        and shot.spin_rpm > 0
        and _finite(shot.spin_confidence)
        and shot.spin_confidence >= SPIN_CONFIDENCE_HIGH
    ):
        return float(shot.spin_rpm), "measured"
    if shot.spin_source == "calculated" and _finite(shot.spin_rpm) and shot.spin_rpm:
        return float(shot.spin_rpm), "calculated"
    if SIMULATORS_ACCEPT_MODEL_GEOMETRY and _model_geometry_spin(shot):
        return float(shot.spin_rpm), MODEL_GEOMETRY
    if SIMULATORS_ACCEPT_ESTIMATED and _estimated_spin(shot):
        return float(shot.spin_rpm), ESTIMATED
    return None


def _spin_axis_provenance(shot: Shot) -> Optional[str]:
    """The label the shot's spin axis goes out under, or None when it is not sent."""
    if not _finite(shot.spin_axis_deg):
        return None
    if _model_geometry_axis(shot):
        return MODEL_GEOMETRY if SIMULATORS_ACCEPT_MODEL_GEOMETRY else None
    if is_estimated_source(shot.spin_axis_source):
        return ESTIMATED if SIMULATORS_ACCEPT_ESTIMATED else None
    # no sensor measures the spin axis: it is calculated, never measured
    return "calculated"


def _withheld_model_geometry(shot: Shot) -> List[str]:
    """The required fields the shot has only as model geometry, which are not sent."""
    if SIMULATORS_ACCEPT_MODEL_GEOMETRY:
        return []
    withheld = []
    if _measured_total_spin(shot) is None and _model_geometry_spin(shot):
        withheld.append("spin")
    if _model_geometry_axis(shot):
        withheld.append("spin axis")
    return withheld


def _withheld_estimated(shot: Shot) -> List[str]:
    """The required fields the shot has only as estimates, each "name estimated (reason)"."""
    if SIMULATORS_ACCEPT_ESTIMATED:
        return []
    withheld = []
    if _measured_total_spin(shot) is None and _estimated_spin(shot):
        withheld.append(f"spin estimated ({_estimated_reason(shot, 'spin_rate')})")
    if _estimated_axis(shot):
        withheld.append(f"spin axis estimated ({_estimated_reason(shot, 'spin_axis')})")
    return withheld


def _unmeasured_required_fields(shot: Shot) -> List[str]:
    """The fields OpenConnect requires that this shot did not measure, in protocol order.

    Required: VLA, HLA, TotalSpin and SpinAxis (ball speed is checked before this);
    carry and club data are optional. A non-finite value counts as unmeasured.
    """
    missing = []
    if not _finite(shot.launch_angle_vertical):
        missing.append("vertical launch")
    if not _finite(shot.launch_angle_horizontal):
        missing.append("horizontal launch")
    if _measured_total_spin(shot) is None:
        missing.append("spin")
    if _spin_axis_provenance(shot) is None:
        missing.append("spin axis")
    return missing


def _resolve_carry(
    shot: Shot, provenance: Dict[str, str], *, substitutes: bool = True
) -> Optional[float]:
    """The carry to send, optional in OpenConnect; tags its provenance when there is one.

    None is not sent: simulators fly the ball from speed, launch and spin themselves.
    """
    carry = shot.reported_carry_yards
    if carry is None:
        return None
    if shot.carry_status == CARRY_ESTIMATED and not substitutes:
        if not SIMULATORS_ACCEPT_ESTIMATED:
            return None
        provenance["carry"] = ESTIMATED
    elif shot.carry_status == CARRY_MODEL_GEOMETRY:
        if not SIMULATORS_ACCEPT_MODEL_GEOMETRY:
            return None
        provenance["carry"] = MODEL_GEOMETRY
    elif shot.carry_status in (CARRY_MEASURED, CARRY_CALCULATED) and not substitutes:
        # a simulated carry is a flight model's output: at most "calculated"
        provenance["carry"] = "calculated"
    else:
        # an OPS-only carry flies a club-typical spin, so it is always an estimate
        provenance["carry"] = "estimated"
    return float(carry)


def _resolve_club_fields(
    shot: Shot, provenance: Dict[str, str], *, substitutes: bool
) -> Tuple[Optional[float], Optional[float]]:
    """Club speed and path, both optional in OpenConnect; tags their provenance.

    A missing path is 0, labelled estimated, only with ``substitutes``.
    """
    club_speed: Optional[float] = None
    if _finite(shot.club_speed_mph) and shot.club_speed_mph > 0:
        club_speed = float(shot.club_speed_mph)
        provenance["club_speed"] = "measured"
    elif substitutes:
        provenance["club_speed"] = "estimated"

    club_path: Optional[float] = None
    arc_path = _model_geometry_club_path(shot) if SIMULATORS_ACCEPT_MODEL_GEOMETRY else None
    if _finite(shot.club_path_deg):
        club_path = float(shot.club_path_deg)
        provenance["club_path"] = "measured"
    elif arc_path is not None:
        club_path = arc_path
        provenance["club_path"] = MODEL_GEOMETRY
    elif substitutes:
        club_path = 0.0
        provenance["club_path"] = "estimated"
    return club_speed, club_path


def _model_geometry_club_path(shot: Shot) -> Optional[float]:
    """The selected club path when it is model geometry and finite, else None."""
    metrics = (shot.club_delivery or {}).get("metrics") or {}
    entry = metrics.get("club_path_deg")
    if not isinstance(entry, dict) or entry.get("status") != MODEL_GEOMETRY:
        return None
    value = entry.get("value")
    return float(value) if _finite(value) else None


def _reject_non_finite(resolved: ResolvedShot) -> None:
    """Last line: whatever produced it, a non-finite value is never sent to a simulator."""
    for name, value in resolved.as_values().items():
        if value is not None and not _finite(value):
            raise IncompleteShotError(f"{name} is not finite")


def resolve_shot(
    shot: Shot, player_state: PlayerState, *, substitutes: bool = True
) -> ResolvedShot:
    """Fill every simulator field from a Shot, applying the fallback table.

    Raises IncompleteShotError when ball speed is missing — the one field that
    has no honest model. Allocates exactly one shot number per physical shot
    (so all connectors share a number for the same shot), and only for a shot
    that is sent.

    A non-finite input is treated as None. ``substitutes`` (OPS-only) fills missing
    fields from club tables or 0, labelled estimated; without it a missing required
    field raises UnmeasuredShotError and missing optional ones are left out.
    """
    if not _finite(shot.ball_speed_mph) or shot.ball_speed_mph <= 0:
        raise IncompleteShotError("ball_speed_mph is required")
    if not substitutes:
        missing = _unmeasured_required_fields(shot)
        if missing:
            raise UnmeasuredShotError(
                missing,
                model_geometry=_withheld_model_geometry(shot),
                estimated=_withheld_estimated(shot),
            )

    # a simulator flies the full speed: the calculated one replaces the OPS radial reading
    full = shot.ball_speed_full if isinstance(shot.ball_speed_full, dict) else {}
    if full.get("status") == "calculated" and _finite(full.get("value_mph")):
        ball_speed = float(full["value_mph"])
        provenance: Dict[str, str] = {"ball_speed": "calculated"}
    else:
        ball_speed = float(shot.ball_speed_mph)
        provenance = {"ball_speed": "measured"}

    if _finite(shot.launch_angle_vertical):
        vla = float(shot.launch_angle_vertical)
        provenance["vla"] = (
            "estimated" if shot.launch_angle_vertical_source == "estimated" else "measured"
        )
    else:
        vla = get_club_physics(shot.club).optimal_launch_deg
        provenance["vla"] = "estimated"

    if _finite(shot.launch_angle_horizontal):
        hla = float(shot.launch_angle_horizontal)
        provenance["hla"] = horizontal_launch_label(
            shot.launch_angle_horizontal_source, shot.launch_angle_horizontal_status
        )
    else:
        hla = 0.0
        provenance["hla"] = "estimated"

    measured_spin = _measured_total_spin(shot)
    if measured_spin is not None:
        total_spin, spin_prov = measured_spin
    else:
        total_spin, spin_prov = SPIN_MODEL_RPM.get(shot.club, _DEFAULT_SPIN_RPM), "estimated"
    provenance["total_spin"] = spin_prov

    axis_prov = _spin_axis_provenance(shot)
    if axis_prov is not None:
        spin_axis = float(shot.spin_axis_deg)
    else:
        spin_axis = 0.0
        axis_prov = "estimated"
    provenance["spin_axis"] = axis_prov

    axis_rad = math.radians(spin_axis)
    back_spin = total_spin * math.cos(axis_rad)
    side_spin = total_spin * math.sin(axis_rad)
    derived_prov = min((spin_prov, axis_prov), key=_PROVENANCE_ORDER.index)
    provenance["back_spin"] = derived_prov
    provenance["side_spin"] = derived_prov

    carry = _resolve_carry(shot, provenance, substitutes=substitutes)

    club_speed, club_path = _resolve_club_fields(shot, provenance, substitutes=substitutes)

    resolved = ResolvedShot(
        shot_number=0,
        ball_speed_mph=ball_speed,
        vla=vla,
        hla=hla,
        total_spin_rpm=total_spin,
        spin_axis_deg=spin_axis,
        back_spin_rpm=back_spin,
        side_spin_rpm=side_spin,
        carry_yards=carry,
        club_path_deg=club_path,
        club=shot.club,
        club_speed_mph=club_speed,
        provenance=provenance,
    )
    # Checked before a shot number is taken, so a refused shot consumes none.
    _reject_non_finite(resolved)
    resolved.shot_number = player_state.next_shot_number()
    return resolved

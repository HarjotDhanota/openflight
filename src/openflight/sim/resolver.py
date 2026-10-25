"""Translate a Shot into a protocol-neutral ResolvedShot with provenance.

This is the single place that decides what a simulator is sent, so every
connector inherits the same rule: only the shot's own values, each under its
label, and nothing filled in on any install (D31; owner decision 1, 25 Oct 2026).
A shot missing a required field is not sent, with the reason. Codecs only
serialize the ResolvedShot into their own wire format.
"""

import math
from typing import Dict, List, Optional, Tuple

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
    LABEL_EXPERIMENTAL,
    horizontal_launch_label,
    is_estimated_source,
    is_model_geometry_source,
    launch_label,
)

# Off: model-geometry or estimated values are never sent and a shot needing one is
# refused. On: each is sent under its label.
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
    elif launch_label(shot.launch_angle_vertical_source) == LABEL_EXPERIMENTAL:
        # a camera launch with no validated geometry is shown, never flown (D31)
        missing.append("vertical launch (experimental)")
    if not _finite(shot.launch_angle_horizontal):
        missing.append("horizontal launch")
    elif (
        launch_label(shot.launch_angle_horizontal_source, shot.launch_angle_horizontal_status)
        == LABEL_EXPERIMENTAL
    ):
        missing.append("horizontal launch (experimental)")
    if _measured_total_spin(shot) is None:
        missing.append("spin")
    if _spin_axis_provenance(shot) is None:
        missing.append("spin axis")
    return missing


def _resolve_carry(shot: Shot, provenance: Dict[str, str]) -> Optional[float]:
    """The carry to send, optional in OpenConnect; tags its provenance when there is one.

    None is not sent: simulators fly the ball from speed, launch and spin themselves.
    """
    carry = shot.reported_carry_yards
    if carry is None:
        return None
    if shot.carry_status == CARRY_ESTIMATED:
        if not SIMULATORS_ACCEPT_ESTIMATED:
            return None
        provenance["carry"] = ESTIMATED
    elif shot.carry_status == CARRY_MODEL_GEOMETRY:
        if not SIMULATORS_ACCEPT_MODEL_GEOMETRY:
            return None
        provenance["carry"] = MODEL_GEOMETRY
    elif shot.carry_status in (CARRY_MEASURED, CARRY_CALCULATED):
        # a simulated carry is a flight model's output: at most "calculated"
        provenance["carry"] = "calculated"
    else:
        # an unlabelled carry is not sent: its inputs are unknown
        return None
    return float(carry)


def _resolve_club_fields(
    shot: Shot, provenance: Dict[str, str]
) -> Tuple[Optional[float], Optional[float]]:
    """Club speed and path, both optional in OpenConnect; tags their provenance.

    A missing one is left out, never sent as 0.
    """
    club_speed: Optional[float] = None
    if _finite(shot.club_speed_mph) and shot.club_speed_mph > 0:
        club_speed = float(shot.club_speed_mph)
        provenance["club_speed"] = "measured"

    club_path: Optional[float] = None
    arc_path = _model_geometry_club_path(shot) if SIMULATORS_ACCEPT_MODEL_GEOMETRY else None
    if _finite(shot.club_path_deg):
        club_path = float(shot.club_path_deg)
        provenance["club_path"] = "measured"
    elif arc_path is not None:
        club_path = arc_path
        provenance["club_path"] = MODEL_GEOMETRY
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


def resolve_shot(shot: Shot, player_state: PlayerState) -> ResolvedShot:
    """Every simulator field from the shot's own values, each under its label.

    Raises IncompleteShotError when ball speed is missing, and UnmeasuredShotError
    when a required field (vertical and horizontal launch, spin, spin axis) is
    missing or is not sent under its label; optional fields that are missing are
    left out. Nothing is filled in, on any install (owner decision 1, 25 Oct 2026).
    Allocates exactly one shot number per physical shot (so all connectors share a
    number for the same shot), and only for a shot that is sent. A non-finite input
    is treated as None.
    """
    if not _finite(shot.ball_speed_mph) or shot.ball_speed_mph <= 0:
        raise IncompleteShotError("ball_speed_mph is required")
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

    # every required field is present and sendable (checked above)
    vla = float(shot.launch_angle_vertical)
    provenance["vla"] = launch_label(shot.launch_angle_vertical_source)
    hla = float(shot.launch_angle_horizontal)
    provenance["hla"] = horizontal_launch_label(
        shot.launch_angle_horizontal_source, shot.launch_angle_horizontal_status
    )
    total_spin, spin_prov = _measured_total_spin(shot)
    provenance["total_spin"] = spin_prov
    spin_axis = float(shot.spin_axis_deg)
    axis_prov = _spin_axis_provenance(shot)
    provenance["spin_axis"] = axis_prov

    axis_rad = math.radians(spin_axis)
    back_spin = total_spin * math.cos(axis_rad)
    side_spin = total_spin * math.sin(axis_rad)
    derived_prov = min((spin_prov, axis_prov), key=_PROVENANCE_ORDER.index)
    provenance["back_spin"] = derived_prov
    provenance["side_spin"] = derived_prov

    carry = _resolve_carry(shot, provenance)

    club_speed, club_path = _resolve_club_fields(shot, provenance)

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

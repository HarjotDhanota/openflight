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
    CARRY_MEASURED,
    SPIN_CONFIDENCE_HIGH,
    Shot,
)
from openflight.pi_trace import _finite
from openflight.sim.types import (
    IncompleteShotError,
    PlayerState,
    ResolvedShot,
    UnmeasuredShotError,
)

# The one club-typical spin table (clubs.physics, E4), used only on an OPS-only
# install when a measured spin is absent or low-confidence; the kiosk's ballistics
# reads the same table, so a shot's simulator spin and its estimated carry agree.
# Labelled an estimate (CLUB_TYPICAL_SPIN_LABEL); never used on a fusion unit (D31).
SPIN_MODEL_RPM: Mapping[ClubType, float] = CLUB_TYPICAL_SPIN_RPM

_DEFAULT_SPIN_RPM = CLUB_TYPICAL_SPIN_RPM[ClubType.UNKNOWN]


def _measured_total_spin(shot: Shot) -> Optional[Tuple[float, str]]:
    """Measured spin if high-confidence, else a calculated one under its own label, else None.

    A calculated spin is a model's output, so its provenance is estimated.
    """
    if (
        _finite(shot.spin_rpm)
        and shot.spin_rpm > 0
        and _finite(shot.spin_confidence)
        and shot.spin_confidence >= SPIN_CONFIDENCE_HIGH
    ):
        return float(shot.spin_rpm), "measured"
    if shot.spin_source == "calculated" and _finite(shot.spin_rpm) and shot.spin_rpm:
        return float(shot.spin_rpm), "estimated"
    return None


def _unmeasured_required_fields(shot: Shot) -> List[str]:
    """The fields OpenConnect requires that this shot did not measure, in protocol order.

    BallData Speed, VLA, HLA, TotalSpin and SpinAxis are required (BackSpin and
    SideSpin only without TotalSpin, and derived from it here); CarryDistance and
    every ClubData field are optional. Ball speed is checked before this. A
    non-finite value (NaN, inf) is an unmeasured one, exactly like None.
    """
    missing = []
    if not _finite(shot.launch_angle_vertical):
        missing.append("vertical launch")
    if not _finite(shot.launch_angle_horizontal):
        missing.append("horizontal launch")
    if _measured_total_spin(shot) is None:
        missing.append("spin")
    if not _finite(shot.spin_axis_deg):
        missing.append("spin axis")
    return missing


def _resolve_carry(shot: Shot, provenance: Dict[str, str]) -> Optional[float]:
    """The carry to send, optional in OpenConnect; tags its provenance when there is one.

    The carry the kiosk reports: the server's committed carry (ballistic
    simulator, or on an OPS-only unit the spin table), the bare launch-angle
    table only for shots that never went through finalization, and None for a
    fusion shot whose carry is not measured (D17). None is not sent: simulators
    fly the ball from speed, launch and spin themselves.
    """
    carry = shot.reported_carry_yards
    if carry is None:
        return None
    if shot.carry_status in (CARRY_MEASURED, CARRY_CALCULATED):
        # a fusion unit's simulator; a calculated spin is a model's, not measured
        provenance["carry"] = "measured" if shot.carry_status == CARRY_MEASURED else "estimated"
    else:
        # An OPS-only carry flies a club-typical spin (or reads the ball-speed table),
        # so it is an estimate whatever the launch angle (E4; trackman-comparison §5
        # item 4). The old "measured when a launch angle was present" badge read as a
        # measured carry.
        provenance["carry"] = "estimated"
    return float(carry)


def _resolve_club_fields(
    shot: Shot, provenance: Dict[str, str], *, substitutes: bool
) -> Tuple[Optional[float], Optional[float]]:
    """Club speed and path, both optional in OpenConnect; tags their provenance.

    A missing club speed is None (``ContainsClubData`` false). A missing path is
    0, labelled estimated, on an OPS-only unit; on a fusion unit both are left
    out entirely, with no provenance (D17).
    """
    club_speed: Optional[float] = None
    if _finite(shot.club_speed_mph) and shot.club_speed_mph > 0:
        club_speed = float(shot.club_speed_mph)
        provenance["club_speed"] = "measured"
    elif substitutes:
        provenance["club_speed"] = "estimated"

    club_path: Optional[float] = None
    if _finite(shot.club_path_deg):
        club_path = float(shot.club_path_deg)
        provenance["club_path"] = "measured"
    elif substitutes:
        club_path = 0.0
        provenance["club_path"] = "estimated"
    return club_speed, club_path


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

    One rule: a non-finite input (NaN, inf) is a missing input, treated exactly
    like None; no NaN reaches any simulator.

    ``substitutes`` lets an OPS-only install, which has nothing to measure the
    rest with, fill a missing launch, spin, spin axis and club path from club
    tables or 0, labelled estimated. A fusion unit passes False (D17, D31): a
    required field it did not measure raises UnmeasuredShotError, so the shot is
    not sent, and an optional one (club speed, club path, carry) is left out.
    """
    if not _finite(shot.ball_speed_mph) or shot.ball_speed_mph <= 0:
        raise IncompleteShotError("ball_speed_mph is required")
    if not substitutes:
        missing = _unmeasured_required_fields(shot)
        if missing:
            raise UnmeasuredShotError(missing)

    provenance: Dict[str, str] = {"ball_speed": "measured"}

    if _finite(shot.launch_angle_vertical):
        vla = float(shot.launch_angle_vertical)
        provenance["vla"] = "measured"
    else:
        vla = get_club_physics(shot.club).optimal_launch_deg
        provenance["vla"] = "estimated"

    if _finite(shot.launch_angle_horizontal):
        hla = float(shot.launch_angle_horizontal)
        provenance["hla"] = "measured"
    else:
        hla = 0.0
        provenance["hla"] = "estimated"

    measured_spin = _measured_total_spin(shot)
    if measured_spin is not None:
        total_spin, spin_prov = measured_spin
    else:
        total_spin, spin_prov = SPIN_MODEL_RPM.get(shot.club, _DEFAULT_SPIN_RPM), "estimated"
    provenance["total_spin"] = spin_prov

    if _finite(shot.spin_axis_deg):
        spin_axis = float(shot.spin_axis_deg)
        axis_prov = "measured"
    else:
        spin_axis = 0.0
        axis_prov = "estimated"
    provenance["spin_axis"] = axis_prov

    axis_rad = math.radians(spin_axis)
    back_spin = total_spin * math.cos(axis_rad)
    side_spin = total_spin * math.sin(axis_rad)
    derived_prov = (
        "measured" if (spin_prov == "measured" and axis_prov == "measured") else "estimated"
    )
    provenance["back_spin"] = derived_prov
    provenance["side_spin"] = derived_prov

    carry = _resolve_carry(shot, provenance)

    club_speed, club_path = _resolve_club_fields(shot, provenance, substitutes=substitutes)

    resolved = ResolvedShot(
        shot_number=0,
        ball_speed_mph=float(shot.ball_speed_mph),
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

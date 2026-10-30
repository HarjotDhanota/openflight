"""Resolve simulator fields from the same labelled metric records the kiosk publishes."""

import math
from typing import Dict, Optional, Tuple

from openflight.launch_monitor import SPIN_CONFIDENCE_HIGH, Shot
from openflight.numeric import finite as _finite
from openflight.sim.types import (
    IncompleteShotError,
    PlayerState,
    ResolvedShot,
    UnmeasuredShotError,
)

SIMULATORS_ACCEPT_MODEL_GEOMETRY = False
SIMULATORS_ACCEPT_ESTIMATED = False

MODEL_GEOMETRY = "model_geometry"
ESTIMATED = "estimated"
_PROVENANCE_ORDER = ("estimated", MODEL_GEOMETRY, "calculated", "measured")
_REQUIRED = (
    ("launch_vertical", "vertical launch"),
    ("launch_horizontal", "horizontal launch"),
    ("spin_rate", "spin"),
    ("spin_axis", "spin axis"),
)


def _sendable(entry: dict) -> bool:
    if not _finite(entry.get("value")) or entry.get("frame") == "mixed":
        return False
    status = entry.get("status")
    return (
        status in ("measured", "calculated")
        or (status == MODEL_GEOMETRY and SIMULATORS_ACCEPT_MODEL_GEOMETRY)
        or (status == ESTIMATED and SIMULATORS_ACCEPT_ESTIMATED)
    )


def _measured_total_spin(shot: Shot, metrics: dict) -> Optional[Tuple[float, str]]:
    """A sendable spin, with the published label and the OPS confidence gate intact."""
    entry = metrics["spin_rate"]
    if not _sendable(entry) or entry["value"] <= 0:
        return None
    if entry["status"] == "measured" and (
        not _finite(shot.spin_confidence) or shot.spin_confidence < SPIN_CONFIDENCE_HIGH
    ):
        return None
    return float(entry["value"]), entry["status"]


def _missing_fields(metrics: dict, spin: Optional[tuple]) -> tuple[list, list, list]:
    missing, geometry, estimated = [], [], []
    for metric_id, name in _REQUIRED:
        entry = metrics[metric_id]
        if _sendable(entry) and (metric_id != "spin_rate" or spin is not None):
            continue
        status = entry.get("status")
        if entry.get("frame") == "mixed":
            missing.append(f"{name} (mixed horizontal frames)")
        else:
            missing.append(f"{name} (experimental)" if status == "experimental" else name)
        if entry.get("value") is None:
            continue
        if status == MODEL_GEOMETRY:
            geometry.append(name)
        elif status == ESTIMATED:
            why = entry.get("note") or entry.get("source") or "an input estimated"
            estimated.append(f"{name} estimated ({why})")
    return missing, geometry, estimated


def _optional_metric(metrics: dict, metric_id: str, field: str, provenance: Dict[str, str]):
    entry = metrics[metric_id]
    if not _sendable(entry):
        return None
    if metric_id == "club_speed" and entry["value"] <= 0:
        return None
    provenance[field] = entry["status"]
    return float(entry["value"])


def _reject_non_finite(resolved: ResolvedShot) -> None:
    for name, value in resolved.as_values().items():
        if value is not None and not _finite(value):
            raise IncompleteShotError(f"{name} is not finite")


def resolve_shot(shot: Shot, player_state: PlayerState) -> ResolvedShot:
    """Send only available, admitted records; refused shots consume no shot number.

    Experimental values never pass either opt-in switch. Estimated and model-geometry
    records retain their labels when explicitly enabled. No missing field is filled in.
    """
    if not _finite(shot.ball_speed_mph) or shot.ball_speed_mph <= 0:
        raise IncompleteShotError("ball_speed_mph is required")
    metrics = shot.to_dict()["metrics"]
    spin = _measured_total_spin(shot, metrics)
    missing, geometry, estimated = _missing_fields(metrics, spin)
    if missing:
        raise UnmeasuredShotError(missing, model_geometry=geometry, estimated=estimated)

    full = metrics.get("ball_speed_full", {})
    if _sendable(full) and full["value"] > 0:
        ball_speed = float(full["value"])
        provenance: Dict[str, str] = {"ball_speed": full["status"]}
    else:
        ball_speed = float(metrics["ball_speed"]["value"])
        provenance = {"ball_speed": metrics["ball_speed"]["status"]}

    vla = float(metrics["launch_vertical"]["value"])
    hla = float(metrics["launch_horizontal"]["value"])
    provenance["vla"] = metrics["launch_vertical"]["status"]
    provenance["hla"] = metrics["launch_horizontal"]["status"]
    total_spin, spin_prov = spin
    provenance["total_spin"] = spin_prov
    spin_axis = float(metrics["spin_axis"]["value"])
    axis_prov = metrics["spin_axis"]["status"]
    provenance["spin_axis"] = axis_prov

    axis_rad = math.radians(spin_axis)
    back_spin = total_spin * math.cos(axis_rad)
    side_spin = total_spin * math.sin(axis_rad)
    derived_prov = min((spin_prov, axis_prov), key=_PROVENANCE_ORDER.index)
    provenance["back_spin"] = provenance["side_spin"] = derived_prov

    carry = _optional_metric(metrics, "carry", "carry", provenance)
    club_speed = _optional_metric(metrics, "club_speed", "club_speed", provenance)
    club_path = _optional_metric(metrics, "club_path", "club_path", provenance)
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
    _reject_non_finite(resolved)
    resolved.shot_number = player_state.next_shot_number()
    return resolved

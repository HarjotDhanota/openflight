"""Qualified-core eligibility of one emitted shot revision (pre-W13 contract).

D19: the 2 s goal is about the qualified core (ball speed, launch and distance, with
statuses) on screen. This predicate is separate from the lifecycle flag `final`.
W13 replaces DISTANCE_KEY and the contract name when it defines the kiosk core.
"""

from __future__ import annotations

from typing import Optional

from .pi_trace import _finite

CONTRACT = "pre_w13_v1"
MEASURED_LAUNCH_SOURCES = frozenset({"radar", "camera"})
DISTANCE_KEY: Optional[str] = None  # no distance field in today's kiosk payload
_CORE_FIELDS = (
    "ball_speed_mph",
    "launch_angle_vertical",
    "launch_angle_vertical_source",
    "launch_angle_horizontal",
    "launch_angle_horizontal_source",
    "launch_angle_horizontal_status",
)
_UNMEASURED_STATUS_PREFIXES = ("rejected", "withheld")
# Coordinator ruling (11 Oct): an uncalibrated core metric (e.g. the IWR's
# "azimuth_uncalibrated") is not measured for the qualified core, until W13 and
# the owner decide otherwise. Matched anywhere in the status, any case.
_UNCALIBRATED_MARKER = "uncalibrated"


def _status_says_unmeasured(status: str) -> bool:
    lowered = status.lower()
    return lowered.startswith(_UNMEASURED_STATUS_PREFIXES) or _UNCALIBRATED_MARKER in lowered


def _measured(payload: dict, axis: str) -> bool:
    """A finite angle from a measuring sensor whose status is not a rejection or uncalibrated.

    A status that is not a string (or a missing one) passes; a non-string status
    that is present is treated as not measured, since its meaning is unknown.
    """
    status = payload.get(f"launch_angle_{axis}_status")
    if status is not None and not isinstance(status, str):
        return False
    return (
        _finite(payload.get(f"launch_angle_{axis}"))
        and payload.get(f"launch_angle_{axis}_source") in MEASURED_LAUNCH_SOURCES
        and not _status_says_unmeasured(status or "")
    )


def evaluate(payload: dict) -> dict:
    """Eligibility, every failing reason, and the exact core values a display must show."""
    reasons = []
    if not _finite(payload.get("ball_speed_mph")):
        reasons.append("ball_speed_missing")
    if not _measured(payload, "vertical"):
        reasons.append("launch_vertical_not_measured")
    if not _measured(payload, "horizontal"):
        reasons.append("launch_horizontal_not_measured")
    core = {key: payload.get(key) for key in _CORE_FIELDS}
    if DISTANCE_KEY is None:
        reasons.append("distance_not_in_payload")
    else:
        status_key = f"{DISTANCE_KEY}_status"
        core[DISTANCE_KEY] = payload.get(DISTANCE_KEY)
        core[status_key] = payload.get(status_key)
        distance_status = core[status_key]
        if (
            not _finite(core[DISTANCE_KEY])
            or not isinstance(distance_status, str)
            or not distance_status
            or _status_says_unmeasured(distance_status)
        ):
            reasons.append("distance_missing")
    return {"eligible": not reasons, "reasons": reasons, "core": core, "contract": CONTRACT}

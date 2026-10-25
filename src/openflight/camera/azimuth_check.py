"""EXPERIMENTAL: the camera's check of the IWR azimuth, per shot and per session.

On a shot with both sensors, ``Shot.experimental_camera_iwr_delta_deg`` is the camera's
horizontal launch minus the IWR's. ``shot_check`` judges it against three sigma of the
two sensors' combined uncertainty; a disagreement is a note on the horizontal launch,
never a relabel (the label policy owns that). ``session_summary`` is the session's
median delta: the offset a future azimuth calibration could start from. Nothing here
changes any angle.

The camera's sigma is the ball estimate's own spread (its parameter sweep and window
MADs as a normal sigma): its precision, not a validated accuracy. The IWR horizontal
carries no uncertainty yet, so the gate rests on the camera's alone and flags at least
as often as a full budget would.
"""

from __future__ import annotations

import math
import statistics
from typing import Any, Iterable, Mapping, Optional

SCHEMA = "openflight.iwr_azimuth_check.v1"
GATE_SIGMAS = 3.0
MAD_TO_SIGMA = 1.4826
IWR_SIGMA_UNKNOWN = "none: the IWR horizontal carries no uncertainty yet"
CAMERA_SIGMA_SOURCE = "camera ball estimate spread (parameter sweep and window MADs)"
STATUSES = ("agrees", "disagrees", "no_uncertainty")


def _finite(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(float(value)) else None


def camera_sigma_deg(ball_estimate: Optional[Mapping[str, Any]]) -> Optional[float]:
    """The camera horizontal's sigma from its estimate's two MADs, or None."""
    if not isinstance(ball_estimate, Mapping):
        return None
    parameter = _finite(ball_estimate.get("parameter_mad_deg"))
    window = _finite(ball_estimate.get("window_mad_deg"))
    if parameter is None or window is None:
        return None
    return MAD_TO_SIGMA * math.hypot(parameter, window)


def shot_check(
    delta_deg: Optional[float],
    *,
    camera_sigma_deg: Optional[float],  # pylint: disable=redefined-outer-name
    iwr_sigma_deg: Optional[float],
) -> Optional[dict[str, Any]]:
    """One shot's verdict on its camera-IWR horizontal delta, or None without a delta."""
    delta = _finite(delta_deg)
    if delta is None:
        return None
    camera = _finite(camera_sigma_deg)
    iwr = _finite(iwr_sigma_deg)
    combined = math.hypot(camera or 0.0, iwr or 0.0)
    record: dict[str, Any] = {
        "schema": SCHEMA,
        "delta_deg": round(delta, 3),
        "camera_sigma_deg": round(camera, 3) if camera is not None else None,
        "camera_sigma_source": CAMERA_SIGMA_SOURCE,
        "iwr_sigma_deg": round(iwr, 3) if iwr is not None else None,
        "iwr_sigma_source": "iwr" if iwr is not None else IWR_SIGMA_UNKNOWN,
        "combined_sigma_deg": None,
        "limit_deg": None,
        "status": "no_uncertainty",
        "note": None,
    }
    if not combined > 0.0:
        return record
    limit = GATE_SIGMAS * combined
    record["combined_sigma_deg"] = round(combined, 3)
    record["limit_deg"] = round(limit, 3)
    if abs(delta) <= limit:
        record["status"] = "agrees"
        return record
    record["status"] = "disagrees"
    record["note"] = (
        f"camera check: the camera's horizontal differs from the IWR's by {delta:+.1f} deg, "
        f"beyond 3 sigma ({limit:.1f} deg)"
    )
    return record


def session_summary(checks: Iterable[Optional[Mapping[str, Any]]]) -> dict[str, Any]:
    """The session's median camera-IWR delta and its spread: a diagnostic, never applied."""
    valid = [
        check
        for check in checks
        if isinstance(check, Mapping)
        and check.get("status") in STATUSES
        and _finite(check.get("delta_deg")) is not None
    ]
    deltas = [float(check["delta_deg"]) for check in valid]
    median = statistics.median(deltas) if deltas else None
    spread = (
        MAD_TO_SIGMA * statistics.median(abs(delta - median) for delta in deltas)
        if deltas
        else None
    )
    return {
        "schema": SCHEMA,
        "n": len(deltas),
        "median_delta_deg": round(median, 3) if median is not None else None,
        "spread_deg": round(spread, 3) if spread is not None else None,
        "n_disagree": sum(check["status"] == "disagrees" for check in valid),
        "applied": False,
    }

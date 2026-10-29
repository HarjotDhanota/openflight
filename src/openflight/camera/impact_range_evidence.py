"""Same-shot IWR launch-range lineage for experimental impact analysis.

Capture association is not RF clock calibration or physical accuracy qualification.
Ball launch-point range never supplies the moving clubhead's range trajectory.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping

from openflight.iwr6843.launch_range import (
    MIN_BALL_INLIERS,
    MIN_BALL_SPAN_S,
    SCHEMA as LAUNCH_SCHEMA,
    SOURCE,
)
from openflight.rig_geometry import geometry_fingerprint

SCHEMA = "openflight.impact_range_evidence.v1"
ACCEPTED = "accepted_for_experimental_impact"


def _positive(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("range_evidence_number_missing")
    try:
        number = float(value)
    except OverflowError as exc:
        raise ValueError("range_evidence_number_invalid") from exc
    if not math.isfinite(number) or number <= 0:
        raise ValueError("range_evidence_number_invalid")
    return number


def _identity(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _sha256(value: Any) -> bool:
    return (
        isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)
    )


def _seal(payload: dict) -> dict:
    payload = json.loads(json.dumps(payload, allow_nan=False))
    payload.pop("sha256", None)
    return {**payload, "sha256": geometry_fingerprint(payload)}


def unavailable(reason: str) -> dict:
    """An explicit refusal that cannot contribute a numeric range."""
    return {"schema": SCHEMA, "status": "unavailable", "reason": reason}


def _checked(record: Mapping[str, Any]) -> dict:  # pylint: disable=too-many-branches
    if not isinstance(record, Mapping) or record.get("schema") != SCHEMA:
        raise ValueError("missing_or_unsupported_range_evidence")
    if record.get("status") != ACCEPTED:
        raise ValueError(str(record.get("reason") or "range_evidence_not_accepted"))
    payload = dict(record)
    fingerprint = payload.pop("sha256", None)
    if fingerprint != geometry_fingerprint(payload):
        raise ValueError("range_evidence_fingerprint_mismatch")
    if (
        not _identity(payload.get("shot_number"))
        or not isinstance(payload.get("session_uuid"), str)
        or not payload["session_uuid"]
    ):
        raise ValueError("range_evidence_shot_identity_missing")
    if (payload.get("target"), payload.get("quantity"), payload.get("value_kind")) != (
        "ball",
        "iwr_slant_launch_point_m",
        "fit_within_observed_support",
    ):
        raise ValueError("range_evidence_target_or_quantity_mismatch")
    if (
        payload.get("physical_qualified") is not False
        or payload.get("camera_radar_clock_mapping") != "unqualified"
    ):
        raise ValueError("range_evidence_cannot_claim_physical_qualification")
    radar = payload.get("radar")
    if (
        not isinstance(radar, Mapping)
        or not _sha256(radar.get("capture_sha256"))
        or not _identity(radar.get("sequence"))
        or not _identity(radar.get("nbytes"))
    ):
        raise ValueError("range_evidence_radar_capture_missing")
    if abs(
        _positive(radar.get("trigger_timestamp")) - _positive(payload.get("shot_timestamp"))
    ) > _positive(radar.get("match_tolerance_s")):
        raise ValueError("range_evidence_capture_outside_shot_association")
    estimate, selected = payload.get("estimate"), payload.get("selected")
    if not isinstance(estimate, Mapping) or not isinstance(selected, Mapping):
        raise ValueError("range_evidence_estimator_snapshot_missing")
    if (
        estimate.get("schema"),
        estimate.get("status"),
        estimate.get("source"),
        estimate.get("label"),
    ) != (LAUNCH_SCHEMA, "measured", SOURCE, "measured"):
        raise ValueError("range_evidence_not_unambiguous_complete_iwr")
    if (
        estimate.get("partial") is not None
        or (estimate.get("competing") or {}).get("verdict") == "ambiguous"
    ):
        raise ValueError("range_evidence_partial_or_ambiguous")
    if (
        selected.get("ball_range_source") != SOURCE
        or selected.get("ball_range_label") != "measured"
    ):
        raise ValueError("range_evidence_selected_source_not_iwr")
    for key, selected_key in (
        ("range_m", "ball_range_m"),
        ("uncertainty_m", "ball_range_uncertainty_m"),
    ):
        if not math.isclose(
            _positive(estimate.get(key)),
            _positive(selected.get(selected_key)),
            rel_tol=1e-12,
            abs_tol=1e-9,
        ):
            raise ValueError("range_evidence_selection_disagrees_with_fit")
    track = estimate.get("ball_track")
    if not isinstance(track, Mapping) or track.get("n_inliers", 0) < MIN_BALL_INLIERS:
        raise ValueError("range_evidence_ball_support_missing")
    first, last, event = track.get("t_first_s"), track.get("t_last_s"), estimate.get("impact_t_s")
    if any(
        isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x)
        for x in (first, last, event)
    ):
        raise ValueError("range_evidence_time_support_invalid")
    if first < 0 or last - first < MIN_BALL_SPAN_S or not first <= event <= last:
        raise ValueError("range_evidence_launch_outside_observed_support")
    if payload.get("radar_clock_domain") != "iwr_capture_relative":
        raise ValueError("range_evidence_clock_domain_unknown")
    return payload


def from_iwr_result(shot, result, *, match_tolerance_s: float | None) -> dict:
    """Freeze the correlated runtime's actual raw bytes and typed fit/selection."""
    try:
        capture = result.capture
        if (
            capture is None
            or not capture.valid
            or capture.error is not None
            or not isinstance(capture.raw, bytes)
            or not capture.raw
        ):
            raise ValueError("range_evidence_complete_radar_capture_missing")
        payload = {
            "schema": SCHEMA,
            "status": ACCEPTED,
            "session_uuid": shot.camera_fusion_session_uuid,
            "shot_number": shot.shot_number,
            "shot_timestamp": shot.impact_timestamp,
            "target": "ball",
            "quantity": "iwr_slant_launch_point_m",
            "value_kind": "fit_within_observed_support",
            "physical_qualified": False,
            "camera_radar_clock_mapping": "unqualified",
            "radar_clock_domain": "iwr_capture_relative",
            "camera_capture_sha256": None,
            "radar": {
                "capture_sha256": hashlib.sha256(capture.raw).hexdigest(),
                "nbytes": len(capture.raw),
                "sequence": capture.sequence,
                "trigger_timestamp": capture.trigger_timestamp,
                "match_tolerance_s": match_tolerance_s,
            },
            "estimate": result.launch_range.to_dict(),
            "selected": result.ball_range.to_dict(),
        }
        record = _seal(payload)
        _checked(record)
        return record
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        return unavailable(str(exc))


def bind_camera(
    record: Mapping | None, capture_sha256: str, *, session_uuid: str, shot_number: int
) -> dict:
    """Bind a verified camera archive on the same server shot, without RF-time claims."""
    try:
        payload = _checked(record)
        if (
            not _identity(shot_number)
            or payload["session_uuid"] != session_uuid
            or payload["shot_number"] != shot_number
        ):
            raise ValueError("range_evidence_camera_shot_mismatch")
        if not _sha256(capture_sha256):
            raise ValueError("range_evidence_camera_capture_missing")
        if payload.get("camera_capture_sha256") not in (None, capture_sha256):
            raise ValueError("range_evidence_camera_capture_already_bound")
        payload["camera_capture_sha256"] = capture_sha256
        return _seal(payload)
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        return unavailable(str(exc))


def read_shot_range(
    shot: Mapping, *, session_uuid: str | None, capture_sha256: str | None
) -> tuple[float, float, dict]:
    """Verify this reader's shot/capture and scalar fields against the frozen evidence."""
    payload = _checked(shot.get("impact_range_evidence"))
    if (
        not session_uuid
        or not _identity(shot.get("shot_number"))
        or payload["session_uuid"] != session_uuid
        or payload["shot_number"] != shot.get("shot_number")
    ):
        raise ValueError("range_evidence_reader_shot_mismatch")
    if not _sha256(capture_sha256) or payload.get("camera_capture_sha256") != capture_sha256:
        raise ValueError("range_evidence_reader_capture_mismatch")
    if payload["shot_timestamp"] != _positive(shot.get("impact_timestamp")):
        raise ValueError("range_evidence_reader_event_mismatch")
    selected = payload["selected"]
    _positive(shot.get("ball_range_m"))
    _positive(shot.get("ball_range_uncertainty_m"))
    for key in (
        "ball_range_m",
        "ball_range_uncertainty_m",
        "ball_range_source",
        "ball_range_label",
    ):
        if shot.get(key) != selected.get(key):
            raise ValueError("range_evidence_reader_selection_mismatch")
    return selected["ball_range_m"], selected["ball_range_uncertainty_m"], _seal(payload)

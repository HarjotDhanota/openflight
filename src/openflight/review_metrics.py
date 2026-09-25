"""Classify one replay report into review metrics with status, source and reason.

Statuses follow each estimator's own acceptance rule; nothing here judges accuracy.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from openflight.camera.ball_flight import (
    REFERENCE_BALL_DIAMETER_PX,
    REFERENCE_BALL_X_FRACTION,
    REFERENCE_BALL_Y_FRACTION,
)

STATUSES = (
    "accepted",
    "experimental",
    "rejected",
    "not_requested",
    "unavailable",
    "processing_failed",
)
ACCEPTED_DELIVERY_STATUSES = frozenset({"ok", "fused", "chained_high", "approach_high"})
# Replay stage errors that mean an input was never recorded, not that processing broke.
_ABSENT_INPUT_MARKERS = (
    "found 0",
    "not in the session folder",
    "records no",
    "is absent",
    "no replayable camera fusion context",
    "records a capture error",
    "has no recorded",
    "no OPS ball speed",
)


def finite(value: Any) -> float | None:
    """A finite float, or None for anything else (bools included)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def mapping(value: Any) -> Mapping[str, Any]:
    """The value when it is a mapping, else an empty one."""
    return value if isinstance(value, Mapping) else {}


def _metric(  # pylint: disable=too-many-arguments
    key: str,
    label: str,
    unit: str,
    status: str,
    *,
    source: str,
    value: Any = None,
    confidence: Any = None,
    reason: str | None = None,
    recorded_status: str | None = None,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if status not in STATUSES:
        raise ValueError(f"unknown metric status {status!r}")
    number = finite(value)
    if status in ("not_requested", "unavailable", "processing_failed"):
        number = None
    return {
        "key": key,
        "label": label,
        "value": number,
        "unit": unit,
        "status": status,
        "source": source,
        "confidence": finite(confidence),
        "reason": reason,
        "recorded_status": recorded_status,
        "validation": "unvalidated",
        "details": dict(details or {}),
    }


def _stage_state(stage: Any, name: str) -> tuple[str | None, str | None]:
    """How an unusable stage reads, or (None, None) when it produced results."""
    if not isinstance(stage, Mapping):
        return "unavailable", f"replay recorded no {name} stage"
    status = stage.get("status")
    if status == "not_requested":
        return "not_requested", str(stage.get("reason") or f"the {name} stage was not requested")
    if status == "error":
        error = str(stage.get("error") or "unknown error")
        if any(marker in error for marker in _ABSENT_INPUT_MARKERS):
            return "unavailable", error
        return "processing_failed", error
    return None, None


def _ops_metrics(stage: Any) -> list[dict[str, Any]]:
    source = "ops_rolling_buffer_replay"
    specs = (
        ("ball_speed_mph", "Ball speed (OPS radial)", "mph"),
        ("club_speed_mph", "Club speed (OPS radial)", "mph"),
        ("spin_rpm", "Spin (OPS)", "rpm"),
    )
    state, reason = _stage_state(stage, "OPS")
    result = mapping(mapping(stage).get("result"))
    if state is None and not result:
        state, reason = "rejected", "OPS processor found no shot in the capture"
    if state is not None:
        return [
            _metric(key, label, unit, state, source=source, reason=reason)
            for key, label, unit in specs
        ]
    details = {"equivalence_status": stage.get("equivalence_status")}
    ball = finite(result.get("ball_speed_mph"))
    club = finite(result.get("club_speed_mph"))
    metrics = [
        _metric(
            "ball_speed_mph",
            "Ball speed (OPS radial)",
            "mph",
            "accepted" if ball is not None else "rejected",
            source=source,
            value=ball,
            reason=None if ball is not None else "OPS processor reported no ball speed",
            details=details,
        ),
        _metric(
            "club_speed_mph",
            "Club speed (OPS radial)",
            "mph",
            "accepted" if club is not None else "rejected",
            source=source,
            value=club,
            reason=None if club is not None else "no club return was found before impact",
            details=details,
        ),
    ]
    metrics.append(_spin_metric(result.get("spin"), source))
    return metrics


def _spin_metric(spin: Any, source: str) -> dict[str, Any]:
    if not isinstance(spin, Mapping):
        return _metric(
            "spin_rpm",
            "Spin (OPS)",
            "rpm",
            "rejected",
            source=source,
            reason="spin was not measured",
        )
    quality = spin.get("quality")
    confidence = finite(spin.get("confidence"))
    value = finite(spin.get("spin_rpm"))
    details = {
        key: spin.get(key)
        for key in ("method", "quality", "snr", "phase_confirmed", "at_lower_rail", "at_upper_rail")
    }
    details["candidate_count"] = len(spin.get("candidates") or [])
    if value is None:
        return _metric(
            "spin_rpm",
            "Spin (OPS)",
            "rpm",
            "rejected",
            source=source,
            confidence=confidence,
            reason=str(spin.get("rejection_reason") or quality or "no spin signal"),
            recorded_status=quality,
            details=details,
        )
    # The processor's own reliability rule (SpinResult.is_reliable).
    reliable = confidence is not None and confidence >= 0.6 and quality in ("high", "medium")
    return _metric(
        "spin_rpm",
        "Spin (OPS)",
        "rpm",
        "accepted" if reliable else "experimental",
        source=source,
        value=value,
        confidence=confidence,
        reason=None if reliable else f"candidate only: quality {quality}, confidence {confidence}",
        recorded_status=quality,
        details=details,
    )


def _iwr_metrics(stage: Any) -> list[dict[str, Any]]:
    source = "iwr6843_raw_replay"
    specs = (
        ("iwr_launch_vertical_deg", "Vertical launch (IWR)", "deg"),
        ("iwr_launch_horizontal_deg", "Horizontal launch (IWR)", "deg"),
        ("iwr_club_path_deg", "Club path (IWR)", "deg"),
        ("iwr_attack_angle_deg", "Attack angle (IWR)", "deg"),
    )
    state, reason = _stage_state(stage, "IWR6843")
    if state is not None:
        return [
            _metric(key, label, unit, state, source=source, reason=reason)
            for key, label, unit in specs
        ]
    status = str(stage.get("status") or "")
    accepted = status.startswith("accepted")
    vertical = finite(stage.get("launch_angle_deg"))
    details = {
        key: stage.get(key)
        for key in ("status", "tracker_quality", "single_channel", "n_frames", "component_std_deg")
    }
    if accepted and vertical is not None:
        cautions = [
            text
            for applies, text in (
                ("warning" in status, f"accepted with estimator warning {status}"),
                (stage.get("single_channel") is True, "one receive channel only"),
                (stage.get("tracker_quality") == "low", "tracker quality low"),
            )
            if applies
        ]
        vertical_reason = "; ".join(cautions) or None
    else:
        vertical_reason = f"IWR estimator status {status or 'unknown'}"
    metrics = [
        _metric(
            "iwr_launch_vertical_deg",
            "Vertical launch (IWR)",
            "deg",
            "accepted" if accepted and vertical is not None else "rejected",
            source=source,
            value=vertical,
            reason=vertical_reason,
            recorded_status=status,
            details=details,
        )
    ]
    horizontal = finite(stage.get("horizontal_deg"))
    horizontal_status = stage.get("horizontal_status")
    metrics.append(
        _metric(
            "iwr_launch_horizontal_deg",
            "Horizontal launch (IWR)",
            "deg",
            "accepted" if accepted and horizontal is not None else "rejected",
            source=source,
            value=horizontal,
            confidence=stage.get("horizontal_confidence"),
            reason=None
            if accepted and horizontal is not None
            else f"withheld: {horizontal_status or status or 'no horizontal estimate'}",
            recorded_status=horizontal_status,
        )
    )
    club = stage.get("club_path")
    if not isinstance(club, Mapping):
        reason = "IWR club estimator returned no result"
        metrics.extend(
            _metric(key, label, unit, "unavailable", source=source, reason=reason)
            for key, label, unit in specs[2:]
        )
        return metrics
    club_status = str(club.get("status") or "")
    path = finite(club.get("path_deg"))
    candidate_path = finite(club.get("candidate_path_deg"))
    club_details = {
        key: club.get(key) for key in ("status", "n_frames", "n_snapshots", "track_selection_mode")
    }
    if path is not None and club_status.startswith("accepted"):
        path_metric = _metric(
            "iwr_club_path_deg",
            "Club path (IWR)",
            "deg",
            "accepted",
            source=source,
            value=path,
            confidence=club.get("confidence"),
            recorded_status=club_status,
            details=club_details,
        )
    elif candidate_path is not None:
        path_metric = _metric(
            "iwr_club_path_deg",
            "Club path (IWR)",
            "deg",
            "experimental",
            source=source,
            value=candidate_path,
            reason=f"candidate only: {club.get('candidate_path_status') or club_status}",
            recorded_status=club_status,
            details=club_details,
        )
    else:
        path_metric = _metric(
            "iwr_club_path_deg",
            "Club path (IWR)",
            "deg",
            "rejected",
            source=source,
            reason=f"IWR club status {club_status or 'unknown'}",
            recorded_status=club_status,
            details=club_details,
        )
    metrics.append(path_metric)
    attack = finite(club.get("candidate_attack_angle_deg"))
    attack_status = club.get("attack_angle_status")
    metrics.append(
        _metric(
            "iwr_attack_angle_deg",
            "Attack angle (IWR)",
            "deg",
            "experimental" if attack is not None else "rejected",
            source=source,
            value=attack,
            reason=f"candidate only: {attack_status}"
            if attack is not None
            else f"IWR club status {attack_status or club_status or 'unknown'}",
            recorded_status=attack_status or club_status,
            details=club_details,
        )
    )
    return metrics


def _camera_result(stage: Mapping[str, Any]) -> tuple[Mapping[str, Any], str]:
    recomputed = mapping(stage.get("recomputed_radar_context"))
    if recomputed.get("status") == "replayed" and isinstance(recomputed.get("result"), Mapping):
        return recomputed["result"], "replayed_radar_context"
    return mapping(mapping(stage.get("recorded_context")).get("replay")), "recorded_live_context"


def _reference_summary(diagnostics: Mapping[str, Any]) -> str:
    parts = []
    for name in ("scene", "impact"):
        entry = mapping(diagnostics.get(name))
        candidate = mapping(entry.get("candidate"))
        where = ""
        if finite(candidate.get("x")) is not None and finite(candidate.get("y")) is not None:
            where = (
                f" at ({candidate['x']:.0f}, {candidate['y']:.0f}),"
                f" {finite(candidate.get('diameter_px')) or 0:.1f} px"
            )
        parts.append(f"{name}: {entry.get('reason') or entry.get('status') or 'no result'}{where}")
    return "; ".join(parts)


def _camera_metrics(stage: Any) -> tuple[list[dict[str, Any]], Mapping[str, Any], str | None]:
    specs = (
        ("camera_launch_horizontal_deg", "Horizontal launch (camera)", "deg"),
        ("camera_launch_vertical_deg", "Vertical launch (camera)", "deg"),
        ("camera_club_path_deg", "Club path (camera + IWR)", "deg"),
        ("camera_attack_angle_deg", "Attack angle (camera + IWR)", "deg"),
    )
    state, reason = _stage_state(stage, "camera")
    if state is not None:
        return (
            [
                _metric(key, label, unit, state, source="camera_replay", reason=reason)
                for key, label, unit in specs
            ],
            {},
            None,
        )
    result, context_source = _camera_result(stage)
    source = f"camera_replay:{context_source}"
    ball = mapping(result.get("ball_estimate"))
    ball_status = str(ball.get("status") or "not recorded")
    tier = ball.get("confidence_tier")
    diagnostics = mapping(ball.get("reference_ball_diagnostics"))
    ball_details = {
        "confidence_tier": tier,
        "support": ball.get("support"),
        "depth_source": ball.get("depth_source"),
    }
    if ball_status.startswith("accepted"):
        ball_state = "accepted" if tier == "high" and ball_status == "accepted" else "experimental"
        ball_reason = None if ball_state == "accepted" else f"confidence tier {tier}"
    else:
        ball_state = "rejected"
        ball_reason = ball_status
        if diagnostics:
            ball_reason = f"{ball_status} ({_reference_summary(diagnostics)})"
    metrics = [
        _metric(
            key,
            label,
            "deg",
            ball_state,
            source=source,
            value=ball.get(field),
            reason=ball_reason,
            recorded_status=ball_status,
            details=ball_details,
        )
        for (key, label, _unit), field in zip(specs[:2], ("horizontal_deg", "vertical_deg"))
    ]
    delivery = mapping(result.get("club_delivery"))
    delivery_status = str(delivery.get("status") or "not recorded")
    for (key, label, _unit), field, tier_field in (
        (specs[2], "club_path_deg", "path_confidence_tier"),
        (specs[3], "attack_angle_deg", "attack_confidence_tier"),
    ):
        value = finite(delivery.get(field))
        field_tier = delivery.get(tier_field)
        if delivery_status in ACCEPTED_DELIVERY_STATUSES and value is not None:
            status = "accepted" if field_tier == "high" else "experimental"
            why = None if status == "accepted" else f"confidence tier {field_tier}"
        else:
            status = "rejected"
            why = delivery_status
            if delivery_status == "rejected_no_ball":
                why = f"{delivery_status}: needs the camera reference ball, which was {ball_status}"
        metrics.append(
            _metric(
                key,
                label,
                "deg",
                status,
                source=source,
                value=value,
                reason=why,
                recorded_status=delivery_status,
                details={
                    "confidence_tier": field_tier,
                    "speed_ratio_ops": delivery.get("speed_ratio_ops"),
                },
            )
        )
    return metrics, result, context_source


def _total_speed_metric(stage: Any) -> dict[str, Any]:
    label = "Total ball speed candidate (OPS projection)"
    state, reason = _stage_state(stage, "total-speed projection")
    if state is not None:
        return _metric(
            "ball_speed_total_mph", label, "mph", state, source="ops_projection", reason=reason
        )
    candidate = mapping(mapping(stage).get("candidate"))
    value = finite(candidate.get("value_mph"))
    if candidate.get("status") == "available" and value is not None:
        return _metric(
            "ball_speed_total_mph",
            label,
            "mph",
            "experimental",
            source="ops_projection",
            value=value,
            reason="unvalidated projection candidate",
            recorded_status="available",
        )
    return _metric(
        "ball_speed_total_mph",
        label,
        "mph",
        "rejected",
        source="ops_projection",
        reason=str(candidate.get("reason") or candidate.get("status") or "no candidate"),
        recorded_status=candidate.get("status"),
    )


def overlay(result: Mapping[str, Any]) -> dict[str, Any]:
    """The gate region the estimator enforces and every candidate it considered."""
    diagnostics = mapping(mapping(result.get("ball_estimate")).get("reference_ball_diagnostics"))
    candidates = []
    for name in ("scene", "impact"):
        entry = mapping(diagnostics.get(name))
        candidate = mapping(entry.get("candidate"))
        if finite(candidate.get("x")) is None or finite(candidate.get("y")) is None:
            continue
        candidates.append(
            {
                "detector": name,
                "x": float(candidate["x"]),
                "y": float(candidate["y"]),
                "diameter_px": finite(candidate.get("diameter_px")),
                "status": entry.get("status"),
                "reason": entry.get("reason"),
            }
        )
    selected = mapping(diagnostics.get("selected_candidate"))
    if finite(selected.get("x")) is not None and finite(selected.get("y")) is not None:
        candidates.append(
            {
                "detector": "selected",
                "x": float(selected["x"]),
                "y": float(selected["y"]),
                "diameter_px": finite(selected.get("diameter_px")),
                "status": "selected",
                "reason": diagnostics.get("selected_source"),
            }
        )
    return {
        "expected_region": {
            "x_fraction": list(REFERENCE_BALL_X_FRACTION),
            "y_fraction": list(REFERENCE_BALL_Y_FRACTION),
            "diameter_px": list(REFERENCE_BALL_DIAMETER_PX),
            "source": "openflight.camera.ball_flight reference-ball gate",
        },
        "candidates": candidates,
    }


def _comparison(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    key: str,
    label: str,
    unit: str,
    first: tuple[str, Any],
    second: tuple[str, Any],
    note: str,
) -> dict[str, Any]:
    a, b = finite(first[1]), finite(second[1])
    compared = a is not None and b is not None
    return {
        "key": key,
        "label": label,
        "unit": unit,
        "status": "compared" if compared else "unavailable",
        "values": [{"source": first[0], "value": a}, {"source": second[0], "value": b}],
        "difference": b - a if compared else None,
        "note": note if compared else "one side was not produced",
    }


def _agreements(
    report: Mapping[str, Any], live: Mapping[str, Any], camera_result: Mapping[str, Any]
) -> list[dict[str, Any]]:
    stages = mapping(report.get("stages"))
    ops = mapping(mapping(stages.get("ops")).get("result"))
    iwr = mapping(stages.get("iwr6843"))
    decision = mapping(camera_result.get("horizontal_decision"))
    rows = [
        _comparison(
            "live_vs_replay_ball_speed",
            "Ball speed: live vs replay",
            "mph",
            ("live_shot", live.get("ball_speed_mph")),
            ("replay", ops.get("ball_speed_mph")),
            "same raw samples; a difference means processing changed",
        ),
        _comparison(
            "ops_ball_vs_iwr_track_speed",
            "Ball speed: OPS radial vs IWR track",
            "mph",
            ("ops_radial", ops.get("ball_speed_mph")),
            ("iwr_track", iwr.get("track_speed_mph")),
            "independent sensors; neither is a reference",
        ),
        _comparison(
            "iwr_vs_camera_horizontal",
            "Horizontal launch: IWR vs camera",
            "deg",
            ("iwr6843", decision.get("iwr_horizontal_deg")),
            ("camera", decision.get("camera_horizontal_deg")),
            f"fusion decision {decision.get('status')}",
        ),
    ]
    recorded = mapping(mapping(stages.get("camera")).get("recorded_context"))
    if "matches_recorded" in recorded:
        mismatches = mapping(recorded.get("comparison")).get("mismatches")
        rows.append(
            {
                "key": "camera_replay_vs_live",
                "label": "Camera: replay vs live result",
                "unit": None,
                "status": "compared"
                if recorded.get("matches_recorded") is not None
                else "unavailable",
                "values": [],
                "difference": None,
                "matches": recorded.get("matches_recorded"),
                "note": f"{len(mismatches or [])} mismatched fields"
                if recorded.get("matches_recorded") is False
                else "identical within 1e-9",
            }
        )
    return rows


def review_replay(report: Mapping[str, Any] | None, live: Mapping[str, Any]) -> dict[str, Any]:
    """Metrics, stage states, overlay and agreements from one replay report."""
    if report is None:
        reviewed = review_replay({"stages": {}}, live)
        for metric in reviewed["metrics"]:
            metric["reason"] = "this shot has not been analysed yet"
        return {**reviewed, "stages": {}}
    stages = mapping(report.get("stages"))
    camera_metrics, camera_result, context_source = _camera_metrics(stages.get("camera"))
    metrics = [
        *_ops_metrics(stages.get("ops")),
        _total_speed_metric(stages.get("measured_total_speed_candidate")),
        *_iwr_metrics(stages.get("iwr6843")),
        *camera_metrics,
    ]
    stage_states = {}
    for name in ("ops", "iwr6843", "camera", "measured_total_speed_candidate"):
        stage = mapping(stages.get(name))
        stage_states[name] = {
            "status": stage.get("status") or ("replayed" if stage else "absent"),
            "error": stage.get("error"),
        }
    if context_source:
        stage_states["camera"]["context"] = context_source
    return {
        "metrics": metrics,
        "stages": stage_states,
        "overlay": overlay(camera_result),
        "agreements": _agreements(report, live, camera_result),
    }

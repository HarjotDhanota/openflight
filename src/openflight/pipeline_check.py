"""Stage-by-stage verdicts for a tester session replayed end to end (P8-6).

``scripts/analysis/check_pipeline.py`` replays every shot through the code the
Pi runs (the kiosk's IWR hand-off, camera fusion and face angle) and through the
review path (``replay_raw_fusion`` and ``review_metrics``). This module turns
what each stage produced into one table per shot. Every stage is ``pass``,
``fail``, ``not_reached`` (an upstream stage stopped it) or ``not_requested``,
and a stage that does not pass names its cause:

- ``DATA``: the capture itself cannot support the stage (no OPS reading, a
  clipped ball, a setup that never saved a range);
- ``CODE``: a wiring or contract failure (a value not handed over, a status
  string not recognised, two paths disagreeing about the same input);
- ``PENDING``: a named dependency that is not built yet;
- ``UNKNOWN``: an estimator refused and the recorded facts do not say why; the
  facts are given and no cause is claimed.

A cause is named only from evidence the stage recorded. Nothing here judges
accuracy, and nothing here changes a gate: each failing stage names the gate
(``gate``, a code location) that stopped it.
"""

# pylint: disable=too-many-lines
from __future__ import annotations

import math
from typing import Any, Iterable, Mapping

from openflight.camera import ball_pixels
from openflight.camera.ball_flight import REFERENCE_BALL_X_FRACTION, REFERENCE_BALL_Y_FRACTION
from openflight.review_metrics import QUALIFIED_TEE_SOURCES, finite, mapping

SCHEMA = "openflight.pipeline_check.v1"
PASS, FAIL, NOT_REACHED, NOT_REQUESTED = "pass", "fail", "not_reached", "not_requested"
CAUSES = ("DATA", "CODE", "PENDING", "UNKNOWN")

# Where each stage can stop a shot on its way to a metric, in pipeline order.
GATES = {
    "setup_admission": "camera/tester_server.py: setup admission (setup_admission.json blockers)",
    "setup_box_ball": "server.py: --camera-setup-ball / --camera-hitting-zone hand-off",
    "tee_range": "server.py: tee_range_handoff (--iwr6843-tee-range-* from the setup)",
    "camera_vertical_offset": "P8-3 (not built)",
    "ops_shot": "rolling_buffer/processor.py: OPS shot extraction",
    "trigger_evidence": "server.py: tester trigger readiness (missing_trigger_evidence, P7-3)",
    "clip_matched": "camera/capture_runtime.py: capture_for_shot association",
    "lighting": "camera/capture_runtime.py::_judged_on_setup_ball / server.py::"
    "_fuse_camera_measurements analysis_eligible",
    "optical_quality": "camera/optical_quality.py::capture_optical_quality",
    "strip_offset": "server.py::_strip_offset_refusal",
    "geometry": "camera/geometry_contract.py::EffectiveCameraGeometryInputs",
    "context": "server.py::_fuse_camera_measurements (build_context)",
    "reference_ball": "camera/ball_flight.py::_select_reference_ball (fixed gate region and size)",
    "ball_flight": "camera/ball_flight.py::estimate_camera_ball_flight",
    "club_delivery": "camera/club_delivery.py::estimate_chained_delivery",
    "iwr_capture": "iwr6843/runtime.py: dump matched to the OPS impact",
    "lcmf": "iwr6843/lcmf.py: LCMF acceptance",
    "iwr_club": "iwr6843/club.py: club track acceptance",
    "review_labels": "review_metrics.py: status labels",
    "face_angle": "server.py::_attach_experimental_face_angle",
}

STAGE_LABELS = {
    "setup_admitted": "Setup admitted",
    "box_and_setup_ball": "Placement box and setup ball",
    "tee_range": "Tee range",
    "camera_vertical_offset": "Camera vertical offset",
    "ops_shot": "OPS shot",
    "trigger_evidence": "Trigger evidence",
    "clip_matched": "Clip matched",
    "lighting": "Lighting eligibility",
    "geometry": "Effective camera geometry",
    "context": "Camera fusion context",
    "replay_agrees": "Review replay hands the camera what the kiosk does",
    "camera_ball": "Camera ball",
    "camera_club": "Camera club",
    "iwr_capture": "IWR capture matched",
    "lcmf": "LCMF status",
}

# Club delivery statuses whose values the kiosk shows (server.displayed_club_path
# refuses only rejected, error or withheld results).
_DELIVERY_REFUSED_PREFIXES = ("rejected", "error")
_ABSENT = object()


def stage(  # pylint: disable=too-many-arguments
    stage_id: str,
    status: str,
    *,
    label: str | None = None,
    value: Any = None,
    cause: str | None = None,
    evidence: str | None = None,
    gate: str | None = None,
    blocked_by: str | None = None,
    injected: bool = False,
    bypassed: bool = False,
) -> dict[str, Any]:
    """One row of the table; a failure without a cause is refused.

    ``bypassed`` marks a gate the operator asked the check to step past
    (``--bypass-gate``): the row keeps its verdict and downstream stages run as
    if it had passed, to show what the next gate would do.
    """
    if status not in (PASS, FAIL, NOT_REACHED, NOT_REQUESTED):
        raise ValueError(f"unknown stage status {status!r}")
    if status in (FAIL, NOT_REACHED) and cause not in CAUSES:
        raise ValueError(f"stage {stage_id} did not pass and names no cause")
    return {
        "id": stage_id,
        "label": label or STAGE_LABELS.get(stage_id, stage_id),
        "status": status,
        "value": value,
        "cause": cause if status in (FAIL, NOT_REACHED) else None,
        "evidence": evidence,
        "gate": GATES.get(gate, gate) if gate else None,
        "blocked_by": blocked_by,
        "injected": injected,
        "bypassed": bypassed and status == FAIL,
    }


def _blocked(stage_id: str, upstream: Mapping[str, Any], **extra) -> dict[str, Any]:
    """A stage an upstream failure stopped: it inherits that failure's cause."""
    return stage(
        stage_id,
        NOT_REACHED,
        cause=upstream["cause"],
        blocked_by=upstream["id"] if upstream["status"] == FAIL else upstream["blocked_by"],
        evidence=f"stopped upstream at {upstream['label']}",
        **extra,
    )


def passed(row: Mapping[str, Any] | None) -> bool:
    """Whether a stage row passed, or was a gate the check was told to step past."""
    return bool(row) and (row["status"] == PASS or bool(row.get("bypassed")))


def _round(value: Any, digits: int = 3) -> float | None:
    number = finite(value)
    return round(number, digits) if number is not None else None


# ---------------------------------------------------------------- setup (per run)


def setup_admitted(admission: Mapping[str, Any] | None, trigger_setup: Mapping[str, Any]) -> dict:
    """The run's recorded setup admission, and the trigger's readiness."""
    if admission is None:
        if trigger_setup.get("ready") is True:
            return stage(
                "setup_admitted",
                PASS,
                value="trigger readiness only",
                evidence="no setup_admission.json in the run; the trigger recorded ready",
            )
        return stage(
            "setup_admitted",
            FAIL,
            cause="DATA",
            gate="setup_admission",
            evidence="the run holds no setup_admission.json and no ready trigger",
        )
    blockers = list(admission.get("blockers") or [])
    if blockers:
        return stage(
            "setup_admitted",
            FAIL,
            cause="DATA",
            gate="setup_admission",
            evidence="admission blockers: " + "; ".join(str(b) for b in blockers),
        )
    warnings = [str(mapping(w).get("id") or w) for w in admission.get("warnings") or []]
    return stage(
        "setup_admitted",
        PASS,
        value=admission.get("config_hash", "")[:12] or "admitted",
        evidence=("warnings: " + ", ".join(warnings)) if warnings else None,
    )


def box_and_setup_ball(
    camera_config: Mapping[str, Any],
    *,
    injected: Mapping[str, Any],
    tester_box: Mapping[str, Any] | None,
) -> dict:
    """The setup ball and placement box the kiosk was started with."""
    ball = camera_config.get("setup_ball")
    box = camera_config.get("hitting_zone")
    is_injected = bool(injected.get("setup_ball") or injected.get("box"))
    if ball and box:
        value = f"ball ({ball['x']:.0f}, {ball['y']:.0f}, {ball['diameter_px']:.0f} px), box {box}"
        return stage("box_and_setup_ball", PASS, value=value, injected=is_injected)
    missing = [name for name, item in (("setup ball", ball), ("box", box)) if not item]
    if "setup_ball" not in camera_config and "hitting_zone" not in camera_config:
        return stage(
            "box_and_setup_ball",
            FAIL,
            cause="DATA",
            gate="setup_box_ball",
            injected=is_injected,
            evidence=(
                "session_start.config.camera_capture has no setup_ball or hitting_zone key: "
                "recorded by a build before P7-8/P7-15"
            ),
        )
    if tester_box is not None and "box" in missing:
        return stage(
            "box_and_setup_ball",
            FAIL,
            cause="CODE",
            gate="setup_box_ball",
            injected=is_injected,
            evidence=(
                f"the tester confirmed a box {tester_box.get('box_px')} but the kiosk session "
                "was started without it (setup-side hand-off)"
            ),
        )
    return stage(
        "box_and_setup_ball",
        FAIL,
        cause="DATA",
        gate="setup_box_ball",
        injected=is_injected,
        evidence="the setup handed the kiosk no " + " and no ".join(missing),
    )


def tee_range(  # pylint: disable=too-many-arguments
    handoff: Mapping[str, Any],
    *,
    configured_tee: Any,
    runtime_tees: Iterable[Any],
    setup_record: Mapping[str, Any] | None,
    injected: bool,
) -> dict:
    """The tee range the swings stand on, its source and label, and that every holder agrees."""
    tee = finite(handoff.get("tee_slant_range_m"))
    source = handoff.get("source") if isinstance(handoff.get("source"), str) else "unknown"
    if tee is None:
        record = mapping(setup_record)
        candidates = [
            f"{c.get('source')} {c['radar_slant_range_m']:.3f} m"
            + ("" if c.get("selectable") else " (not selectable)")
            for c in record.get("candidates") or []
            if finite(mapping(c).get("radar_slant_range_m")) is not None
        ]
        facts = [f"handoff status {handoff.get('status')!r}, source {source!r}"]
        if record:
            facts.append(f"setup {record.get('status')!r}: {record.get('reason')}")
        if candidates:
            facts.append("saved candidates: " + ", ".join(candidates))
        return stage(
            "tee_range",
            FAIL,
            cause="DATA",
            gate="tee_range",
            injected=injected,
            evidence="the session recorded no tee range; " + "; ".join(facts),
        )
    # only holders the session recorded: an absent block is not a hand-off failure
    holders = {}
    if configured_tee is not _ABSENT:
        holders["session iwr6843.tee_slant_range_m"] = finite(configured_tee)
    for index, value in enumerate(runtime_tees, 1):
        holders[f"IWR runtime snapshot {index}"] = finite(value)
    disagree = {
        name: value
        for name, value in holders.items()
        if value is None or not math.isclose(value, tee, abs_tol=1e-9)
    }
    label = "qualified" if source in QUALIFIED_TEE_SOURCES else "experimental"
    value = f"{tee:.3f} m ({source}, {label})"
    if disagree:
        return stage(
            "tee_range",
            FAIL,
            cause="CODE",
            gate="tee_range",
            value=value,
            injected=injected,
            evidence="the tee range was not handed to every holder: "
            + ", ".join(f"{name}={holder}" for name, holder in disagree.items()),
        )
    return stage("tee_range", PASS, value=value, injected=injected)


def _find_vertical_offset(config: Mapping[str, Any]) -> tuple[str, Any] | None:
    for block_name in ("camera_capture", "scene", "effective_camera_geometry"):
        block = mapping(config.get(block_name))
        parameters = mapping(block.get("parameters"))
        for key, value in {**block, **parameters}.items():
            if "vertical_offset" in str(key) and value is not None:
                return f"{block_name}.{key}", value
    return None


def camera_vertical_offset(config: Mapping[str, Any]) -> dict:
    """The setup's solved camera vertical offset (P8-3); absent until it is built."""
    found = _find_vertical_offset(config)
    if found is not None:
        return stage("camera_vertical_offset", PASS, value=f"{found[0]} = {found[1]}")
    return stage(
        "camera_vertical_offset",
        FAIL,
        cause="PENDING",
        gate="camera_vertical_offset",
        value="absent",
        evidence="P8-3 (the setup's solved pitch plus principal-point row) is not built; "
        "the session records no vertical offset and the camera uses the level, "
        "nominal-centre model",
    )


def setup_stages(  # pylint: disable=too-many-arguments
    *,
    admission: Mapping[str, Any] | None,
    trigger_setup: Mapping[str, Any],
    config: Mapping[str, Any],
    injected: Mapping[str, Any],
    tester_box: Mapping[str, Any] | None,
    runtime_tees: Iterable[Any],
    setup_record: Mapping[str, Any] | None,
) -> list[dict]:
    """The four setup stages every swing of a run stands on."""
    return [
        setup_admitted(admission, trigger_setup),
        box_and_setup_ball(
            mapping(config.get("camera_capture")), injected=injected, tester_box=tester_box
        ),
        tee_range(
            mapping(config.get("tee_range_handoff")),
            configured_tee=mapping(config.get("iwr6843")).get("tee_slant_range_m", _ABSENT),
            runtime_tees=runtime_tees,
            setup_record=setup_record,
            injected=injected.get("tee_range_m") is not None,
        ),
        camera_vertical_offset(config),
    ]


# ---------------------------------------------------------------- per shot

_ABSENT_INPUT_MARKERS = (
    "found 0",
    "not in the session folder",
    "records no",
    "is absent",
    "records a capture error",
    "has no recorded",
    "no OPS ball speed",
)


def _error_cause(error: str) -> str:
    return "DATA" if any(marker in error for marker in _ABSENT_INPUT_MARKERS) else "UNKNOWN"


def ops_shot(report_stage: Mapping[str, Any]) -> dict:
    """The replayed OPS shot: ball speed, and club speed when there was a club return."""
    if report_stage.get("status") == "error":
        error = str(report_stage.get("error"))
        return stage("ops_shot", FAIL, cause=_error_cause(error), gate="ops_shot", evidence=error)
    result = mapping(report_stage.get("result"))
    ball = finite(result.get("ball_speed_mph"))
    if ball is None:
        return stage(
            "ops_shot",
            FAIL,
            cause="DATA",
            gate="ops_shot",
            evidence="the OPS processor found no ball in the saved I/Q",
        )
    club = finite(result.get("club_speed_mph"))
    value = f"ball {ball:.1f} mph, club " + (f"{club:.1f} mph" if club is not None else "none")
    return stage(
        "ops_shot",
        PASS,
        value=value,
        evidence=None if club is not None else "no club return before impact",
    )


def trigger_evidence(
    shot: Mapping[str, Any] | None,
    trigger_setup: Mapping[str, Any],
    *,
    dropped: Iterable[Mapping[str, Any]] = (),
) -> dict:
    """Whether the kiosk kept the shot with its trigger evidence (P7-3).

    ``dropped`` are the session's "Tester shot rejected" errors: the kiosk's
    readiness gate refusing an OPS shot, which logs no shot number.
    """
    if shot is None:
        dropped = list(dropped)
        facts = ""
        if dropped:
            blockers = {
                str(mapping(b).get("reason"))
                for event in dropped
                for b in mapping(mapping(event.get("context")).get("readiness")).get("blockers")
                or []
            }
            facts = (
                f"; the kiosk's readiness gate refused {len(dropped)} OPS shot(s) this run: "
                + "; ".join(sorted(blockers))
            )
        return stage(
            "trigger_evidence",
            FAIL,
            cause="DATA",
            gate="trigger_evidence",
            evidence="no shot_detected event was logged for these sensor events" + facts,
        )
    missing = list(shot.get("missing_trigger_evidence") or [])
    if missing:
        return stage(
            "trigger_evidence",
            FAIL,
            cause="DATA",
            gate="trigger_evidence",
            evidence="shot kept without trigger evidence: "
            + "; ".join(f"{mapping(m).get('id')} ({mapping(m).get('reason')})" for m in missing),
        )
    if trigger_setup.get("required") and trigger_setup.get("ready") is not True:
        return stage(
            "trigger_evidence",
            FAIL,
            cause="DATA",
            gate="trigger_evidence",
            evidence=f"trigger readiness not ready: {trigger_setup.get('blockers')}",
        )
    return stage("trigger_evidence", PASS, value="ready" if trigger_setup else "not required")


def clip_matched(outcome: Mapping[str, Any], clip_on_disk: bool) -> dict:
    """The camera clip matched to the shot, and present in the session folder."""
    if outcome.get("category") != "captured":
        detail = f" ({outcome['detail']})" if outcome.get("detail") else ""
        return stage(
            "clip_matched",
            FAIL,
            cause="DATA",
            gate="clip_matched",
            evidence=f"{outcome.get('label')}{detail}",
        )
    if not clip_on_disk:
        return stage(
            "clip_matched",
            FAIL,
            cause="DATA",
            gate="clip_matched",
            evidence="the matched clip's frames.npz is not in the session folder",
        )
    return stage("clip_matched", PASS, value="frames.npz")


def lighting(judgement: Mapping[str, Any], *, config_has_setup_ball: bool) -> dict:
    """Capture-time analysis eligibility: on the setup's ball (P7-8) or the zone rule."""
    row = _lighting(judgement, config_has_setup_ball=config_has_setup_ball)
    if judgement.get("bypassed") and row["status"] == FAIL:
        row["bypassed"] = True
    return row


def _lighting(judgement: Mapping[str, Any], *, config_has_setup_ball: bool) -> dict:
    rule = judgement.get("rule")
    injected = judgement.get("source") == "rejudged_with_injected_setup_ball"
    reason = judgement.get("reason")
    if (
        config_has_setup_ball
        and not injected
        and rule != "setup_ball"
        and judgement.get("manual_exposure")
    ):
        return stage(
            "lighting",
            FAIL if not judgement.get("eligible") else PASS,
            cause="CODE" if not judgement.get("eligible") else None,
            gate="lighting",
            evidence="the session carries a setup ball but the clip was judged on "
            f"rule {rule!r}, not the setup ball",
        )
    if judgement.get("eligible"):
        return stage(
            "lighting",
            PASS,
            value=f"eligible ({rule or 'zone'} rule)",
            evidence=reason,
            injected=injected,
        )
    if rule == "setup_ball":
        return stage(
            "lighting",
            FAIL,
            cause="DATA",
            gate="lighting",
            injected=injected,
            evidence=f"judged on the setup ball: {reason}",
        )
    return stage(
        "lighting",
        FAIL,
        cause="DATA",
        gate="lighting",
        injected=injected,
        evidence=f"judged on the hitting-zone box ({reason}); the session has no setup ball "
        "for the ball rule",
    )


def geometry(
    snapshot: Mapping[str, Any] | None, error: str | None, tee: dict, *, handed_tee_m: Any
) -> dict:
    """The effective camera geometry the kiosk builds per shot, which needs the tee range."""
    if snapshot is not None:
        parameters = mapping(snapshot.get("parameters"))
        value = (
            f"tee {parameters.get('tee_slant_range_m'):.3f} m, "
            f"{parameters.get('image_width_px')}x{parameters.get('image_height_px')}, "
            f"camera {parameters.get('camera_height_m')} m"
        )
        tee_value = finite(parameters.get("tee_slant_range_m"))
        handed = finite(handed_tee_m)
        if handed is not None and tee_value is not None and abs(tee_value - handed) > 1e-9:
            return stage(
                "geometry",
                FAIL,
                cause="CODE",
                gate="geometry",
                value=value,
                evidence=f"geometry tee {tee_value} differs from the handed tee {handed}",
            )
        return stage("geometry", PASS, value=value, injected=bool(tee.get("injected")))
    error = error or "no geometry was built"
    if "tee_slant_range_m" in error and not passed(tee):
        return _blocked("geometry", tee, gate="geometry")
    return stage("geometry", FAIL, cause="UNKNOWN", gate="geometry", evidence=error)


def context(live: Mapping[str, Any], upstream: Mapping[str, dict]) -> dict:
    """The camera fusion context the kiosk freezes for the shot."""
    built = mapping(live.get("context"))
    if built.get("available") is True:
        return stage(
            "context",
            PASS,
            value=f"{live.get('context_source')} ({str(built.get('sha256'))[:12]})",
        )
    reason = str(built.get("reason") or live.get("error") or "no context")
    if "lighting" in reason and not passed(upstream["lighting"]):
        return _blocked("context", upstream["lighting"], gate="lighting")
    if "missing effective camera geometry" in reason and not passed(upstream["geometry"]):
        return _blocked("context", upstream["geometry"], gate="geometry")
    if "optical quality withheld" in reason:
        return stage("context", FAIL, cause="DATA", gate="optical_quality", evidence=reason)
    if "strip offset" in reason:
        return stage("context", FAIL, cause="DATA", gate="strip_offset", evidence=reason)
    return stage("context", FAIL, cause="UNKNOWN", gate="context", evidence=reason)


# Inputs the kiosk freezes into the context that the review replay rebuilds on its own.
_HANDED_INPUTS = (
    "ops_ball_speed_mph",
    "ops_club_speed_mph",
    "iwr_vertical_deg",
    "iwr_horizontal_deg",
    "iwr_horizontal_confidence",
)


def replay_agrees(
    live_context: Mapping[str, Any] | None, recomputed: Mapping[str, Any] | None, ctx: dict
) -> dict:
    """The review replay's rebuilt context must hand the camera what the kiosk handed it."""
    if not passed(ctx):
        return _blocked("replay_agrees", ctx, gate="context")
    if mapping(live_context).get("available") is not True:
        return stage(
            "replay_agrees",
            FAIL,
            cause="UNKNOWN",
            gate="context",
            evidence="the kiosk path built no context for this shot: "
            + str(mapping(live_context).get("reason")),
        )
    if recomputed is None:
        return stage(
            "replay_agrees",
            NOT_REQUESTED,
            evidence="the review replay rebuilt no context (its IWR stage did not complete)",
        )
    live = mapping(live_context)
    differences = []
    for key in _HANDED_INPUTS:
        a, b = live.get(key), recomputed.get(key)
        if finite(a) is not None and finite(b) is not None:
            if not math.isclose(float(a), float(b), rel_tol=0.0, abs_tol=1e-9):
                differences.append(f"{key}: kiosk {a}, review {b}")
        elif a != b:
            differences.append(f"{key}: kiosk {a}, review {b}")
    for key in ("ball_range_evidence", "club_range_evidence"):
        if (live.get(key) is None) != (recomputed.get(key) is None):
            differences.append(
                f"{key}: kiosk {'set' if live.get(key) else 'none'}, review "
                f"{'set' if recomputed.get(key) else 'none'}"
            )
    if differences:
        return stage(
            "replay_agrees",
            FAIL,
            cause="CODE",
            gate="scripts/analysis/replay_raw_fusion.py::replay (recomputed_radar_context)",
            evidence="; ".join(differences),
        )
    return stage("replay_agrees", PASS, value="same inputs")


def _near_setup_ball(candidate: Mapping[str, Any], setup_ball: Mapping[str, Any]) -> bool:
    x, y = finite(candidate.get("x")), finite(candidate.get("y"))
    if x is None or y is None:
        return False
    return math.hypot(x - setup_ball["x"], y - setup_ball["y"]) <= max(
        8.0, setup_ball["diameter_px"]
    )


def _gate_admits(ball: Mapping[str, Any], width: int, height: int) -> list[str]:
    """Why the fixed resting-ball gate would refuse a ball at the setup's position."""
    smallest, largest = ball_pixels.ball_diameter_bounds_px(ball_pixels.pixel_scale(width))
    refusals = []
    if not smallest <= ball["diameter_px"] <= largest:
        refusals.append(
            f"diameter {ball['diameter_px']:.1f} px outside {smallest:.1f}-{largest:.1f} px"
        )
    left, right = (width * f for f in REFERENCE_BALL_X_FRACTION)
    top, bottom = (height * f for f in REFERENCE_BALL_Y_FRACTION)
    if not left <= ball["x"] <= right or not top <= ball["y"] <= bottom:
        refusals.append(
            f"centre ({ball['x']:.0f}, {ball['y']:.0f}) outside x {left:.0f}-{right:.0f}, "
            f"y {top:.0f}-{bottom:.0f}"
        )
    return refusals


def _reference_facts(diagnostics: Mapping[str, Any]) -> str:
    parts = []
    for name in ("scene", "impact"):
        entry = mapping(diagnostics.get(name))
        candidate = mapping(entry.get("candidate"))
        where = ""
        if finite(candidate.get("x")) is not None:
            where = (
                f" at ({candidate['x']:.0f}, {candidate['y']:.0f}) "
                f"{finite(candidate.get('diameter_px')) or 0:.1f} px"
            )
        parts.append(f"{name}: {entry.get('reason') or entry.get('status')}{where}")
    return "; ".join(parts)


def camera_ball(  # pylint: disable=too-many-return-statements
    result: Mapping[str, Any],
    upstream: Mapping[str, dict],
    *,
    setup_ball: Mapping[str, Any] | None,
    image_size: tuple[int, int] | None,
) -> dict:
    """The camera's ball-flight estimate for the shot."""
    if not passed(upstream["context"]):
        return _blocked("camera_ball", upstream["context"], gate="context")
    estimate = mapping(result.get("ball_estimate"))
    status = str(estimate.get("status") or "not recorded")
    if status.startswith("accepted"):
        return stage(
            "camera_ball",
            PASS,
            value=(
                f"{status}: horizontal {_round(estimate.get('horizontal_deg'), 2)}, "
                f"vertical {_round(estimate.get('vertical_deg'), 2)} "
                f"({estimate.get('confidence_tier')}, depth {estimate.get('depth_source')}, "
                f"timing {estimate.get('timing_anchor')})"
            ),
        )
    error = mapping(result.get("errors")).get("ball")
    diagnostics = mapping(estimate.get("reference_ball_diagnostics"))
    facts = f"{status}"
    if status == "rejected_lighting_quality":
        return _blocked("camera_ball", upstream["lighting"], gate="lighting")
    if status in ("rejected_insufficient_post_trigger_frames", "rejected_invalid_camera_frames"):
        return stage("camera_ball", FAIL, cause="DATA", gate="ball_flight", evidence=facts)
    if status == "rejected_calibrated_requires_iwr_range":
        return _blocked("camera_ball", upstream["lcmf"], gate="ball_flight")
    if status in ("rejected_reference_ball_not_found", "rejected_implausible_reference_ball"):
        facts = f"{status} ({_reference_facts(diagnostics)})"
        if setup_ball and image_size:
            refusals = _gate_admits(setup_ball, *image_size)
            if refusals:
                return stage(
                    "camera_ball",
                    FAIL,
                    cause="CODE",
                    gate="reference_ball",
                    evidence=f"{facts}; the gate refuses the setup's own ball: "
                    + "; ".join(refusals),
                )
            near = [
                name
                for name in ("scene", "impact")
                if _near_setup_ball(
                    mapping(mapping(diagnostics.get(name)).get("candidate")), setup_ball
                )
                and mapping(diagnostics.get(name)).get("reason")
            ]
            if near:
                return stage(
                    "camera_ball",
                    FAIL,
                    cause="CODE",
                    gate="reference_ball",
                    evidence=f"{facts}; the {', '.join(near)} candidate sits on the setup ball "
                    "and was refused",
                )
        return stage("camera_ball", FAIL, cause="UNKNOWN", gate="reference_ball", evidence=facts)
    details = ", ".join(
        f"{key} {estimate.get(key)}"
        for key in (
            "n_points",
            "support",
            "vertical_deg",
            "speed_mph",
            "speed_error_mph",
            "window_mad_deg",
            "depth_source",
            "range_evidence_status",
        )
        if estimate.get(key) is not None
    )
    evidence = f"{status}" + (f" ({details})" if details else "") + (f"; {error}" if error else "")
    return stage("camera_ball", FAIL, cause="UNKNOWN", gate="ball_flight", evidence=evidence)


def delivered(delivery: Mapping[str, Any]) -> bool:
    """Whether the kiosk shows the club delivery's values (not a refusal)."""
    status = str(delivery.get("status") or "")
    has_value = any(
        finite(delivery.get(k)) is not None for k in ("club_path_deg", "attack_angle_deg")
    )
    return has_value and not status.startswith(_DELIVERY_REFUSED_PREFIXES)


def camera_club(  # pylint: disable=too-many-return-statements
    result: Mapping[str, Any], upstream: Mapping[str, dict], *, ops_club_speed: Any
) -> dict:
    """The camera + IWR club delivery for the shot."""
    if not passed(upstream["context"]):
        return _blocked("camera_club", upstream["context"], gate="context")
    delivery = mapping(result.get("club_delivery"))
    status = str(delivery.get("status") or "not recorded")
    if delivered(delivery):
        return stage(
            "camera_club",
            PASS,
            value=(
                f"{status}: path {delivery.get('club_path_deg')}, "
                f"attack {delivery.get('attack_angle_deg')}"
            ),
        )
    if status == "rejected_no_ball":
        if passed(upstream["camera_ball"]):
            return stage(
                "camera_club",
                FAIL,
                cause="CODE",
                gate="club_delivery",
                evidence="the camera ball passed but club delivery says rejected_no_ball",
            )
        return _blocked("camera_club", upstream["camera_ball"], gate="club_delivery")
    if status == "rejected_lighting_quality":
        return _blocked("camera_club", upstream["lighting"], gate="lighting")
    if status == "rejected_no_ops_speed" and finite(ops_club_speed) is None:
        return stage(
            "camera_club",
            FAIL,
            cause="DATA",
            gate="club_delivery",
            evidence="rejected_no_ops_speed: the OPS found no club speed",
        )
    if status == "rejected_calibrated_requires_iwr_range":
        return _blocked("camera_club", upstream["lcmf"], gate="club_delivery")
    if status == "rejected_low_light":
        return stage(
            "camera_club",
            FAIL,
            cause="DATA",
            gate="club_delivery",
            evidence=f"rejected_low_light (scene p99.5 {delivery.get('scene_p995')} DN)",
        )
    details = ", ".join(
        f"{key} {delivery.get(key)}"
        for key in (
            "n_features",
            "speed_ratio_ops",
            "velocity_mad_mph",
            "impact_frame",
            "impact_vs_trigger_ms",
            "range_evidence_status",
        )
        if delivery.get(key) not in (None, 0)
    )
    error = mapping(result.get("errors")).get("club")
    evidence = status + (f" ({details})" if details else "") + (f"; {error}" if error else "")
    if status == "rejected_no_impact":
        anchor = mapping(result.get("ball_estimate")).get("timing_anchor")
        evidence += "; camera_contact_time found no ball departure in the clip" + (
            f" (the ball stage's timing: {anchor})" if anchor else ""
        )
    return stage("camera_club", FAIL, cause="UNKNOWN", gate="club_delivery", evidence=evidence)


def iwr_capture(report_stage: Mapping[str, Any], iwr_event: Mapping[str, Any]) -> dict:
    """The IWR dump matched to the shot and present in the session folder."""
    if not iwr_event:
        return stage(
            "iwr_capture",
            FAIL,
            cause="DATA",
            gate="iwr_capture",
            evidence="no iwr6843_capture event for the shot",
        )
    if iwr_event.get("capture_error"):
        return stage(
            "iwr_capture",
            FAIL,
            cause="DATA",
            gate="iwr_capture",
            evidence=str(iwr_event["capture_error"]),
        )
    error = str(report_stage.get("error") or "")
    if "dump is unreadable" in error or "dump is empty" in error:
        return stage("iwr_capture", FAIL, cause="DATA", gate="iwr_capture", evidence=error)
    return stage("iwr_capture", PASS, value=f"{iwr_event.get('capture_bytes')} bytes")


def lcmf(  # pylint: disable=too-many-return-statements
    report_stage: Mapping[str, Any], upstream: Mapping[str, dict]
) -> dict:
    """The replayed LCMF vertical launch."""
    if not passed(upstream["iwr_capture"]):
        return _blocked("lcmf", upstream["iwr_capture"], gate="lcmf")
    status = str(report_stage.get("status") or "")
    if status == "withheld" and report_stage.get("reason") == "tee_range_unresolved":
        return _blocked("lcmf", upstream["tee_range"], gate="lcmf")
    if status == "error":
        error = str(report_stage.get("error"))
        if "no OPS ball speed" in error and not passed(upstream["ops_shot"]):
            return _blocked("lcmf", upstream["ops_shot"], gate="lcmf")
        return stage("lcmf", FAIL, cause=_error_cause(error), gate="lcmf", evidence=error)
    angle = finite(report_stage.get("launch_angle_deg"))
    if status.startswith("accepted") and angle is not None:
        channel = " one channel" if report_stage.get("single_channel") else ""
        return stage("lcmf", PASS, value=f"{status}: {angle:.2f} deg{channel}")
    details = ", ".join(
        f"{key} {report_stage.get(key)}"
        for key in (
            "tracker_quality",
            "n_frames",
            "n_snapshots",
            "track_inliers",
            "track_speed_mph",
            "component_std_deg",
        )
        if report_stage.get(key) not in (None, 0)
    )
    return stage(
        "lcmf",
        FAIL,
        cause="UNKNOWN",
        gate="lcmf",
        evidence=f"LCMF status {status or 'unknown'}" + (f" ({details})" if details else ""),
    )


# ---------------------------------------------------------------- metrics

# Which stage a review metric stands on.
_METRIC_UPSTREAM = {
    "ball_speed_mph": "ops_shot",
    "club_speed_mph": "ops_shot",
    "spin_rpm": "ops_shot",
    "iwr_launch_vertical_deg": "lcmf",
    "iwr_launch_horizontal_deg": "lcmf",
    "iwr_club_path_deg": "lcmf",
    "iwr_attack_angle_deg": "lcmf",
    "camera_launch_horizontal_deg": "camera_ball",
    "camera_launch_vertical_deg": "camera_ball",
    "camera_club_path_deg": "camera_club",
    "camera_attack_angle_deg": "camera_club",
}
_SHOWN = ("accepted", "experimental")


def _metric_value(metric: Mapping[str, Any]) -> str:
    value = finite(metric.get("value"))
    shown = f"{value:.2f} {metric.get('unit')}" if value is not None else "-"
    return f"{shown} [{metric['status']}]"


def metric_stage(  # pylint: disable=too-many-return-statements
    metric: Mapping[str, Any],
    upstream: Mapping[str, dict],
    *,
    estimator: Mapping[str, Any],
    azimuth_calibrated: bool,
) -> dict:
    """One review metric: its value, status and label, checked against its estimator."""
    key = metric["key"]
    stage_id = f"metric:{key}"
    label = metric.get("label")
    status = metric.get("status")
    value = _metric_value(metric)
    if status == "not_requested":
        return stage(
            stage_id, NOT_REQUESTED, label=label, value=value, evidence=metric.get("reason")
        )
    if status in _SHOWN and finite(metric.get("value")) is not None:
        details = mapping(metric.get("details"))
        if (
            key.startswith("iwr_launch")
            and status == "accepted"
            and details.get("tee_range_qualified") is False
        ):
            return stage(
                stage_id,
                FAIL,
                label=label,
                value=value,
                cause="CODE",
                gate="review_labels",
                evidence="an IWR launch on an unqualified tee is labelled accepted",
            )
        if (
            key in ("iwr_launch_horizontal_deg", "iwr_club_path_deg")
            and not azimuth_calibrated
            and status == "accepted"
        ):
            return stage(
                stage_id,
                FAIL,
                label=label,
                value=value,
                cause="CODE",
                gate="review_labels",
                evidence="the kiosk marks this value azimuth_uncalibrated (no horizontal phase "
                "reference, audit F8); the review labels it accepted",
            )
        return stage(stage_id, PASS, label=label, value=value, evidence=metric.get("reason"))
    parent = upstream.get(_METRIC_UPSTREAM.get(key, ""))
    if key.startswith("camera_club") or key.startswith("camera_attack"):
        if delivered(estimator.get("club_delivery", {})):
            return stage(
                stage_id,
                FAIL,
                label=label,
                value=value,
                cause="CODE",
                gate="review_labels",
                evidence=(
                    f"club delivery {estimator['club_delivery'].get('status')!r} carries a "
                    f"value the kiosk shows, but the review reports {status!r} "
                    "(review_metrics.ACCEPTED_DELIVERY_STATUSES does not list it)"
                ),
            )
    if key.startswith("camera_launch") and parent is not None and passed(parent):
        return stage(
            stage_id,
            FAIL,
            label=label,
            value=value,
            cause="CODE",
            gate="review_labels",
            evidence=f"the camera ball passed but the review reports {status!r}: "
            f"{metric.get('reason')}",
        )
    if parent is not None and not passed(parent):
        return {
            **_blocked(stage_id, parent, gate=parent.get("gate")),
            "label": label,
            "value": value,
        }
    reason = str(metric.get("reason") or status)
    if key in ("iwr_launch_horizontal_deg",):
        return stage(
            stage_id, FAIL, label=label, value=value, cause="UNKNOWN", gate="lcmf", evidence=reason
        )
    if key in ("iwr_club_path_deg", "iwr_attack_angle_deg"):
        return stage(
            stage_id,
            FAIL,
            label=label,
            value=value,
            cause="UNKNOWN",
            gate="iwr_club",
            evidence=reason,
        )
    if key == "club_speed_mph":
        return stage(
            stage_id, FAIL, label=label, value=value, cause="DATA", gate="ops_shot", evidence=reason
        )
    if key == "spin_rpm":
        return stage(
            stage_id,
            FAIL,
            label=label,
            value=value,
            cause="UNKNOWN",
            gate="ops_shot",
            evidence=reason,
        )
    return stage(stage_id, FAIL, label=label, value=value, cause="UNKNOWN", evidence=reason)


def face_angle_stage(live: Mapping[str, Any], upstream: Mapping[str, dict]) -> dict:
    """The kiosk's D-plane face angle (server._attach_experimental_face_angle)."""
    status = live.get("face_angle_status")
    value = finite(live.get("face_angle_deg"))
    label = "Face angle (D-plane, kiosk)"
    if status == "d_plane_estimate" and value is not None:
        return stage(
            "metric:face_angle_deg",
            PASS,
            label=label,
            value=f"{value:.1f} deg (path {live.get('face_angle_path_source')}, "
            f"launch {live.get('face_angle_launch_source')})",
        )
    if status == "start_direction_azimuth_uncalibrated":
        return stage(
            "metric:face_angle_deg",
            FAIL,
            label=label,
            cause="PENDING",
            gate="face_angle",
            evidence="the start direction is the IWR's, which has no horizontal phase "
            "reference until the alignment-stick session (F8, D3)",
        )
    if status == "missing_measured_start_direction":
        parent = (
            upstream["camera_ball"] if not passed(upstream["camera_ball"]) else upstream["lcmf"]
        )
        if not passed(parent):
            return {**_blocked("metric:face_angle_deg", parent, gate="face_angle"), "label": label}
    if status == "missing_accepted_club_path" and not passed(upstream["camera_club"]):
        return {
            **_blocked("metric:face_angle_deg", upstream["camera_club"], gate="face_angle"),
            "label": label,
        }
    return stage(
        "metric:face_angle_deg",
        FAIL,
        label=label,
        cause="UNKNOWN",
        gate="face_angle",
        evidence=f"kiosk face-angle status {status}",
    )


def summarize(rows: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """Counts of each status and cause across rows."""
    counts: dict[str, int] = {}
    for row in rows:
        key = (
            row["status"]
            if row["status"] in (PASS, NOT_REQUESTED)
            else (f"{row['status']}:{row['cause']}")
        )
        counts[key] = counts.get(key, 0) + 1
    return counts


def format_table(rows: Iterable[Mapping[str, Any]], width: int = 110) -> str:
    """A plain fixed-width table: stage, status, value, cause, evidence."""
    lines = []
    for row in rows:
        verdict = row["status"].upper()
        if row["status"] in (FAIL, NOT_REACHED):
            verdict += f" {row['cause']}"
        if row.get("injected"):
            verdict += " (INJECTED)"
        if row.get("bypassed"):
            verdict += " (BYPASSED)"
        detail = row.get("evidence") or ""
        if row.get("blocked_by"):
            detail = f"blocked by {row['blocked_by']}"
        value = "" if row.get("value") is None else str(row["value"])
        line = f"  {row['label'][:40]:<40} {verdict:<26} {value[:44]:<44} {detail}"
        lines.append(line if len(line) <= width + 80 else line[: width + 77] + "...")
    return "\n".join(lines)

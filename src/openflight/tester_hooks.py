"""The tester's hooks into the kiosk: study mode, the setup gate and free play.

None of this runs on a plain kiosk. ``openflight.server`` knows only the
``KioskExtension`` hooks; this module registers ``extension`` on import (the
``openflight.kiosk_extensions`` entry point imports it at kiosk start-up) and
adds the tester's routes and flags:

- ``--study-mode``: the study page may set exposure and gain, read raw frames,
  and hold the ready light red (``/api/camera/study/*``, ``/api/ready-light/hold``);
- ``--tester-setup-required`` / ``--tester-config-hash``: the readiness gate every
  shot's trigger evidence must pass, and the setup problems on the ready light;
- ``--tester-free-play`` (with ``--free-play-ladder``): run-bound free play, its
  exposure ladder and the Stop & package proxy (``/api/free-play``), and its pauses
  for a face photo or a new aim (``/api/free-play/pause``, ``photo``, ``target-line``);
- ``--tester-control-port``: the tester page a tester-started kiosk's Tester button goes
  back to, while that page answers (``/api/tester-page``, ``tester_link``).
"""

from __future__ import annotations

import dataclasses
import json
import logging
import platform
import threading
import time
from copy import deepcopy
from pathlib import Path
from typing import Mapping

from flask import Response, jsonify, request

from openflight import (
    free_play,
    instrumentation,
    kiosk_extensions,
    server,
    shot_metrics,
    tester_link,
)
from openflight.free_play import register_kiosk_routes
from openflight.launch_monitor import Shot

# This module is the server's tester half: it reads a few of the server's own
# helpers (the LIS3DH's expected orientation, the ready light's publisher).
# pylint: disable=protected-access

logger = logging.getLogger("openflight.server")

# The tester study page may set exposure and gain and read raw frames only when
# the kiosk is started with --study-mode; production never exposes either.
study_mode_enabled = False
free_play_config: dict = {}
# Free play's exposure ladder (19 Oct): the plan the tester server wrote for the
# tapped resolution (--free-play-ladder), where it lives, and the kiosk's study of it.
free_play_plan: dict | None = None
free_play_plan_path: Path | None = None
free_play_study = None
tester_setup_required = False
tester_config_hash: str | None = None
# A paused free play: {"cause", "since", "controls"}, empty while recording.
free_play_pause: dict = {}
_pause_lock = threading.RLock()
PAUSE_CAUSES = {"photo": "taking a face photo", "aim": "moving the aim"}
# The red cause the tester page holds the ready light at (the ladder's phase), or None.
ready_hold: str | None = None
FACE_PHOTO_DIR = "face-photos"


def _study_runtime():
    """The capture runtime the study page may drive, or the response refusing it."""
    if not study_mode_enabled:
        return None, (jsonify({"error": "study mode is off"}), 404)
    if server.camera_capture_runtime is None:
        return None, (jsonify({"error": "camera capture is not running"}), 409)
    if server.camera_capture_runtime.settings.auto_exposure:
        return None, (
            jsonify({"error": "exposure is automatic; start with manual exposure"}),
            409,
        )
    return server.camera_capture_runtime, None


def _loaded_setup_config_hash() -> str:
    """The fingerprint of the rig geometry and LIS3DH settings this kiosk loaded."""
    from openflight.camera.setup_eligibility import setup_config_hash

    address = server.inclinometer_runtime_config.get("i2c_address")
    parsed_address = int(address, 0) if isinstance(address, str) else address
    return setup_config_hash(
        (server.rig_geometry_config.get("snapshot") or {}).get("sha256") or "",
        server.inclinometer_runtime_config.get("i2c_bus") or 0,
        parsed_address or 0,
        server.inclinometer_runtime_config.get("zero_offset_deg") or 0.0,
    )


def _tester_setup_readiness(
    *, require_camera_armed: bool = True, checked_at: float | None = None
) -> dict:
    """Report current health of the hardware required by the tester kiosk."""
    checks = []
    observations: dict = {}

    def add(
        check_id: str, passed: bool, reason: str | None = None, *, warned: bool = False
    ) -> None:
        checks.append(
            {
                "id": check_id,
                "status": "warn" if warned else ("pass" if passed else "block"),
                "reason": reason if warned or not passed else None,
            }
        )

    actual_hash = _loaded_setup_config_hash()
    add(
        "geometry",
        bool(tester_config_hash and actual_hash == tester_config_hash),
        "loaded rig geometry and LIS3DH settings do not match the required setup fingerprint",
    )
    observations["geometry"] = {"actual_config_hash": actual_hash}
    ops_thread = getattr(server.monitor, "_capture_thread", None)
    ops_serial = getattr(getattr(server.monitor, "radar", None), "serial", None)
    ops_ok = bool(
        server.monitor is not None
        and not server.mock_mode
        and getattr(server.monitor, "_running", False)
        and ops_thread is not None
        and ops_thread.is_alive()
        and ops_serial is not None
        and getattr(ops_serial, "is_open", False)
    )
    add("ops", ops_ok, "OPS capture transport is not running with an open serial link")
    observations["ops"] = {
        "running": bool(getattr(server.monitor, "_running", False)),
        "worker_alive": bool(ops_thread is not None and ops_thread.is_alive()),
        "serial_open": bool(ops_serial is not None and getattr(ops_serial, "is_open", False)),
    }

    camera_status = (
        server.camera_capture_runtime.status() if server.camera_capture_runtime is not None else {}
    )
    camera_age = camera_status.get("latest_frame_age_s")
    capture_mode = (
        getattr(server.camera_capture_runtime, "_startup_capture_mode", None)
        if server.camera_capture_runtime is not None
        else None
    ) or {}
    camera_model = ((capture_mode.get("camera_properties") or {}).get("Model") or "").casefold()
    resolved_raw_size = ((capture_mode.get("resolved_config") or {}).get("raw") or {}).get("size")
    requested_size = (
        [
            server.camera_capture_runtime.settings.width,
            server.camera_capture_runtime.settings.height,
        ]
        if server.camera_capture_runtime is not None
        else None
    )
    camera_ok = bool(
        server.camera_capture_runtime is not None
        and (
            not server.camera_capture_runtime.settings.auto_exposure
            or free_play_config.get("enabled")
        )
        and camera_status.get("running")
        and (camera_status.get("armed") or not require_camera_armed)
        and camera_age is not None
        and camera_age <= 2.0
        and ("ov9281" in camera_model or "ov9282" in camera_model)
        and resolved_raw_size == requested_size
    )
    add(
        "camera",
        camera_ok,
        "OV9281/OV9282 camera mode is not identified, dimension-matched, manual, armed, and fresh",
    )
    observations["camera"] = {
        "running": bool(camera_status.get("running")),
        "armed": bool(camera_status.get("armed")),
        "latest_frame_age_s": camera_age,
        "model": camera_model or None,
        "resolved_raw_size": resolved_raw_size,
        "requested_size": requested_size,
    }

    iwr_monitor = getattr(server.iwr6843_runtime, "capture_monitor", None)
    iwr_worker = getattr(iwr_monitor, "_worker", None)
    iwr_serial = getattr(getattr(iwr_monitor, "radar", None), "ser", None)
    iwr_ok = bool(
        server.iwr6843_runtime_config.get("enabled")
        and iwr_monitor is not None
        and getattr(iwr_monitor, "_running", False)
        and getattr(iwr_monitor, "_armed", False)
        and iwr_worker is not None
        and iwr_worker.is_alive()
        and iwr_serial is not None
        and getattr(iwr_serial, "is_open", False)
    )
    add("iwr6843", iwr_ok, "IWR6843 capture transport, serial link, or GPIO arm is unavailable")
    observations["iwr6843"] = {
        "running": bool(getattr(iwr_monitor, "_running", False)),
        "armed": bool(getattr(iwr_monitor, "_armed", False)),
        "worker_alive": bool(iwr_worker is not None and iwr_worker.is_alive()),
        "serial_open": bool(iwr_serial is not None and getattr(iwr_serial, "is_open", False)),
    }
    if free_play_config.get("native_sync_enabled") and iwr_monitor is not None:
        native_health = iwr_monitor.ready_snapshot()
        iwr_ok = bool(server.iwr6843_runtime_config.get("enabled")) and all(
            native_health.get(key) for key in ("running", "worker_alive", "serial_open")
        )
        checks[-1] = {
            "id": "iwr6843",
            "status": "pass" if iwr_ok else "block",
            "reason": None if iwr_ok else "Native IWR acquisition worker or UART is unavailable",
        }
        observations["iwr6843"] = native_health
    checked_at = time.time() if checked_at is None else checked_at
    selection = (
        server.inclinometer_service.snapshot_for_impact(checked_at)
        if server.inclinometer_service is not None
        else None
    )
    from openflight.inclinometer.placement import placement_guard

    expected_orientation = server._expected_inclinometer_orientation()
    placement = (
        placement_guard(
            selection.snapshot,
            expected_pitch_deg=expected_orientation.get("pitch_deg") or 0.0,
            expected_roll_deg=expected_orientation.get("roll_deg") or 0.0,
        )
        if selection is not None and selection.snapshot is not None
        else {"ready": False, "reason": "no stable LIS3DH placement snapshot"}
    )
    lis_ok = bool(
        server.inclinometer_runtime_config.get("enabled")
        and selection is not None
        and selection.status == "stable"
        and selection.snapshot is not None
        and placement["ready"]
    )
    lis_reason = placement.get("reason") or (
        f"LIS3DH reading is {selection.status}"
        if selection is not None
        else "LIS3DH is not enabled"
    )
    add(
        "lis3dh",
        lis_ok,
        placement.get("warning") or lis_reason,
        warned=bool(lis_ok and placement.get("warned")),
    )
    observations["lis3dh"] = (
        selection.to_dict()
        if selection is not None and hasattr(selection, "to_dict")
        else {
            "status": selection.status if selection is not None else "unavailable",
            "age_s": getattr(selection, "age_s", None),
        }
    )
    observations["lis3dh"]["placement_guard"] = placement
    active_logger = server.get_session_logger()
    session_uuid = active_logger.active_session_uuid if active_logger is not None else None
    run_dir = (
        str(active_logger.log_dir.expanduser().resolve()) if active_logger is not None else None
    )
    logging_ok = bool(active_logger is not None and session_uuid and run_dir)
    add("logging", logging_ok, "required session logger is not active")
    observations["runtime"] = {"run_dir": run_dir, "session_uuid": session_uuid}
    blockers = [dict(check) for check in checks if check["status"] == "block"]
    warnings = [dict(check) for check in checks if check["status"] == "warn"]
    return {
        "schema_version": 1,
        "required": tester_setup_required,
        "checked_at": checked_at,
        "trigger_checked_at": checked_at,
        "ready": not blockers,
        "config_hash": tester_config_hash,
        "checks": checks,
        "blockers": [{"id": item["id"], "reason": item["reason"]} for item in blockers],
        "warnings": [{"id": item["id"], "reason": item["reason"]} for item in warnings],
        "observations": observations,
    }


@server.app.get("/api/camera/study/readiness")
def study_setup_readiness():
    """Expose the mandatory tester hardware gate without taking sensor ownership."""
    if not (study_mode_enabled or free_play_config.get("enabled")):
        return jsonify({"error": "study mode is off"}), 404
    return jsonify(_tester_setup_readiness())


def _tester_trigger_evidence_problem(  # pylint: disable=too-many-return-statements
    evidence: object,
) -> str | None:
    """Reject trigger evidence that is incomplete or belongs to another run."""
    if not isinstance(evidence, dict):
        return "no matching trigger readiness evidence"
    if evidence.get("schema_version") != 1:
        return "trigger readiness schema is not version 1"
    if evidence.get("required") is not True:
        return "trigger readiness does not mark the setup gate required"
    if evidence.get("ready") is not True:
        return "trigger readiness was blocked"
    if evidence.get("config_hash") != tester_config_hash:
        return "trigger readiness setup hash does not match the active requirement"
    runtime = (evidence.get("observations") or {}).get("runtime") or {}
    active_logger = server.get_session_logger()
    active_uuid = active_logger.active_session_uuid if active_logger is not None else None
    active_run_dir = (
        str(active_logger.log_dir.expanduser().resolve()) if active_logger is not None else None
    )
    if not active_uuid or runtime.get("session_uuid") != active_uuid:
        return "trigger readiness belongs to a different or inactive logging session"
    if not active_run_dir or runtime.get("run_dir") != active_run_dir:
        return "trigger readiness belongs to a different runtime log directory"
    return None


# Readiness blockers that stay hard stops under D15: a sensor that is not
# connected or running, and a rig that moved (the LIS3DH placement guard).
_HARD_STOP_BLOCKERS = frozenset({"ops", "camera", "iwr6843", "lis3dh"})


def _tester_trigger_hard_stop(evidence: dict) -> str | None:
    """Why trigger evidence must still refuse the shot (D15), or None to keep it.

    Only an unconnected sensor, a moved rig, or evidence from another session
    refuses; everything else the evidence says becomes a note on the shot.
    """
    for blocker in evidence.get("blockers") or []:
        if (
            free_play_config.get("enabled")
            and isinstance(blocker, dict)
            and blocker.get("id") in {"camera", "iwr6843"}
        ):
            continue
        if isinstance(blocker, dict) and blocker.get("id") in _HARD_STOP_BLOCKERS:
            return f"{blocker.get('id')}: {blocker.get('reason')}"
    runtime = (evidence.get("observations") or {}).get("runtime") or {}
    active_logger = server.get_session_logger()
    active_uuid = active_logger.active_session_uuid if active_logger is not None else None
    recorded_uuid = runtime.get("session_uuid")
    if recorded_uuid and active_uuid and recorded_uuid != active_uuid:
        return "trigger readiness belongs to a different logging session"
    active_run_dir = (
        str(active_logger.log_dir.expanduser().resolve()) if active_logger is not None else None
    )
    recorded_run_dir = runtime.get("run_dir")
    if recorded_run_dir and active_run_dir and recorded_run_dir != active_run_dir:
        return "trigger readiness belongs to a different runtime log directory"
    return None


def _tester_trigger_evidence_notes(evidence: dict, problem: str) -> list[dict]:
    """What a kept shot's trigger evidence said against the setup, as notes (P8-7)."""
    notes = [{"id": "trigger_readiness", "reason": problem}]
    for blocker in evidence.get("blockers") or []:
        if isinstance(blocker, dict):
            notes.append({"id": str(blocker.get("id")), "reason": str(blocker.get("reason"))})
    return notes


@server.app.route("/api/camera/study/controls", methods=["POST"])
def study_camera_controls():
    """Set exposure and gain live, without restarting the rolling buffer."""
    runtime, refusal = _study_runtime()
    if refusal is not None:
        return refusal
    body = request.get_json(silent=True) or {}
    try:
        applied = runtime.update_image_controls(
            exposure_us=int(body["exposure_us"]),
            gain=float(body["gain"]),
            purpose=str(body.get("purpose", "capture")),
        )
    except (KeyError, TypeError, ValueError) as error:
        return jsonify({"error": str(error)}), 400
    except RuntimeError as error:
        return jsonify({"error": str(error)}), 409
    return jsonify(applied)


@server.app.route("/api/camera/study/frames")
def study_camera_frames():
    """The next few raw frames, with the exposure and gain each was taken at."""
    import io  # pylint: disable=import-outside-toplevel

    import numpy as np  # pylint: disable=import-outside-toplevel

    runtime, refusal = _study_runtime()
    if refusal is not None:
        return refusal
    try:
        count = max(1, min(20, int(request.args.get("n", 5))))
    except ValueError:
        return jsonify({"error": "n must be a whole number"}), 400
    frames = runtime.recent_frames(count, timeout_s=2.0)
    if len(frames) < count:
        return jsonify({"error": "the camera did not deliver frames"}), 503
    buffer = io.BytesIO()
    np.savez(
        buffer,
        frames=np.stack([frame.image for frame in frames]),
        exposure_us=np.asarray([frame.exposure_us for frame in frames], dtype=np.int32),
        analogue_gain=np.asarray([frame.analogue_gain for frame in frames], dtype=np.float32),
        sensor_timestamp_ns=np.asarray(
            [frame.sensor_timestamp_ns for frame in frames], dtype=np.int64
        ),
    )
    return Response(
        buffer.getvalue(),
        mimetype="application/octet-stream",
        headers={"Cache-Control": "no-store"},
    )


def _ready_light_setup_problems() -> list[str]:
    """The tester setup gate's red causes, from cheap reads only (P7-14).

    The camera and radars are judged by the light itself; this adds what makes a
    tester capture ineligible besides them: the admitted setup, the LIS3DH, the
    session log and manual exposure.
    """
    from openflight.inclinometer.placement import placement_guard

    problems = []
    if not tester_config_hash or _loaded_setup_config_hash() != tester_config_hash:
        problems.append("no admitted setup: this rig or LIS3DH is not the admitted one")
    if server.inclinometer_service is None or not server.inclinometer_runtime_config.get("enabled"):
        problems.append("tilt sensor (LIS3DH) is not running")
    else:
        selection = server.inclinometer_service.snapshot_for_impact(time.time())
        if selection.snapshot is None:
            problems.append(f"tilt sensor reading is {selection.status}")
        else:
            expected = server._expected_inclinometer_orientation()
            placement = placement_guard(
                selection.snapshot,
                expected_pitch_deg=expected.get("pitch_deg") or 0.0,
                expected_roll_deg=expected.get("roll_deg") or 0.0,
            )
            if not placement["ready"]:
                problems.append(f"tilt sensor: {placement.get('reason')}")
    active_logger = server.get_session_logger()
    if active_logger is None or not active_logger.active_session_uuid:
        problems.append("session log is not open")
    if (
        server.camera_capture_runtime is not None
        and server.camera_capture_runtime.settings.auto_exposure
        and not free_play_config.get("enabled")
    ):
        problems.append("camera exposure is automatic")
    return problems


@server.app.get("/api/shots/latest")
def api_latest_shot():
    """The session's latest shot payload (every metric, labelled) and the metric catalog."""
    return jsonify({"shot": server._latest_session_shot(), "catalog": shot_metrics.catalog()})


@server.app.get("/api/ready-light")
def ready_light_status():
    """The ready light for the tester page; the kiosk screen gets it on the socket."""
    return jsonify(server.ready_light.update(server._ready_light_states()))


@server.app.post("/api/ready-light/hold")
def ready_light_hold():
    """Hold the light red for what only the tester page knows (the ladder's phase)."""
    if not study_mode_enabled:
        return jsonify({"error": "study mode is off"}), 404
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "request body must be an object"}), 400
    cause = body.get("cause")
    if cause is not None and (not isinstance(cause, str) or not cause.strip() or len(cause) > 120):
        return jsonify({"error": "cause must be null or a short text"}), 400
    global ready_hold  # pylint: disable=global-statement
    ready_hold = cause.strip() if cause else None
    return jsonify({**server._publish_ready_light(), "hold": ready_hold})


def load_free_play_plan(path) -> dict:
    """Read free play's exposure plan (``free_play_ladder.build_plan``) for this run."""
    from openflight.camera import free_play_ladder  # pylint: disable=import-outside-toplevel

    global free_play_plan, free_play_plan_path  # pylint: disable=global-statement
    source = Path(path)
    try:
        plan = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"free play's exposure plan {source} is unreadable: {error}") from error
    if not isinstance(plan, dict) or plan.get("schema") != free_play_ladder.PLAN_SCHEMA:
        raise ValueError(f"{source} is not a {free_play_ladder.PLAN_SCHEMA} exposure plan")
    free_play_plan, free_play_plan_path = plan, source
    return plan


def init_free_play_study(*, run_async=None, settle_s: float | None = None):
    """Start the kiosk's study of the plan, on the running camera (or none).

    The camera was started at the plan's startup controls: the first rung, held,
    or the automatic seed when no rung can run. The study is kept beside the plan.
    """
    from openflight.camera import free_play_ladder  # pylint: disable=import-outside-toplevel

    global free_play_study  # pylint: disable=global-statement
    if free_play_plan is None or free_play_plan_path is None:
        free_play_study = None
        return None
    runtime = server.camera_capture_runtime
    limits = getattr(runtime, "sensor_limits", None) if runtime is not None else None
    options = {}
    if run_async is not None:
        options["run_async"] = run_async
    if settle_s is not None:
        options["settle_s"] = settle_s
    free_play_study = free_play_ladder.FreePlayExposureStudy(
        free_play_plan,
        free_play_plan_path.parent / free_play_ladder.STATE_NAME,
        apply_controls=runtime.apply_held_controls if runtime is not None else None,
        begin_automatic=runtime.begin_automatic_exposure if runtime is not None else None,
        current_controls=(
            (lambda: (runtime.settings.exposure_us, runtime.settings.gain))
            if runtime is not None
            else None
        ),
        live_sensor_min_us=limits.exposure_min_us if limits is not None else None,
        run_id=free_play_config.get("run_id"),
        **options,
    )
    return free_play_study


def _free_play_progress() -> dict | None:
    """The study's line for the kiosk band and the tester page, or None without one."""
    study = free_play_study
    return study.progress() if study is not None else None


def _count_free_play_shot(shot: Shot) -> None:
    """Count a real (OPS) shot toward the current rung and record its ladder fields.

    Called from the serialized shot callback, so rungs advance in impact order. A
    false trigger's clip never gets here, so it never counts.
    """
    study = free_play_study
    if study is None:
        shot.extras["free_play_exposure_study"] = None
        return
    try:
        shot.extras["free_play_exposure_study"] = study.on_shot(shot.shot_number)
    except Exception as error:  # pylint: disable=broad-exception-caught
        logger.warning("[FREE PLAY] Exposure study could not count a shot", exc_info=True)
        shot.extras["free_play_exposure_study"] = {"error": f"{type(error).__name__}: {error}"}


def _judge_free_play_clip(shot: Shot, capture) -> None:
    """The ladder's picture-only verdict for the shot's own clip, on the shot and its rung."""
    from openflight.camera import free_play_ladder  # pylint: disable=import-outside-toplevel

    record = shot.extras.get("free_play_exposure_study")
    study = free_play_study
    if study is None or not isinstance(record, dict) or "ladder_phase" not in record:
        return
    if capture is None:
        verdict = free_play_ladder.no_capture_verdict("no matched camera capture")
    elif capture.error or capture.path is None:
        verdict = free_play_ladder.no_capture_verdict(
            f"the clip was not saved: {capture.error or 'no path'}"
        )
    else:
        settings = getattr(server.camera_capture_runtime, "settings", None)
        on_rung = record["ladder_phase"] == "ladder"
        verdict = free_play_ladder.judge_swing(
            Path(capture.path),
            arm_id=record["resolution"]["arm_id"],
            rung_id=record["rung_id"],
            # an automatic shot is judged at the controls its own trigger recorded
            exposure_us=record["exposure_us"] if on_rung else None,
            gain=record["gain"] if on_rung else None,
            expected_ball=getattr(settings, "setup_ball", None),
            zone_box=getattr(settings, "hitting_zone", None),
        )
    shot.extras["free_play_exposure_study"] = {**record, "verdict": verdict}
    try:
        study.record_verdict(shot.shot_number, verdict)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.warning("[FREE PLAY] Exposure study could not file a verdict", exc_info=True)


def _free_play_exposure_from_clip(capture) -> dict:
    """The free-play exposure record from the shot's own clip (F6).

    The clip's ``settings`` is the capture's startup snapshot and holds no lens;
    the lens the clip was taken through is its own top-level ``lens`` key.
    """
    metadata = capture.metadata or {}
    return {
        "status": "recorded" if not capture.error else "capture_error",
        "reason": capture.error,
        "decision_scope": "clip",
        "decision": deepcopy(metadata.get("auto_exposure")),
        "decision_reason": None,
        "lens_scope": "clip",
        "lens": deepcopy(metadata.get("lens")),
        "capture_settings": deepcopy(metadata.get("settings")),
        "capture_mode": deepcopy(metadata.get("capture_mode")),
    }


def _free_play_exposure_at_shot() -> dict:
    """The record for a shot with no matched clip: the policy's live decision.

    Read when the shot is detected and labelled ``live_at_shot``: it is what the
    swing-exposure policy held then, not a clip's measurement. A camera that
    cannot answer leaves the decision missing, with its reason.
    """
    record = {
        "status": "unavailable",
        "reason": "no matched camera capture",
        "decision_scope": None,
        "decision": None,
        "decision_reason": "no camera runtime",
        "lens_scope": None,
        "lens": None,
        "capture_settings": deepcopy(server.camera_capture_config),
    }
    runtime = server.camera_capture_runtime
    if runtime is None:
        return record
    try:
        decision = dict(runtime.auto_exposure_status())
    except Exception as error:  # pylint: disable=broad-exception-caught
        record["decision_reason"] = f"auto_exposure_status failed: {error}"
    else:
        decision.pop("recent_decisions", None)  # the live page's log, not this shot's
        record.update(
            decision_scope="live_at_shot", decision=deepcopy(decision), decision_reason=None
        )
    lens = getattr(getattr(runtime, "settings", None), "lens", None)
    if lens:
        record.update(lens_scope="kiosk_run", lens=deepcopy(dict(lens)))
    return record


def _finish_free_play_capture(timeout_s: float = 60.0) -> None:
    """Close acquisition only after accepted shots have finished writing."""
    resume_free_play()
    from openflight import kiosk_extensions

    kiosk_extensions.call_all("before_capture_stop")
    if server.monitor is not None:
        server.monitor.stop()
        capture_thread = getattr(server.monitor, "_capture_thread", None)
        if capture_thread is not None and capture_thread.is_alive():
            raise RuntimeError("radar capture is still finishing; retry Stop & package")
    deadline = time.monotonic() + timeout_s
    while True:
        if not server.shot_processing_pending():
            break
        if time.monotonic() >= deadline:
            raise RuntimeError(
                "shots are still finishing; retry Stop & package when processing finishes"
            )
        time.sleep(0.05)
    if free_play_study is not None:
        # every accepted shot has its verdict: the ladder is complete or partial
        free_play_study.finish()
    for service in (
        server.camera_capture_runtime,
        server.iwr6843_runtime,
        server.inclinometer_service,
    ):
        if service is not None:
            service.stop()
    camera_writer = getattr(server.camera_capture_runtime, "_worker", None)
    if camera_writer is not None and camera_writer.is_alive():
        raise RuntimeError("camera save is still finishing; retry Stop & package")
    if server.monitor is not None:
        server.monitor.disconnect()
        server.monitor = None
    kiosk_extensions.call_all("after_capture_stop")
    session = server.get_session_logger()
    if session is not None:
        session.end_session()


def _admit_shot(shot) -> bool:
    """The tester gate: refuse an OPS shot whose required setup dropped at its trigger.

    D15: measure and label. Missing camera evidence or a blocked setup is noted on
    the shot; only a hard stop (a sensor gone, a moved rig, another session) refuses it.
    A paused free play counts no shot.
    """
    paused = free_play_pause.get("cause")
    if paused:
        server.ready_light.swing_result("paused")
        _log_event("free_play_shot_not_counted", {"cause": paused, "mph": shot.ball_speed_mph})
        logger.warning("[SERVER] Shot not counted: free play is paused (%s)", paused)
        return False
    if not tester_setup_required:
        return True
    try:
        readiness = (
            server.camera_capture_runtime.trigger_evidence_for_shot(shot.impact_timestamp)
            if server.camera_capture_runtime is not None
            else None
        )
    except (OSError, RuntimeError) as error:
        if not free_play_config.get("enabled"):
            raise
        readiness = None
        server.log_session_error(str(error), component="camera_trigger")
    evidence_problem = _tester_trigger_evidence_problem(readiness)
    if readiness is None and evidence_problem is not None:
        # No camera trigger near this impact (P7-3): the edge never reached
        # the camera, which says nothing against the setup. Keep the OPS
        # shot and name the gap, instead of dropping a real swing.
        shot.extras["missing_trigger_evidence"] = [
            {
                "id": "camera_trigger",
                "reason": f"{evidence_problem}: the camera recorded no trigger near this impact",
            }
        ]
        logger.warning(
            "[SERVER] Tester shot kept without camera trigger evidence (%.1f mph)",
            shot.ball_speed_mph,
        )
    elif evidence_problem is not None and _tester_trigger_hard_stop(readiness) is None:
        # D15: measure and label. A blocked or mismatched setup is noted on
        # the shot; only a hard stop below still drops it (P8-7).
        shot.extras["trigger_evidence_notes"] = _tester_trigger_evidence_notes(
            readiness, evidence_problem
        )
        logger.warning(
            "[SERVER] Tester shot kept with trigger readiness notes: %s",
            shot.extras["trigger_evidence_notes"],
        )
    elif evidence_problem is not None:
        evidence = readiness or {
            "schema_version": 1,
            "required": True,
            "ready": False,
            "config_hash": tester_config_hash,
            "blockers": [
                {"id": "trigger_evidence", "reason": "no matching trigger readiness evidence"}
            ],
        }
        if readiness:
            evidence.setdefault("blockers", []).append(
                {"id": "trigger_evidence", "reason": evidence_problem}
            )
        server.log_session_error(
            "Tester shot rejected because required setup readiness dropped",
            component="tester_setup",
            context={"readiness": evidence},
        )
        logger.warning("[SERVER] Tester shot rejected: %s", evidence["blockers"])
        server.ready_light.swing_result("not_counted")
        instrumentation.mark(
            "server_shot_rejected",
            shot.extras.get("trace_attempt_id"),
            reason="tester_setup_readiness",
        )
        return False
    return True


def _free_play_camera_rule(camera: Mapping, now: float, red: list, amber: list) -> None:
    """Free play's camera causes, once the camera's own checks pass (F4)."""
    del now  # the rule names no time left
    if camera.get("free_play"):
        study = camera.get("exposure_study") or {}
        if study.get("changing"):
            # free play's exposure ladder is moving to its next setting, or handing
            # over to the automatic exposure: a swing now may be at either setting
            amber.append(("changing the camera setting", None))
            return
        if study.get("phase") == "ladder":
            return  # a held rung: the automatic policy is not running, nothing to read
        policy = camera.get("auto_exposure") or {}
        if policy.get("status") == "holding_no_ball" and policy.get("reason") == "no_ball_searched":
            red.append("no ball in the box")
        elif policy.get("status") in {"holding_no_ball", "searching_for_ball", "seeded"}:
            amber.append(("finding the ball", None))
        # "unavailable" (no exposure limits from the camera) is a label on the
        # green light, never a red: the OPS numbers and the clips still record


def _free_play_note(camera: Mapping | None) -> str | None:
    """The free-play exposure state a green light carries as a note (F4)."""
    if not (camera or {}).get("free_play"):
        return None
    if (camera.get("exposure_study") or {}).get("phase") == "ladder":
        return None  # held rung controls: the policy's idle state says nothing
    policy = camera.get("auto_exposure") or {}
    status = policy.get("status")
    if status in {"too_dark", "too_bright"}:
        return status.replace("_", " ")
    if status == "unavailable":
        message = policy.get("message")
        return "camera exposure unavailable" + (f" ({message})" if message else "")
    return None


def _add_arguments(parser) -> None:
    parser.add_argument(
        "--study-mode",
        action="store_true",
        help="Let the tester study page set camera exposure and gain and read raw frames",
    )
    parser.add_argument(
        "--tester-setup-required",
        action="store_true",
        help="Require the study kiosk hardware readiness gate before accepting shots",
    )
    parser.add_argument(
        "--tester-config-hash",
        default=None,
        help="Required SHA-256 fingerprint of the measured tester rig geometry",
    )
    parser.add_argument("--tester-free-play", action="store_true")
    parser.add_argument("--tester-native-sync-package", type=Path, default=None)
    parser.add_argument("--tester-native-sync-control", type=Path, default=None)
    parser.add_argument("--tester-trigger-policy", default=None)
    parser.add_argument("--tester-id")
    parser.add_argument("--tester-run-id")
    parser.add_argument("--tester-control-port", type=int, default=8765)
    parser.add_argument("--tester-geometry-unverified", action="store_true")
    parser.add_argument(
        "--free-play-ladder",
        default=None,
        help=(
            "Free play's exposure plan for the tapped resolution (written by the tester "
            "server in the run folder): five shots per rung, then the automatic exposure"
        ),
    )


def _configure(args, parser) -> None:
    """Refuse inconsistent tester flags, then apply them to this kiosk."""
    if args.tester_free_play and (
        not args.tester_setup_required
        or not args.tester_id
        or not args.tester_run_id
        or not 1 <= args.tester_control_port <= 65535
    ):
        parser.error("free play requires admitted setup, tester ID, run ID and a valid tester port")
    if args.free_play_ladder and not args.tester_free_play:
        parser.error("--free-play-ladder is free play's: add --tester-free-play")
    if args.tester_setup_required and not args.tester_config_hash:
        parser.error("--tester-setup-required requires --tester-config-hash")
    native_package = getattr(args, "tester_native_sync_package", None)
    native_control = getattr(args, "tester_native_sync_control", None)
    native_sync = native_package is not None
    selected_policy = getattr(args, "tester_trigger_policy", None)
    if selected_policy is not None:
        from openflight.camera.trigger_policy import validate

        try:
            validate(selected_policy, package=native_package)
            if (selected_policy != "sound") != native_sync:
                raise ValueError("trigger policy conflicts with native package selection")
        except ValueError as error:
            parser.error(str(error))
    if native_sync != (native_control is not None):
        parser.error("native fusion requires both package and control directory")
    if native_sync:
        if (
            not args.tester_free_play
            or not args.tester_setup_required
            or args.trigger != "hardware"
            or not args.iwr6843
            or not args.camera_capture
            or args.mock
            or args.swing_speed
        ):
            parser.error(
                "native fusion requires real admitted free play, hardware trigger, IWR and camera"
            )
        from openflight.camera.native_fusion import NativeFusionExtension
        from openflight.kiosk_extensions import register

        register(
            NativeFusionExtension(
                args.tester_native_sync_package.resolve(),
                args.tester_native_sync_control.resolve(),
                policy=selected_policy or "independent",
            )
        )
    global study_mode_enabled, tester_setup_required, tester_config_hash  # pylint: disable=global-statement
    study_mode_enabled = bool(args.study_mode)
    tester_setup_required = bool(args.tester_setup_required)
    tester_config_hash = args.tester_config_hash
    # a kiosk the tester started links back to its page (the kiosk's Tester button)
    tester_page.configure(args.tester_control_port if args.tester_setup_required else None)
    free_play_config.clear()
    if args.tester_free_play:
        free_play_config.update(
            enabled=True,
            phase="recording",
            tester_id=args.tester_id,
            run_id=args.tester_run_id,
            control_port=args.tester_control_port,
            unit_id=platform.node(),
            geometry_unverified=args.tester_geometry_unverified,
        )
        if native_sync:
            free_play_config["native_sync_enabled"] = True
        from openflight.camera.trigger_policy import snapshot

        free_play_config["trigger_policy"] = snapshot(
            selected_policy or ("independent" if native_sync else "sound")
        )
    global free_play_plan, free_play_plan_path, free_play_study  # pylint: disable=global-statement
    free_play_plan = free_play_plan_path = free_play_study = None
    if args.free_play_ladder:
        try:
            load_free_play_plan(args.free_play_ladder)
        except ValueError as error:
            parser.error(str(error))


def _session_start_config(config: dict) -> None:
    if free_play_config.get("enabled"):
        config["free_play"] = {
            key: free_play_config.get(key)
            for key in ("enabled", "tester_id", "run_id", "unit_id", "geometry_unverified")
        }
        # the resolution the tester tapped, and the exposure ladder planned for it
        config["free_play"]["resolution"] = (
            deepcopy(free_play_plan["resolution"]) if free_play_plan is not None else None
        )
        config["free_play"]["exposure_plan"] = deepcopy(free_play_plan)


def _ready_light_states(states):
    """A tester's kiosk adds its setup problems and free play's camera causes.

    Outside a tester's kiosk (the ladder or plain swings) the light is the plain
    kiosk's: the sensors alone decide it.
    """
    if states is None or not (
        tester_setup_required or study_mode_enabled or free_play_pause or ready_hold
    ):
        return states
    camera = states.camera
    if server.camera_capture_runtime is not None and camera is not None:
        camera = {
            **camera,
            "free_play": bool(free_play_config.get("enabled")),
            # free play's exposure ladder: amber while its setting changes
            "exposure_study": (
                free_play_study.ready_state() if free_play_study is not None else None
            ),
        }
    problems = tuple(_ready_light_setup_problems()) if tester_setup_required else ()
    return dataclasses.replace(
        states,
        camera=camera,
        # the page's hold is red and comes first
        setup_problems=(*((ready_hold,) if ready_hold else ()), *states.setup_problems, *problems),
        camera_rule=_free_play_camera_rule,
        green_note=_free_play_note(camera),
        amber_causes=(
            *states.amber_causes,
            *((PAUSE_CAUSES[free_play_pause["cause"]],) if free_play_pause else ()),
        ),
        # the exposure ladder (the study-mode kiosk) records no ball range
        setup_optional=(
            ("ball_range",)
            if study_mode_enabled and not free_play_config.get("enabled")
            else states.setup_optional
        ),
    )


def _check_camera_capture(camera_capture, session_uuid: str) -> None:
    """A tester kiosk fuses only a clip whose trigger evidence is this shot's session."""
    trigger_setup = getattr(camera_capture, "metadata", {}).get("tester_setup", {})
    trigger_runtime = trigger_setup.get("observations", {}).get("runtime", {})
    trigger_session_uuid = trigger_runtime.get("session_uuid")
    if tester_setup_required and trigger_session_uuid != session_uuid:
        raise ValueError("camera trigger session does not match the registered shot session")


def _on_shot_accepted(shot) -> None:
    if free_play_config.get("enabled"):
        shot.extras["free_play_run_id"] = free_play_config["run_id"]
        shot.extras["geometry_unverified"] = bool(free_play_config.get("geometry_unverified"))
        shot.extras["free_play_exposure"] = _free_play_exposure_at_shot()
        _count_free_play_shot(shot)


def _on_camera_capture(shot, camera_capture) -> None:
    if free_play_config.get("enabled"):
        shot.extras["free_play_exposure"] = _free_play_exposure_from_clip(camera_capture)
        _judge_free_play_clip(shot, camera_capture)


def _extend_shot_record(shot, data: dict) -> None:
    """A tester shot's trigger-evidence notes and free play fields, in its record."""
    extras = shot.extras
    data["missing_trigger_evidence"] = deepcopy(extras.get("missing_trigger_evidence"))
    data["trigger_evidence_notes"] = deepcopy(extras.get("trigger_evidence_notes"))
    run_id = extras.get("free_play_run_id")
    if not run_id:
        return
    unverified = bool(extras.get("geometry_unverified"))
    data["free_play"] = {"run_id": run_id, "geometry_unverified": unverified}
    if extras.get("free_play_exposure_study") is not None:
        data["free_play"]["exposure_study"] = deepcopy(extras["free_play_exposure_study"])
    data["exposure_decision"] = deepcopy(extras.get("free_play_exposure"))
    if not unverified:
        return
    for metric_id, metric in data["metrics"].items():
        source = metric.get("source") or ""
        if (
            metric_id in {"ball_speed", "club_speed"}
            or (metric_id == "spin_rate" and metric["status"] == "measured")
            or source.startswith("radial ratio")
            or metric["value"] is None
        ):
            continue
        metric["geometry_validation"] = "unverified"
        metric["note"] = "; ".join(filter(None, [metric.get("note"), "geometry unverified"]))


class TesterKioskExtension(kiosk_extensions.KioskExtension):
    """The tester's kiosk hooks; inert unless a tester flag turned them on."""

    name = "tester"

    def add_arguments(self, parser) -> None:
        _add_arguments(parser)

    def configure(self, args, parser) -> None:
        _configure(args, parser)

    def after_camera_setup(self) -> None:
        if free_play_plan is not None:
            # after the camera, before any shot: it counts shots and moves the camera
            init_free_play_study()

    def diagnostic_camera(self) -> bool:
        return study_mode_enabled

    def camera_runtime_class(self, base):  # pylint: disable=import-outside-toplevel
        if not isinstance(base, type):
            return None  # a stand-in factory, not a runtime class
        from openflight.camera import tester_capture_runtime

        return tester_capture_runtime.runtime_class(base)

    def trigger_evidence_provider(self):
        if not tester_setup_required:
            return None
        return lambda timestamp: _tester_setup_readiness(
            require_camera_armed=False, checked_at=timestamp
        )

    def iwr_optional(self) -> str | None:
        return "free play keeps OPS numbers" if free_play_config.get("enabled") else None

    def session_start_config(self, config: dict) -> None:
        _session_start_config(config)

    def ready_light_states(self, states):
        return _ready_light_states(states)

    def admit_shot(self, shot) -> bool:
        return _admit_shot(shot)

    def on_shot_accepted(self, shot) -> None:
        _on_shot_accepted(shot)

    def check_camera_capture(self, shot, camera_capture, session_uuid: str) -> None:
        _check_camera_capture(camera_capture, session_uuid)

    def on_camera_capture(self, shot, camera_capture) -> None:
        _on_camera_capture(shot, camera_capture)

    def extend_shot_record(self, shot, record: dict) -> None:
        _extend_shot_record(shot, record)

    def before_setup_action(self, section: str, action: str, payload: dict) -> str | None:
        # a run bound to a setup confirmation keeps the setup it was admitted with
        return "a run is in progress" if tester_setup_required and tester_config_hash else None


def _log_event(kind: str, data: dict) -> None:
    session = server.get_session_logger()
    if session is not None:
        session.log_event(kind, data)


def _latest_shot_number() -> int | None:
    with server._shot_sequence_lock:
        return server._shot_sequence_number or None


def _pause_state() -> dict | None:
    return dict(free_play_pause) if free_play_pause else None


def _tell_tester() -> None:
    """Tell the tester page (through its server) that free play paused or resumed."""
    config, paused = dict(free_play_config), _pause_state()

    def send():
        try:
            free_play.tester_request(config, "/paused", {"paused": paused})
        except (OSError, ValueError):
            logger.warning("[FREE PLAY] The tester page did not hear the pause", exc_info=True)

    threading.Thread(target=send, daemon=True, name="free-play-paused").start()


def _view_controls(runtime) -> tuple[int, float]:
    """The ladder's still-photo controls, from the light index or the swing's brightness."""
    from openflight.camera.light_judging import SENSOR_BLACK_LEVEL_DN  # noqa: PLC0415
    from openflight.camera.study_ladder import photo_controls  # noqa: PLC0415

    settings = runtime.settings
    light = getattr(settings, "light_index_dn_per_us_gain", None)
    return photo_controls(
        float(light) if light else None,
        SENSOR_BLACK_LEVEL_DN,
        settings.fps,
        rung_exposure_us=settings.exposure_us,
        rung_gain=settings.gain,
    )


def pause_free_play(cause: str) -> dict:
    """Stop counting shots and show the face or the target at still-photo controls."""
    if cause not in PAUSE_CAUSES:
        raise ValueError("free play pauses for a face photo or a new aim")
    with _pause_lock:
        if free_play_config.get("phase") != "recording":
            raise RuntimeError("free play is not recording")
        if free_play_pause:
            if free_play_pause["cause"] == cause:
                return _pause_state()
            raise RuntimeError(f"free play is paused: {PAUSE_CAUSES[free_play_pause['cause']]}")
        runtime = server.camera_capture_runtime
        controls = runtime.hold_still_controls(*_view_controls(runtime)) if runtime else None
        free_play_pause.update(cause=cause, since=time.time(), controls=controls)
        _log_event(
            "free_play_paused",
            {"cause": cause, "controls": controls, "after_shot": _latest_shot_number()},
        )
    _tell_tester()
    return _pause_state()


def resume_free_play() -> None:
    """Put the swing controls back and count shots again."""
    with _pause_lock:
        if not free_play_pause:
            return
        runtime = server.camera_capture_runtime
        restored = runtime.release_still_controls() if runtime is not None else None
        cause = free_play_pause["cause"]
        free_play_pause.clear()
        _log_event("free_play_resumed", {"cause": cause, "controls": restored})
    _tell_tester()


def _paused_frame(runtime):
    """The newest frame taken at the paused controls."""
    from openflight.camera.optical_quality import controls_match  # noqa: PLC0415

    held = free_play_pause.get("controls") or {}
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        frames = runtime.recent_frames(1, timeout_s=0.5)
        if frames and controls_match(
            held.get("exposure_us"),
            held.get("gain"),
            frames[-1].exposure_us,
            frames[-1].analogue_gain,
        ):
            return frames[-1]
    raise RuntimeError("the camera did not deliver a picture")


def take_face_photo() -> dict:
    """Save the face against the latest shot, then resume free play."""
    from openflight.camera.study_ladder import write_pgm  # noqa: PLC0415

    with _pause_lock:
        if free_play_pause.get("cause") != "photo":
            raise RuntimeError("tap Photograph the face first")
        runtime, session = server.camera_capture_runtime, server.get_session_logger()
        if runtime is None or session is None:
            raise RuntimeError("the camera or the session log is not running")
        frame = _paused_frame(runtime)
        shot = _latest_shot_number()
        run_dir = Path(session.log_dir).expanduser()
        folder = run_dir / FACE_PHOTO_DIR
        folder.mkdir(parents=True, exist_ok=True)
        stem = f"shot-{shot:04d}" if shot else "before-first-shot"
        path, copy = folder / f"{stem}.pgm", 2
        while path.exists():
            path, copy = folder / f"{stem}-{copy}.pgm", copy + 1
        write_pgm(path, frame.image)
        record = {
            "shot_number": shot,
            "path": path.relative_to(run_dir).as_posix(),
            "exposure_us": frame.exposure_us,
            "gain": frame.analogue_gain,
            "taken_at": time.time(),
        }
        _log_event("face_photo", record)
    resume_free_play()
    return record


def _free_play_request():
    """This run's request body, or the response refusing it."""
    if not free_play_config.get("enabled"):
        return None, (jsonify({"error": "free play is not running"}), 404)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return None, (jsonify({"error": "request body must be an object"}), 400)
    if any(
        payload.get(key, free_play_config.get(key)) != free_play_config.get(key)
        for key in ("tester_id", "run_id")
    ):
        return None, (jsonify({"error": "free-play request belongs to another run"}), 409)
    return payload, None


def _answer(action):
    """Run ``action`` on this run's request; every answer carries the pause."""
    payload, refusal = _free_play_request()
    if refusal is not None:
        return refusal
    try:
        return jsonify({**action(payload), "paused": _pause_state()})
    except ValueError as error:
        return jsonify({"error": str(error), "paused": _pause_state()}), 400
    except (OSError, RuntimeError) as error:
        return jsonify({"error": str(error), "paused": _pause_state()}), 409


@server.app.post("/api/free-play/pause")
def free_play_pause_route():
    """Pause free play: ``cause`` is ``photo`` or ``aim``."""
    return _answer(lambda body: {"pause": pause_free_play(body.get("cause"))})


@server.app.post("/api/free-play/resume")
def free_play_resume_route():
    """Count shots again at the swing controls."""
    return _answer(lambda _body: resume_free_play() or {})


@server.app.post("/api/free-play/next-exposure")
def free_play_next_exposure_route():
    """Advance the exposure ladder between shots, retaining partial rung counts."""

    def act(body):
        expected = body.get("rung_id")
        if not isinstance(expected, str) or not expected:
            raise ValueError("rung_id is required")

        def advance():
            with _pause_lock:
                if free_play_config.get("phase") != "recording" or free_play_pause:
                    raise RuntimeError("resume recording before changing exposure")
                runtime = server.camera_capture_runtime
                if runtime is None or free_play_study is None:
                    raise RuntimeError("the exposure ladder is unavailable")
                ops_phase = tuple(
                    getattr(getattr(server.monitor, "radar", None), "trigger_phase", None) or ()
                )
                if ops_phase and ops_phase[0] != "armed":
                    raise RuntimeError("wait for OPS243 to finish processing the shot")
                camera = runtime.ready_snapshot()
                if not camera.get("running") or any(
                    camera.get(key)
                    for key in ("collecting_tail", "awaiting_handoff", "saving", "pending_saves")
                ):
                    raise RuntimeError("wait for the camera capture to finish")
                progress = free_play_study.next_exposure(expected)
                _log_event(
                    "free_play_next_exposure",
                    {"rung_id": expected, "after_shot": _latest_shot_number()},
                )
                return {"exposure_study": progress}

        return server._when_idle(advance, timeout_s=0)

    return _answer(act)


@server.app.post("/api/free-play/photo")
def free_play_photo_route():
    """Photograph the face (``capture``) or go on without it (``skip``), then resume."""

    def act(body):
        if body.get("action") == "skip":
            if free_play_pause.get("cause") != "photo":
                raise RuntimeError("tap Photograph the face first")
            _log_event("face_photo_skipped", {"shot_number": _latest_shot_number()})
            resume_free_play()
            return {"photo": None}
        if body.get("action") != "capture":
            raise ValueError("photo action must be capture or skip")
        return {"photo": take_face_photo()}

    return _answer(act)


@server.app.post("/api/free-play/target-line")
def free_play_target_line_route():
    """The aim the tester just set: the next shot is measured from it."""

    def act(body):
        line = server.apply_target_line(body.get("target_line"))
        if free_play_pause.get("cause") == "aim":
            resume_free_play()
        return {"target_line": line}

    return _answer(act)


@server.app.get("/api/free-play/view.jpg")
def free_play_view():
    """The camera at the paused controls, for the tester page's face photo or aim."""
    import cv2  # pylint: disable=import-error,import-outside-toplevel

    runtime = server.camera_capture_runtime
    if not free_play_pause:
        return jsonify({"error": "free play is not paused"}), 409
    if runtime is None:
        return jsonify({"error": "the camera is not running"}), 503
    frames = runtime.recent_frames(1, timeout_s=1.0)
    if not frames:
        return jsonify({"error": "the camera did not deliver a picture"}), 503
    quality = [int(cv2.IMWRITE_JPEG_QUALITY), 85]  # pylint: disable=no-member
    ok, encoded = cv2.imencode(".jpg", frames[-1].image, quality)  # pylint: disable=no-member
    if not ok:
        return jsonify({"error": "the picture could not be encoded"}), 500
    return Response(encoded.tobytes(), mimetype="image/jpeg", headers={"Cache-Control": "no-store"})


def _free_play_extra() -> dict:
    host = (request.host or "localhost").split(":")[0] or "localhost"
    return {
        "paused": _pause_state(),
        "tester_url": f"http://{host}:{int(free_play_config.get('control_port') or 8765)}/",
    }


extension = kiosk_extensions.register(TesterKioskExtension())

# the tester page this kiosk's Tester button goes back to (set by _configure)
tester_page = tester_link.TesterLink(None)
tester_link.register(server.app, tester_page)

# late-bound, so a test can replace either function on this module
register_kiosk_routes(
    server.app,
    free_play_config,
    lambda: _finish_free_play_capture(),  # pylint: disable=unnecessary-lambda
    progress=lambda: _free_play_progress(),  # pylint: disable=unnecessary-lambda
    extra=_free_play_extra,
)

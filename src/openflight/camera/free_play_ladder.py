"""Free play's automatic exposure ladder: one resolution, its rungs, five counted shots each.

The tester taps one resolution at setup (1280x800 or 640x400, of the modes the
lens enables). Free play then walks that mode's ladder rungs from the lens
settings, longest exposure first, so the owner can lock in which exposures work
for fusion while the tester simply plays:

- before any swing, a rung the setup's measured light cannot support
  (``study_ladder.rung_light_support``, 640x400 at its measured 2x signal) or one
  below the sensor's recorded minimum is skipped, with why;
- each rung's gain keeps the setup's ball lock brightness (``starting_gain``);
- only real shots count (the kiosk calls ``on_shot`` for OPS shots; a false
  trigger's clip never reaches it), five per rung, then the next rung's controls
  are applied live on the running camera: no window, no restart;
- every shot carries the ladder's picture-only verdict, recorded silently;
- after the last rung the automatic swing exposure takes over, seeded from the
  light index.

``build_plan`` runs in the tester server, which knows the lens settings, the
setup's light and lock; ``FreePlayExposureStudy`` runs in the kiosk, which knows
the shots and owns the camera. The plan travels as a JSON file in the run folder,
and the study keeps its progress in ``STATE_NAME`` beside it, so the bundle and
the bench read both.
"""

from __future__ import annotations

import json
import logging
import math
import os
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from openflight.camera import study_ladder, swing_exposure as sx, tester_light

logger = logging.getLogger(__name__)

PLAN_SCHEMA = "openflight.free_play_exposure_plan.v1"
STATE_SCHEMA = "openflight.free_play_exposure_study.v1"
SHOT_SCHEMA = "openflight.free_play_exposure_study_shot.v1"
VERDICT_SCHEMA = "openflight.free_play_swing_verdict.v1"
PLAN_NAME = "free_play_exposure_plan.json"
STATE_NAME = "free_play_exposure_study.json"
SWINGS_PER_RUNG = study_ladder.SWINGS_PER_RUNG
# New controls take a few frames to reach the sensor, and the ring then refills
# its pre-trigger frames at them: the ready light stays amber that long.
SETTLE_S = 0.6
VERDICT_COUNT_KEYS = ("green", "amber", "red", "no_capture", "error")
# Without any measured light nothing can be predicted: the camera starts at the
# blur ceiling at unity gain, labelled, and the automatic exposure searches from there.
NO_LIGHT_STARTUP_GAIN = 1.0
_BALL_LIGHT_CAUSES = frozenset({"ball_clipped", "ball_dark"})
_ZONE_LIGHT_CAUSES = frozenset({"zone_dark", "zone_clipped_no_ball"})


def resolution(arm_id: str) -> dict:
    """The mode the tester chose, as the session log and the bench record it."""
    config = sx.load_config()
    mode = config["modes"][arm_id]
    width, height = int(mode["width"]), int(mode["height"])
    return {
        "arm_id": arm_id,
        "label": f"{width}×{height}",
        "width": width,
        "height": height,
        "fps": float(mode["fps"]),
    }


def _rung(arm_id: str, exposure_us: int) -> study_ladder.Rung:
    for rung in study_ladder.LADDER:
        if rung.arm_id == arm_id and rung.exposure_us == int(exposure_us):
            return rung
    prefix = "full" if arm_id == "arm5" else "half"
    return study_ladder.Rung(f"{prefix}-{int(exposure_us)}", arm_id, int(exposure_us), False)


def _light_reason(item: Mapping) -> str:
    return (
        f"the setup's light gives the ball about {item['predicted_ball_dn']:g} DN here even at "
        f"gain {study_ladder.GAIN_CEILING:g}, under the {study_ladder.BALL_MIN_SIGNAL_DN:g} DN "
        f"gate: it needs {item['light_needed_factor']:g}x more light"
    )


def _startup(
    arm_id: str,
    rungs: list[dict],
    light_index: float | None,
    sensor_limits: sx.SensorLimits | None,
) -> dict:
    """The controls the kiosk's camera starts at: never the near-black 8 us x 1."""
    first = next((rung for rung in rungs if rung["status"] == "pending"), None)
    if first is not None:
        return {"exposure_us": first["exposure_us"], "gain": first["gain"], "source": "first_rung"}
    seed = tester_light.kiosk_would_use(light_index, arm_id, sensor_limits)
    if "exposure_us" in seed:
        return {
            "exposure_us": int(seed["exposure_us"]),
            "gain": round(float(seed["gain"]), 3),
            "source": "automatic_seed_estimate",
        }
    return {
        "exposure_us": sx.blur_ceiling_us(sx.load_config(), arm_id),
        "gain": NO_LIGHT_STARTUP_GAIN,
        "source": "no_light_measured_blur_ceiling",
        "reason": seed.get("reason"),
    }


def build_plan(  # pylint: disable=too-many-arguments
    arm_id: str,
    ladder_us: list[int],
    *,
    light_index: float | None,
    sensor_limits: sx.SensorLimits | None,
    gain_basis: Mapping | None,
    gain_at_300: float | None = None,
    resolution_choice: Mapping | None = None,
) -> dict:
    """The ladder free play will walk at this resolution, decided before any swing.

    ``ladder_us`` is the lens settings' rungs for the mode; they run longest first.
    ``light_index`` is the setup's 1280x800 ball index (DN above black per us x
    gain), ``sensor_limits`` the mode's limits as its camera last reported them,
    ``gain_basis`` the setup lock a rung's gain keeps (``ladder_gain_basis``) and
    ``gain_at_300`` a gain screen's, used only without a lock.
    """
    if arm_id not in study_ladder.RUNG_FPS:
        raise ValueError(f"free play's ladder runs at 1280x800 or 640x400, not {arm_id}")
    exposures = sorted({int(value) for value in ladder_us}, reverse=True)
    rungs = tuple(_rung(arm_id, exposure) for exposure in exposures)
    measured = light_index is not None and math.isfinite(light_index) and light_index > 0
    support = (
        {
            item["rung_id"]: item
            for item in study_ladder.rung_light_support(light_index, rungs=rungs)
        }
        if measured
        else {}
    )
    minimum = sensor_limits.exposure_min_us if sensor_limits is not None else None
    below = study_ladder.rungs_below_sensor_minimum({arm_id: minimum}, rungs=rungs)
    entries = []
    for rung in rungs:
        entry = {
            "rung_id": rung.rung_id,
            "exposure_us": rung.exposure_us,
            "gain": None,
            "gain_source": None,
            "gain_basis": None,
            "status": "pending",
            "skip_cause": None,
            "skip_reason": None,
            "light_support": support.get(rung.rung_id),
        }
        if rung.rung_id in below:
            entry.update(
                status="skipped", skip_cause="below_sensor_minimum", skip_reason=below[rung.rung_id]
            )
        elif support and not support[rung.rung_id]["supported"]:
            entry.update(
                status="skipped",
                skip_cause="light",
                skip_reason=_light_reason(support[rung.rung_id]),
            )
        elif gain_basis is None and gain_at_300 is None:
            entry.update(
                status="skipped",
                skip_cause="no_gain",
                skip_reason="the setup has no ball lock or gain screen to set this gain from",
            )
        else:
            gain, basis = study_ladder.starting_gain(
                rung.exposure_us,
                gain_at_300 if gain_basis is None else None,
                dict(gain_basis) if gain_basis is not None else None,
            )
            entry.update(gain=gain, gain_source=basis["source"], gain_basis=basis)
        entries.append(entry)
    runnable = [entry for entry in entries if entry["status"] == "pending"]
    if runnable:
        status, reason = "ready", None
    elif not measured and gain_basis is None and gain_at_300 is None:
        status, reason = "not_run", "the setup measured no light: no rung's gain can be set"
    elif not entries:
        status, reason = "not_run", "the lens settings give this mode no rungs"
    else:
        status, reason = "not_run", "no rung can work in the setup's light at this resolution"
    return {
        "schema": PLAN_SCHEMA,
        "resolution": resolution(arm_id),
        "resolution_choice": dict(resolution_choice) if resolution_choice else None,
        "swings_per_rung": SWINGS_PER_RUNG,
        "light_index_dn_per_us_gain": float(light_index) if measured else None,
        "light_status": "measured" if measured else "not_measured",
        "mode_signal_factor": study_ladder.MODE_SIGNAL_FACTOR[arm_id],
        "sensor_limits": sensor_limits.to_dict() if sensor_limits is not None else None,
        "rungs": entries,
        "status": status,
        "reason": reason,
        "startup": _startup(arm_id, entries, light_index if measured else None, sensor_limits),
        "label": "estimated",  # the skips are predictions from the setup's still ball
    }


def write_json(path: Path, payload: Mapping) -> None:
    """Atomically replace ``path`` with ``payload`` (the bench may read it mid-session)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", delete=False, suffix=".tmp", dir=path.parent
        ) as handle:
            temporary = Path(handle.name)
            handle.write(json.dumps(payload, indent=2) + "\n")
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _counts(swings: list[dict]) -> dict:
    counts = dict.fromkeys(VERDICT_COUNT_KEYS, 0)
    for swing in swings:
        verdict = swing.get("verdict") or {}
        status = verdict.get("status")
        if status == "judged" and verdict.get("color") in counts:
            counts[verdict["color"]] += 1
        elif status == "error":
            counts["error"] += 1
        elif status == "no_capture":
            counts["no_capture"] += 1
    return counts


class FreePlayExposureStudy:  # pylint: disable=too-many-instance-attributes
    """The kiosk side: counts shots per rung, moves the controls, files the verdicts.

    ``apply_controls(exposure_us, gain)`` sets held controls on the running camera,
    ``begin_automatic()`` hands over to the swing exposure policy and returns its
    seed decision, and ``current_controls()`` reads the controls in force. With no
    camera (both None) shots are labelled ``camera_unavailable``.
    """

    def __init__(  # pylint: disable=too-many-arguments
        self,
        plan: Mapping,
        state_path: Path,
        *,
        apply_controls: Callable[[int, float], Any] | None,
        begin_automatic: Callable[[], Any] | None,
        current_controls: Callable[[], tuple[int, float]] | None = None,
        live_sensor_min_us: int | None = None,
        run_id: str | None = None,
        run_async: Callable[[Callable[[], None]], None] | None = None,
        settle_s: float = SETTLE_S,
    ):
        self.state_path = Path(state_path)
        self._apply_controls = apply_controls
        self._begin_automatic = begin_automatic
        self._current_controls = current_controls
        self._run_async = run_async or self._thread
        self._settle_s = float(settle_s)
        self._lock = threading.RLock()
        self._data = {
            "schema": STATE_SCHEMA,
            "run_id": run_id,
            "plan_schema": plan.get("schema"),
            "resolution": dict(plan["resolution"]),
            "resolution_choice": plan.get("resolution_choice"),
            "swings_per_rung": int(plan.get("swings_per_rung") or SWINGS_PER_RUNG),
            "light_index_dn_per_us_gain": plan.get("light_index_dn_per_us_gain"),
            "startup": plan.get("startup"),
            "phase": "ladder",
            "ladder_status": "running",
            "reason": plan.get("reason"),
            "rungs": [
                {**dict(rung), "swings": [], "counts": _counts([]), "started_at": None}
                for rung in plan["rungs"]
            ],
            "automatic": None,
            "started_at": _now(),
            "updated_at": None,
            "stopped_at": None,
            # the ready light is amber while a new setting reaches the sensor
            "changing": False,
        }
        with self._lock:  # a hand-over started here saves from its own thread
            self._start(plan, live_sensor_min_us)

    def _start(self, plan: Mapping, live_sensor_min_us: int | None) -> None:
        if live_sensor_min_us is not None:
            self._skip_below_live_minimum(int(live_sensor_min_us))
        if self._apply_controls is None or self._begin_automatic is None:
            self._data.update(
                phase="camera_unavailable",
                ladder_status="not_run",
                reason="the camera is not running: no exposure can be set",
            )
            self._save()
            return
        first = self._next_pending()
        if first is None:
            self._data["ladder_status"] = "not_run"
            self._data["reason"] = self._data["reason"] or "no rung can run"
            self._start_automatic_now()
        else:
            # the kiosk's camera was started at this rung's controls (the plan's startup)
            first.update(status="active", started_at=_now())
            if (plan.get("startup") or {}).get("source") != "first_rung":
                self._schedule_apply(first)
        self._save()

    # --- helpers ---------------------------------------------------------------------

    @staticmethod
    def _thread(work: Callable[[], None]) -> None:
        threading.Thread(target=work, daemon=True, name="free-play-exposure").start()

    def _skip_below_live_minimum(self, minimum_us: int) -> None:
        mode = self._data["resolution"]["label"]
        for rung in self._data["rungs"]:
            if rung["status"] == "pending" and rung["exposure_us"] < minimum_us:
                rung.update(
                    status="skipped",
                    skip_cause="below_sensor_minimum",
                    skip_reason=f"below the sensor's shortest exposure at {mode} ({minimum_us} us)",
                )

    def _runnable(self) -> list[dict]:
        return [rung for rung in self._data["rungs"] if rung["status"] != "skipped"]

    def _active(self) -> dict | None:
        return next((rung for rung in self._data["rungs"] if rung["status"] == "active"), None)

    def _next_pending(self) -> dict | None:
        return next((rung for rung in self._data["rungs"] if rung["status"] == "pending"), None)

    def _save(self) -> None:
        self._data["updated_at"] = _now()
        try:
            write_json(self.state_path, self._data)
        except OSError:
            logger.warning("[FREE PLAY] Could not save the exposure study", exc_info=True)

    def _schedule_apply(self, rung: dict) -> None:
        self._data["changing"] = True
        rung_id, exposure, gain = rung["rung_id"], rung["exposure_us"], rung["gain"]

        def work() -> None:
            error = None
            try:
                self._apply_controls(exposure, gain)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                error = str(exc) or type(exc).__name__
                logger.warning("[FREE PLAY] Could not set %s: %s", rung_id, error)
            if error is None and self._settle_s > 0:
                time.sleep(self._settle_s)
            with self._lock:
                for entry in self._data["rungs"]:
                    if entry["rung_id"] == rung_id:
                        entry["applied_at"] = None if error else _now()
                        entry["apply_error"] = error
                self._data["changing"] = False
                self._save()

        self._run_async(work)

    def _start_automatic_now(self) -> None:
        self._data["phase"] = "automatic"
        self._data["automatic"] = {"started_at": _now(), "seed": None, "error": None}
        self._data["changing"] = True

        def work() -> None:
            seed, error = None, None
            try:
                seed = self._begin_automatic()
            except Exception as exc:  # pylint: disable=broad-exception-caught
                error = str(exc) or type(exc).__name__
                logger.warning("[FREE PLAY] Could not start the automatic exposure: %s", error)
            with self._lock:
                self._data["automatic"].update(
                    seed=json.loads(json.dumps(seed, default=str)) if seed else None,
                    error=error,
                )
                self._data["changing"] = False
                self._save()

        self._run_async(work)

    def _advance(self) -> None:
        following = self._next_pending()
        if following is None:
            self._data["ladder_status"] = "complete"
            self._start_automatic_now()
            return
        following.update(status="active", started_at=_now())
        self._schedule_apply(following)

    # --- the kiosk's calls -------------------------------------------------------------

    def on_shot(self, shot_number: int | None) -> dict:
        """Count one real (OPS) shot and return its per-shot record (``SHOT_SCHEMA``)."""
        with self._lock:
            phase = self._data["phase"]
            runnable = self._runnable()
            record = {
                "schema": SHOT_SCHEMA,
                "ladder_phase": phase,
                "resolution": dict(self._data["resolution"]),
                "rung_id": None,
                "setting_index": None,
                "settings_total": len(runnable),
                "swing_of_rung": None,
                "swings_per_rung": self._data["swings_per_rung"],
                "exposure_us": None,
                "gain": None,
                "controls_source": None,
                "verdict": no_capture_verdict("no matched camera capture"),
            }
            rung = self._active() if phase == "ladder" else None
            if rung is not None:
                rung["swings"].append(
                    {"shot_number": shot_number, "counted_at": _now(), "verdict": None}
                )
                record.update(
                    rung_id=rung["rung_id"],
                    setting_index=runnable.index(rung) + 1,
                    swing_of_rung=len(rung["swings"]),
                    exposure_us=rung["exposure_us"],
                    gain=rung["gain"],
                    controls_source="ladder_rung",
                )
                if len(rung["swings"]) >= self._data["swings_per_rung"]:
                    rung.update(status="done", finished_at=_now())
                    self._advance()
            elif phase == "automatic":
                controls = None
                if self._current_controls is not None:
                    try:
                        controls = self._current_controls()
                    except Exception:  # pylint: disable=broad-exception-caught
                        logger.warning("[FREE PLAY] Controls unreadable", exc_info=True)
                if controls is not None:
                    record.update(exposure_us=int(controls[0]), gain=float(controls[1]))
                record["controls_source"] = "automatic_policy"
                automatic: dict = self._data["automatic"] or {}
                automatic["shots"] = int(automatic.get("shots") or 0) + 1
                self._data["automatic"] = automatic
            self._save()
            return record

    def record_verdict(self, shot_number: int | None, verdict: Mapping) -> None:
        """File a shot's picture verdict against the rung it was counted for."""
        with self._lock:
            for rung in self._data["rungs"]:
                for swing in rung["swings"]:
                    if swing["shot_number"] == shot_number:
                        swing["verdict"] = dict(verdict)
                        rung["counts"] = _counts(rung["swings"])
                        self._save()
                        return
            automatic = self._data.get("automatic")
            if automatic is not None and shot_number is not None:
                verdicts = automatic.setdefault("verdicts", [])
                verdicts.append({"shot_number": shot_number, "verdict": dict(verdict)})
                automatic["counts"] = _counts(verdicts)
                self._save()

    def finish(self) -> None:
        """Stop & package: the ladder is complete, or recorded as partial."""
        with self._lock:
            if self._data["ladder_status"] == "running":
                self._data["ladder_status"] = "partial"
                for rung in self._data["rungs"]:
                    if rung["status"] == "active":
                        rung.update(status="incomplete", finished_at=_now())
                    elif rung["status"] == "pending":
                        rung["status"] = "not_reached"
            self._data["stopped_at"] = _now()
            self._save()

    # --- what the pages read -----------------------------------------------------------

    def ready_state(self) -> dict:
        """For the ready light: the phase, and whether the setting is changing (amber)."""
        with self._lock:
            return {"phase": self._data["phase"], "changing": bool(self._data["changing"])}

    def progress(self) -> dict:
        """The one line both pages show, plus each rung's verdict counts (tester page)."""
        with self._lock:
            return progress_from_state(self._data)

    def to_dict(self) -> dict:
        """A copy of the saved study: what ``STATE_NAME`` holds."""
        with self._lock:
            return json.loads(json.dumps(self._data))


# --- the per-swing picture verdict ------------------------------------------------------


def no_capture_verdict(reason: str) -> dict:
    """A shot with no clip to judge: it still counted, and says why there is no picture."""
    return {
        "schema": VERDICT_SCHEMA,
        "status": "no_capture",
        "color": None,
        "reason": reason,
        "capture": None,
        "ball_usable": None,
        "club_usable": None,
    }


def judge_swing(  # pylint: disable=too-many-arguments
    capture_dir: Path,
    *,
    arm_id: str,
    rung_id: str | None,
    exposure_us: int | None,
    gain: float | None,
    expected_ball: Mapping | None,
    zone_box=None,
    black_floor: float = 16.0,
    previous_balls: list[dict] | None = None,
) -> dict:
    """The ladder's picture-only verdict for one clip, with ball and club usability.

    ``exposure_us`` and ``gain`` are the rung's controls; an automatic shot (no
    rung) is judged against the controls its own trigger recorded. ``ball_usable``:
    the resting ball was found and is neither clipped nor too dark. ``club_usable``:
    the frames arrived at the mode's rate without gaps, at the intended controls,
    and the hitting zone (the club's background) is neither too dark nor clipped.
    """
    capture_dir = Path(capture_dir)
    try:
        if exposure_us is None or gain is None:
            metadata = json.loads((capture_dir / "metadata.json").read_text(encoding="utf-8"))
            controls = metadata.get("auto_exposure") or {}
            exposure_us = int(round(float(controls["exposure_us"])))
            gain = float(controls["gain"])
        rung = study_ladder.Rung(rung_id or "automatic", arm_id, int(exposure_us), False)
        verdict = study_ladder.swing_verdict(
            capture_dir,
            rung,
            float(gain),
            float(black_floor),
            list(previous_balls or []),
            dict(expected_ball) if expected_ball is not None else None,
            zone_box,
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return {
            **no_capture_verdict(f"the clip could not be judged: {type(exc).__name__}: {exc}"),
            "status": "error",
            "capture": capture_dir.name,
        }
    reasons = verdict.get("reasons") or []
    # frames and controls reasons are always red: the clip cannot carry the club
    frames_or_controls = any(reason.startswith(("frames:", "controls:")) for reason in reasons)
    return {
        "schema": VERDICT_SCHEMA,
        "status": "judged",
        "reason": None,
        **verdict,
        "ball_usable": bool(
            verdict.get("ball") is not None and verdict.get("light_cause") not in _BALL_LIGHT_CAUSES
        ),
        "club_usable": bool(
            not frames_or_controls
            and verdict.get("light_cause") not in _ZONE_LIGHT_CAUSES
            and not verdict.get("zone_dark")
        ),
        "judged_against": {"rung_id": rung_id, "exposure_us": int(exposure_us), "gain": gain},
    }


def _runnable_rungs(data: Mapping) -> list[Mapping]:
    return [rung for rung in data["rungs"] if rung["status"] != "skipped"]


def progress_from_state(data: Mapping) -> dict:
    """What the kiosk band and the tester page show, from a study (``STATE_NAME``).

    ``text`` is the one line: ``Exposure study: 150 µs · swing 3 of 5 · setting 3 of 6``
    on the ladder, ``Automatic exposure`` after it.
    """
    phase = data["phase"]
    runnable = _runnable_rungs(data)
    active = next((rung for rung in runnable if rung["status"] == "active"), None)
    if phase != "ladder":
        active = None
    per_rung = int(data["swings_per_rung"])
    rung, index = None, None
    if active is not None:
        index = runnable.index(active) + 1
        done = len(active["swings"])
        rung = {
            "rung_id": active["rung_id"],
            "exposure_us": active["exposure_us"],
            "gain": active["gain"],
            "swings_done": done,
        }
        text = (
            f"Exposure study: {active['exposure_us']} µs · swing "
            f"{min(done + 1, per_rung)} of {per_rung} · setting {index} of {len(runnable)}"
        )
    elif phase == "automatic":
        text = "Automatic exposure"
    elif phase == "starting":
        text = "Exposure study: starting"
    else:
        text = "Exposure study: camera unavailable"
    return {
        "phase": phase,
        "ladder_status": data.get("ladder_status"),
        "resolution": dict(data["resolution"]),
        "text": text,
        "changing": bool(data.get("changing")),
        "rung": rung,
        "setting_index": index,
        "settings_total": len(runnable),
        "swings_per_rung": per_rung,
        "rungs": [
            {
                key: entry.get(key)
                for key in ("rung_id", "exposure_us", "gain", "status", "skip_reason")
            }
            | {
                "swings": len(entry.get("swings") or []),
                "counts": dict(entry.get("counts") or _counts([])),
            }
            for entry in data["rungs"]
        ],
        "automatic_counts": dict((data.get("automatic") or {}).get("counts") or _counts([])),
    }


def progress_from_plan(plan: Mapping) -> dict:
    """Before the kiosk has written its study: the plan, waiting for the camera."""
    return progress_from_state(
        {
            "phase": "starting",
            "ladder_status": "starting" if plan.get("status") == "ready" else plan.get("status"),
            "resolution": plan["resolution"],
            "swings_per_rung": plan.get("swings_per_rung") or SWINGS_PER_RUNG,
            "rungs": [{**rung, "swings": []} for rung in plan["rungs"]],
        }
    )

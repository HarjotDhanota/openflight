"""EXPERIMENTAL impact location on the kiosk, read after each shot.

The owner chose to show testers the camera's impact location before it is
validated, so they can judge it against their own face spray marks. It is the
post-session method of ``openflight.camera.impact_location`` and
``impact_session``, run live:

- once a shot with a saved camera clip has been shown, its clip goes to a
  worker process at low priority, which never holds up the kiosk, the next shot,
  the ready light or the camera;
- the worker keeps a running head outline per (club, frame size) from this
  session's swings so far, built once the club has ``swings_needed`` swings with
  a head in them and rebuilt from the latest ones while the worker is idle (a new
  shot interrupts the rebuild). The swings pool their SHAPE only; every reading
  is the shot's own;
- clips wider than ``analysis_max_width`` are averaged down to it first, which
  keeps a reading to a few seconds on a Pi 5;
- the reading goes on ``Shot.impact_location`` and out as a shot update. Its two
  metric records are labelled ``experimental`` and feed no other metric;
- a reading still being worked on when the next shot arrives is dropped, and
  says so. Every reading and refusal is written to the session log
  (``impact_location`` entries).

The kiosk loads this module through the ``openflight.kiosk_extensions`` entry
point; ``--no-impact-live`` turns it off. The worker is this module run with
``--worker``; it reads jobs as JSON lines on stdin and answers on stdout.
"""

from __future__ import annotations

import importlib
import json
import logging
import math
import os
import queue
import re
import subprocess
import sys
import threading
import time
from copy import deepcopy
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Mapping

from openflight import kiosk_extensions

logger = logging.getLogger(__name__)

SCHEMA = "openflight.impact_location.live.v1"
SWINGS_NEEDED = 5
POOL_MAX_SWINGS = 8
ANALYSIS_MAX_WIDTH = 640
WORKER_NICE = 10
IDLE_REFRESH_S = 0.5
STOP_WAIT_S = 3.0
RECENT_CLIPS = 32
START_KEYS = ("golfer", "rig_geometry", "target_line", "tee_range_handoff")

BUILDING_CODE = "building_the_head_outline"
DROPPED_CODE = "dropped_the_next_shot_arrived"
DROPPED_REASON = "dropped: the next shot arrived before this one was read"
STOPPED_REASON = "the impact analysis stopped before it read this shot"

PLAIN_REASONS: tuple[tuple[str, str], ...] = (
    (r"^no_ball_range_for_this_shot", "no radar distance to the ball on this shot"),
    (r"^ball_range_.*_implausible", "the ball's distance is implausible"),
    (r"^resting_ball_size_", "the resting ball's size disagrees with its distance"),
    (r"^resting_ball_not_found", "no resting ball found in the clip"),
    (r"^frames_saturat", "glare: the picture is saturated around the ball"),
    (r"^resting_ball_contrast_", "the ball does not stand out from what is behind it"),
    (r"^ball_never_moved", "the camera never saw the ball leave"),
    (r"^ball_departure_witnesses_disagree", "the moment the ball left is unclear"),
    (r"^ball_moved_before", "the ball moved before the clip's reference frames ended"),
    (r"^clip_has_\d+_frames", "the clip is too short"),
    (r"^no_background_frames", "the swing starts too early in the clip"),
    (r"^no_template_for_this_club_no_head_mask", "the club head could not be isolated in the clip"),
    (r"^no_accepted_frame_", "the head outline did not fit the frames around contact"),
    (r"^bracket_", "the frames around contact are too far apart"),
    (r"^carried_outline_has_no_heel_toe_span", "the head outline has no heel-to-toe span"),
    (r"^contact_.*(beyond_the_toe|beyond_any_iron_face)", "the reading falls off the face"),
    (r"^ball_centre_.*below_the_sole_line", "the ball sits below the head's sole line"),
    (r"^height_.*_is_off_any_face", "the height falls off the face"),
    (r"^the_ball_is_beyond_the_outline_sole", "the ball is beyond the outline's sole"),
    (r"^no_sole_line_at_contact", "no sole line at contact"),
    (r"^frame_rate_unknown", "the clip's frame rate is unknown"),
    (r"^(unreadable_frames_npz|frames_npz_is_not_a_clip)", "the camera clip could not be read"),
    (r"^head_outline_did_not_build", "the head outline did not build from this club's swings"),
    (r"^analysis_failed", "the impact analysis failed on this shot"),
)


def _server():
    """The kiosk server, imported only on the kiosk side (the worker never loads it)."""
    from openflight import server  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

    return server


def plain_reason(code: str | None) -> str:
    """A refusal code in the words the kiosk shows; an unknown code with spaces."""
    text = str(code or "not_read")
    for pattern, words in PLAIN_REASONS:
        if re.search(pattern, text):
            return words
    return text.replace("_", " ")


def _number(value: Any, digits: int = 2) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, digits) if math.isfinite(number) else None


@dataclass
class _Pool:
    """One (club, frame size)'s swings with a head in them, and the outline built from them."""

    samples: list = field(default_factory=list)
    template: Any = None
    built_from: int | None = None
    version: int = 0
    built_version: int = -1


class LiveAnalyser:
    """Reads one shot at a time against a running per-club outline (EXPERIMENTAL)."""

    def __init__(
        self,
        *,
        swings_needed: int = SWINGS_NEEDED,
        pool_max_swings: int = POOL_MAX_SWINGS,
        analysis_max_width: int | None = ANALYSIS_MAX_WIDTH,
        lens_id: str | None = None,
    ):
        self.swings_needed = max(1, int(swings_needed))
        self.pool_max_swings = max(self.swings_needed, int(pool_max_swings))
        self.analysis_max_width = analysis_max_width
        self.lens_id = lens_id
        self._pools: dict[str, _Pool] = {}
        self._session: Any = object()

    def _prepare(self, job: Mapping[str, Any]):
        from openflight.camera import impact_session  # noqa: PLC0415

        path = Path(str(job["clip_path"]))
        number = job.get("shot_number")
        clip = impact_session.Clip(path.name, path, path.name, run_dir=path.parent)
        run = impact_session.RunRecord(
            {"config": dict(job.get("start") or {})},
            {number: dict(job.get("shot") or {})},
            {path.name: number},
            {},
            None,
        )
        options = impact_session.Options(
            handedness=job.get("handedness"),
            analysis_max_width=self.analysis_max_width,
            lens_id=self.lens_id,
        )
        impact_session.prepare_clip(clip, {path.parent: run}, {}, options)
        return clip

    def _template_note(self, pool: _Pool) -> dict[str, Any]:
        return {
            "swings": len(pool.samples),
            "needed": self.swings_needed,
            "built_from": pool.built_from,
        }

    def analyse(  # pylint: disable=too-many-return-statements
        self, job: Mapping[str, Any], should_stop: Callable[[], bool] | None = None
    ) -> dict[str, Any]:
        """One shot's reading: ok with both axes, or refused with a named reason."""
        from openflight.camera import impact_location as il  # noqa: PLC0415

        started = time.perf_counter()
        stop = should_stop or (lambda: False)
        if job.get("session_uuid") != self._session:
            self._pools.clear()
            self._session = job.get("session_uuid")
        stages: dict[str, float] = {}
        clip = self._prepare(job)
        stages["prepare_ms"] = (time.perf_counter() - started) * 1000.0
        base = {
            "schema": SCHEMA,
            "label": il.EXPERIMENTAL_LABEL,
            "club": clip.club,
            "club_category": il.club_category(clip.club),
            "handedness": clip.handedness,
            "analysis_frame_size": clip.frame_size,
            "recorded_frame_size": clip.extra.get("recorded_frame_size", clip.frame_size),
            "ball_range_source": clip.range_source,
            "stages_ms": stages,
            "dynamic_lie": clip.extra.get("dynamic_lie"),
            "angle_input_decisions": clip.extra.get("angle_input_decisions", {}),
        }
        pool = self._pools.setdefault(f"{clip.club}_{clip.frame_size}", _Pool())

        def finish(reading: dict[str, Any]) -> dict[str, Any]:
            reading.setdefault("template", self._template_note(pool))
            reading["analysis_ms"] = round((time.perf_counter() - started) * 1000.0, 1)
            for key, value in stages.items():
                stages[key] = round(value, 1)
            result = {**base, **reading}
            if reading.get("status") == "dropped":
                result.pop("dynamic_lie", None)
            return result

        prepared = clip.prepared
        if prepared is None:
            return finish(_refused(clip.error or "not_analysed"))
        if prepared.contact.status != "ok":
            return finish(_refused(prepared.contact.reason))
        has_head = bool(prepared.masks and prepared.masks.masks)
        if has_head:
            pool.samples = (pool.samples + [replace(prepared, frames=prepared.frames[:0])])[
                -self.pool_max_swings :
            ]
            pool.version += 1
        if stop():
            return finish(_dropped())
        if pool.template is None:
            if len(pool.samples) < self.swings_needed:
                if not has_head:
                    return finish(_refused(il.locate_impact(prepared, None, face=clip.face).reason))
                words = (
                    f"building the head outline: {len(pool.samples)} of {self.swings_needed} "
                    "swings with this club"
                )
                return finish({**_refused(BUILDING_CODE), "reason": words, "height_reason": words})
            built = time.perf_counter()
            template, why = il.build_self_template(pool.samples, club=clip.club)
            stages["template_ms"] = (time.perf_counter() - built) * 1000.0
            if template is None:
                return finish(_refused(f"head_outline_did_not_build_{why}"))
            pool.template, pool.built_from, pool.built_version = (
                template,
                len(pool.samples),
                pool.version,
            )
        if stop():
            return finish(_dropped())
        located = time.perf_counter()
        location = il.locate_impact(prepared, pool.template, face=clip.face)
        stages["locate_ms"] = (time.perf_counter() - located) * 1000.0
        return finish(_reading(location, clip))

    def refresh(self, should_stop: Callable[[], bool] | None = None) -> bool:
        """Rebuild one outline that has new swings since it was built; True when one was."""
        from openflight.camera import impact_location as il  # noqa: PLC0415

        for key, pool in self._pools.items():
            if pool.template is None or pool.version == pool.built_version:
                continue
            version, samples = pool.version, list(pool.samples)
            template, why = il.build_self_template(samples, club=key, should_stop=should_stop)
            if template is None:
                if why != "stopped":
                    pool.built_version = version
                return False
            pool.template, pool.built_from, pool.built_version = template, len(samples), version
            return True
        return False


def _refused(code: str) -> dict[str, Any]:
    words = plain_reason(code)
    return {
        "status": "rejected",
        "reason": words,
        "reason_code": code,
        "height_status": "rejected",
        "height_reason": words,
        "height_reason_code": code,
    }


def _dropped() -> dict[str, Any]:
    return {
        "status": "dropped",
        "reason": DROPPED_REASON,
        "reason_code": DROPPED_CODE,
        "height_status": "rejected",
        "height_reason": DROPPED_REASON,
        "height_reason_code": DROPPED_CODE,
    }


def _reading(location, clip) -> dict[str, Any]:
    if location.status != "ok":
        return _refused(location.reason)
    height_ok = location.height_status == "ok"
    face = clip.face
    return {
        "status": "ok",
        "reason": None,
        "reason_code": None,
        "impact_offset_mm": _number(location.impact_offset_mm),
        "impact_offset_sigma_mm": _number(location.impact_offset_sigma_mm),
        "height_status": location.height_status,
        "height_reason": None if height_ok else plain_reason(location.height_reason),
        "height_reason_code": None if height_ok else location.height_reason,
        "impact_height_mm": _number(location.impact_height_mm) if height_ok else None,
        "impact_height_sigma_mm": _number(location.impact_height_sigma_mm) if height_ok else None,
        "contact_frame": location.contact_frame,
        "face_centre_height_source": face.face_centre_height.source if face else None,
        "face_centre_from_toe_source": face.face_centre_from_toe.source if face else None,
        "dynamic_loft_source": face.dynamic_loft.source if face else None,
    }


def read_lines(fd: int):
    """Each line of a pipe as it arrives (a buffered reader can wait for a full block)."""
    pending = b""
    while chunk := os.read(fd, 65536):
        pending += chunk
        *lines, pending = pending.split(b"\n")
        yield from (line.decode("utf-8") for line in lines if line.strip())


def worker_main(argv: list[str]) -> int:
    """The worker process: jobs in on stdin, readings out on stdout, one JSON line each."""
    protocol = os.dup(1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    if hasattr(os, "nice"):
        try:
            os.nice(WORKER_NICE)
        except OSError:
            pass
    settings = json.loads(argv[0]) if argv else {}
    analyser = LiveAnalyser(**settings)
    # Everything the analysis imports is loaded before stdin is read: on Windows an
    # import that looks at stdin waits for a pending pipe read on another thread.
    importlib.import_module("openflight.camera.impact_session")
    importlib.import_module("cv2").setNumThreads(1)
    jobs: queue.Queue = queue.Queue()
    cancelled = {"through": 0}

    def read_commands() -> None:
        for line in read_lines(sys.stdin.fileno()):
            try:
                command = json.loads(line)
            except ValueError:
                continue
            if command.get("type") == "job":
                jobs.put(command)
            elif command.get("type") == "cancel":
                cancelled["through"] = max(cancelled["through"], int(command.get("through") or 0))
            elif command.get("type") == "stop":
                break
        jobs.put(None)

    threading.Thread(target=read_commands, name="impact-live-commands", daemon=True).start()
    while True:
        try:
            job = jobs.get(timeout=IDLE_REFRESH_S)
        except queue.Empty:
            analyser.refresh(should_stop=lambda: not jobs.empty())
            continue
        if job is None:
            return 0
        job_id = int(job["job_id"])
        if cancelled["through"] >= job_id:
            continue
        try:
            reading = analyser.analyse(job, should_stop=lambda: cancelled["through"] >= job_id)
        except Exception as error:  # pylint: disable=broad-exception-caught
            logger.warning("[IMPACT] shot #%s failed: %s", job.get("shot_number"), error)
            reading = _refused(f"analysis_failed_{type(error).__name__}")
        message = {"type": "result", "job_id": job_id, "reading": reading}
        os.write(protocol, (json.dumps(message) + "\n").encode("utf-8"))


class ProcessRunner:
    """The worker process, started at low priority with one thread for its maths."""

    def __init__(self, on_result: Callable[[dict], None], *, settings: dict | None = None):
        self._on_result = on_result
        self._settings = dict(settings or {})
        self._lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self._start()

    def _start(self) -> None:
        env = {**os.environ, "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1"}
        flags = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
        process = subprocess.Popen(  # pylint: disable=consider-using-with
            [sys.executable, "-m", __name__, "--worker", json.dumps(self._settings)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            bufsize=0,
            env=env,
            creationflags=flags,
        )
        self._process = process
        threading.Thread(
            target=self._read, args=(process,), name="impact-live-results", daemon=True
        ).start()

    def _read(self, process: subprocess.Popen) -> None:
        for line in read_lines(process.stdout.fileno()):
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if message.get("type") == "result":
                self._on_result(message)
        self._on_result({"type": "worker_exited", "runner": self})

    @staticmethod
    def _write(process: subprocess.Popen, command: dict) -> None:
        process.stdin.write((json.dumps(command, default=str) + "\n").encode("utf-8"))
        process.stdin.flush()

    def alive(self) -> bool:
        """Whether the worker process is running."""
        return self._process is not None and self._process.poll() is None

    def submit(self, job: dict) -> None:
        """Queue one shot's job; raises OSError when the worker cannot take it."""
        with self._lock:
            if not self.alive():
                raise OSError("the impact worker is not running")
            self._write(self._process, {"type": "job", **job})

    def cancel_through(self, job_id: int) -> None:
        """Drop every job up to ``job_id``, the one being read included."""
        with self._lock:
            if self.alive():
                try:
                    self._write(self._process, {"type": "cancel", "through": job_id})
                except OSError:
                    pass

    def stop(self) -> None:
        """Ask the worker to finish, and end it if it does not."""
        with self._lock:
            process, self._process = self._process, None
        if process is None:
            return
        try:
            self._write(process, {"type": "stop"})
            process.stdin.close()
        except OSError:
            pass
        try:
            process.wait(timeout=STOP_WAIT_S)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=STOP_WAIT_S)


@dataclass
class _OpenJob:
    shot: Any
    session_uuid: str | None
    submitted: float
    runner: Any = None


class Dispatcher:
    """Sends shots to the worker and puts each reading on its shot, or drops it."""

    def __init__(self, runner_factory, publish, log):
        self._runner_factory = runner_factory
        self._publish = publish
        self._log = log
        self._lock = threading.Lock()
        self._open: dict[int, _OpenJob] = {}
        self._next_id = 0
        self._runner = None

    def _ensure_runner(self):
        if self._runner is None:
            self._runner = self._runner_factory(self.on_result)
        return self._runner

    def warm(self) -> None:
        """Start the worker now, so its imports are done before the first shot."""
        try:
            with self._lock:
                self._ensure_runner()
        except OSError as error:
            logger.warning("[IMPACT] the impact worker did not start: %s", error)

    def submit(self, shot, body: dict) -> int:
        """Send one shot's clip to the worker; its reading arrives in ``on_result``."""
        with self._lock:
            self._next_id += 1
            job_id = self._next_id
            try:
                runner = self._ensure_runner()
            except OSError:
                runner = None
            self._open[job_id] = _OpenJob(shot, body.get("session_uuid"), time.monotonic(), runner)
        try:
            if runner is None:
                raise OSError("the impact worker did not start")
            runner.submit({"job_id": job_id, **body})
        except OSError as error:
            logger.warning("[IMPACT] the worker did not take shot #%s: %s", shot.shot_number, error)
            with self._lock:
                if self._runner is runner:
                    self._runner = None
            self._finish(job_id, _refused("analysis_failed_worker_unavailable"))
        return job_id

    def next_shot_arrived(self) -> None:
        """A new shot: drop every reading still open (the worker stops on it too)."""
        with self._lock:
            dropped = sorted(self._open)
            runner = self._runner
        if not dropped:
            return
        if runner is not None:
            runner.cancel_through(dropped[-1])
        for job_id in dropped:
            self._finish(job_id, _dropped())

    def on_result(self, message: dict) -> None:
        """A reading from the worker, or word that it exited."""
        if message.get("type") == "worker_exited":
            runner = message.get("runner")
            with self._lock:
                orphans = sorted(k for k, job in self._open.items() if job.runner is runner)
                if self._runner is runner:
                    self._runner = None
            for job_id in orphans:
                self._finish(job_id, {**_refused("worker_stopped"), "reason": STOPPED_REASON})
            return
        self._finish(int(message["job_id"]), dict(message.get("reading") or {}))

    def _finish(self, job_id: int, reading: dict) -> None:
        with self._lock:
            job = self._open.pop(job_id, None)
        if job is None:
            return
        reading["latency_ms"] = round((time.monotonic() - job.submitted) * 1000.0, 1)
        try:
            self._publish(job.shot, reading, job.session_uuid)
            self._log(job.session_uuid, {"shot_number": job.shot.shot_number, **reading})
        except Exception as error:  # pylint: disable=broad-exception-caught
            logger.warning("[IMPACT] shot #%s reading not shown: %s", job.shot.shot_number, error)

    def stop(self) -> None:
        """Stop the worker (kiosk shutdown)."""
        with self._lock:
            runner, self._runner = self._runner, None
        if runner is not None:
            runner.stop()


class ImpactLiveExtension(kiosk_extensions.KioskExtension):
    """The kiosk's hooks: note each shot's clip, send it once shown, drop on the next shot.

    Off until the kiosk configures it (``configure``), so importing it changes nothing.
    """

    name = "impact_live"

    def __init__(self, runner_factory=None):
        self.enabled = False
        self._start: dict[str, Any] = {}
        self._clips: dict[tuple, str] = {}
        self.dispatcher = Dispatcher(
            runner_factory or ProcessRunner,
            self._publish,
            self._log,
        )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--no-impact-live",
            action="store_true",
            help="Do not read the experimental impact location after each shot",
        )

    def configure(self, args, parser) -> None:
        self.enabled = not getattr(args, "no_impact_live", False)

    def session_start_config(self, config: dict) -> None:
        start = {key: config.get(key) for key in START_KEYS if key in config}
        self._start = json.loads(json.dumps(start, default=str))

    def after_camera_setup(self) -> None:
        if self.enabled and (_server().camera_capture_config or {}).get("enabled"):
            self.dispatcher.warm()

    def on_shot_accepted(self, shot) -> None:
        if self.enabled:
            self.dispatcher.next_shot_arrived()

    def on_camera_capture(self, shot, camera_capture) -> None:
        if not self.enabled or not getattr(camera_capture, "valid", False):
            return
        if getattr(camera_capture, "path", None) is None:
            return
        self._clips[(shot.camera_fusion_session_uuid, shot.shot_number)] = str(camera_capture.path)
        while len(self._clips) > RECENT_CLIPS:
            self._clips.pop(next(iter(self._clips)))

    def after_shot_published(self, shot, *, update_kind: str) -> None:
        if not self.enabled:
            return
        path = self._clips.pop((shot.camera_fusion_session_uuid, shot.shot_number), None)
        if path is None:
            return
        server = _server()
        # the hand the shot was hit with, not one the golfer chose since
        handed = server.shot_handedness(shot)
        self.dispatcher.submit(
            shot,
            {
                "session_uuid": shot.camera_fusion_session_uuid,
                "shot_number": shot.shot_number,
                "clip_path": path,
                "shot": json.loads(json.dumps(server.shot_to_dict(shot), default=str)),
                "start": deepcopy(self._start),
                "handedness": "left" if handed == "LH" else "right",
            },
        )

    def on_shutdown(self) -> None:
        self.dispatcher.stop()

    @staticmethod
    def _publish(shot, reading: dict, session_uuid: str | None) -> None:
        shot.impact_location = reading
        _server().publish_shot_update(
            shot,
            update={"reason": "impact_location", "status": reading.get("status")},
            session_uuid=session_uuid,
        )

    @staticmethod
    def _log(session_uuid: str | None, record: dict) -> None:
        session_log = _server().get_session_logger()
        if session_log is None:
            return
        active = getattr(session_log, "active_session_uuid", None)
        if session_uuid is not None and active != session_uuid:
            return
        session_log.log_event("impact_location", record)


extension = kiosk_extensions.register(ImpactLiveExtension())

if __name__ == "__main__" and sys.argv[1:2] == ["--worker"]:
    sys.exit(worker_main(sys.argv[2:]))

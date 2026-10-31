"""The bounded address preview's state machine; never canonical geometry or confirmation.

One ``observe`` per five-frame window. A broad rival search runs in its own
worker process (with two fit workers of its own); detector and appearance
proposals, local refreshes and the exposure bridge run in the caller's thread.
A ball is shown only while its fit is under 2 s old and current pixels agree.
"""

from __future__ import annotations

import hashlib
import multiprocessing
import threading
import time
from collections import deque
from concurrent.futures import ProcessPoolExecutor
from copy import deepcopy

import numpy as np

from openflight.camera.golf_ball_hint import controls_match
from openflight.camera.ground_patch import point_in_polygon
from openflight.camera.worker_scheduling import initialize_analysis_worker

from .appearance import propose as appearance_propose, remember as remember_appearance
from .detector_proposal import propose as fast_propose, warm as warm_fast
from .exposure_bridge import measure as measure_control_frames
from .local_refresh import refresh_local
from .rank import templates
from .search import close_fit_pool, configure_cancellation, search, warm_fit_pool
from .watch import PatchWatch, picture

APPEARANCE_CUE_MAX_AGE_S = 12.0  # Assumed cue lifetime; geometry freshness remains 2s.
POOL_START_TIMEOUT_S = 30


def boot(cancel_event=None) -> bool:
    """Prepare a broad-search worker: cancellation, templates and its fit pool."""
    if cancel_event is not None:
        configure_cancellation(cancel_event)
    templates()
    warm_fit_pool()
    return True


def _initialize_broad_worker(cancel_event) -> None:
    initialize_analysis_worker()
    boot(cancel_event)


def _ball_bounds(ball, camera):
    x, y, r = ball["x_px"], ball["y_px"], ball["radius_px"]
    return (
        max(0, int(x - 1.5 * r)),
        max(0, int(y - 1.5 * r)),
        min(camera.image_width_px, int(np.ceil(x + 1.5 * r))),
        min(camera.image_height_px, int(np.ceil(y + 1.5 * r))),
    )


def _window_sha256(frames) -> list[str]:
    return [hashlib.sha256(f.tobytes()).hexdigest() for f in frames]


class Preview:  # pylint: disable=too-many-instance-attributes
    """Observe five-frame windows; a snapshot says where the ball is while it is fresh."""

    def __init__(self, *, pool=None, clock=time.monotonic, model_path=None, bridge_exposure=False):
        self.lock = threading.RLock()
        self.clock = clock
        self.owns_pool = pool is None
        self.context = multiprocessing.get_context("spawn")
        self.cancel_event = self.context.Event()
        self.pool = (
            pool
            if pool is not None
            else ProcessPoolExecutor(
                max_workers=1,
                mp_context=self.context,
                initializer=_initialize_broad_worker,
                initargs=(self.cancel_event,),
            )
        )
        self.model_path = model_path
        self.fast_pending = None
        self.last_fast_at = None
        self.bridge_exposure = bridge_exposure
        self.cue = None
        self.geometry_signature = None
        self.measurement_method = None
        self.appearance = None
        self.bridge_events = deque(maxlen=512)
        self.fast_events = deque(maxlen=512)
        self.preheat_ms = 0
        try:
            if model_path is not None:
                warm_fast(model_path)
            if pool is None:
                start = time.monotonic()
                if self.pool.submit(int).result(timeout=POOL_START_TIMEOUT_S) != 0:
                    raise RuntimeError("the broad search worker did not start")
                self.preheat_ms = (time.monotonic() - start) * 1000
        except BaseException:
            if self.owns_pool:
                self.pool.shutdown(wait=False, cancel_futures=True)
            raise
        self.watch = PatchWatch(2)
        self.bounds = None
        self.selected = None
        self.source_at = None
        self.source_sequence = None
        self.phase = "waiting"
        self.generation = 0
        self.signature = None
        self.future = None
        self.job = None
        self.reader = None
        self.sender = None
        self.jobs = deque(maxlen=256)
        self.early_events = deque(maxlen=256)
        self.local_events = deque(maxlen=512)
        self.fit_parameters = None
        self.scene_restarts = deque(maxlen=256)
        self.broad_source_at = None
        self.last_broad_started_at = None
        self.closed = False
        self.failed = False
        self.last_sequence = None
        self.last_at = None

    def clear(self, *, keep_cue=False):
        """Forget the ball and its evidence (and the exposure cue unless kept)."""
        if not keep_cue:
            self.cue = None
        self.measurement_method = None
        self.selected = None
        self.source_at = None
        self.source_sequence = None
        self.fast_pending = None
        self.fit_parameters = None
        self.broad_source_at = None
        self.phase = "waiting"
        self.watch.forget()

    def _restart(self):
        """Discard every pending result: a new generation, and the broad search is told to stop."""
        self.generation += 1
        self.cancel_event.set()

    def _accept(  # pylint: disable=too-many-arguments
        self,
        ball,
        frames,
        camera,
        patch,
        controls,
        sequence,
        at,
        now,
        phase,
        parameters=None,
        job=None,
    ):
        job = self.job if job is None else job
        if (
            ball is None
            or job["generation"] != self.generation
            or job["signature"] != self.signature
            or not 0 <= now - job["input_at"] < 2
            or sequence <= job["sequence"]
        ):
            return False
        x, y, r = ball["x_px"], ball["y_px"], ball["radius_px"]
        bounds = _ball_bounds(ball, camera)
        check = PatchWatch(2)
        check.remember(
            job["frames"], bounds, self.signature, at=job["input_at"], sequence=job["sequence"]
        )
        evidence = check.check(frames, bounds, self.signature, at=now, sequence=sequence)
        job["current_pixel_check"] = evidence
        if not evidence["matched"]:
            if parameters is not None and self.selected is None and phase == "checking_rivals":
                self._refresh(frames, camera, patch, controls, sequence, at, parameters=parameters)
                return self.selected is not None
            return False
        if self.source_at is not None and self.source_at > job["input_at"]:
            if phase == "proposal":
                same = np.hypot(x - self.selected["x_px"], y - self.selected["y_px"]) <= 0.7 * min(
                    r, self.selected["radius_px"]
                )
                if same:
                    self.phase = phase
                    self.broad_source_at = job["input_at"]
                    return True
                self.clear()
            return False
        self.watch = check
        self.bounds = bounds
        self.selected = deepcopy(ball)
        self.source_at = job["input_at"]
        self.source_sequence = job["sequence"]
        self.phase = phase
        if phase == "proposal":
            self.broad_source_at = job["input_at"]
        self.fit_parameters = deepcopy(parameters)
        if parameters is not None:
            self.measurement_method = "same_controls_window"
            self._save_cue()
            self._save_appearance(frames, controls, at)
        return True

    def _refresh(self, frames, camera, patch, controls, sequence, at, parameters=None):
        started = self.clock()
        local_error = None
        window_sha256 = _window_sha256(frames)
        try:
            measured = refresh_local(
                frames, camera, self.fit_parameters if parameters is None else parameters
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            measured = None
            local_error = type(exc).__name__
            self.failed = True
        now = self.clock()
        accepted = bool(
            measured
            and 0 <= now - at < 2
            and point_in_polygon(
                measured["selected"]["x_px"], measured["selected"]["y_px"], patch.outline_px
            )
        )
        self.local_events.append(
            {
                "sequence": sequence,
                "input_at": at,
                "completed_at": now,
                "elapsed_ms": (now - started) * 1000,
                "accepted": accepted,
                "result": measured,
                "error": local_error,
                "window_sha256": window_sha256,
                "applied_controls": deepcopy(controls),
                "context_sha256": hashlib.sha256(self.signature.encode()).hexdigest(),
            }
        )
        if accepted:
            self.selected = deepcopy(measured["selected"])
            self.fit_parameters = deepcopy(measured["parameters"])
            self.source_at = at
            self.source_sequence = sequence
            self.phase = "checking_rivals"
            self.bounds = _ball_bounds(self.selected, camera)
            self.watch.remember(frames, self.bounds, self.signature, at=at, sequence=sequence)
            self.measurement_method = "same_controls_window"
            self._save_cue()
            self._save_appearance(frames, controls, at)
        else:
            self._restart()
            self.clear()
        return self.clock()

    def _save_cue(self):
        self.cue = {
            "parameters": deepcopy(self.fit_parameters),
            "at": self.source_at,
            "context": self.geometry_signature,
        }

    def _save_appearance(self, frames, controls, at):
        pixels = frames if controls_match(frames, controls) else frames[-1:]
        template = remember_appearance(pixels, self.selected)
        if template is not None:
            self.appearance = {"template": template, "at": at, "context": self.geometry_signature}

    def _propose(self, frames, camera, patch, controls, now):
        previous = self.appearance
        attempt = {"status": "not_attempted", "reason": "no measured appearance"}
        if previous is not None:
            age = now - previous["at"]
            attempt.update(age_s=age, limit_s=APPEARANCE_CUE_MAX_AGE_S)
            if previous["context"] != self.geometry_signature:
                attempt["reason"] = "geometry context changed"
            elif not 0 <= age < APPEARANCE_CUE_MAX_AGE_S:
                attempt["reason"] = "appearance cue expired or future dated"
            else:
                result = appearance_propose(frames, camera, patch, controls, previous["template"])
                attempt = {
                    "status": "attempted",
                    "age_s": age,
                    "limit_s": APPEARANCE_CUE_MAX_AGE_S,
                    "result": result,
                }
                if result["selected"] is not None or result["status"] == "ambiguous":
                    return dict(result, appearance_attempt=attempt)
        result = fast_propose(frames, camera, patch, controls, self.model_path)
        return dict(result, appearance_attempt=attempt)

    def _bridge(self, frames, camera, patch, controls, sequence, at):
        now = self.clock()
        cue = self.cue
        if cue is None or cue["context"] != self.geometry_signature or not 0 <= now - cue["at"] < 2:
            self.clear()
            return now
        error = None
        try:
            measured = measure_control_frames(frames, camera, patch, controls, cue["parameters"])
        except Exception as exc:  # pylint: disable=broad-exception-caught
            measured = None
            error = type(exc).__name__
        now = self.clock()
        accepted = bool(measured and 0 <= now - at < 2)
        self.bridge_events.append(
            {
                "sequence": sequence,
                "input_at": at,
                "completed_at": now,
                "accepted": accepted,
                "result": measured,
                "error": error,
                "controls": deepcopy(controls[-2:]),
                "seed_kind": cue.get("seed_kind", "previous_measured_geometry"),
                "frame_sha256": _window_sha256(frames[-2:]),
            }
        )
        if not accepted:
            self.clear()
            return now
        self.selected = deepcopy(measured["selected"])
        self.fit_parameters = deepcopy(measured["parameters"])
        self.source_at = at
        self.source_sequence = sequence
        self.phase = "checking_rivals"
        self.broad_source_at = None
        self.measurement_method = "separate_frame_readbacks"
        self._save_cue()
        self._save_appearance(frames, controls, at)
        self.bounds = _ball_bounds(self.selected, camera)
        if controls_match(frames, controls):
            self.watch.remember(frames, self.bounds, self.signature, at=at, sequence=sequence)
        else:
            self.watch.forget()
        return now

    def observe(  # pylint: disable=too-many-arguments,too-many-branches,too-many-statements
        self, frames, camera, patch, controls, sequence, *, at=None
    ):
        """Analyse one window; the snapshot of what is fresh after it."""
        with self.lock:
            now = self.clock()
            at = now if at is None else at
            if self.closed or self.failed:
                return self.snapshot(now)
            geometry_valid = (
                frames.ndim == 3
                and frames.dtype == np.uint8
                and len(frames) >= 5
                and frames.shape[1:] == (camera.image_height_px, camera.image_width_px)
            )
            valid = geometry_valid and controls_match(frames, controls)
            geometry_signature = (
                repr((camera, patch, frames.shape, frames.dtype.str)) if geometry_valid else None
            )
            geometry_changed = geometry_signature != self.geometry_signature
            if geometry_changed:
                self.cue = None
                self.appearance = None
                self.geometry_signature = geometry_signature
            signature = (
                repr((camera, patch, frames.shape, frames.dtype.str, controls)) if valid else None
            )
            if signature != self.signature:
                pending = self.fast_pending
                # An unpublished raw fit is only an initial guess. A controls
                # change requires separate NEW current-frame fits, never its
                # cached geometry. Camera/mode changes and stale seeds cannot
                # cross this boundary.
                if (
                    self.bridge_exposure
                    and not geometry_changed
                    and self.cue is None
                    and pending is not None
                    and pending["generation"] == self.generation
                    and pending["signature"] == self.signature
                    and sequence > pending["sequence"]
                    and at > pending["input_at"]
                    and 0 <= now - pending["input_at"] < 2
                ):
                    self.cue = {
                        "parameters": deepcopy(pending["parameters"]),
                        "at": pending["input_at"],
                        "context": geometry_signature,
                        "seed_kind": "unpublished_raw_fit",
                    }
                self.generation += 1
                self.signature = signature
                self.cancel_event.set()
                self.clear(keep_cue=not geometry_changed)
            if self.last_sequence is not None and (
                sequence <= self.last_sequence or at <= self.last_at
            ):
                self.appearance = None
                self._restart()
                self.clear()
                return self.snapshot(now)
            self.last_sequence = sequence
            self.last_at = at
            if (
                self.bridge_exposure
                and geometry_valid
                and self.cue is not None
                and (not valid or self.selected is None)
            ):
                now = self._bridge(frames, camera, patch, controls, sequence, at)
            if self.fast_pending is not None:
                pending = self.fast_pending
                self.fast_pending = None
                published = self._accept(
                    pending["selected"],
                    frames,
                    camera,
                    patch,
                    controls,
                    sequence,
                    at,
                    now,
                    "checking_rivals",
                    pending["parameters"],
                    job=pending,
                )
                self.fast_events.append(
                    {
                        "kind": "current_pixel_validation",
                        "sequence": sequence,
                        "proposal_sequence": pending["sequence"],
                        "published": published,
                        "evidence": pending.get("current_pixel_check"),
                    }
                )
                if published:
                    self._restart()
            if (
                self.future is not None
                and self.selected is None
                and signature is not None
                and self.job["generation"] == self.generation
            ):
                current, _ = picture(frames, self.job["scene_bounds"])
                delta = current - self.job["scene_picture"]
                change = float(np.max(np.abs(delta - np.median(delta))))
                if change > 20:
                    self._restart()
                    self.scene_restarts.append(
                        {
                            "sequence": sequence,
                            "job_sequence": self.job["sequence"],
                            "max_change_dn": change,
                            "limit_dn": 20,
                        }
                    )
            if self.future is not None:
                while self.reader.poll():
                    try:
                        event = self.reader.recv()
                    except EOFError:
                        break
                    published = self._accept(
                        event["selected"],
                        frames,
                        camera,
                        patch,
                        controls,
                        sequence,
                        at,
                        now,
                        "checking_rivals",
                        event.get("fit_parameters"),
                    )
                    self.early_events.append(
                        {
                            "sequence": self.job["sequence"],
                            "observed_at": now,
                            "input_at": self.job["input_at"],
                            "published": published,
                            "event": event,
                        }
                    )
                if self.future.done():
                    try:
                        result = self.future.result()
                        published = self._accept(
                            result["selected"],
                            frames,
                            camera,
                            patch,
                            controls,
                            sequence,
                            at,
                            now,
                            "proposal",
                            result.get("selected_parameters"),
                        )
                        if (
                            result["status"] == "ambiguous"
                            and self.job["generation"] == self.generation
                            and self.job["signature"] == self.signature
                        ):
                            self.clear()
                        self.job.update(result=result, published=published, completed_at=now)
                    except Exception as exc:  # pylint: disable=broad-exception-caught
                        self.clear()
                        self.failed = True
                        self.job.update(error=type(exc).__name__, published=False, completed_at=now)
                    self._finish_job()
            if valid and self.selected is not None and self.source_sequence != sequence:
                evidence = self.watch.check(
                    frames, self.bounds, self.signature, at=now, sequence=sequence
                )
                if not evidence["matched"]:
                    if (
                        self.fit_parameters is not None
                        and 0 <= now - self.source_at < 2
                        and evidence["reason"] != "fresh broad search required"
                    ):
                        now = self._refresh(frames, camera, patch, controls, sequence, at)
                    else:
                        if evidence["reason"] != "fresh broad search required":
                            self._restart()
                        self.clear()
            if (
                valid
                and self.selected is not None
                and self.fit_parameters is not None
                and 1 <= now - self.source_at < 2
            ):
                now = self._refresh(frames, camera, patch, controls, sequence, at)
            if (
                self.model_path is not None
                and self.selected is None
                and self.fast_pending is None
                and signature is not None
                and (self.last_fast_at is None or now - self.last_fast_at >= 0.5)
            ):
                self.last_fast_at = now
                fast_error = None
                try:
                    proposal = self._propose(frames, camera, patch, controls, now)
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    proposal = None
                    fast_error = type(exc).__name__
                now = self.clock()
                self.fast_events.append(
                    {
                        "kind": "inference",
                        "sequence": sequence,
                        "input_at": at,
                        "completed_at": now,
                        "result": proposal,
                        "error": fast_error,
                        "window_sha256": _window_sha256(frames),
                        "applied_controls": deepcopy(controls),
                        "context_sha256": hashlib.sha256(self.signature.encode()).hexdigest(),
                    }
                )
                if proposal and proposal["selected"] is not None and 0 <= now - at < 2:
                    self.fast_pending = {
                        "selected": proposal["selected"],
                        "parameters": proposal["parameters"],
                        "sequence": sequence,
                        "input_at": at,
                        "generation": self.generation,
                        "signature": self.signature,
                        "frames": frames.copy(),
                    }
            if (
                not self.failed
                and self.future is None
                and signature is not None
                and (
                    self.selected is None
                    or self.last_broad_started_at is None
                    or now - self.last_broad_started_at >= 0.6
                )
            ):
                self.reader, self.sender = self.context.Pipe(duplex=False)
                self.cancel_event.clear()
                self.last_broad_started_at = now
                self.job = {
                    "sequence": sequence,
                    "input_at": at,
                    "generation": self.generation,
                    "signature": self.signature,
                    "frames": frames.copy(),
                    "window_sha256": _window_sha256(frames),
                }
                self.job["scene_bounds"] = patch.bounds_px(
                    camera.image_width_px, camera.image_height_px
                )
                self.job["scene_picture"] = picture(frames, self.job["scene_bounds"])[0]
                self.future = self.pool.submit(
                    search, frames, camera, patch, controls, event_sink=self.sender
                )
            return self.snapshot(now)

    def _finish_job(self):
        self.job.pop("frames")
        self.job.pop("scene_picture", None)
        self.job["context_sha256"] = hashlib.sha256(self.job.pop("signature").encode()).hexdigest()
        self.jobs.append(self.job)
        self.reader.close()
        self.sender.close()
        self.reader = None
        self.sender = None
        self.future = None
        self.job = None

    def last_search(self) -> dict | None:
        """The newest finished broad search: its status, and whether it saw these controls."""
        with self.lock:
            if not self.jobs:
                return None
            job = self.jobs[-1]
            current = (
                hashlib.sha256(self.signature.encode()).hexdigest()
                if self.signature is not None
                else None
            )
            return {
                "status": (job.get("result") or {}).get("status"),
                "input_at": job.get("input_at"),
                "current_controls": current is not None and job.get("context_sha256") == current,
            }

    def snapshot(self, now=None):
        """What is fresh now; nothing is ever metric-eligible or confirmable."""
        with self.lock:
            now = self.clock() if now is None else now
            fresh = (
                self.selected is not None
                and 0 <= now - self.source_at < 2
                and not self.closed
                and not self.failed
            )
            phase = self.phase
            if phase == "proposal" and (
                self.broad_source_at is None or now - self.broad_source_at >= 2
            ):
                phase = "checking_rivals"
            return {
                "status": phase if fresh else ("worker_error" if self.failed else "waiting"),
                "selected": deepcopy(self.selected) if fresh else None,
                "source_sequence": self.source_sequence if fresh else None,
                "source_observed_at_monotonic": self.source_at if fresh else None,
                "broad_source_observed_at_monotonic": self.broad_source_at if fresh else None,
                "broad_look_age_s": (
                    now - self.broad_source_at
                    if fresh and self.broad_source_at is not None
                    else None
                ),
                "look_age_s": now - self.source_at if fresh else None,
                "rivals_pending": fresh and phase == "checking_rivals",
                "worker_pending": self.future is not None,
                "measurement_method": self.measurement_method if fresh else None,
                "metric_eligible": False,
                "confirmable": False,
            }

    def close(self):
        """Discard pending work and end the worker processes this preview started."""
        with self.lock:
            if self.closed:
                return
            self.appearance = None
            self.closed = True
            self._restart()
            self.clear()
            if self.future is not None:
                try:
                    self.future.result(timeout=30)
                except Exception:  # pylint: disable=broad-exception-caught
                    pass
                self.job.update(discarded_after_stop=True, published=False)
                self._finish_job()
            if self.owns_pool:
                try:
                    self.pool.submit(close_fit_pool).result(timeout=30)
                except Exception:  # pylint: disable=broad-exception-caught
                    pass
            self.pool.shutdown(wait=True, cancel_futures=True)

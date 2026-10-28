"""Per-shot re-location of the resting ball inside the setup's box, and its range.

The setup measures the ball once; a tester then tees the next ball wherever it
lands in the box. Before each swing the kiosk looks at its own latest camera
frame, finds the resting ball inside the setup record's box, and gives
that shot its own range:

* the setup's radar range, while the ball sits where the setup measured it
  (within ``SAME_SPOT_DIAMETERS`` of the setup ball, ``setup_radar_range``);
* else that range scaled by the setup ball's size over this ball's
  (``camera_size_ratio_to_the_setup_radar_range``): a relative size, which the
  lens's focal length cancels out of, anchored on the radar's distance. It is
  labelled, never passed off as a radar range.

Since 13 Oct (decision "a") the setup hands over the camera's coarse size estimate
as a PRIOR (``setup_range_kind="camera_prior"``, with its uncertainty): each look is
then a per-shot prior, scaled with its uncertainty, and the shot's own IWR capture
measures the distance.

A ball outside the box, or none, is the shot's label (``outside_box``,
``not_seen``) and carries no range; nothing is filled in. A shot takes the last
look before its trigger, and none older than ``MAX_LOOK_AGE_S``.

The look's search (``locate``) is pure numpy/scipy work. The kiosk runs it in a worker
process (``LookWorker``): on a kiosk thread it held the interpreter lock against the
camera's frame callback, and the frames the sensor took meanwhile were lost
(Harjot-outdoor-test-1, 24 Oct: 10 of 11 callback stalls began inside a look).
"""

from __future__ import annotations

import math
import multiprocessing
import pickle
import threading
from concurrent.futures import ProcessPoolExecutor, TimeoutError as FutureTimeout
from concurrent.futures.process import BrokenProcessPool
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

import numpy as np

from . import ground_patch, worker_scheduling

SCHEMA = "openflight.ball_at_address.v1"
SAME_SPOT_DIAMETERS = 0.5
MAX_LOOK_AGE_S = 5.0
IDENTITY_CONFIRM_LOOKS = 2
# the tuned detector's size limits (9-30 px at 640x400) are centred on the setup ball
_TUNED_GEOMETRIC_MIDDLE_PX = math.sqrt(9.0 * 30.0)


def _outline(points) -> tuple[tuple[float, float], ...] | None:
    if points is None:
        return None
    return tuple((float(x), float(y)) for x, y in points)


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


# A look's search: (image, box, outline, pixel scale) -> {"in_box": [x, y, d] | None,
# "outside": [x, y, d] | None}
Locate = Callable[..., dict]
LOOK_TIMEOUT_S = 10.0


def _find_ball(image: np.ndarray, roi: Sequence[int], pixel_scale: float | None):
    """The detector's resting ball inside ``roi`` (clipped to the frame), or None."""
    from openflight.camera.club_motion import detect_reference_ball  # noqa: PLC0415

    height, width = image.shape
    x0, y0, x1, y1 = roi
    roi = (max(0, x0), max(0, y0), min(width, x1), min(height, y1))
    if roi[0] >= roi[2] or roi[1] >= roi[3]:
        return None
    stack = np.repeat(np.asarray(image, dtype=np.uint8)[None], 3, axis=0)
    try:
        return detect_reference_ball(stack, roi=roi, pixel_scale=pixel_scale)
    except (RuntimeError, ValueError):
        return None


def _inside(x: float, y: float, box: Sequence[int], outline) -> bool:
    if outline is not None:
        return ground_patch.point_in_polygon(x, y, outline)
    x0, y0, x1, y1 = box
    return x0 <= x <= x1 and y0 <= y <= y1


def _same_ball(first, second) -> bool:
    """The existing box preview's spatial identity bounds, in this image's pixels."""
    size = max(first[2], second[2], 1.0)
    return math.hypot(first[0] - second[0], first[1] - second[1]) <= max(3.0, 0.2 * size) and abs(
        first[2] - second[2]
    ) <= max(2.0, 0.15 * size)


def _current_anchor(image, anchor):
    from .club_motion import ReferenceBall, measure_resting_ball  # noqa: PLC0415

    ball = ReferenceBall(*anchor, area_px=0)
    try:
        measured = measure_resting_ball(np.repeat(image[None], 3, axis=0), ball, anchor[2])
    except (RuntimeError, ValueError) as exc:
        raise LookUnavailable(f"the previous ball's current fit failed: {exc}") from exc
    if measured is None:
        return None
    current = [float(measured.x), float(measured.y), float(measured.diameter_px)]
    return current if _same_ball(anchor, current) else None


def locate(
    image: np.ndarray,
    box: Sequence[int],
    outline_px,
    pixel_scale: float | None,
    *,
    find: Callable | None = None,
    anchor=None,
) -> dict:
    """A look's search: the ball in the box, else one just outside it (a label).

    Plain values in and out, so a worker process can run it (``LookWorker``).
    """
    find = find or _find_ball
    x0, y0, x1, y1 = (int(value) for value in box)
    found = find(image, (x0, y0, x1, y1), pixel_scale)
    in_box = (
        [float(found.x), float(found.y), float(found.diameter_px)]
        if found is not None and _inside(found.x, found.y, box, outline_px)
        else None
    )
    if anchor is not None and (in_box is None or not _same_ball(anchor, in_box)):
        held = _current_anchor(image, anchor)
        if held is not None and _inside(held[0], held[1], box, outline_px):
            if in_box is not None:
                return {
                    "in_box": None,
                    "outside": None,
                    "ambiguous": True,
                    "candidates": [held, in_box],
                }
            in_box = held
    if in_box is not None:
        return {"in_box": in_box, "outside": None, "candidates": [in_box]}
    width, height = x1 - x0, y1 - y0
    wider = (x0 - width, y0 - height, x1 + width, y1 + height)
    elsewhere = find(image, wider, pixel_scale)
    if elsewhere is not None and not _inside(elsewhere.x, elsewhere.y, box, outline_px):
        outside = [float(elsewhere.x), float(elsewhere.y), float(elsewhere.diameter_px)]
        return {"in_box": None, "outside": outside}
    return {"in_box": None, "outside": None}


class LookUnavailable(RuntimeError):
    """The look's worker could not answer: this look is skipped, the next starts a worker."""


class LookWorker:
    """Runs each look's search in one spawn worker process, off the kiosk's interpreter lock.

    Started on the first look. A worker that dies or hangs is dropped and its look
    skipped (``LookUnavailable``), never run on a kiosk thread instead.
    """

    def __init__(self, timeout_s: float = LOOK_TIMEOUT_S):
        self.timeout_s = float(timeout_s)
        self._pool: ProcessPoolExecutor | None = None
        self._lock = threading.Lock()

    def _worker(self) -> ProcessPoolExecutor:
        with self._lock:
            if self._pool is None:
                # spawn, not fork: the kiosk has camera and web threads a fork would copy
                self._pool = ProcessPoolExecutor(
                    max_workers=1,
                    mp_context=multiprocessing.get_context("spawn"),
                    initializer=worker_scheduling.initialize_analysis_worker,
                )
            return self._pool

    def locate(self, image, box, outline_px, pixel_scale, *, anchor=None) -> dict:
        """``locate`` in the worker process."""
        try:
            future = self._worker().submit(
                locate, image, box, outline_px, pixel_scale, anchor=anchor
            )
            return future.result(timeout=self.timeout_s)
        except (BrokenProcessPool, FutureTimeout, OSError, pickle.PicklingError) as exc:
            self.close()
            raise LookUnavailable(f"the look's worker failed: {type(exc).__name__}: {exc}") from exc

    def close(self) -> None:
        """Stop the worker; the next look starts another."""
        with self._lock:
            pool, self._pool = self._pool, None
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)


class BallAtAddress:
    """The kiosk's resting-ball looks, one per frame it is given."""

    def __init__(
        self,
        *,
        box_px: Sequence[int],
        setup_ball: Mapping[str, float] | None,
        setup_range_m: float | None,
        lens: Mapping[str, Any] | None = None,
        setup_range_uncertainty_m: float | None = None,
        setup_range_kind: str = "radar_range",
        outline_px: Sequence[Sequence[float]] | None = None,
    ):
        self.box = tuple(int(value) for value in box_px)
        # None (an older box) is the box_px rectangle
        self.outline_px = _outline(outline_px)
        self.setup_ball = dict(setup_ball) if setup_ball else None
        self.setup_range_m = float(setup_range_m) if setup_range_m else None
        self.setup_range_uncertainty_m = (
            float(setup_range_uncertainty_m) if setup_range_uncertainty_m else None
        )
        # "radar_range" or "camera_prior"
        self.setup_range_kind = str(setup_range_kind)
        self.lens = dict(lens) if lens else None
        self._lock = threading.Lock()
        self._looks: list[dict] = []
        self._generation = 0
        self._candidate = None
        self._support = 0
        self._reacquiring = False
        self._last_frame_id = None

    def reconfigure(  # pylint: disable=too-many-arguments
        self, box_px, setup_ball, setup_range_m, range_prior, *, outline_px=None
    ) -> None:
        """Take a new box, setup ball and range; looks judged against the old ones are dropped.

        A ``range_prior`` (``range_m``, ``uncertainty_m``) is used only without a radar range.
        """
        prior_only = setup_range_m is None and range_prior is not None
        with self._lock:
            self.box = tuple(int(value) for value in box_px)
            self.outline_px = _outline(outline_px)
            self.setup_ball = dict(setup_ball) if setup_ball else None
            self.setup_range_m = (
                float(range_prior.range_m)
                if prior_only
                else float(setup_range_m)
                if setup_range_m
                else None
            )
            self.setup_range_uncertainty_m = (
                float(range_prior.uncertainty_m) if prior_only else None
            )
            self.setup_range_kind = "camera_prior" if prior_only else "radar_range"
            self._looks = []
            self._candidate = None
            self._support = 0
            self._reacquiring = False
            self._last_frame_id = None
            self._generation += 1

    def _pixel_scale(self) -> float | None:
        if not self.setup_ball:
            return None
        return float(self.setup_ball["diameter_px"]) / _TUNED_GEOMETRIC_MIDDLE_PX

    def _find(self, image: np.ndarray, roi: tuple[int, int, int, int]):
        return _find_ball(image, roi, self._pixel_scale())

    def _in_box(self, x: float, y: float) -> bool:
        return _inside(x, y, self.box, self.outline_px)

    def _locate_here(self, image, box, outline_px, _pixel_scale, *, anchor=None) -> dict:
        """``locate`` in this process, through ``_find``."""
        return locate(
            image,
            box,
            outline_px,
            None,
            find=lambda img, roi, _: self._find(img, roi),
            anchor=anchor,
        )

    def _range(self, x: float, y: float, diameter: float) -> tuple[float | None, str | None]:
        if self.setup_range_m is None or not self.setup_ball:
            return None, None
        setup_d = float(self.setup_ball["diameter_px"])
        moved = math.hypot(x - float(self.setup_ball["x"]), y - float(self.setup_ball["y"]))
        kind = self.setup_range_kind
        if moved <= SAME_SPOT_DIAMETERS * setup_d:
            return self.setup_range_m, f"setup_{kind}"
        if diameter <= 0.0:
            return None, None
        return self.setup_range_m * setup_d / diameter, f"camera_size_ratio_to_the_setup_{kind}"

    def observe(
        self,
        image: np.ndarray,
        *,
        at: float,
        locate: Locate | None = None,
        frame_id: int | None = None,
        frame_metadata: Mapping | None = None,
    ) -> dict:
        """Look for the resting ball in this frame; keep and return what was found.

        ``locate`` runs the search (the kiosk's ``LookWorker.locate``); by default it runs
        here. A failed worker records unavailable evidence before raising.
        """
        with self._lock:
            generation = self._generation
            if (
                frame_id is not None
                and self._last_frame_id is not None
                and frame_id <= self._last_frame_id
                and self._looks
            ):
                return deepcopy(self._looks[-1])
            box, outline, scale = self.box, self.outline_px, self._pixel_scale()
            anchor = self._candidate
        search = locate or self._locate_here
        failure = None
        try:
            found = search(image, box, outline, scale, **({"anchor": anchor} if anchor else {}))
        except LookUnavailable as error:
            failure = error
            found = {"in_box": None, "outside": None}
        with self._lock:
            if generation != self._generation:
                return {
                    "state": "unknown",
                    "reason": "address configuration changed during the look",
                }
            if self._looks and float(at) <= self._looks[-1]["observed_at"]:
                return deepcopy(self._looks[-1])
            look = self._keep_look(found, at, frame_id, frame_metadata, failure)
        if failure is not None:
            raise failure
        return deepcopy(look)

    def _keep_look(self, found, at, frame_id, frame_metadata, failure) -> dict:
        state, reason = "not_seen", "no ball in the box"
        center = diameter = range_m = source = None
        if failure is not None:
            state, reason = "unavailable", str(failure)
        elif found.get("ambiguous"):
            state, reason = "ambiguous", "more than one currently supported ball candidate"
            self._support = 0
            self._reacquiring = self._candidate is not None
        elif found["in_box"] is not None:
            x, y, size = (float(value) for value in found["in_box"])
            state, reason = "in_box", None
            candidate = (x, y, size)
            same = self._candidate is not None and _same_ball(self._candidate, candidate)
            if not same:
                self._reacquiring = self._candidate is not None
            self._support = self._support + 1 if same else 1
            self._candidate = candidate
            if self._support >= IDENTITY_CONFIRM_LOOKS:
                self._reacquiring = False
            if self._reacquiring:
                state, reason = "acquiring", "checking the changed ball position"
            else:
                center = [round(x, 1), round(y, 1)]
                diameter = round(size, 2)
                range_m, source = self._range(x, y, size)
        elif found["outside"] is not None:
            x, y, size = (float(value) for value in found["outside"])
            state = "outside_box"
            reason = "the ball is outside the box: its shot is labelled, not blocked"
            center = [round(x, 1), round(y, 1)]
            diameter = round(size, 2)
        if state in {"not_seen", "outside_box", "unavailable"}:
            self._support = 0
            self._reacquiring = self._candidate is not None
        look = {
            "schema": SCHEMA,
            "state": state,
            "observation_state": "observed" if state == "in_box" else state,
            "identity_support": self._support,
            "candidate_px": deepcopy(found.get("in_box")),
            "candidates": deepcopy(found.get("candidates", [])),
            "range_prior_rejection": None if state == "in_box" else reason,
            "reason": reason,
            "center_px": center,
            "diameter_px": diameter,
            "range_m": round(range_m, 4) if range_m is not None else None,
            "range_source": source,
            "range_uncertainty_m": (
                round(self.setup_range_uncertainty_m * range_m / self.setup_range_m, 4)
                if range_m is not None and self.setup_range_uncertainty_m and self.setup_range_m
                else None
            ),
            "observed_at": float(at),
            "frame_id": frame_id,
            "frame_metadata": deepcopy(frame_metadata),
            "updated_at": _now_utc(),
            "box_px": list(self.box),
            "outline_px": [list(point) for point in self.outline_px] if self.outline_px else None,
            "lens": self.lens,
        }
        self._last_frame_id = frame_id
        self._looks = [*self._looks[-9:], look]
        return look

    def latest(self) -> dict | None:
        """The last observation as recorded; callers qualify it against their clock."""
        with self._lock:
            return deepcopy(self._looks[-1]) if self._looks else None

    def for_shot(self, trigger_at: float) -> dict | None:
        """The last look before this shot's trigger, no older than ``MAX_LOOK_AGE_S``."""
        with self._lock:
            looks = [look for look in self._looks if look["observed_at"] <= float(trigger_at)]
        if not looks:
            return None
        look = looks[-1]
        if float(trigger_at) - look["observed_at"] > MAX_LOOK_AGE_S:
            return None
        return deepcopy(look)

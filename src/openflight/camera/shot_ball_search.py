"""Free play's per-shot resting ball: the address preview's search on the shot's own frames.

A shot's claimed clip has its resting frames (the ball before the club arrives). The
address preview's broad rival search runs once on them in its own worker process, and
its ball is kept only inside the setup's ball band: the placement record's ball-centre
band (``ball_outline_px``) moved vertically so it passes through the ball confirmed at
setup (``ball_at_confirm``), widened by max(0.5 x 45 px, 10 px) at 1280x800. On the
saved swing-exposure shots this kept every real ball and refused every object the
search proposed when no ball was in the box (research/free-play-ball-shot-search-
2026-10-30, protocol amendment_3). 640x400 is the 1280x800 view binned 2x2.
"""

from __future__ import annotations

import logging
import multiprocessing
import threading
import time
from concurrent.futures import ProcessPoolExecutor, TimeoutError as FutureTimeout

import numpy as np

from openflight.camera.address_preview import native, preview, search
from openflight.camera.address_preview.search import close_fit_pool
from openflight.camera.ground_patch import PatchSearch

logger = logging.getLogger(__name__)

EXPECTED_DIAMETER_1280_PX = 45.0
MIN_BAND_MARGIN_PX = 10.0
CLOSE_TIMEOUT_S = 30.0
INPUTS_RETRY_S = 5.0
WORKER_START_TIMEOUT_S = preview.POOL_START_TIMEOUT_S


def mode_factor(record, frame_shape) -> float:
    """1.0 for the record's own 1280x800 frames, 0.5 for the binned 640x400 ones."""
    width, height = (int(value) for value in record["frame_size_px"])
    shape = (int(frame_shape[-2]), int(frame_shape[-1]))
    if shape == (height, width):
        return 1.0
    if shape == (height // 2, width // 2):
        return 0.5
    raise ValueError(f"the placement record is for {width}x{height}, not {shape[1]}x{shape[0]}")


def _crossings(polygon, x) -> list[float]:
    """The rows where the polygon's edges cross column ``x`` (none left or right of it)."""
    rows = []
    for (x0, y0), (x1, y1) in zip(polygon, polygon[1:] + polygon[:1]):
        if x0 != x1 and min(x0, x1) <= x <= max(x0, x1):
            rows.append(y0 + (x - x0) * (y1 - y0) / (x1 - x0))
    return rows


class BallBand:
    """Where a resting ball's centre can be: the record's band through the setup's ball."""

    def __init__(self, polygon, dy: float, margin: float):
        self.polygon = polygon
        self.dy = dy
        self.margin = margin

    @classmethod
    def from_record(cls, record, factor: float, *, shifted: bool = True) -> "BallBand":
        """The band in the mode ``factor`` times 1280x800; unshifted is the record's geometry."""
        for key in ("ball_outline_px", "ball_at_confirm"):
            if not record.get(key):
                raise ValueError(f"the placement record has no {key}")

        def scale(value):
            return (float(value) + 0.5) * factor - 0.5

        polygon = [(scale(x), scale(y)) for x, y in record["ball_outline_px"]]
        x, y = (scale(value) for value in record["ball_at_confirm"]["center_px"])
        rows = _crossings(polygon, x)
        if not rows:
            raise ValueError("the confirmed ball is outside the record's ball band columns")
        dy = y - (min(rows) + max(rows)) / 2.0 if shifted else 0.0
        margin = max(0.5 * EXPECTED_DIAMETER_1280_PX * factor, MIN_BAND_MARGIN_PX)
        return cls(polygon, dy, margin)

    def contains(self, x: float, y: float) -> bool:
        """Whether a centre at (x, y) lies in the moved band, margin included."""
        rows = _crossings(self.polygon, x)
        if not rows:
            return False
        return min(rows) + self.dy - self.margin <= y <= max(rows) + self.dy + self.margin


# the camera model and box the worker searches with: set once per session (load_inputs)
_WORKER_INPUTS: dict = {}


def load_inputs(camera, patch) -> bool:
    """Give the worker the session's camera model and box, so a shot sends only its frames."""
    _WORKER_INPUTS.update(camera=camera, patch=patch)
    return True


def run_search(frames, controls) -> dict:
    """One broad search in the worker; a shot is searched on its own pixels only."""
    if not _WORKER_INPUTS:
        return {"status": "unavailable", "reason": "the worker has no camera model or box"}
    search._REUSE_CACHE.clear()  # pylint: disable=protected-access
    started = time.perf_counter()
    result = search.search(frames, _WORKER_INPUTS["camera"], _WORKER_INPUTS["patch"], controls)
    return {
        "status": result.get("status"),
        "reason": result.get("reason"),
        "selected": result.get("selected"),
        "worker_ms": (time.perf_counter() - started) * 1000.0,
    }


def _broad_worker_pool(cancel_event) -> ProcessPoolExecutor:
    return ProcessPoolExecutor(
        max_workers=1,
        mp_context=multiprocessing.get_context("spawn"),
        initializer=preview._initialize_broad_worker,  # pylint: disable=protected-access
        initargs=(cancel_event,),
    )


def _answer(status: str, reason: str | None, **extra) -> dict:
    return {"status": status, "reason": reason, **extra}


class ShotBallSearch:  # pylint: disable=too-many-instance-attributes
    """The search worker, warmed once; ``find`` is the kiosk's ``shot_ball_finder``.

    The placement record and the camera model are read off the shot path and given to
    the worker once; until both are usable they are read again every ``retry_s``.
    """

    def __init__(  # pylint: disable=too-many-arguments
        self,
        record_loader,
        camera_loader,
        *,
        executor_factory=_broad_worker_pool,
        availability=native.availability,
        retry_s: float = INPUTS_RETRY_S,
    ):
        self._record_loader = record_loader
        self._camera_loader = camera_loader
        self._executor_factory = executor_factory
        self._availability = availability
        self._retry_s = retry_s
        self._lock = threading.Lock()
        self._cancel = multiprocessing.get_context("spawn").Event()
        self._started = threading.Event()
        self._ready = threading.Event()
        self._stopping = threading.Event()
        self._executor = None
        self._warm = None
        self._pending = None
        self._prepared = None
        self._not_ready = ("warming", "the search's camera model and box are not read yet")
        self._unavailable: str | None = None
        self._closed = False

    def start(self) -> None:
        """Build the residual, start the worker and read its inputs, off the caller's thread."""
        threading.Thread(target=self._start, name="shot-ball-search-start", daemon=True).start()

    def _start(self) -> None:
        try:
            available = self._availability()
            if not available["available"]:
                self._disable(f"the search's native residual is unavailable: {available['reason']}")
                return
            with self._lock:
                if self._closed:
                    return
                self._executor = self._executor_factory(self._cancel)
                self._warm = self._executor.submit(int)
            while not self._prepare():
                self._started.set()
                if self._stopping.wait(self._retry_s):
                    return
        except Exception as error:  # pylint: disable=broad-exception-caught
            self._disable(f"the search worker did not start: {type(error).__name__}: {error}")
        finally:
            self._started.set()

    def _not_yet(self, status: str, reason: str) -> bool:
        with self._lock:
            self._not_ready = (status, reason)
        return False

    def _prepare(self) -> bool:
        """Read the record and the camera and hand them to the worker; False to try again."""
        try:
            record = self._record_loader()
        except Exception as error:  # pylint: disable=broad-exception-caught
            return self._not_yet("no_band", str(error) or type(error).__name__)
        if record is None:
            return self._not_yet("no_band", "this tester has no placement record")
        try:
            camera = self._camera_loader()
        except Exception as error:  # pylint: disable=broad-exception-caught
            return self._not_yet("no_camera", f"{type(error).__name__}: {error}")
        if camera is None:
            return self._not_yet("no_camera", "no camera model for this mode")
        try:
            shape = (int(camera.image_height_px), int(camera.image_width_px))
            factor = mode_factor(record, shape)
            band = BallBand.from_record(record, factor)
            patch = PatchSearch.from_record(record, factor=factor)
        except (KeyError, TypeError, ValueError) as error:
            return self._not_yet("no_band", str(error))
        self._executor.submit(load_inputs, camera, patch).result(timeout=WORKER_START_TIMEOUT_S)
        with self._lock:
            self._prepared = (shape, band)
        self._ready.set()
        return True

    def wait_started(self, timeout_s: float) -> bool:
        """Whether starting (residual, worker, first read of the inputs) ended in ``timeout_s``."""
        return self._started.wait(timeout_s)

    def wait_ready(self, timeout_s: float) -> bool:
        """Whether the worker had its camera model and box within ``timeout_s``."""
        return self._ready.wait(timeout_s)

    def _disable(self, reason: str) -> None:
        with self._lock:
            first = self._unavailable is None
            self._unavailable = self._unavailable or reason
        if first:
            logger.warning("[BALL_AT_ADDRESS] Per-shot ball search off: %s", reason)

    def find(  # pylint: disable=too-many-return-statements
        self, frames, controls, *, timeout_s: float
    ) -> dict:
        """The shot's ball in ``frames`` if the search selects one inside the band, else why not.

        Only the search runs here, and the whole call stays within ``timeout_s``.
        """
        entered = time.monotonic()
        with self._lock:
            if self._unavailable is not None or self._closed:
                return _answer("unavailable", self._unavailable or "the search is closed")
            warm, executor, pending = self._warm, self._executor, self._pending
            prepared, not_ready = self._prepared, self._not_ready
        if warm is None or not warm.done():
            return _answer("warming", "the search worker is still starting")
        if warm.exception() is not None:
            self._disable(f"the search worker did not start: {warm.exception()}")
            return _answer("unavailable", self._unavailable)
        if prepared is None:
            return _answer(*not_ready)
        shape, band = prepared
        height, width = np.shape(frames)[-2:]
        if (height, width) != shape:
            return _answer(
                "no_band",
                f"the frames are {width}x{height}; the search is set up for {shape[1]}x{shape[0]}",
            )
        if pending is not None and not pending.done():
            return _answer("busy", "the previous shot's search is still stopping")
        self._cancel.clear()
        try:
            future = executor.submit(run_search, np.ascontiguousarray(frames), list(controls))
            with self._lock:
                self._pending = future
            result = future.result(timeout=max(0.0, timeout_s - (time.monotonic() - entered)))
        except FutureTimeout:
            self._cancel.set()
            return _answer("timeout", f"the search took longer than {timeout_s:.1f} s")
        except Exception as error:  # pylint: disable=broad-exception-caught
            self._disable(f"the search worker failed: {type(error).__name__}: {error}")
            return _answer("worker_failed", self._unavailable)
        return self._judge(result, band)

    @staticmethod
    def _judge(result: dict, band: BallBand) -> dict:
        status, selected = result.get("status"), result.get("selected")
        timing = {"search_status": status, "worker_ms": result.get("worker_ms")}
        if status != "proposed_unqualified" or not selected:
            return _answer(status or "unavailable", result.get("reason"), **timing)
        x, y = float(selected["x_px"]), float(selected["y_px"])
        ball = {"x": x, "y": y, "diameter_px": 2.0 * float(selected["radius_px"])}
        if not band.contains(x, y):
            return _answer(
                "outside_band",
                f"the search's ball at ({x:.0f}, {y:.0f}) px is outside the setup's ball band",
                candidate=ball,
                **timing,
            )
        return _answer("selected", None, ball=ball, **timing)

    def close(self) -> None:
        """Stop any search and end the worker and its fit processes; waits for them."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            executor = self._executor
        self._stopping.set()
        if executor is None:
            return
        self._cancel.set()
        try:
            executor.submit(close_fit_pool).result(timeout=CLOSE_TIMEOUT_S)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.warning("[BALL_AT_ADDRESS] The search's fit workers did not stop cleanly")
        executor.shutdown(wait=True, cancel_futures=True)

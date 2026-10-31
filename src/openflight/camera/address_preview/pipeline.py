"""Feed the address preview every new five-frame window from the live camera.

One analysis thread observes windows; the newest complete window wins and
older pending ones are skipped. Offering a frame does constant work and never
waits for an analysis. Reads re-check the committed ball against the current
window (``display.CurrentRead``), so they never wait for a fit either.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable

import numpy as np

from .display import CurrentRead, unavailable as unavailable_read

logger = logging.getLogger(__name__)

WINDOW_FRAMES = 5
CLOSE_TIMEOUT_S = 90.0


def _control(value, kind):
    return kind(value) if value is not None else None


def start_preview(model_path):
    """The preview with its worker processes, or an error saying why it cannot run."""
    # the fitter needs OpenCV and the compiled residual: imported only to start
    from . import native  # pylint: disable=import-outside-toplevel
    from .preview import Preview  # pylint: disable=import-outside-toplevel

    available = native.availability()
    if not available["available"]:
        raise native.NativeUnavailable(available["reason"])
    return Preview(model_path=str(model_path), bridge_exposure=True)


class AddressPreviewPipeline:  # pylint: disable=too-many-instance-attributes
    """The preview over one camera mode and image box, fed frame by frame."""

    def __init__(
        self,
        camera,
        patch,
        *,
        model_path,
        clock: Callable[[], float] = time.monotonic,
        preview_factory: Callable[[object], object] = start_preview,
    ):
        self.camera = camera
        self.patch = patch
        self.display = CurrentRead(clock)
        self.skipped = 0
        self.observed = 0
        self.failure: str | None = None
        self._factory = preview_factory
        self._model_path = model_path
        self._last_search: dict | None = None
        self._ready = False
        self._closed = False
        self._history: deque = deque(maxlen=WINDOW_FRAMES)
        self._pending: tuple | None = None
        self._condition = threading.Condition()
        self._thread = threading.Thread(target=self._run, daemon=True, name="address-preview")
        self._thread.start()

    def offer(self, pixels: np.ndarray, metadata, sequence: int, observed_at: float) -> None:
        """Queue the newest window; an unanalysed older window is dropped."""
        controls = {
            "ExposureTime": _control(metadata.get("ExposureTime"), int),
            "AnalogueGain": _control(metadata.get("AnalogueGain"), float),
        }
        with self._condition:
            if self._closed:
                return
            self._history.append((pixels, controls, int(sequence), float(observed_at)))
            if len(self._history) >= WINDOW_FRAMES:
                if self._pending is not None:
                    self.skipped += 1
                self._pending = tuple(self._history)
                self._condition.notify()

    def read(self) -> dict:
        """The committed ball as the current window shows it, else why there is none."""
        with self._condition:
            records = tuple(self._history)
        if not records:
            return unavailable_read("no committed measurement")
        return self.display.read(records, self.camera, self.patch)

    def status(self) -> dict:
        """Whether the preview runs, and its newest finished broad search."""
        with self._condition:
            return {
                "ready": self._ready,
                "failure": self.failure,
                "closed": self._closed,
                "last_search": dict(self._last_search) if self._last_search else None,
                "skipped_windows": self.skipped,
                "observed_windows": self.observed,
            }

    def _fail(self, reason: str) -> None:
        with self._condition:
            if self.failure is None:
                self.failure = reason
        self.display.close()

    def _run(self) -> None:
        try:
            preview = self._factory(self._model_path)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self._fail(f"{type(exc).__name__}: {exc}")
            return
        try:
            with self._condition:
                if self._closed:
                    return
                self._ready = True
            self._analyse(preview)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self._fail(f"{type(exc).__name__}: {exc}")
        finally:
            preview.close()

    def _analyse(self, preview) -> None:
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._pending is not None or self._closed)
                if self._closed:
                    return
                records, self._pending = self._pending, None
            frames = np.stack([r[0] for r in records])
            controls = [(r[1]["ExposureTime"], r[1]["AnalogueGain"]) for r in records]
            state = preview.observe(
                frames, self.camera, self.patch, controls, records[-1][2], at=records[-1][3]
            )
            self.display.publish(
                state, records, self.camera, self.patch, preview.watch, preview.bounds
            )
            last = preview.last_search()
            with self._condition:
                self.observed += 1
                self._last_search = last
            if state["status"] == "worker_error":
                self._fail("the preview's search worker failed")

    def close(self) -> None:
        """Stop analysing and end every worker process; waits for them."""
        with self._condition:
            self._closed = True
            self._pending = None
            self._condition.notify_all()
        self.display.close()
        self._thread.join(timeout=CLOSE_TIMEOUT_S)
        if self._thread.is_alive():
            logger.warning(
                "[BOX_BALL] the address preview did not stop within %.0f s", CLOSE_TIMEOUT_S
            )

"""Per-shot re-location of the resting ball inside the setup's box, and its range.

Within ``SAME_SPOT_DIAMETERS`` of the setup ball the setup's range is used; elsewhere it is
scaled by the setup ball's size over this ball's (focal length cancels), labelled, never a
radar range. A ball outside the box or unseen carries no range. A shot takes the last look
before its trigger, none older than ``MAX_LOOK_AGE_S``.
"""

from __future__ import annotations

import math
import threading
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

import numpy as np

from . import ground_patch

SCHEMA = "openflight.ball_at_address.v1"
SAME_SPOT_DIAMETERS = 0.5
MAX_LOOK_AGE_S = 30.0
# the tuned detector's size limits (9-30 px at 640x400) are centred on the setup ball
_TUNED_GEOMETRIC_MIDDLE_PX = math.sqrt(9.0 * 30.0)


def _outline(points) -> tuple[tuple[float, float], ...] | None:
    if points is None:
        return None
    return tuple((float(x), float(y)) for x, y in points)


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


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
            self._generation += 1

    def _pixel_scale(self) -> float | None:
        if not self.setup_ball:
            return None
        return float(self.setup_ball["diameter_px"]) / _TUNED_GEOMETRIC_MIDDLE_PX

    def _find(self, image: np.ndarray, roi: tuple[int, int, int, int]):
        from openflight.camera.club_motion import detect_reference_ball  # noqa: PLC0415

        height, width = image.shape
        x0, y0, x1, y1 = roi
        roi = (max(0, x0), max(0, y0), min(width, x1), min(height, y1))
        if roi[0] >= roi[2] or roi[1] >= roi[3]:
            return None
        stack = np.repeat(np.asarray(image, dtype=np.uint8)[None], 3, axis=0)
        try:
            return detect_reference_ball(stack, roi=roi, pixel_scale=self._pixel_scale())
        except (RuntimeError, ValueError):
            return None

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

    def _in_box(self, x: float, y: float) -> bool:
        if self.outline_px is not None:
            return ground_patch.point_in_polygon(x, y, self.outline_px)
        x0, y0, x1, y1 = self.box
        return x0 <= x <= x1 and y0 <= y <= y1

    def observe(self, image: np.ndarray, *, at: float) -> dict:
        """Look for the resting ball in this frame; keep and return what was found."""
        with self._lock:
            generation = self._generation
        x0, y0, x1, y1 = self.box
        found = self._find(image, self.box)
        state, reason = "not_seen", "no ball in the box"
        center = diameter = range_m = source = None
        if found is not None and self._in_box(found.x, found.y):
            state, reason = "in_box", None
            center = [round(float(found.x), 1), round(float(found.y), 1)]
            diameter = round(float(found.diameter_px), 2)
            range_m, source = self._range(float(found.x), float(found.y), float(found.diameter_px))
        else:
            width, height = x1 - x0, y1 - y0
            wider = (x0 - width, y0 - height, x1 + width, y1 + height)
            elsewhere = self._find(image, wider)
            if elsewhere is not None and not self._in_box(elsewhere.x, elsewhere.y):
                state = "outside_box"
                reason = "the ball is outside the box: its shot is labelled, not blocked"
                center = [round(float(elsewhere.x), 1), round(float(elsewhere.y), 1)]
                diameter = round(float(elsewhere.diameter_px), 2)
        look = {
            "schema": SCHEMA,
            "state": state,
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
            "updated_at": _now_utc(),
            "box_px": list(self.box),
            "outline_px": [list(point) for point in self.outline_px] if self.outline_px else None,
            "lens": self.lens,
        }
        with self._lock:
            # a look begun before a reconfigure was judged against the old box
            if generation == self._generation:
                self._looks = [*self._looks[-9:], look]
        return dict(look)

    def latest(self) -> dict | None:
        with self._lock:
            return dict(self._looks[-1]) if self._looks else None

    def for_shot(self, trigger_at: float) -> dict | None:
        """The last look before this shot's trigger, no older than ``MAX_LOOK_AGE_S``."""
        with self._lock:
            looks = [look for look in self._looks if look["observed_at"] <= float(trigger_at)]
        if not looks:
            return None
        look = looks[-1]
        if float(trigger_at) - look["observed_at"] > MAX_LOOK_AGE_S:
            return None
        return dict(look)

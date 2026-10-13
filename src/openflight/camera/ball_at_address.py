"""Per-shot re-location of the resting ball inside the fixed box (13 Oct 2026).

The setup measures the ball once; a tester then tees the next ball wherever it
lands in the box. Before each swing the kiosk looks at its own latest camera
frame, finds the resting ball inside the box (``--camera-ball-box``), and gives
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
"""

from __future__ import annotations

import math
import threading
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

import numpy as np

SCHEMA = "openflight.ball_at_address.v1"
SAME_SPOT_DIAMETERS = 0.5
MAX_LOOK_AGE_S = 30.0
# the tuned detector's size limits (9-30 px at 640x400) are centred on the setup ball
_TUNED_GEOMETRIC_MIDDLE_PX = math.sqrt(9.0 * 30.0)


def parse_ball_box(value: str) -> tuple[int, int, int, int]:
    """``x0,y0,x1,y1`` in the kiosk mode's pixels."""
    try:
        numbers = [int(round(float(part))) for part in str(value).split(",")]
    except ValueError as exc:
        raise ValueError("the ball box must be four numbers x0,y0,x1,y1") from exc
    if len(numbers) != 4:
        raise ValueError("the ball box must be four numbers x0,y0,x1,y1")
    x0, y0, x1, y1 = numbers
    if not (0 <= x0 < x1 and 0 <= y0 < y1):
        raise ValueError("the ball box must have x0 < x1 and y0 < y1, inside the picture")
    return x0, y0, x1, y1


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
    ):
        self.box = tuple(int(value) for value in box_px)
        self.setup_ball = dict(setup_ball) if setup_ball else None
        self.setup_range_m = float(setup_range_m) if setup_range_m else None
        self.setup_range_uncertainty_m = (
            float(setup_range_uncertainty_m) if setup_range_uncertainty_m else None
        )
        # what the setup's range is: "radar_range" before 13 Oct, "camera_prior" since
        self.setup_range_kind = str(setup_range_kind)
        self.lens = dict(lens) if lens else None
        self._lock = threading.Lock()
        self._looks: list[dict] = []

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

    def observe(self, image: np.ndarray, *, at: float) -> dict:
        """Look for the resting ball in this frame; keep and return what was found."""
        x0, y0, x1, y1 = self.box
        found = self._find(image, self.box)
        state, reason = "not_seen", "no ball in the box"
        center = diameter = range_m = source = None
        if found is not None and x0 <= found.x <= x1 and y0 <= found.y <= y1:
            state, reason = "in_box", None
            center = [round(float(found.x), 1), round(float(found.y), 1)]
            diameter = round(float(found.diameter_px), 2)
            range_m, source = self._range(float(found.x), float(found.y), float(found.diameter_px))
        else:
            width, height = x1 - x0, y1 - y0
            wider = (x0 - width, y0 - height, x1 + width, y1 + height)
            elsewhere = self._find(image, wider)
            if elsewhere is not None and not (x0 <= elsewhere.x <= x1 and y0 <= elsewhere.y <= y1):
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
            # the setup prior's relative uncertainty, carried to this look's range
            "range_uncertainty_m": (
                round(self.setup_range_uncertainty_m * range_m / self.setup_range_m, 4)
                if range_m is not None and self.setup_range_uncertainty_m and self.setup_range_m
                else None
            ),
            "observed_at": float(at),
            "updated_at": _now_utc(),
            "box_px": list(self.box),
            "lens": self.lens,
        }
        with self._lock:
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

"""The resting ball, searched for once and then watched.

A ball on the ground does not move until it is hit or picked up, so the search,
which costs most of a second, need not run on every look. Each look compares
the picture around the last answer with the one it came from, and searches
again only when that changed beyond the noise: the ball moved, was placed or
taken away, the light changed there, or the size and row it has to have did.
A change of the whole frame's brightness, as a light flickers, is not one.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

import numpy as np

from openflight.camera.club_motion import (
    DISK_SEARCH_WIDTH_PX,
    MEDIAN_NOISE_FACTOR,
    ReferenceBall,
    _bin,
    detect_reference_ball,
)

# How far the picture may move, over what its noise allows, before it counts
# as changed: a placed or moved ball shifts it by tens of DN, noise by one or two.
CHANGE_NOISE_MULTIPLE = 6.0
CHANGE_FLOOR_DN = 3.0
# The fit holds the size to 1%: a size hint that moves further asks a new question.
SIZE_CHANGE_FRACTION = 0.01
# Around a found ball, the picture compared reaches this many radii out.
WATCH_RADII = 3.0

Search = Callable[..., ReferenceBall]


@dataclass(frozen=True)
class _Answer:
    shape: tuple[int, int]
    factor: int
    box: tuple[int, int, int, int]  # coarse x0, y0, x1, y1 watched
    watched: np.ndarray  # the binned median picture inside the box
    diameter: float | None
    row: tuple[float, float] | None
    ball: ReferenceBall | None
    reason: str | None


class RestingBallTracker:
    """``detect_reference_ball`` for a stream of looks at a still scene."""

    def __init__(self, search: Search = detect_reference_ball):
        self._search = search
        self._answer: _Answer | None = None
        self.looks = 0
        self.searches = 0

    def look(
        self,
        frames: np.ndarray,
        *,
        expected_diameter_px: float | None = None,
        expected_row_px: tuple[float, float] | None = None,
    ) -> ReferenceBall:
        """The ball in these frames; raises ``ValueError`` when there is none, as the search does."""
        self.looks += 1
        frames = frames[: min(20, len(frames))]
        shape = frames.shape[1:]
        factor = max(1, round(shape[1] / DISK_SEARCH_WIDTH_PX))
        answer = self._answer
        if (
            answer is not None
            and answer.shape == shape
            and self._still_asks(answer, expected_diameter_px, expected_row_px)
            and not self._changed(answer, frames)
        ):
            if answer.ball is None:
                raise ValueError(answer.reason)
            return answer.ball
        self.searches += 1
        try:
            ball, reason = (
                self._search(
                    frames,
                    expected_diameter_px=expected_diameter_px,
                    expected_row_px=expected_row_px,
                ),
                None,
            )
        except ValueError as exc:
            ball, reason = None, str(exc)
        box = self._watched((shape[0] // factor, shape[1] // factor), factor, ball, expected_row_px)
        self._answer = _Answer(
            shape=shape,
            factor=factor,
            box=box,
            watched=_picture(frames, box, factor),
            diameter=expected_diameter_px,
            row=expected_row_px,
            ball=ball,
            reason=reason,
        )
        if ball is None:
            raise ValueError(reason)
        return ball

    def forget(self) -> None:
        """Search on the next look, whatever it shows."""
        self._answer = None

    @staticmethod
    def _still_asks(
        answer: _Answer, diameter: float | None, row: tuple[float, float] | None
    ) -> bool:
        """Whether the size and row hints still ask the question the answer was to."""
        if (diameter is None) != (answer.diameter is None) or (row is None) != (answer.row is None):
            return False
        if diameter is not None and abs(diameter / answer.diameter - 1.0) > SIZE_CHANGE_FRACTION:
            return False
        if row is None:
            return True
        if answer.ball is not None:
            # the tilt reading wanders a little; the answer stands while it
            # still lies in the rows the ball can rest in
            return abs(answer.ball.y - row[0]) <= row[1]
        return abs(row[0] - answer.row[0]) <= 0.25 * answer.row[1] and math.isclose(
            row[1], answer.row[1], rel_tol=0.25
        )

    @staticmethod
    def _watched(
        shape: tuple[int, int],
        factor: int,
        ball: ReferenceBall | None,
        row: tuple[float, float] | None,
    ) -> tuple[int, int, int, int]:
        height, width = shape
        if ball is not None:
            reach = WATCH_RADII * ball.diameter_px / 2.0
            return (
                max(0, int((ball.x - reach) / factor)),
                max(0, int((ball.y - reach) / factor)),
                min(width, int(math.ceil((ball.x + reach) / factor)) + 1),
                min(height, int(math.ceil((ball.y + reach) / factor)) + 1),
            )
        if row is not None:
            return (
                0,
                max(0, int((row[0] - row[1]) / factor)),
                width,
                min(height, int(math.ceil((row[0] + row[1]) / factor)) + 1),
            )
        return 0, 0, width, height

    @staticmethod
    def _changed(answer: _Answer, frames: np.ndarray) -> bool:
        x0, y0, x1, y1 = answer.box
        factor = answer.factor
        delta = _picture(frames, answer.box, factor) - answer.watched
        if not delta.size:
            return True
        # the whole picture brighter or dimmer is not a change
        delta = delta - float(np.median(delta))
        region = frames[:, y0 * factor : y1 * factor, x0 * factor : x1 * factor].astype(np.float32)
        noise = float(np.median(np.std(region, axis=0))) if len(frames) > 1 else 0.0
        coarse_noise = MEDIAN_NOISE_FACTOR * noise / math.sqrt(len(frames)) / factor
        limit = max(CHANGE_FLOOR_DN, CHANGE_NOISE_MULTIPLE * coarse_noise)
        return float(np.abs(delta).max()) > limit


def _picture(frames: np.ndarray, box: tuple[int, int, int, int], factor: int) -> np.ndarray:
    """The binned median picture inside a box: only what is watched is computed."""
    x0, y0, x1, y1 = box
    region = frames[:, y0 * factor : y1 * factor, x0 * factor : x1 * factor]
    return _bin(np.median(region, axis=0).astype(np.float32), factor)

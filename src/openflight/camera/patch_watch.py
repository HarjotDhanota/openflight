"""Bound reuse of a fitted preview ball by checking fresh pixels over its patch."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .club_motion import DISK_SEARCH_WIDTH_PX

# Change thresholds from the earlier resting-ball watcher; only trigger refitting.
CHANGE_FLOOR_DN = 3.0
CHANGE_NOISE_MULTIPLE = 6.0


@dataclass(frozen=True)
class _Snapshot:
    picture: np.ndarray
    noise: float
    shape: tuple[int, ...]
    dtype: str
    bounds: tuple[int, ...]
    context: str
    at: float
    sequence: int


def _picture(frames, bounds):
    x0, y0, x1, y1 = bounds
    height, width = frames.shape[-2:]
    factor = max(1, round(width / DISK_SEARCH_WIDTH_PX))
    # Include edge blocks instead of trimming off a possible new rival.
    x0, y0 = max(0, x0 // factor * factor), max(0, y0 // factor * factor)
    x1 = min(width, ((x1 + factor - 1) // factor) * factor)
    y1 = min(height, ((y1 + factor - 1) // factor) * factor)
    region = frames[:, y0:y1, x0:x1].astype(np.float32)
    if region.size == 0 or len(region) < 3:
        raise ValueError("a patch watch needs at least three nonempty frames")
    pad_y, pad_x = (-region.shape[1]) % factor, (-region.shape[2]) % factor
    if pad_y or pad_x:
        region = np.pad(region, ((0, 0), (0, pad_y), (0, pad_x)), mode="edge")
    blocks = region.reshape(len(region), region.shape[1] // factor, factor, -1, factor)
    coarse = blocks.mean(axis=(2, 4))
    return np.median(coarse, axis=0), float(np.median(np.std(coarse, axis=0)))


class PatchWatch:
    """Current-pixel agreement, never an unbounded cache or a new ball detector."""

    def __init__(self, max_age_s: float):
        self.max_age_s = max_age_s
        self._snapshot: _Snapshot | None = None

    def forget(self) -> None:
        """A miss, ambiguity or failed worker requires a fresh search."""
        self._snapshot = None

    def remember(self, frames, bounds, context, *, at: float, sequence: int) -> None:
        """Remember pixels only after the caller independently qualifies a ball."""
        picture, noise = _picture(frames, bounds)
        self._snapshot = _Snapshot(
            picture, noise, frames.shape, frames.dtype.str, tuple(bounds), context, at, sequence
        )

    def check(self, frames, bounds, context, *, at: float, sequence: int) -> dict:
        """Why this current image can be watched, or why the caller must refit."""
        saved = self._snapshot
        if saved is None:
            return {"matched": False, "reason": "no qualified search"}
        evidence = {
            "source_sequence": saved.sequence,
            "search_age_s": at - saved.at,
            "broad_refresh_limit_s": self.max_age_s,
            "bin_factor": max(1, round(frames.shape[-1] / DISK_SEARCH_WIDTH_PX)),
        }
        if sequence <= saved.sequence or not 0.0 <= at - saved.at < self.max_age_s:
            return {**evidence, "matched": False, "reason": "fresh broad search required"}
        if (
            frames.shape != saved.shape
            or frames.dtype.str != saved.dtype
            or tuple(bounds) != saved.bounds
            or context != saved.context
        ):
            return {**evidence, "matched": False, "reason": "camera, patch or controls changed"}
        picture, noise = _picture(frames, bounds)
        delta = picture - saved.picture
        offset = float(np.median(delta))
        change = float(np.abs(delta - offset).max())
        noisy = CHANGE_NOISE_MULTIPLE * max(noise, saved.noise) > CHANGE_FLOOR_DN
        return {
            **evidence,
            "matched": bool(not noisy and change <= CHANGE_FLOOR_DN),
            "reason": "noisy patch" if noisy else "current patch pixels compared",
            "max_change_dn": change,
            "offset_dn": offset,
            "noise_dn": noise,
            "limit_dn": CHANGE_FLOOR_DN,
        }

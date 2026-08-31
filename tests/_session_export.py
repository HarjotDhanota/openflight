"""Locate and load the 2026-08-25 camera session export, when it is present.

The export is not in the repository -- it is ~130 MB of raw frames -- so every
test that reads it skips cleanly when it is absent. Point `OPENFLIGHT_SESSION`
at the export directory to override the default download location.

Conventions this module owns, so that no test has to restate them:

  * frames are stored MIRRORED and are un-mirrored with ``[:, :, ::-1]``;
  * `shot_001` is excluded from the session (clipped exposure);
  * contact is the acoustic trigger walked back by the ball-to-unit flight
    time, ``pre_trigger_frames - 1.575 / 343 * fps``;
  * the per-shot plate scale is 42.67 mm over the teed ball's diameter.

NO GROUND TRUTH EXISTS in this export. Nothing here is an independent
measurement of anything; it is the rig's own pixels.
"""

from __future__ import annotations

import csv
import functools
import json
import os
from dataclasses import dataclass
from pathlib import Path

import pytest

BALL_DIAMETER_MM = 42.67
BALL_TO_UNIT_M = 1.575
SPEED_OF_SOUND_M_S = 343.0
EXCLUDED_SHOTS = frozenset({1})

_DEFAULT = (
    Path.home()
    / "Downloads"
    / "openflight_session_20260825_181734_filtered"
    / "openflight_session_20260825_181734_filtered"
)


def session_dir() -> Path | None:
    """The export directory, or None when it is not on this machine."""
    override = os.environ.get("OPENFLIGHT_SESSION")
    candidate = Path(override).expanduser() if override else _DEFAULT
    return candidate if (candidate / "shots.csv").is_file() else None


requires_session = pytest.mark.skipif(
    session_dir() is None,
    reason="the 2026-08-25 camera session export is not present locally",
)


@dataclass(frozen=True)
class SessionShot:
    """One shot's frames, teed ball and timing. Loaded once per test session."""

    name: str
    number: int
    club: str
    frames: object  # np.ndarray, kept untyped so importing needs no numpy
    ball: object  # club_motion.ReferenceBall
    fps: float
    contact_frame: float
    range_rate_ms: float

    @property
    def plate_mm_per_px(self) -> float:
        """Millimetres per pixel at the teed ball's range."""
        return BALL_DIAMETER_MM / self.ball.diameter_px


@functools.lru_cache(maxsize=1)
def _shot_rows() -> dict[int, dict[str, str]]:
    root = session_dir()
    assert root is not None
    with (root / "shots.csv").open(newline="", encoding="utf-8") as handle:
        return {int(row["shot_number"]): row for row in csv.DictReader(handle)}


def shot_names() -> tuple[str, ...]:
    """Every included shot directory name, in shot order."""
    root = session_dir()
    if root is None:
        return ()
    names = [
        entry.name
        for entry in (root / "shots").iterdir()
        if entry.is_dir() and int(entry.name.split("_", 2)[1]) not in EXCLUDED_SHOTS
    ]
    return tuple(sorted(names, key=lambda name: int(name.split("_", 2)[1])))


@functools.lru_cache(maxsize=32)
def load_shot(name: str) -> SessionShot:
    """Frames (un-mirrored), teed ball, fps and contact frame for one shot."""
    import numpy as np

    from openflight.camera.club_motion import detect_reference_ball

    root = session_dir()
    assert root is not None
    shot_dir = root / "shots" / name
    with (shot_dir / "camera_metadata.json").open(encoding="utf-8") as handle:
        metadata = json.load(handle)
    with np.load(shot_dir / "frames.npz") as capture:
        frames = capture["frames"][:, :, ::-1].astype(np.uint8)
    fps = float(metadata["delivered_fps"])
    number = int(name.split("_", 2)[1])
    return SessionShot(
        name=name,
        number=number,
        club=_shot_rows()[number]["club"],
        frames=frames,
        ball=detect_reference_ball(frames),
        fps=fps,
        contact_frame=int(metadata["pre_trigger_frames"])
        - BALL_TO_UNIT_M / SPEED_OF_SOUND_M_S * fps,
        range_rate_ms=float(_shot_rows()[number]["iwr_club_path_range_rate_ms"]),
    )


def teed_balls() -> tuple[tuple[str, object], ...]:
    """(shot name, ReferenceBall) for every included shot."""
    return tuple((name, load_shot(name).ball) for name in shot_names())

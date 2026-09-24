"""The tester suite's exposure ladder: its rungs, their gains, and what a swing must show.

The ladder finds the shortest exposure at full resolution where the camera's
pictures still carry the ball and the club, and compares 640x400. A rung that
cannot work in the tester's light is skipped before anyone swings, and a swing
fails only on what its own pictures show; the club pipeline's live results never
fail a swing, because its thresholds were tuned for 320x200.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from openflight.camera.auto_exposure import measure_exposure
from openflight.camera.club_motion import detect_reference_ball

SWINGS_PER_RUNG = 5
GAIN_CEILING = 12.0
LIGHT_FLOOR_DN = 10.0  # hitting-zone signal above the black floor
CLIPPED_MAX_PCT = 5.0
FPS_MIN_FRACTION = 0.9
EXPOSURE_TOLERANCE_US = 15  # exposure applies in whole rows
EXPOSURE_TOLERANCE_FRACTION = 0.10
GAIN_TOLERANCE_FRACTION = 0.10
EARLY_EXIT_SWINGS = 3
EARLY_EXIT_REDS = 2
PHOTO_TARGET_DN = 100.0
PHOTO_GAIN = 2.0
PHOTO_EXPOSURE_US = (100, 8000)
BALL_MOVED_RADII = 3.0
RESTING_FRAMES = 10  # the first pre-impact frames, before the club arrives


@dataclass(frozen=True)
class Rung:
    """One exposure on one readout mode; ``photos`` asks for impact photos."""

    rung_id: str
    arm_id: str
    exposure_us: int
    photos: bool


LADDER: tuple[Rung, ...] = tuple(
    [Rung(f"full-{e}", "arm5", e, True) for e in (300, 200, 150, 100, 75)]
    + [Rung(f"half-{e}", "arm6", e, False) for e in (300, 150, 75)]
)
RUNG_FPS = {"arm5": 120.0, "arm6": 288.0}


def rung_gain(gain_at_300: float, exposure_us: int) -> float:
    """The gain that keeps the gain screen's brightness at this exposure, up to the ceiling."""
    return round(min(GAIN_CEILING, gain_at_300 * 300.0 / exposure_us), 3)


def photo_exposure_us(light_index: float, black_floor: float, fps: float) -> int:
    """An exposure that puts the hitting zone near 100 DN at gain 2, under the frame period."""
    frame_limit = int(1_000_000 / fps) - 300
    wanted = (PHOTO_TARGET_DN - black_floor) / max(light_index * PHOTO_GAIN, 1e-9)
    return int(max(PHOTO_EXPOSURE_US[0], min(PHOTO_EXPOSURE_US[1], frame_limit, wanted)))


def _zone(image: np.ndarray, black_floor: float) -> dict:
    observation = measure_exposure(np.clip(np.asarray(image), 0, 255).astype(np.uint8))
    median = float(observation.median or 0.0)
    return {
        "median_dn": median,
        "signal_dn": median - black_floor,
        "clipped_pct": float(observation.clipped_pct or 0.0),
    }


def _zone_noise(frames: np.ndarray) -> float:
    height, width = frames.shape[1:]
    zone = frames[
        :, round(height * 0.45) : round(height * 0.9), round(width * 0.2) : round(width * 0.8)
    ]
    return float(np.median(np.std(zone.astype(np.float32), axis=0)))


def pre_rung_check(frames: np.ndarray, black_floor: float) -> dict:
    """Whether this rung can work in this light, from a few raw frames and no swing."""
    frames = np.asarray(frames)
    stats = _zone(np.median(frames, axis=0), black_floor)
    ok = stats["signal_dn"] >= LIGHT_FLOOR_DN
    return {
        **stats,
        "noise_dn": _zone_noise(frames),
        "ok": ok,
        "reason": None
        if ok
        else (
            f"too dark in this light: the hitting zone is {stats['signal_dn']:.0f} DN above "
            f"black, under {LIGHT_FLOOR_DN:.0f}"
        ),
    }


def swing_verdict(  # pylint: disable=too-many-locals
    capture_dir: Path, rung: Rung, gain: float, black_floor: float, previous_balls: list[dict]
) -> dict:
    """Green, amber or red for one saved swing, from its own pictures and timing."""
    metadata = json.loads((capture_dir / "metadata.json").read_text(encoding="utf-8"))
    with np.load(capture_dir / "frames.npz") as data:
        frames = data["frames"]
        applied_exposure = float(np.median(data["exposure_us"]))
        applied_gain = float(np.median(data["analogue_gain"]))
    fps = RUNG_FPS[rung.arm_id]
    red: list[str] = []
    amber: list[str] = []
    delivered = float(metadata.get("delivered_fps", 0.0))
    gaps = int(metadata.get("gap_count", 0))
    if delivered < FPS_MIN_FRACTION * fps:
        red.append(f"frames: {delivered:.0f} fps delivered, under 90% of {fps:.0f}")
    if gaps:
        red.append(f"frames: {gaps} gap(s) in the capture")
    tolerance = max(EXPOSURE_TOLERANCE_US, EXPOSURE_TOLERANCE_FRACTION * rung.exposure_us)
    if abs(applied_exposure - rung.exposure_us) > tolerance:
        red.append(f"controls: exposure {applied_exposure:.0f} us, not {rung.exposure_us}")
    if abs(applied_gain - gain) > GAIN_TOLERANCE_FRACTION * gain:
        red.append(f"controls: gain {applied_gain:.2f}, not {gain:.2f}")
    resting = frames[: max(3, min(RESTING_FRAMES, len(frames)))]
    stats = _zone(np.median(resting, axis=0), black_floor)
    if stats["signal_dn"] < LIGHT_FLOOR_DN:
        red.append(f"light: too dark, {stats['signal_dn']:.0f} DN above black")
    if stats["clipped_pct"] > CLIPPED_MAX_PCT:
        red.append(f"light: {stats['clipped_pct']:.0f}% of the hitting zone clipped")
    ball = None
    try:
        found = detect_reference_ball(resting)
        ball = {"x": found.x, "y": found.y, "diameter_px": found.diameter_px}
    except ValueError:
        amber.append("resting ball not found in the pre-impact frames")
    if ball and previous_balls:
        x = float(np.median([b["x"] for b in previous_balls]))
        y = float(np.median([b["y"] for b in previous_balls]))
        if math.hypot(ball["x"] - x, ball["y"] - y) > BALL_MOVED_RADII * ball["diameter_px"] / 2:
            amber.append("resting ball found somewhere else than on this rung's other swings")
    return {
        "capture": capture_dir.name,
        "color": "red" if red else ("amber" if amber else "green"),
        "reasons": red + amber,
        "delivered_fps": delivered,
        "gaps": gaps,
        "exposure_us": applied_exposure,
        "gain": applied_gain,
        "signal_dn": stats["signal_dn"],
        "clipped_pct": stats["clipped_pct"],
        "ball": ball,
    }

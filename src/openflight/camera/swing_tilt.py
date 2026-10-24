"""The unit's camera tilt, calibrated from swings.

A pair is one swing's measured IWR launch-point range (never the camera prior), the
resting-ball row in that swing's own pre-impact frames, and its LIS3DH pitch;
``camera_tilt`` solves and stores the pairs per unit and lens.
"""

from __future__ import annotations

import math
from typing import Any, Callable, Mapping, Sequence

import numpy as np

SCHEMA = "openflight.camera_swing_tilt_pair.v1"
METHOD = "median_of_swing_pairs"
# the resting frames: before the club reaches the box (light_judging.RESTING_FRAMES)
RESTING_FRAMES = 10
# frames just before the trigger may already hold the club
TRIGGER_MARGIN_FRAMES = 2
MIN_RESTING_FRAMES = 3


def resting_ball_px(
    frames: np.ndarray,
    *,
    trigger_index: int | None,
    box_px: Sequence[int],
    pixel_scale: float | None = None,
) -> dict | None:
    """The resting ball in this swing's own pre-impact frames, inside the box, or None."""
    from openflight.camera.club_motion import detect_reference_ball  # noqa: PLC0415

    frames = np.asarray(frames)
    if frames.ndim != 3 or frames.dtype != np.uint8 or not len(frames):
        return None
    last = len(frames) if trigger_index is None else int(trigger_index) - TRIGGER_MARGIN_FRAMES
    count = min(RESTING_FRAMES, max(0, last))
    if count < MIN_RESTING_FRAMES:
        return None
    still = np.median(frames[:count], axis=0).astype(np.uint8)
    height, width = still.shape
    x0, y0, x1, y1 = (int(value) for value in box_px)
    roi = (max(0, x0), max(0, y0), min(width, x1), min(height, y1))
    if roi[0] >= roi[2] or roi[1] >= roi[3]:
        return None
    try:
        found = detect_reference_ball(
            np.repeat(still[None], 3, axis=0), roi=roi, pixel_scale=pixel_scale
        )
    except (RuntimeError, ValueError):
        return None
    if found is None or not (x0 <= found.x <= x1 and y0 <= found.y <= y1):
        return None
    return {
        "x_px": float(found.x),
        "y_px": float(found.y),
        "diameter_px": float(found.diameter_px),
        "frames_used": int(count),
        "box_px": [x0, y0, x1, y1],
    }


def nominal_camera_at_pitch(
    *,
    focal_px: float,
    width: int,
    height: int,
    camera_origin_lfu: Sequence[float],
    radar_origin_lfu: Sequence[float],
    roll_correction_deg: float = 0.0,
) -> Callable[[float], Any]:
    """The nominal camera at a trial pitch, as the tester's solve builds it, with the
    swing's roll correction (0 unless the unit verified its roll)."""
    from openflight.camera.reference_ball_range import BallPlaneCamera  # noqa: PLC0415

    def camera(pitch_deg: float):
        return BallPlaneCamera.nominal(
            focal_px=float(focal_px),
            image_width_px=int(width),
            image_height_px=int(height),
            pitch_deg=float(pitch_deg),
            roll_correction_deg=float(roll_correction_deg),
            mirror_horizontal=False,
            camera_origin_lfu=tuple(camera_origin_lfu),
            radar_origin_lfu=tuple(radar_origin_lfu),
            angular_uncertainty_deg=3.0,
            focal_relative_uncertainty=0.08,
        )

    return camera


def swing_pair(
    shot_range: Mapping[str, Any],
    *,
    ball_px: Sequence[float] | Mapping | None,
    lis3dh_pitch_deg: float | None,
    camera_at_pitch: Callable[[float], Any],
) -> dict:
    """One swing's tilt pair: solved, or skipped with the reason."""
    from openflight.camera import camera_tilt  # noqa: PLC0415

    base = {"schema": SCHEMA, "range_source": shot_range.get("ball_range_source")}
    if shot_range.get("ball_range_source") != "iwr_post_impact":
        return {
            **base,
            "status": "skipped",
            "reason": "the shot's ball range was not measured by the IWR "
            f"({shot_range.get('ball_range_source') or 'none'})",
        }
    range_m = shot_range.get("ball_range_m")
    uncertainty = shot_range.get("ball_range_uncertainty_m")
    if range_m is None or uncertainty is None:
        return {**base, "status": "skipped", "reason": "the shot has no IWR launch point"}
    if ball_px is None:
        return {
            **base,
            "status": "skipped",
            "reason": "no resting ball in the box in this swing's pre-impact frames",
        }
    if lis3dh_pitch_deg is None or not math.isfinite(float(lis3dh_pitch_deg)):
        return {**base, "status": "skipped", "reason": "no stable LIS3DH pitch for this shot"}
    pixel = (
        (float(ball_px["x_px"]), float(ball_px["y_px"]))
        if isinstance(ball_px, Mapping)
        else (float(ball_px[0]), float(ball_px[1]))
    )
    try:
        solved = camera_tilt.solve_vertical_offset(
            camera_at_pitch,
            pixel,
            radar_range_m=float(range_m),
            radar_uncertainty_m=float(uncertainty),
            lis3dh_pitch_deg=float(lis3dh_pitch_deg),
        )
    except (ValueError, ArithmeticError) as error:
        return {**base, "status": "skipped", "reason": f"the tilt did not solve: {error}"}
    return {**base, "status": "solved", "reason": None, "solved": solved}


def record_swing_pair(path, pair: Mapping, *, facts: Mapping | None = None) -> dict:
    """Store a solved swing pair in the unit's per-lens tilt file (camera_tilt)."""
    from openflight.camera import camera_tilt  # noqa: PLC0415

    if pair.get("status") != "solved":
        return {"action": "not_recorded", "reason": pair.get("reason")}
    return camera_tilt.record_pair(
        path,
        pair["solved"],
        facts={**dict(facts or {}), "pair_source": "swing", "range_source": "iwr_post_impact"},
        method=METHOD,
    )


__all__ = [
    "METHOD",
    "SCHEMA",
    "nominal_camera_at_pitch",
    "record_swing_pair",
    "resting_ball_px",
    "swing_pair",
]

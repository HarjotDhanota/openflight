"""The resting ball's distance from its own size, for a shot with no other range (experimental).

The last route of the ball-range chain (``source_chain``): the IWR's launch point,
then the setup's camera prior, then this. The ball is measured as the setup measures
it, a lit sphere sized on its outline (``club_motion.measure_resting_ball``), in
windows of the frames before the club arrives; their median is its size and their
spread (at least a pixel: the outline's own ambiguity) its uncertainty. Its distance
from the lens is ``focal x D / d``. The focal length is the unit's calibrated one
when it has one, else its lens's listed value, carried at +-8 %.

The place the ball rests also follows: on its ray, at that distance, on the floor
(the pitch that puts it there), so the radar's slant range to it and its side offset
are recorded with the same label. None of this is validated against a tape.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any, Optional, Sequence

import numpy as np

from ..constants import BALL_DIAMETER_M
from ..source_labels import LABEL_EXPERIMENTAL
from . import ball_pixels
from .ball_launch_fallback import PinholeCamera, resting_ball_pitch_deg
from .club_motion import detect_reference_ball, measure_resting_ball

SOURCE = "camera_ball_size"
LABEL = LABEL_EXPERIMENTAL
# A lens's listed focal length against the unit's (uncalibrated M12 lenses).
NOMINAL_FOCAL_REL_SD = 0.08
CALIBRATED_FOCAL_REL_SD = 0.02
# The outline method's own ambiguity on a resting ball.
MIN_DIAMETER_SD_PX = 1.0
# Short windows: the lit-sphere fit now and then grows into the shade on one window
# (Harjot-outdoor-test-1: 2-4 of 25 windows 15-20 % large); the median of five holds.
WINDOW_FRAMES = 3
# At most this many windows, spread over the frames before the club arrives.
MAX_WINDOWS = 6
# The frames before the club arrives: a fast swing's club is near the ball in the last
# 25-35 ms before the trigger (Harjot-outdoor-test-1 shots 20, 22: 3-4 frames at 120 fps).
CLUB_ARRIVAL_S = 0.030
# Where around the seed the ball may rest, in the seed's diameters, and how big it may be.
SEED_REACH_DIAMETERS = 4.0
SEED_SIZE_RATIO = (0.6, 1.6)
# A window whose size is this far from the median is not the ball's (a club edge, glare).
WINDOW_OUTLIER_FRACTION = 0.15


@dataclass(frozen=True)
class RestingBallSize:
    """The resting ball's centre and outline diameter (px), and the diameter's 1-sigma."""

    x: float
    y: float
    diameter_px: float
    diameter_sd_px: float
    windows: int


@dataclass(frozen=True)
class SizeDistance:
    """The lens-to-ball distance from the ball's size, with each part of its 1-sigma."""

    distance_m: float
    sd_m: float
    size_sd_m: float
    focal_sd_m: float


def measure_resting_ball_size(
    frames: np.ndarray,
    *,
    first_post_index: int,
    seed: Sequence[float],
    frame_interval_s: float = 1.0 / 120.0,
) -> Optional[RestingBallSize]:
    """The resting ball near ``seed`` (x, y, diameter px) in the pre-impact frames, or None."""
    seed_x, seed_y, seed_d = (float(value) for value in seed)
    arrival = max(1, math.ceil(CLUB_ARRIVAL_S / max(frame_interval_s, 1e-4) - 1e-9))
    before = frames[: max(3, int(first_post_index) - arrival)]
    height, width = before.shape[-2:]
    reach = SEED_REACH_DIAMETERS * seed_d
    region = (
        max(0, int(seed_x - reach)),
        max(0, int(seed_y - reach)),
        min(width, int(seed_x + reach)),
        min(height, int(seed_y + reach)),
    )
    if region[0] >= region[2] or region[1] >= region[3]:
        return None
    try:
        found = detect_reference_ball(
            before, pixel_scale=ball_pixels.pixel_scale(width), roi=region
        )
    except (RuntimeError, ValueError):
        return None
    low, high = (ratio * seed_d for ratio in SEED_SIZE_RATIO)
    # the fit starts from the detector's own size when it is plausible, else the seed's
    start_d = found.diameter_px if low <= found.diameter_px <= high else seed_d
    sizes, centres = [], []
    starts = range(0, max(1, len(before) - WINDOW_FRAMES + 1), WINDOW_FRAMES)
    step = max(1, math.ceil(len(starts) / MAX_WINDOWS))
    for start in list(starts)[::step]:
        window = before[start : start + WINDOW_FRAMES]
        if len(window) < 3:
            continue
        measured = measure_resting_ball(window, found, start_d)
        if measured is not None:
            sizes.append(measured.diameter_px)
            centres.append((measured.x, measured.y))
    if not sizes:
        return None
    median = float(np.median(sizes))
    keep = [abs(size - median) <= WINDOW_OUTLIER_FRACTION * median for size in sizes]
    kept = [size for size, ok in zip(sizes, keep) if ok]
    diameter = float(np.median(kept))
    if not low <= diameter <= high:
        return None
    spread = (
        1.4826 * float(np.median(np.abs(np.asarray(kept) - diameter))) if len(kept) > 1 else 0.0
    )
    centre = np.median(np.asarray([c for c, ok in zip(centres, keep) if ok]), axis=0)
    return RestingBallSize(
        x=float(centre[0]),
        y=float(centre[1]),
        diameter_px=diameter,
        diameter_sd_px=max(MIN_DIAMETER_SD_PX, spread),
        windows=len(kept),
    )


def distance_from_size(
    *,
    diameter_px: float,
    diameter_sd_px: float,
    focal_px: float,
    focal_rel_sd: float,
    ball_diameter_m: float = BALL_DIAMETER_M,
) -> SizeDistance:
    """``focal x D / d`` and its 1-sigma from the size and the focal length."""
    distance = float(focal_px) * float(ball_diameter_m) / float(diameter_px)
    size_sd = distance * float(diameter_sd_px) / float(diameter_px)
    focal_sd = distance * float(focal_rel_sd)
    return SizeDistance(
        distance_m=distance,
        sd_m=math.hypot(size_sd, focal_sd),
        size_sd_m=size_sd,
        focal_sd_m=focal_sd,
    )


def focal_for(
    *, lens_focal_px: Optional[float], calibrated_focal_px: Optional[float]
) -> Optional[tuple[float, str, float]]:
    """(focal px, its source, its relative 1-sigma): the unit's calibration, else the lens's."""
    for value, source, sd in (
        (calibrated_focal_px, "unit_calibration", CALIBRATED_FOCAL_REL_SD),
        (lens_focal_px, "lens_nominal", NOMINAL_FOCAL_REL_SD),
    ):
        if value is not None and math.isfinite(float(value)) and float(value) > 0.0:
            return float(value), source, sd
    return None


def radar_place(ball_lfu: np.ndarray, *, radar_origin_lfu: np.ndarray) -> dict[str, float]:
    """The radar's slant range to the ball and the ball's side offset from the radar's axis."""
    ball, radar = np.asarray(ball_lfu, dtype=float), np.asarray(radar_origin_lfu, dtype=float)
    return {
        "slant_range_m": float(np.linalg.norm(ball - radar)),
        "side_offset_m": float(ball[0] - radar[0]),
    }


def resting_ball_range(  # pylint: disable=too-many-arguments,too-many-locals
    frames: np.ndarray,
    *,
    first_post_index: int,
    frame_interval_s: float = 1.0 / 120.0,
    seed: Sequence[float],
    lens_focal_px: Optional[float],
    calibrated_focal_px: Optional[float],
    camera_origin_lfu: np.ndarray,
    radar_origin_lfu: np.ndarray,
    ball_height_m: float,
    image_width_px: int,
    image_height_px: int,
    roll_correction_deg: float,
    horizontal_pixel_sign: float = 1.0,
) -> dict[str, Any]:
    """The ball's range from its own size, as one plain record (status, label, source...)."""
    record: dict[str, Any] = {"label": LABEL, "source": SOURCE}
    focal = focal_for(lens_focal_px=lens_focal_px, calibrated_focal_px=calibrated_focal_px)
    if focal is None:
        return {
            **record,
            "status": "no_focal_length",
            "reason": "no calibrated or listed focal length for this lens and mode",
        }
    focal_px, focal_source, focal_rel_sd = focal
    size = measure_resting_ball_size(
        frames, first_post_index=first_post_index, seed=seed, frame_interval_s=frame_interval_s
    )
    if size is None:
        return {
            **record,
            "status": "no_resting_ball",
            "reason": "no lit resting ball measured on its outline before impact",
            "focal_px": focal_px,
            "focal_source": focal_source,
        }
    distance = distance_from_size(
        diameter_px=size.diameter_px,
        diameter_sd_px=size.diameter_sd_px,
        focal_px=focal_px,
        focal_rel_sd=focal_rel_sd,
    )
    camera = PinholeCamera(
        focal_px=focal_px,
        width=int(image_width_px),
        height=int(image_height_px),
        pitch_deg=0.0,
        roll_correction_deg=float(roll_correction_deg),
        horizontal_pixel_sign=float(horizontal_pixel_sign),
        origin_lfu=tuple(float(value) for value in camera_origin_lfu),
    )
    try:
        pitch = resting_ball_pitch_deg(camera, (size.x, size.y), distance.distance_m, ball_height_m)
    except ValueError as error:
        return {**record, "status": "geometry", "reason": str(error)}
    camera = replace(camera, pitch_deg=pitch)
    ball = np.asarray(camera.origin_lfu) + distance.distance_m * camera.rays(
        np.array([size.x, size.y])
    )
    placed = radar_place(ball, radar_origin_lfu=radar_origin_lfu)
    # the slant range moves with the lens distance almost one for one at these angles
    scale = placed["slant_range_m"] / distance.distance_m
    return {
        **record,
        "status": "measured_from_size",
        "reason": None,
        "rest_px": [size.x, size.y],
        "diameter_px": size.diameter_px,
        "diameter_sd_px": size.diameter_sd_px,
        "windows": size.windows,
        "focal_px": focal_px,
        "focal_source": focal_source,
        "focal_rel_sd": focal_rel_sd,
        "camera_distance_m": distance.distance_m,
        "camera_distance_sd_m": distance.sd_m,
        "size_sd_m": distance.size_sd_m,
        "inferred_pitch_deg": pitch,
        "ball_lfu_m": [float(value) for value in ball],
        "slant_range_m": placed["slant_range_m"],
        "uncertainty_m": distance.sd_m * scale,
        "side_offset_m": placed["side_offset_m"],
        "side_offset_sd_m": abs(placed["side_offset_m"]) * distance.sd_m / distance.distance_m,
        "note": (
            f"the resting ball's own size ({size.diameter_px:.1f} px) at the {focal_source} "
            f"focal length ({focal_px:.0f} px, +-{100 * focal_rel_sd:.0f} %): experimental, "
            "not validated against a tape"
        ),
    }


__all__ = [
    "LABEL",
    "RestingBallSize",
    "SOURCE",
    "SizeDistance",
    "distance_from_size",
    "focal_for",
    "measure_resting_ball_size",
    "radar_place",
    "resting_ball_range",
]

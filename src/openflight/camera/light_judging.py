"""Judge a clip's light on the resting ball and the hitting zone.

One rule for analysis eligibility, auto-exposure and the exposure studies: the ball
is judged first, where the setup saw it; with no ball in view the hitting zone decides.
"""

from __future__ import annotations

import math

import numpy as np

from openflight.camera import swing_exposure
from openflight.camera.auto_exposure import measure_exposure
from openflight.camera.club_motion import detect_reference_ball

# OV9281 black level in the raw R8 stream (libcamera SensorBlackLevels 4096 / 16-bit),
# used when no floor was measured; never the ring around the ball (a contrast gate).
SENSOR_BLACK_LEVEL_DN = 16.0
LIGHT_FLOOR_DN = 10.0  # hitting-zone signal above the black floor
CLIPPED_MAX_PCT = 5.0
RESTING_FRAMES = 10  # the first pre-impact frames, before the club arrives
# Outdoor background can be far brighter than the ball, so the ball decides when visible.
BALL_MIN_SIGNAL_DN = 20.0
# Only a mostly clipped zone is too bright: a clipped strip of background is normal outdoors.
ZONE_TOO_BRIGHT_PCT = 50.0
BALL_SEARCH_DIAMETERS = 2.0
BALL_SEARCH_MIN_PX = 30.0
BALL_MATCH_DIAMETERS = 1.0
DARK_LIGHT_CAUSES = frozenset({"ball_dark", "zone_dark"})
BRIGHT_LIGHT_CAUSES = frozenset({"ball_clipped", "zone_clipped_no_ball"})


def _zone(image: np.ndarray, black_floor: float, zone_box=None) -> dict:
    """The hitting zone's light: the tester's box when given, else the fixed box."""
    observation = measure_exposure(
        np.clip(np.asarray(image), 0, 255).astype(np.uint8), zone_box=zone_box
    )
    median = float(observation.median or 0.0)
    return {
        "median_dn": median,
        "signal_dn": median - black_floor,
        "clipped_pct": float(observation.clipped_pct or 0.0),
        "zone_source": observation.zone_source,
        "zone_box_px": (
            list(observation.zone_box_px) if observation.zone_box_px is not None else None
        ),
    }


def _zone_name(zone: dict) -> str:
    """How a verdict names the zone it judged."""
    return "the box" if zone.get("zone_source") == "placement_box" else "the hitting zone"


def ball_light(
    frames: np.ndarray, black_floor: float, expected_ball: dict | None = None
) -> dict | None:
    """The resting ball's brightness in these frames, if the ball can be found.

    Only a ball of the size of ``expected_ball`` (x, y, diameter_px) within about one
    diameter of it counts; without it nothing is searched. When the setup's spot is
    itself clipped, that spot is judged (``found_by`` is ``setup_position``).
    """
    if expected_ball is None:
        return None
    stack = np.clip(np.asarray(frames), 0, 255).astype(np.uint8)
    diameter = float(expected_ball["diameter_px"])
    expected_x, expected_y = float(expected_ball["x"]), float(expected_ball["y"])
    height, width = stack.shape[1:]
    half = max(BALL_SEARCH_DIAMETERS * diameter, BALL_SEARCH_MIN_PX)
    roi = (
        max(0, int(expected_x - half)),
        max(0, int(expected_y - half)),
        min(width, int(math.ceil(expected_x + half))),
        min(height, int(math.ceil(expected_y + half))),
    )
    image = np.median(stack, axis=0)
    try:
        # no size given: the strict lit-sphere mode refuses sunlit or half-shaded balls
        found = detect_reference_ball(stack, roi=roi)
    except (RuntimeError, ValueError):
        found = None
    if found is not None and (
        0.6 * diameter <= found.diameter_px <= 1.6 * diameter
        and math.hypot(found.x - expected_x, found.y - expected_y)
        <= BALL_MATCH_DIAMETERS * diameter
    ):
        x, y, size, found_by = float(found.x), float(found.y), float(found.diameter_px), "detector"
    else:
        x, y, size, found_by = expected_x, expected_y, diameter, "setup_position"
    core = swing_exposure.ball_core(image, x, y, size)
    if not core.size:
        return None
    clipped = float(np.mean(core >= 250) * 100.0)
    if found_by == "setup_position" and clipped <= CLIPPED_MAX_PCT:
        return None  # no ball there, and nothing clipped where it should be
    median = float(np.median(core))
    return {
        "x": x,
        "y": y,
        "diameter_px": size,
        "median_dn": median,
        "peak_dn": float(np.max(core)),
        "signal_dn": median - black_floor,
        "clipped_pct": clipped,
        "found_by": found_by,
    }


def judge_light(
    frames: np.ndarray,
    black_floor: float,
    expected_ball: dict | None = None,
    zone_box=None,
) -> dict:
    """The one light rule both the pre-rung check and the swing verdict apply.

    ``cause`` is one of: ``ball_clipped``, ``ball_dark``, ``zone_dark``,
    ``zone_clipped_no_ball``, ``background_clipped`` (amber only) or ``ok``.
    """
    frames = np.asarray(frames)
    zone = _zone(np.median(frames, axis=0), black_floor, zone_box)
    where = _zone_name(zone)
    ball = ball_light(frames, black_floor, expected_ball)
    cause, message = "ok", None
    if ball is not None and ball["clipped_pct"] > CLIPPED_MAX_PCT:
        cause = "ball_clipped"
        message = f"too bright for the ball: {ball['clipped_pct']:.0f}% of it is clipped"
    elif ball is not None and ball["signal_dn"] < BALL_MIN_SIGNAL_DN:
        cause = "ball_dark"
        message = f"too dark for the ball: {ball['signal_dn']:.0f} DN above black"
    elif zone["signal_dn"] < LIGHT_FLOOR_DN:
        cause = "zone_dark"
        message = (
            f"too dark in this light: {where} is {zone['signal_dn']:.0f} DN above "
            f"black, under {LIGHT_FLOOR_DN:.0f}"
        )
    elif ball is None and zone["clipped_pct"] > ZONE_TOO_BRIGHT_PCT:
        cause = "zone_clipped_no_ball"
        message = (
            f"too bright in this light: {zone['clipped_pct']:.0f}% of {where} is "
            "clipped; a shorter exposure follows"
        )
    elif zone["clipped_pct"] > CLIPPED_MAX_PCT:
        cause = "background_clipped"
        message = f"background: {zone['clipped_pct']:.0f}% of {where} clipped" + (
            " behind a well-exposed ball" if ball is not None else ""
        )
    return {"zone": zone, "ball": ball, "cause": cause, "message": message}


def resting_frames(frames: np.ndarray) -> np.ndarray:
    """A clip's first frames, before the club arrives: what the ball is judged on."""
    return frames[: max(3, min(RESTING_FRAMES, len(frames)))]


def capture_analysis_eligibility(
    frames: np.ndarray, black_floor: float, expected_ball: dict, zone_box=None
) -> dict:
    """Whether a clip's light permits camera analysis, judged on the setup's ball.

    Only the ball decides; a clipped background or dark zone does not withhold the camera.
    """
    light = judge_light(resting_frames(np.asarray(frames)), black_floor, expected_ball, zone_box)
    ball = light["ball"]
    if ball is None:
        reason = "the resting ball was not found where the setup saw it"
    elif ball["clipped_pct"] > CLIPPED_MAX_PCT:
        reason = f"too bright for the ball: {ball['clipped_pct']:.0f}% of it is clipped"
    elif ball["signal_dn"] < BALL_MIN_SIGNAL_DN:
        reason = f"too dark for the ball: {ball['signal_dn']:.0f} DN above black"
    else:
        reason = None
    return {
        "rule": "setup_ball",
        "eligible": reason is None,
        "reason": reason,
        "light_cause": light["cause"],
        "ball": ball,
        "zone": light["zone"],
        "setup_ball": dict(expected_ball),
        "black_floor_dn": float(black_floor),
    }

"""Judge a clip's light on the resting ball and the hitting zone.

One rule for analysis eligibility, auto-exposure and the exposure studies: the ball
is judged first, anywhere in the setup's box (else where the setup saw it); with no
ball in view the hitting zone decides.
"""

from __future__ import annotations

import math

import numpy as np

from openflight.camera import swing_exposure
from openflight.camera.auto_exposure import hitting_zone, measure_exposure
from openflight.camera.club_motion import detect_reference_ball, measure_resting_ball

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
# With a box the ball may rest anywhere in it; only its size must be the setup ball's.
BOX_BALL_SIZE_RATIO = (0.6, 1.6)
# A dark box is searched brightened to about this many DN above black at its 99th
# percentile (Harjot-outdoor-test-1, 30 us: the ball's lit side ~25 DN, too dim to find);
# its light is still read on the frames as taken.
DARK_SEARCH_TARGET_DN = 120.0
DARK_SEARCH_MIN_GAIN = 2.0
CLIPPED_DN = 250
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
    return "the box" if zone.get("zone_source") == "placement_box" else "the hitting zone"


def _near_the_setup_ball(
    stack: np.ndarray, x: float, y: float, diameter: float
) -> tuple[float, float, float] | None:
    """Without a box: a ball of the setup's size within about one diameter of its spot."""
    height, width = stack.shape[1:]
    half = max(BALL_SEARCH_DIAMETERS * diameter, BALL_SEARCH_MIN_PX)
    roi = (
        max(0, int(x - half)),
        max(0, int(y - half)),
        min(width, int(math.ceil(x + half))),
        min(height, int(math.ceil(y + half))),
    )
    try:
        # no size given: the strict lit-sphere mode refuses sunlit or half-shaded balls
        found = detect_reference_ball(stack, roi=roi)
    except (RuntimeError, ValueError):
        return None
    if (
        0.6 * diameter <= found.diameter_px <= 1.6 * diameter
        and math.hypot(found.x - x, found.y - y) <= BALL_MATCH_DIAMETERS * diameter
    ):
        return float(found.x), float(found.y), float(found.diameter_px)
    return None


def _search_pictures(stack: np.ndarray, box: tuple[int, int, int, int], black_floor: float):
    """The frames as taken, then, when the box is dark, brightened to find a dim ball."""
    yield stack
    x0, y0, x1, y1 = box
    signal = float(np.percentile(np.median(stack[:, y0:y1, x0:x1], axis=0), 99)) - black_floor
    gain = DARK_SEARCH_TARGET_DN / max(signal, 1.0)
    if gain >= DARK_SEARCH_MIN_GAIN:
        brightened = (stack.astype(np.float32) - float(black_floor)) * gain
        yield np.clip(np.round(brightened), 0, 255).astype(np.uint8)


def _in_the_box(
    stack: np.ndarray,
    image: np.ndarray,
    box: tuple[int, int, int, int],
    diameter: float,
    black_floor: float,
) -> tuple[float, float, float, str] | None:
    """The resting ball anywhere in the box, measured as the setup measures its ball.

    A candidate counts when it is a lit sphere of the setup ball's size (sized on its
    own outline, ``measure_resting_ball``): a gap in the net is not (Harjot-outdoor-test-1
    shots 5 and 8). A clipped candidate no lit sphere fits is judged for its light
    (``clipped_spot``), never placed as the ball.
    """
    low, high = BOX_BALL_SIZE_RATIO
    x0, y0, x1, y1 = box
    for picture in _search_pictures(stack, box, black_floor):
        try:
            candidate = detect_reference_ball(picture, roi=box)
        except (RuntimeError, ValueError):
            continue
        # sized from its own outline, not the setup's: an oversized setup ball (58.98 px
        # on 24 Oct, the ball ~43) grows the fit into the shade under the ball
        measured = measure_resting_ball(picture, candidate, candidate.diameter_px)
        ball = measured if measured is not None else candidate
        if not (
            low * diameter <= ball.diameter_px <= high * diameter
            and x0 <= ball.x <= x1
            and y0 <= ball.y <= y1
        ):
            continue
        if measured is not None:
            return float(ball.x), float(ball.y), float(ball.diameter_px), "detector"
        core = swing_exposure.ball_core(image, ball.x, ball.y, ball.diameter_px)
        if core.size and float(np.mean(core >= CLIPPED_DN) * 100.0) > CLIPPED_MAX_PCT:
            return float(ball.x), float(ball.y), float(ball.diameter_px), "clipped_spot"
    return None


def ball_light(
    frames: np.ndarray,
    black_floor: float,
    expected_ball: dict | None = None,
    zone_box=None,
) -> dict | None:
    """The resting ball's brightness near ``expected_ball`` (x, y, diameter_px), if found.

    With the setup's box (``zone_box``) the ball may rest anywhere in it: the box is
    fixed and the tester tees each ball wherever it lands. Without one, only a ball
    within about one diameter of ``expected_ball`` (x, y, diameter_px) counts. A ball
    of another size never counts; without ``expected_ball`` nothing is searched. When
    no ball is found but the setup's spot is itself clipped, that spot is judged
    (``found_by`` is ``setup_position``).
    """
    if expected_ball is None:
        return None
    stack = np.clip(np.asarray(frames), 0, 255).astype(np.uint8)
    diameter = float(expected_ball["diameter_px"])
    expected_x, expected_y = float(expected_ball["x"]), float(expected_ball["y"])
    image = np.median(stack, axis=0)
    if zone_box is None:
        near = _near_the_setup_ball(stack, expected_x, expected_y, diameter)
        found = (*near, "detector") if near is not None else None
    else:
        box, _source = hitting_zone(stack.shape[1], stack.shape[2], zone_box)
        found = _in_the_box(stack, image, box, diameter, black_floor)
    if found is None:
        found = (expected_x, expected_y, diameter, "setup_position")
    x, y, size, found_by = found
    core = swing_exposure.ball_core(image, x, y, size)
    if not core.size:
        return None
    clipped = float(np.mean(core >= CLIPPED_DN) * 100.0)
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
    ``zone_dark`` (and ``zone_message``) says the zone, the club's background, is too
    dark whatever the ball's cause: a dim ball found in a dark box is still that.
    """
    frames = np.asarray(frames)
    zone = _zone(np.median(frames, axis=0), black_floor, zone_box)
    where = _zone_name(zone)
    ball = ball_light(frames, black_floor, expected_ball, zone_box)
    zone_dark = zone["signal_dn"] < LIGHT_FLOOR_DN
    zone_message = (
        f"too dark in this light: {where} is {zone['signal_dn']:.0f} DN above "
        f"black, under {LIGHT_FLOOR_DN:.0f}"
        if zone_dark
        else None
    )
    cause, message = "ok", None
    if ball is not None and ball["clipped_pct"] > CLIPPED_MAX_PCT:
        cause = "ball_clipped"
        message = f"too bright for the ball: {ball['clipped_pct']:.0f}% of it is clipped"
    elif ball is not None and ball["signal_dn"] < BALL_MIN_SIGNAL_DN:
        cause = "ball_dark"
        message = f"too dark for the ball: {ball['signal_dn']:.0f} DN above black"
    elif zone_dark:
        cause, message = "zone_dark", zone_message
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
    return {
        "zone": zone,
        "ball": ball,
        "cause": cause,
        "message": message,
        "zone_dark": zone_dark,
        "zone_message": zone_message,
    }


def resting_frames(frames: np.ndarray) -> np.ndarray:
    """A clip's first frames, before the club arrives: what the ball is judged on."""
    return frames[: max(3, min(RESTING_FRAMES, len(frames)))]


def capture_analysis_eligibility(
    frames: np.ndarray, black_floor: float, expected_ball: dict, zone_box=None
) -> dict:
    """Whether a clip's light permits camera analysis; only the setup's ball decides."""
    light = judge_light(resting_frames(np.asarray(frames)), black_floor, expected_ball, zone_box)
    ball = light["ball"]
    if ball is None:
        reason = (
            "the resting ball was not found where the setup saw it"
            if zone_box is None
            else "the resting ball was not found in the box"
        )
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

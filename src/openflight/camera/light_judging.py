"""Judge a clip's light on the resting ball and the hitting zone.

One light rule for every judge of the camera's pictures: the capture runtime's
analysis eligibility and auto-exposure, and the exposure studies built on them.
The ball is judged first, where the setup saw it; with no ball in view the
hitting zone (the placement box in this mode when there is one) decides.
"""

from __future__ import annotations

import math

import numpy as np

from openflight.camera import swing_exposure
from openflight.camera.auto_exposure import measure_exposure
from openflight.camera.club_motion import detect_reference_ball

# The OV9281's black level in the raw R8 stream every analysis uses: libcamera's
# SensorBlackLevels reports 4096 on a 16-bit scale (read on the Pi, 29 Sept). It
# stands in when no floor was measured; the ring around the ball never does, as
# that turned the signal gate back into a contrast gate (wiring audit B5).
SENSOR_BLACK_LEVEL_DN = 16.0
LIGHT_FLOOR_DN = 10.0  # hitting-zone signal above the black floor
CLIPPED_MAX_PCT = 5.0
RESTING_FRAMES = 10  # the first pre-impact frames, before the club arrives
# Outdoors the background beyond the ball can be 30x brighter than the ball's
# surroundings (29 Sept: a sunlit patio clipped at every setting while the shaded
# mat sat at 71 DN), so a rung is judged on the ball whenever it is visible.
BALL_MIN_SIGNAL_DN = 20.0
# With no ball in view the hitting zone decides, and only a mostly clipped zone
# counts as too bright: a clipped strip of background is normal outdoors.
ZONE_TOO_BRIGHT_PCT = 50.0
# The ball is searched for this many diameters around where the setup saw it, and
# must lie within about one diameter of that spot (P7-9).
BALL_SEARCH_DIAMETERS = 2.0
BALL_SEARCH_MIN_PX = 30.0
BALL_MATCH_DIAMETERS = 1.0
DARK_LIGHT_CAUSES = frozenset({"ball_dark", "zone_dark"})
BRIGHT_LIGHT_CAUSES = frozenset({"ball_clipped", "zone_clipped_no_ball"})


def _zone(image: np.ndarray, black_floor: float, zone_box=None) -> dict:
    """The hitting zone's light: the tester's box when given, else the fixed box (P7-15)."""
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
    """How a verdict names the zone it judged: the tester's box, or the fixed zone."""
    return "the box" if zone.get("zone_source") == "placement_box" else "the hitting zone"


def ball_light(
    frames: np.ndarray, black_floor: float, expected_ball: dict | None = None
) -> dict | None:
    """The resting ball's brightness in these frames, if the ball can be found.

    ``expected_ball`` (x, y, diameter_px) is where the setup saw the ball in this
    mode. The ball is searched for only around that spot, and only a ball of that
    size within about one diameter of it counts (P7-9: a six-diameter match took
    fence clutter 175 px away for the ball in sun). Without it nothing is
    searched: a whole-frame search took a 5 px speck for the ball (P6-1).

    A ball in a sunlit, clipped patch of mat melts into it and cannot be found;
    when the setup's spot is itself clipped, that spot is judged (``found_by`` is
    ``setup_position``), since a ball placed there clips too.
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
        # The detector's strict lit-sphere mode (given a size) refuses sunlit or
        # half-shaded balls, so the result is checked against the setup instead.
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
        # the brightest of the ball's core, for the page's live readout
        "peak_dn": float(np.max(core)),
        "signal_dn": median - black_floor,
        "clipped_pct": clipped,
        "found_by": found_by,
    }


def light_label(cause: str | None) -> str | None:
    """The tester-facing label for a light cause: what to do about it (13 Oct)."""
    if cause in BRIGHT_LIGHT_CAUSES:
        return swing_exposure.TOO_BRIGHT_LABEL
    if cause in DARK_LIGHT_CAUSES:
        return swing_exposure.TOO_DARK_LABEL
    return None


def judge_light(
    frames: np.ndarray,
    black_floor: float,
    expected_ball: dict | None = None,
    zone_box=None,
) -> dict:
    """The one light rule both the pre-rung check and the swing verdict apply.

    ``cause`` is one of: ``ball_clipped``, ``ball_dark``, ``zone_dark`` (the club's
    background), ``zone_clipped_no_ball``, ``background_clipped`` (amber only) or
    ``ok``. The ball is judged first when it can be found. The zone is the
    tester's placement box in this mode (``zone_box``), else the fixed box (P7-15).
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
    """Whether a clip's light permits camera analysis, judged on the setup's ball (P7-8).

    The same ``judge_light`` as the ladder, on the clip's resting frames. Only the
    ball decides: its core clipped ``CLIPPED_MAX_PCT`` or less and
    ``BALL_MIN_SIGNAL_DN`` or more above black. A clipped background or a dark
    hitting zone does not withhold the camera (outdoors a sunny mat is always
    about 20 % clipped, and at the setup's own sun lock the zone reads too dark).
    No ball where the setup saw it is not eligible.
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

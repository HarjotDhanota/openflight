"""The resting ball's rest witnesses on the camera frames (shipped; C12).

Moved here unchanged from ``impact_location`` (research), which imports them back, so the
shipped club stage reads the same witnesses: the ball's top cap (its pixels unchanged from
the reference frames) and its rim (the top edge on the central columns, to a quarter pixel,
watched from just inside it). ``ball`` is anything with ``x``, ``y`` and ``diameter_px``.
"""

from __future__ import annotations

import math

import numpy as np

EDGE_COLUMNS_FRACTION = 0.35  # of the radius, either side of the ball's centre
EDGE_INNER_BAND = (0.75, 0.45)  # radii above the centre: the ball's own level
EDGE_OUTER_BAND = (1.6, 1.3)  # radii above the centre: what is behind the ball
EDGE_INNER_OFFSET_FRACTION = 0.1  # of the radius, below the resting edge
EDGE_INNER_OFFSET_MIN_PX = 1.0
EDGE_SEARCH_ABOVE_FRACTION = 1.2  # of the radius, above the resting edge
EDGE_SAMPLE_PX = 0.25
EDGE_MOVE_PX = 1.0
EDGE_MIN_CONTRAST_DN = 15.0
EDGE_MIN_CONTRAST_NOISE = 8.0
CAP_TOP_FRACTION = 0.25  # of the ball's height, from its top
CAP_RADIUS_FRACTION = 0.9
CAP_NOISE_SIGMA = 4.0
CAP_FLOOR_DN = 12.0
CAP_CHANGED_FRACTION = 0.5
REFERENCE_FRAMES_MAX = 8


def cap_mask(shape: tuple[int, int], ball) -> np.ndarray:
    radius = ball.diameter_px / 2.0
    grid_y, grid_x = np.mgrid[0 : shape[0], 0 : shape[1]]
    inside = (grid_x - ball.x) ** 2 + (grid_y - ball.y) ** 2 <= (CAP_RADIUS_FRACTION * radius) ** 2
    top = grid_y <= ball.y - radius + 2.0 * radius * CAP_TOP_FRACTION
    cap = inside & top
    if int(cap.sum()) < 3:
        cap = inside & (grid_y <= ball.y)
    return cap


def column_profile(image: np.ndarray, ball) -> np.ndarray:
    """Mean over the ball's central columns, one value per image row."""
    half = max(1.0, EDGE_COLUMNS_FRACTION * ball.diameter_px / 2.0)
    first = max(0, int(math.floor(ball.x - half)))
    last = min(image.shape[1] - 1, int(math.ceil(ball.x + half)))
    return image[:, first : last + 1].astype(np.float32).mean(axis=1)


def band_mean(profile: np.ndarray, ball, band: tuple[float, float]) -> float | None:
    radius = ball.diameter_px / 2.0
    top = max(0, int(math.floor(ball.y - band[0] * radius)))
    bottom = min(profile.size - 1, int(math.ceil(ball.y - band[1] * radius)))
    return float(profile[top : bottom + 1].mean()) if bottom >= top else None


def crossing(
    profile: np.ndarray, level: float, sign: float, start: float, stop: float
) -> float | None:
    """Walking UP from ``start`` (ball side) to ``stop``: the row the profile leaves the ball.

    None when ``start`` is not on the ball's side of ``level`` (the rim is
    gone) or the profile never crosses it inside the window.
    """
    rows = np.arange(profile.size, dtype=float)
    ys = np.arange(start, stop - 1e-9, -EDGE_SAMPLE_PX)
    if ys.size < 2 or ys[-1] < 0.0 or ys[0] > profile.size - 1:
        return None
    values = np.interp(ys, rows, profile)
    inside = sign * (values - level) > 0.0
    if not inside[0] or inside.all():
        return None
    index = int(np.argmin(inside))
    before, after = values[index - 1], values[index]
    fraction = (before - level) / (before - after) if before != after else 0.0
    return float(ys[index - 1] - fraction * EDGE_SAMPLE_PX)


def edge_witness(frames: np.ndarray, ball, reference_count: int) -> tuple[bool, list[float | None]]:
    """(available, the rim's rise in px per frame; None where the rim is gone)."""
    profiles = [column_profile(frame, ball) for frame in frames]
    stack = np.stack(profiles[:reference_count])
    reference = np.median(stack, axis=0)
    noise = float(np.median(stack.std(axis=0)))
    inner = band_mean(reference, ball, EDGE_INNER_BAND)
    outer = band_mean(reference, ball, EDGE_OUTER_BAND)
    if inner is None or outer is None:
        return False, []
    contrast = inner - outer
    if abs(contrast) < max(EDGE_MIN_CONTRAST_DN, EDGE_MIN_CONTRAST_NOISE * noise):
        return False, []
    level, sign = 0.5 * (inner + outer), math.copysign(1.0, contrast)
    radius = ball.diameter_px / 2.0
    rest = crossing(
        reference,
        level,
        sign,
        ball.y - EDGE_INNER_BAND[1] * radius,
        ball.y - EDGE_OUTER_BAND[0] * radius,
    )
    if rest is None:
        return False, []
    start = rest + max(EDGE_INNER_OFFSET_MIN_PX, EDGE_INNER_OFFSET_FRACTION * radius)
    stop = rest - EDGE_SEARCH_ABOVE_FRACTION * radius
    rises: list[float | None] = []
    for profile in profiles:
        edge = crossing(profile, level, sign, start, stop)
        rises.append(None if edge is None else rest - edge)
    return True, rises


def ball_at_rest(frames: np.ndarray, ball) -> tuple[np.ndarray, bool] | None:
    """Per frame, whether the resting ball is positively seen where it rested, or None when
    the witnesses cannot see it: its top cap unchanged from the reference frames and, when
    its rim has contrast, the rim at its resting row. A head covering only the ball's lower part leaves both; a covered or a
    departed ball fails both. Returns (at rest per frame, whether the rim witnessed)."""
    count = int(frames.shape[0])
    if frames.ndim != 3 or count < 4:
        return None
    reference_count = min(REFERENCE_FRAMES_MAX, max(3, count // 4))
    stack = frames[:reference_count].astype(np.float32)
    reference = np.median(stack, axis=0)
    cap = cap_mask(reference.shape, ball)
    if not cap.any():
        return None
    noise = float(np.median(stack.std(axis=0)[cap]))
    threshold = max(CAP_NOISE_SIGMA * noise, CAP_FLOOR_DN)
    changed = np.array(
        [
            float(np.mean(np.abs(frame.astype(np.float32)[cap] - reference[cap]) > threshold))
            for frame in frames
        ]
    )
    at_rest = changed < CAP_CHANGED_FRACTION
    available, rises = edge_witness(frames, ball, reference_count)
    if available:
        rim = np.array([rise is not None and abs(rise) < EDGE_MOVE_PX for rise in rises])
        at_rest &= rim
    return at_rest, bool(available)


def last_rest_before_departure(
    at_rest: np.ndarray, start: int, end: int, *, rim: bool
) -> int | None:
    """The last frame the ball rests in, within ``start``..``end``, after which it is never
    at rest again (C12): a ball the head covered reappears at rest, a departed one never
    does. With the rim witnessing as well as the cap, one frame of rest is the ball's (a
    head can uncover it for a single frame just before contact); with the cap alone, rest
    must persist two frames (the witnesses' rule), so a club glint resembling the ball for
    one frame is not its return."""
    seen = np.nonzero(at_rest if rim else (at_rest[:-1] & at_rest[1:]))[0]
    if len(seen) == 0:
        return None
    last = int(seen[-1]) + (0 if rim else 1)
    if not start <= last <= end or last + 1 >= len(at_rest):
        return None
    return last

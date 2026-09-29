"""EXPERIMENTAL, UNQUALIFIED: where on the face the ball was struck, heel to toe.

**No reading from this module has been scored against an independent strike
reference.** The method it ports agreed with one annotator's hand marks on the
same pixels (heel-toe r = 0.97, 2.6 mm rms over a 10.8 mm spread, August 2026
session, 467 fps); that is a consistency figure, not an accuracy one. Every
result carries ``label == "experimental_unqualified"``. The face centre is a
CONVENTION (the aligned outline's heel-toe midpoint plus 8 mm toe-ward) until a
foot-spray session calibrates it per club.

Ported, not re-invented, from ``research/session-metrics`` 37dd859
(``research/empirical_template/{build_template,align_and_carry}_v3.py``) and its
promotion on ``integration/cv-2026-09`` (``clubpose/head_outline.py``,
``clubpose/impact_zone.py``, ``clubpose/head_split.py``):

* head mask: background median, ``|diff| > max(4 sigma, 18 DN)``, 3x3 close and
  hole fill, largest moving component near the ball, head split from the shaft
  by the measured thickness gap (``split_head``);
* the departing ball is vetoed from the mask AFTER contact only;
* a SELF-BUILT occupancy template: landmarks seeded from each mask's own
  extremes, sole and heel edges from the occupancy fall-off, re-read over a few
  passes, toe landmark calibrated against the slow frames;
* placement by translation and roll only (FFT local IoU), at a scale pinned by
  a range model rather than fitted; IoU, search-boundary and track gates;
* the reading convention (face centre 8 mm toe-ward of the heel-toe midpoint),
  the toe-anchored reading and its physics gate.

New in v1, because the tester captures 120-288 fps rather than 467 fps:

* the contact frame is BALL-DEFINED -- the first frame in which the resting
  ball has moved -- not the acoustic trigger minus a fixed flight time (that
  contract was measured wrong by 0.5-4 ms on the August session);
* every pixel constant of the research is re-expressed in millimetres at the
  research's plate scale (3.5732 mm/px), so 320x200, 640x400 and 1280x800
  share one set;
* the scale is pinned by a club-speed depth model (the research used the IWR
  range ramp);
* the head at contact is INTERPOLATED between the frames bracketing contact in
  metric lateral coordinates (``docs/development/2026-09-28-impact-location-
  plan.md``), because at 120 fps only one frame lies either side of contact and
  the research's quadratic over f_c-6..f_c+1 has nothing to fit;
* an explicit per-shot uncertainty budget.

Orientation: the analysis works in ONE orientation -- unmirrored, toe to image
RIGHT, which is a right-handed golfer in an unmirrored capture. Mirrored saved
frames and left-handed golfers are both mapped into it by an explicit flip
(``analysis_flip``); the heel-toe offset is a length along the face and needs
no sign change, so + is toward the toe for either hand.
"""

# cv2's bindings are generated at import, so pylint cannot see any of them.
# pylint: disable=no-member,too-many-arguments,too-many-lines

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from functools import cached_property
from typing import Any, Callable, Iterable, Mapping, Sequence

import cv2
import numpy as np
from scipy import ndimage, signal

EXPERIMENTAL_LABEL = "experimental_unqualified"
HANDEDNESS = ("right", "left")

BALL_DIAMETER_MM = 42.67
BALL_RADIUS_MM = BALL_DIAMETER_MM / 2.0

# Nominal OV9281 focal lengths used by the tester (tester_server.FOCAL_PX_1X/2X).
# Uncalibrated; they enter only through the depth model's range-to-ball ratio.
NOMINAL_FOCAL_PX_1X = 933.3333
NOMINAL_FOCAL_PX_2X = 466.6667

# The research measured its pixel constants at this plate scale (320x200,
# 1581 mm, f = 466.7 px). Each one below is that measurement in millimetres.
RESEARCH_MM_PER_PX = 3.5732

# --- segmentation (research/session_metrics/swing_plane.py recipe) ----------
MOVING_SIGMA = 4.0
MOVING_FLOOR_DN = 18.0
MIN_COMPONENT_AREA_MM2 = 40.0 * RESEARCH_MM_PER_PX**2
MIN_HEAD_AREA_MM2 = 60.0 * RESEARCH_MM_PER_PX**2
MAX_HEAD_AREA_MM2 = 11_000.0
# split_head: shafts measured 2-3 px inscribed radius, heads 5-11.6 px.
HEAD_CORE_MM = 4.0 * RESEARCH_MM_PER_PX
SHAFT_MAX_MM = 3.5 * RESEARCH_MM_PER_PX
SHAFT_REACH_MM = 60.0 * RESEARCH_MM_PER_PX

# The analysis crops to a box around the resting ball. The golfer's feet stand
# ~0.8 m to the side and would otherwise be the largest moving component at
# 1280x800, where the whole golfer is in view.
ROI_HALF_WIDTH_MM = 330.0
ROI_ABOVE_MM = 170.0
ROI_BELOW_MM = 250.0

# --- the post-contact flying-ball veto -------------------------------------
BALL_BRIGHTNESS_FRACTION = 0.8
BALL_SEARCH_SPAN = 2.0  # teed-ball diameters either side of the tee
BALL_VETO_PAD_PX = 1.0

# --- the ball-defined contact frame ----------------------------------------
# Three witnesses on the resting ball. Before contact the head sits in front
# of the ball's lower part with its topline below the ball's top, so:
#   * the TOP EDGE (the ball's upper rim on its central columns, located to
#     a quarter pixel) rises or vanishes only when the ball leaves -- the
#     primary witness, sensitive to the first millimetres of flight;
#   * the TOP CAP (the upper quarter of the disk) changing, and
#   * the FLYING BALL (a ball-bright disk well clear of the tee)
# confirm it. A high toe strike seen from a low camera can still put the
# topline over the rim; see the docs' known limits.
EDGE_COLUMNS_FRACTION = 0.35  # of the radius, either side of the ball's centre
EDGE_INNER_BAND = (0.75, 0.45)  # radii above the centre: the ball's own level
EDGE_OUTER_BAND = (1.6, 1.3)  # radii above the centre: what is behind the ball
# At contact a soled iron's topline sits only 3-5 mm below the ball's top, so
# the rim is watched from just inside it.
EDGE_INNER_OFFSET_FRACTION = 0.1  # of the radius, below the resting edge
EDGE_INNER_OFFSET_MIN_PX = 1.0
EDGE_SEARCH_ABOVE_FRACTION = 1.2  # of the radius, above the resting edge
EDGE_SAMPLE_PX = 0.25
EDGE_MOVE_PX = 1.0
EDGE_MIN_CONTRAST_DN = 15.0
EDGE_MIN_CONTRAST_NOISE = 8.0
CAP_CONFIRM_FRAMES = (-1, 2)  # cap change this many frames either side of the edge
FLYING_CONFIRM_FRAMES = (0, 3)
CAP_TOP_FRACTION = 0.25  # of the ball's height, from its top
CAP_RADIUS_FRACTION = 0.9
CAP_NOISE_SIGMA = 4.0
CAP_FLOOR_DN = 12.0
CAP_CHANGED_FRACTION = 0.5
# A departed ball changes the whole cap; a toe rising over the rim only part.
CAP_FULL_FRACTION = 0.95
REFERENCE_FRAMES_MAX = 8
FLYING_MIN_SEPARATION_DIAMETERS = 0.75
MIN_CLIP_FRAMES = 6
# A resting ball whose apparent size puts it outside this camera range is a
# detector failure, not a ball (the tester's hitting zone is ~1-2.5 m).
BALL_RANGE_PLAUSIBLE_MM = (500.0, 4000.0)

# --- which frames are segmented --------------------------------------------
# The research aligned f_c-6..f_c+1 at 467 fps (12.8 ms before contact).
WINDOW_BEFORE_S = 0.012
# The background ends this long before contact (head_outline: 15 frames at
# 468 fps), so the approaching head never enters its median or its noise.
BACKGROUND_LEAD_S = 0.032
MIN_BACKGROUND_FRAMES = 3
# The two frames used for the reading may be at most this far apart: one frame
# gap at 120 fps, four at 450 fps.
MAX_BRACKET_S = 0.0090

# --- the template grid (mm, at the ball's range) -----------------------------
UPSAMPLE = 4  # template pixels per plate pixel
TEMPLATE_HALF_WIDTH_MM = 110.0
TEMPLATE_ABOVE_MM = 25.0
TEMPLATE_BELOW_MM = 80.0
OCCUPANCY_THRESHOLD = 0.5
CROP_TOP_MARGIN_MM = 2.0 * RESEARCH_MM_PER_PX
FALLBACK_EDGE_FRACTION = 0.5
SELF_BUILD_PASSES = 3
CONVERGENCE_MM = 0.5 * RESEARCH_MM_PER_PX
# Toe calibration: the research's 10 mm/frame at 467.6 fps, as a speed.
TOE_CALIBRATION_MAX_SPEED_MM_PER_MS = 10.0 * 467.6 / 1000.0
MIN_TOE_CALIBRATION_FRAMES = 4

# --- alignment ---------------------------------------------------------------
ROLL_LIMIT_DEG = 20.0
ROLL_STEP_DEG = 1.0
REFINE_ROLL_DEG = 1.0
REFINE_ROLL_STEP_DEG = 0.25
REFINE_SHIFT_PX = 1.0
REFINE_SHIFT_STEP_PX = 0.25
MAX_ANCHOR_RADIUS_MM = 55.0 * RESEARCH_MM_PER_PX
PATCH_PAD_PX = 2
IOU_GATE = 0.60
TRACK_GATE_MM = 12.0 * RESEARCH_MM_PER_PX
MIN_TRACK_FRAMES = 3
LANDMARKS = ("heel", "toe", "topline", "midpoint")

# --- the depth model that pins the scale ---------------------------------------
# OPS club speed is a measured, validated input when the session carries it.
# Without it this nominal mid-iron speed is ASSUMED and the result says so.
DEFAULT_CLUB_SPEED_MPS = 35.0
ASSUMED_CLUB_SPEED_RELATIVE_SIGMA = 0.15
MEASURED_CLUB_SPEED_RELATIVE_SIGMA = 0.03
# The head slows 10-25 % through impact (plan, 2026-09-28).
POST_IMPACT_SPEED_FRACTION = 0.83
MIN_DEPTH_RATIO = 0.3

# --- the reading ---------------------------------------------------------------
FACE_CENTRE_TOEWARD_MM = 8.0
MAX_IRON_BLADE_MM = 87.0  # official game-improvement 7-iron band (Mizuno/Srixon)
BLADE_GATE_MARGIN_MM = 15.0
TOE_SIDE_MARGIN_MM = 10.0

# --- the uncertainty budget (1 sigma) -------------------------------------------
# Landmark placement noise: research median 0.5-0.9 px, p90 1.1-1.7 px.
ALIGN_SIGMA_PX = 1.0
# With no witness of where inside the bracket contact fell, it is uniform.
UNIFORM_FRACTION_SIGMA = 1.0 / math.sqrt(12.0)
GIVEN_FRACTION_SIGMA = 0.1
BALL_DIAMETER_SIGMA_PX = 1.0
# The chord between the bracketing frames cuts inside the curved path. Arc
# radius and swing plane are the August 2026 session's measured medians; used
# for the diagnostic and the budget only, never applied as a correction.
ARC_RADIUS_MM = 1140.0
SWING_PLANE_DEG = 58.9

# The overlay's view around the resting ball.
OVERLAY_HALF_WIDTH_MM = 150.0
OVERLAY_ABOVE_MM = 110.0
OVERLAY_BELOW_MM = 90.0


# ---------------------------------------------------------------------------
# inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RestingBall:
    """The resting ball in the image: centre and apparent diameter, in pixels."""

    x: float
    y: float
    diameter_px: float


@dataclass(frozen=True)
class ImpactCamera:
    """Pinhole numbers the depth model needs. Nominal unless a caller knows better."""

    focal_px: float
    principal_point_px: tuple[float, float]
    source: str = "nominal_uncalibrated"

    @classmethod
    def nominal(cls, width: int, height: int) -> "ImpactCamera":
        """The tester's own uncalibrated assumption for a saved-image size."""
        focal = NOMINAL_FOCAL_PX_1X if width >= 1280 else NOMINAL_FOCAL_PX_2X
        return cls(focal, ((width - 1) / 2.0, (height - 1) / 2.0), "nominal_uncalibrated")


def analysis_flip(*, frames_mirrored: bool, handedness: str) -> bool:
    """Whether the saved frames must be flipped into the analysis orientation.

    The analysis orientation is unmirrored with the toe to image right, which
    is a right-handed golfer in an unmirrored capture. A mirrored save and a
    left-handed golfer each need one flip; both together need none.
    """
    if handedness not in HANDEDNESS:
        raise ValueError(f"handedness must be one of {HANDEDNESS}, got {handedness!r}")
    if not isinstance(frames_mirrored, bool):
        raise ValueError("frames_mirrored must be a boolean")
    return frames_mirrored != (handedness == "left")


# ---------------------------------------------------------------------------
# the ball-defined contact frame
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ContactEstimate:
    """The frames bracketing contact, as read off the resting ball itself.

    ``contact_frame`` is the first frame in which the resting ball has moved;
    contact happened after ``last_rest_frame`` and no later than it.
    """

    status: str
    reason: str
    contact_frame: int | None = None
    last_rest_frame: int | None = None
    source: str = "none"
    edge_frame: int | None = None
    cap_frame: int | None = None
    flying_ball_frame: int | None = None
    confirmed_by: str | None = None
    warning: str | None = None
    reference_frames: int = 0
    edge_available: bool = False
    edge_rise_px: tuple[float | None, ...] = ()
    cap_change: tuple[float, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """JSON-ready fields."""
        return {
            "status": self.status,
            "reason": self.reason,
            "contact_frame": self.contact_frame,
            "last_rest_frame": self.last_rest_frame,
            "source": self.source,
            "edge_frame": self.edge_frame,
            "cap_frame": self.cap_frame,
            "flying_ball_frame": self.flying_ball_frame,
            "confirmed_by": self.confirmed_by,
            "warning": self.warning,
            "reference_frames": self.reference_frames,
            "edge_available": self.edge_available,
            "edge_rise_px": [None if v is None else round(v, 2) for v in self.edge_rise_px],
            "cap_change": [round(v, 3) for v in self.cap_change],
        }


def _disk_kernel(radius: float) -> np.ndarray:
    half = max(1, int(round(radius)))
    grid_y, grid_x = np.mgrid[-half : half + 1, -half : half + 1]
    kernel = ((grid_x**2 + grid_y**2) <= radius**2).astype(np.float32)
    return kernel / max(kernel.sum(), 1.0)


def flying_ball(
    frame: np.ndarray, ball: RestingBall, background: np.ndarray
) -> tuple[float, float, float] | None:
    """The brightest ball-sized disk above the tee row, or None (fail closed).

    Ported from ``head_outline.flying_ball``: accepted only when at least
    ``BALL_BRIGHTNESS_FRACTION`` as bright as the resting ball on the
    background. Returns ``(x, y, veto_radius)``.
    """
    radius = ball.diameter_px / 2.0
    kernel = _disk_kernel(radius)
    response = cv2.filter2D(frame.astype(np.float32), -1, kernel, borderType=cv2.BORDER_REPLICATE)
    reference = cv2.filter2D(
        background.astype(np.float32), -1, kernel, borderType=cv2.BORDER_REPLICATE
    )
    row = min(max(int(round(ball.y)), 0), reference.shape[0] - 1)
    column = min(max(int(round(ball.x)), 0), reference.shape[1] - 1)
    teed = float(reference[row, column])
    grid_y, grid_x = np.mgrid[0 : response.shape[0], 0 : response.shape[1]]
    window = (np.abs(grid_x - ball.x) <= BALL_SEARCH_SPAN * ball.diameter_px) & (
        grid_y <= ball.y + radius
    )
    scored = np.where(window, response, -1.0)
    flat = int(np.argmax(scored))
    if float(scored.ravel()[flat]) < BALL_BRIGHTNESS_FRACTION * teed:
        return None
    found_row, found_column = divmod(flat, response.shape[1])
    return (float(found_column), float(found_row), radius + BALL_VETO_PAD_PX)


def _cap_mask(shape: tuple[int, int], ball: RestingBall) -> np.ndarray:
    radius = ball.diameter_px / 2.0
    grid_y, grid_x = np.mgrid[0 : shape[0], 0 : shape[1]]
    inside = (grid_x - ball.x) ** 2 + (grid_y - ball.y) ** 2 <= (CAP_RADIUS_FRACTION * radius) ** 2
    top = grid_y <= ball.y - radius + 2.0 * radius * CAP_TOP_FRACTION
    cap = inside & top
    if int(cap.sum()) < 3:
        cap = inside & (grid_y <= ball.y)
    return cap


def _column_profile(image: np.ndarray, ball: RestingBall) -> np.ndarray:
    """Mean over the ball's central columns, one value per image row."""
    half = max(1.0, EDGE_COLUMNS_FRACTION * ball.diameter_px / 2.0)
    first = max(0, int(math.floor(ball.x - half)))
    last = min(image.shape[1] - 1, int(math.ceil(ball.x + half)))
    return image[:, first : last + 1].astype(np.float32).mean(axis=1)


def _band_mean(profile: np.ndarray, ball: RestingBall, band: tuple[float, float]) -> float | None:
    radius = ball.diameter_px / 2.0
    top = max(0, int(math.floor(ball.y - band[0] * radius)))
    bottom = min(profile.size - 1, int(math.ceil(ball.y - band[1] * radius)))
    return float(profile[top : bottom + 1].mean()) if bottom >= top else None


def _crossing(
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


def _edge_witness(
    frames: np.ndarray, ball: RestingBall, reference_count: int
) -> tuple[bool, list[float | None]]:
    """(available, the rim's rise in px per frame; None where the rim is gone)."""
    profiles = [_column_profile(frame, ball) for frame in frames]
    stack = np.stack(profiles[:reference_count])
    reference = np.median(stack, axis=0)
    noise = float(np.median(stack.std(axis=0)))
    inner = _band_mean(reference, ball, EDGE_INNER_BAND)
    outer = _band_mean(reference, ball, EDGE_OUTER_BAND)
    if inner is None or outer is None:
        return False, []
    contrast = inner - outer
    if abs(contrast) < max(EDGE_MIN_CONTRAST_DN, EDGE_MIN_CONTRAST_NOISE * noise):
        return False, []
    level, sign = 0.5 * (inner + outer), math.copysign(1.0, contrast)
    radius = ball.diameter_px / 2.0
    rest = _crossing(
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
        edge = _crossing(profile, level, sign, start, stop)
        rises.append(None if edge is None else rest - edge)
    return True, rises


def _disk_mean(image: np.ndarray, centre: Sequence[float], ball: RestingBall) -> float:
    radius = max(1.0, 0.5 * ball.diameter_px * CAP_RADIUS_FRACTION)
    x0 = max(0, int(math.floor(centre[0] - radius)))
    x1 = min(image.shape[1], int(math.ceil(centre[0] + radius)) + 1)
    y0 = max(0, int(math.floor(centre[1] - radius)))
    y1 = min(image.shape[0], int(math.ceil(centre[1] + radius)) + 1)
    patch = image[y0:y1, x0:x1].astype(np.float32)
    return float(patch.mean()) if patch.size else 0.0


def _first_persistent(flags: Sequence[bool], start: int) -> int | None:
    count = len(flags)
    for index in range(start, count):
        if flags[index] and (index == count - 1 or flags[index + 1]):
            return index
    return None


def _within(frame: int | None, anchor: int, window: tuple[int, int]) -> bool:
    return frame is not None and anchor + window[0] <= frame <= anchor + window[1]


def find_contact(  # pylint: disable=too-many-branches
    frames: np.ndarray, ball: RestingBall
) -> ContactEstimate:
    """The first frame in which the resting ball has moved. Fails closed.

    The top-edge witness decides when the ball's rim has contrast against
    what is behind it, but only once the cap or the flying ball confirms it
    (a club passing behind an untouched ball can move the rim, never the
    cap). Without a usable rim the cap decides. The flying ball alone is
    accepted last. Each witness's first persistent frame is reported.
    """
    count = int(frames.shape[0])
    if frames.ndim != 3 or count < MIN_CLIP_FRAMES:
        return ContactEstimate("rejected", f"clip_has_{count}_frames_need_{MIN_CLIP_FRAMES}")
    reference_count = min(REFERENCE_FRAMES_MAX, max(3, count // 4))
    reference_stack = frames[:reference_count].astype(np.float32)
    reference = np.median(reference_stack, axis=0)

    edge_available, rises = _edge_witness(frames, ball, reference_count)
    edge_frame = (
        _first_persistent([r is None or abs(r) >= EDGE_MOVE_PX for r in rises], reference_count)
        if edge_available
        else None
    )

    cap = _cap_mask(reference.shape, ball)
    noise = float(np.median(reference_stack.std(axis=0)[cap])) if cap.any() else 0.0
    threshold = max(CAP_NOISE_SIGMA * noise, CAP_FLOOR_DN)
    change = np.zeros(count)
    for index in range(count):
        diff = np.abs(frames[index].astype(np.float32)[cap] - reference[cap])
        change[index] = float(np.mean(diff > threshold)) if diff.size else 0.0
    cap_frame = _first_persistent(list(change >= CAP_CHANGED_FRACTION), reference_count)

    flying_frame: int | None = None
    for index in range(reference_count, count):
        found = flying_ball(frames[index], ball, reference)
        if found is None:
            continue
        separation = math.hypot(found[0] - ball.x, found[1] - ball.y)
        # A departing ball is NEW where it is found: a highlight that was
        # already there before the swing is not the ball.
        new = _disk_mean(frames[index], found, ball) - _disk_mean(reference, found, ball)
        if separation >= FLYING_MIN_SEPARATION_DIAMETERS * ball.diameter_px and new >= CAP_FLOOR_DN:
            flying_frame = index
            break

    common = {
        "edge_frame": edge_frame,
        "cap_frame": cap_frame,
        "flying_ball_frame": flying_frame,
        "reference_frames": reference_count,
        "edge_available": edge_available,
        "edge_rise_px": tuple(rises),
        "cap_change": tuple(float(v) for v in change),
    }
    contact: int | None = None
    source, confirmed = "none", None
    if edge_frame is not None:
        if _within(cap_frame, edge_frame, CAP_CONFIRM_FRAMES):
            contact, source, confirmed = edge_frame, "top_edge", "cap"
        elif _within(flying_frame, edge_frame, FLYING_CONFIRM_FRAMES):
            contact, source, confirmed = edge_frame, "top_edge", "flying_ball"
    if contact is None and cap_frame is not None:
        if _within(flying_frame, cap_frame, FLYING_CONFIRM_FRAMES):
            contact, source, confirmed = cap_frame, "cap_departure", "flying_ball"
        elif not edge_available:
            contact, source = cap_frame, "cap_departure"
    if contact is None:
        reason = (
            "ball_never_moved"
            if cap_frame is None and edge_frame is None
            else "ball_departure_witnesses_disagree"
        )
        return ContactEstimate("rejected", reason, **common)
    if contact <= reference_count:
        return ContactEstimate(
            "rejected",
            "ball_moved_before_the_reference_frames_ended",
            contact_frame=contact,
            source=source,
            **common,
        )
    warning = None
    if (
        source == "top_edge"
        and rises[contact] is None
        and contact + 1 < count
        and change[contact] < CAP_FULL_FRACTION <= change[contact + 1]
    ):
        # The rim vanished under a partly changed cap and the cap went whole
        # only a frame later: either the ball left late in the gap, or the
        # toe rose over a ball that had not moved yet. Not decidable here.
        warning = "rim_lost_under_a_partial_cap_change_contact_may_be_one_frame_later"
    return ContactEstimate(
        "ok",
        "ok",
        contact_frame=contact,
        last_rest_frame=contact - 1,
        source=source,
        confirmed_by=confirmed,
        warning=warning,
        **common,
    )


# ---------------------------------------------------------------------------
# one shot, prepared for the template and the reading
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MaskReport:
    """Per-frame head masks and, for every frame without one, why."""

    masks: dict[int, np.ndarray]
    reasons: dict[int, str]
    ball_vetoes: dict[int, tuple[float, float, float]]


@dataclass(frozen=True)
class PreparedShot:  # pylint: disable=too-many-instance-attributes
    """One shot in the analysis orientation, cropped to the box around the ball."""

    frames: np.ndarray
    ball: RestingBall
    fps: float
    camera: ImpactCamera
    handedness: str
    flipped: bool
    roi_origin: tuple[int, int]
    full_size: tuple[int, int]
    contact: ContactEstimate
    contact_fraction: float
    contact_fraction_source: str
    club_speed_mps: float
    club_speed_source: str
    ball_range_mm: float
    ball_range_source: str
    window: tuple[int, ...] = ()
    masks: MaskReport | None = None
    toe_speeds_mm_per_ms: dict[int, float] = field(default_factory=dict)
    name: str = ""
    club: str = ""

    @property
    def plate_mm_per_px(self) -> float:
        """Millimetres per pixel at the resting ball's range."""
        return self.ball_range_mm / self.camera.focal_px

    @property
    def contact_time(self) -> float | None:
        """Fractional frame of contact: the last rest frame plus the fraction."""
        if self.contact.last_rest_frame is None:
            return None
        return self.contact.last_rest_frame + self.contact_fraction

    def depth_ratio(
        self, frame: float, *, fraction: float | None = None, speed: float | None = None
    ) -> float:
        """Head range over ball range on ``frame``, from the club-speed model."""
        last = self.contact.last_rest_frame
        if last is None:
            return 1.0
        contact = last + (self.contact_fraction if fraction is None else fraction)
        speed_mps = self.club_speed_mps if speed is None else speed
        elapsed_ms = (frame - contact) * 1000.0 / self.fps
        if elapsed_ms > 0.0:
            speed_mps *= POST_IMPACT_SPEED_FRACTION
        depth = self.ball_range_mm + speed_mps * elapsed_ms  # m/s == mm/ms
        return max(MIN_DEPTH_RATIO, depth / self.ball_range_mm)

    def mm_per_px(self, frame: float) -> float:
        """Millimetres per pixel at the head's own range on ``frame``."""
        return self.plate_mm_per_px * self.depth_ratio(frame)

    def to_original(self, x: float, y: float) -> tuple[float, float]:
        """Analysis (cropped, canonical) pixel -> the saved frame's pixel."""
        full_x = x + self.roi_origin[0]
        full_y = y + self.roi_origin[1]
        if self.flipped:
            full_x = self.full_size[0] - 1 - full_x
        return float(full_x), float(full_y)


def _split_head(component: np.ndarray, mm_per_px: float) -> tuple[np.ndarray, np.ndarray] | None:
    """``clubpose.head_split.split_head`` with its measured radii in millimetres."""
    core_px = HEAD_CORE_MM / mm_per_px
    shaft_px = SHAFT_MAX_MM / mm_per_px
    reach_px = SHAFT_REACH_MM / mm_per_px
    binary = (component > 0).astype(np.uint8)
    distance = cv2.distanceTransform(binary, cv2.DIST_L2, 5)
    core = (distance >= core_px).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(core, 8)
    if count <= 1:
        return None
    head_core = (labels == 1 + int(np.argmax(stats[1:, 4]))).astype(np.uint8)
    reach = cv2.distanceTransform(1 - head_core, cv2.DIST_L2, 5)
    far = binary.copy()
    far[reach <= reach_px] = 0
    far[distance >= shaft_px] = 0
    markers = np.zeros(binary.shape, np.int32)
    markers[binary == 0] = 1
    markers[head_core > 0] = 2
    markers[far > 0] = 3
    if not (markers == 3).any():
        return binary, np.zeros_like(binary)
    relief = (255.0 * (1.0 - distance / max(float(distance.max()), 1e-6))).astype(np.uint8)
    cv2.watershed(cv2.cvtColor(relief, cv2.COLOR_GRAY2BGR), markers)
    head = ((markers == 2) & (binary > 0)).astype(np.uint8)
    shaft = ((markers == 3) & (binary > 0)).astype(np.uint8)
    ridge = (markers == -1) & (binary > 0)
    if ridge.any():
        to_head = cv2.distanceTransform(1 - head, cv2.DIST_L2, 5)
        to_shaft = cv2.distanceTransform(1 - np.clip(shaft, 0, 1), cv2.DIST_L2, 5)
        head[ridge & (to_head <= to_shaft)] = 1
        shaft[ridge & (to_head > to_shaft)] = 1
    return head, shaft


def _moving_masks(
    frames: np.ndarray, background: slice, wanted: Iterable[int]
) -> dict[int, np.ndarray]:
    stack = frames[background].astype(np.float32)
    median = np.median(stack, axis=0)
    threshold = np.maximum(MOVING_SIGMA * stack.std(axis=0), MOVING_FLOOR_DN)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    out: dict[int, np.ndarray] = {}
    for index in wanted:
        raw = np.abs(frames[index].astype(np.float32) - median) > threshold
        closed = cv2.morphologyEx(raw.astype(np.uint8), cv2.MORPH_CLOSE, kernel)
        out[index] = ndimage.binary_fill_holes(closed > 0)
    return out


def head_masks(shot: PreparedShot, window: Sequence[int], background: slice) -> MaskReport:
    """The head half of the largest moving component near the ball, per frame.

    Ported from ``head_outline.head_masks``: no veto before contact (the
    stationary ball is absent from the moving mask while the head sits on
    it); the departing ball is vetoed on frames after contact.
    """
    frames = shot.frames
    moving = _moving_masks(frames, background, window)
    background_image = np.median(frames[background].astype(np.float32), axis=0)
    grid_y, grid_x = np.mgrid[0 : frames.shape[1], 0 : frames.shape[2]]
    masks: dict[int, np.ndarray] = {}
    reasons: dict[int, str] = {}
    vetoes: dict[int, tuple[float, float, float]] = {}
    last_rest = shot.contact.last_rest_frame
    for index in window:
        scale = shot.mm_per_px(index)
        current = moving[index].copy()
        if last_rest is not None and index > last_rest:
            found = flying_ball(frames[index], shot.ball, background_image)
            if found is not None:
                x, y, radius = found
                current[((grid_x - x) ** 2 + (grid_y - y) ** 2) <= radius**2] = False
                vetoes[index] = found
        count, labels, stats, _ = cv2.connectedComponentsWithStats(current.astype(np.uint8), 8)
        minimum = MIN_COMPONENT_AREA_MM2 / scale**2
        best_area, best_label = 0, -1
        for label in range(1, count):
            area = int(stats[label, cv2.CC_STAT_AREA])
            if area >= minimum and area > best_area:
                best_area, best_label = area, label
        if best_label < 0:
            reasons[index] = "no_moving_component_near_the_ball"
            continue
        split = _split_head((labels == best_label).astype(np.uint8), scale)
        if split is None:
            reasons[index] = "no_body_thick_enough_to_be_a_clubhead"
            continue
        head = split[0] > 0
        area_mm2 = float(head.sum()) * scale**2
        if area_mm2 < MIN_HEAD_AREA_MM2:
            reasons[index] = f"head_mask_only_{area_mm2:.0f}_mm2"
            continue
        if area_mm2 > MAX_HEAD_AREA_MM2:
            reasons[index] = f"head_mask_{area_mm2:.0f}_mm2_merged_with_turf"
            continue
        masks[index] = head
    return MaskReport(masks=masks, reasons=reasons, ball_vetoes=vetoes)


def toe_speeds(shot: PreparedShot, report: MaskReport) -> dict[int, float]:
    """Toe-edge image speed per frame in mm per MILLISECOND (the blur proxy).

    ``head_outline.mask_speeds`` in time rather than per frame, so one
    threshold serves every frame rate. The toe edge, not the centroid: the
    centroid jumps 20+ mm at contact from shape changes alone.
    """
    edges: dict[int, float] = {}
    for frame, mask in report.masks.items():
        columns = np.flatnonzero(mask.any(axis=0))
        if columns.size:
            edges[frame] = float(columns.max())
    ordered = sorted(edges)
    gathered: dict[int, list[float]] = {}
    for first, second in zip(ordered, ordered[1:]):
        gap_ms = (second - first) * 1000.0 / shot.fps
        step_px = abs(edges[second] - edges[first])
        gathered.setdefault(first, []).append(step_px * shot.mm_per_px(first) / gap_ms)
        gathered.setdefault(second, []).append(step_px * shot.mm_per_px(second) / gap_ms)
    return {frame: float(np.mean(values)) for frame, values in gathered.items()}


def prepare_shot(  # pylint: disable=too-many-branches
    frames: np.ndarray,
    ball: RestingBall,
    *,
    fps: float,
    camera: ImpactCamera | None = None,
    handedness: str = "right",
    frames_mirrored: bool = False,
    club_speed_mps: float | None = None,
    ball_range_mm: float | None = None,
    contact_fraction: float | None = None,
    name: str = "",
    club: str = "",
) -> PreparedShot:
    """Orient, crop, find contact and segment one shot. Never raises on data.

    ``frames`` and ``ball`` are in the SAVED orientation. ``camera`` defaults
    to the tester's nominal pinhole for the frame size. ``ball_range_mm``
    (camera to ball centre) defaults to the ball's apparent size through the
    focal length. ``contact_fraction`` is where inside the bracketing frame
    gap contact fell; without a witness for it, 0.5.
    """
    if frames.ndim != 3:
        raise ValueError("frames must have shape (n, height, width)")
    if not math.isfinite(fps) or fps <= 0.0:
        raise ValueError(f"fps must be positive, got {fps}")
    if ball.diameter_px <= 0.0:
        raise ValueError("ball diameter must be positive")
    height, width = int(frames.shape[1]), int(frames.shape[2])
    camera = camera or ImpactCamera.nominal(width, height)
    flip = analysis_flip(frames_mirrored=frames_mirrored, handedness=handedness)
    principal_x, principal_y = camera.principal_point_px
    ball_x = ball.x
    if flip:
        frames = frames[:, :, ::-1]
        ball_x = width - 1 - ball.x
        principal_x = width - 1 - principal_x

    if ball_range_mm is None:
        range_mm, range_source = camera.focal_px * BALL_DIAMETER_MM / ball.diameter_px, "ball_size"
    else:
        range_mm, range_source = float(ball_range_mm), "given"
    plate = range_mm / camera.focal_px
    x0 = max(0, int(math.floor(ball_x - ROI_HALF_WIDTH_MM / plate)))
    x1 = min(width, int(math.ceil(ball_x + ROI_HALF_WIDTH_MM / plate)) + 1)
    y0 = max(0, int(math.floor(ball.y - ROI_ABOVE_MM / plate)))
    y1 = min(height, int(math.ceil(ball.y + ROI_BELOW_MM / plate)) + 1)
    cropped = np.ascontiguousarray(frames[:, y0:y1, x0:x1])
    local_ball = RestingBall(ball_x - x0, ball.y - y0, ball.diameter_px)
    local_camera = replace(camera, principal_point_px=(principal_x - x0, principal_y - y0))

    if club_speed_mps is None:
        speed, speed_source = DEFAULT_CLUB_SPEED_MPS, "assumed_default"
    else:
        speed, speed_source = float(club_speed_mps), "given"
    if contact_fraction is None:
        fraction, fraction_source = 0.5, "uniform_midpoint"
    else:
        fraction, fraction_source = float(contact_fraction), "given"
        if not 0.0 <= fraction <= 1.0:
            raise ValueError("contact_fraction must lie in [0, 1]")

    low, high = BALL_RANGE_PLAUSIBLE_MM
    if low <= range_mm <= high:
        contact = find_contact(cropped, local_ball)
    else:
        contact = ContactEstimate(
            "rejected", f"resting_ball_range_{range_mm:.0f}_mm_implausible_check_the_ball"
        )
    shot = PreparedShot(
        frames=cropped,
        ball=local_ball,
        fps=float(fps),
        camera=local_camera,
        handedness=handedness,
        flipped=flip,
        roi_origin=(x0, y0),
        full_size=(width, height),
        contact=contact,
        contact_fraction=fraction,
        contact_fraction_source=fraction_source,
        club_speed_mps=speed,
        club_speed_source=speed_source,
        ball_range_mm=range_mm,
        ball_range_source=range_source,
        name=name,
        club=club,
    )
    if contact.status != "ok" or contact.last_rest_frame is None:
        return shot
    last_rest = contact.last_rest_frame
    before = max(1, int(round(WINDOW_BEFORE_S * fps)))
    first = max(0, last_rest - before)
    window = tuple(range(first, min(cropped.shape[0], last_rest + 2)))
    stop = min(first, last_rest + 1 - int(math.ceil(BACKGROUND_LEAD_S * fps)))
    if stop < MIN_BACKGROUND_FRAMES:
        return replace(
            shot,
            window=window,
            contact=replace(
                contact, status="rejected", reason="no_background_frames_before_the_swing"
            ),
        )
    shot = replace(shot, window=window)
    report = head_masks(shot, window, slice(0, stop))
    return replace(shot, masks=report, toe_speeds_mm_per_ms=toe_speeds(shot, report))


# ---------------------------------------------------------------------------
# the self-built template
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HeadTemplate:  # pylint: disable=too-many-instance-attributes
    """One club's empirical outline on a millimetre grid at the ball's range."""

    outline: np.ndarray
    occupancy: np.ndarray
    landmarks: dict[str, np.ndarray]
    grid_mm_per_px: float
    origin: tuple[int, int]
    n_samples: int
    source: str = "self-built"
    heel_from_data: bool = True
    movement_mm: tuple[float, ...] = ()
    converged: bool = False
    toe_calibrated: bool = False
    toe_calibration_frames: int = 0
    toe_overhang_mm: float | None = None
    club: str = ""

    @cached_property
    def outline_u8(self) -> np.ndarray:
        """The outline as the uint8 image every rasterisation warps."""
        return self.outline.astype(np.uint8)

    @cached_property
    def hull_points(self) -> np.ndarray:
        """Homogeneous (3, N) convex-hull pixels: a rotated outline's extent is theirs."""
        rows, cols = np.nonzero(self.outline)
        points = np.stack([cols, rows], axis=1).astype(np.int32)
        hull = cv2.convexHull(points).reshape(-1, 2).astype(float)
        return np.vstack([hull[:, 0], hull[:, 1], np.ones(len(hull))])

    def extent_mm(self) -> tuple[float, float]:
        """(width, height) of the outline's bounding box."""
        rows, cols = np.nonzero(self.outline)
        if rows.size == 0:
            return (0.0, 0.0)
        return (
            float(np.ptp(cols) + 1) * self.grid_mm_per_px,
            float(np.ptp(rows) + 1) * self.grid_mm_per_px,
        )

    def summary(self) -> dict[str, Any]:
        """JSON-ready description (no arrays)."""
        width, height = self.extent_mm()
        span = self.landmarks["toe"] - self.landmarks["heel"]
        return {
            "source": self.source,
            "club": self.club,
            "n_samples": self.n_samples,
            "grid_mm_per_px": self.grid_mm_per_px,
            "bbox_mm": [round(width, 1), round(height, 1)],
            "heel_to_toe_landmarks_mm": round(float(np.hypot(*span)) * self.grid_mm_per_px, 1),
            "heel_from_data": self.heel_from_data,
            "movement_mm": [round(v, 3) for v in self.movement_mm],
            "converged": self.converged,
            "toe_calibrated": self.toe_calibrated,
            "toe_calibration_frames": self.toe_calibration_frames,
            "toe_overhang_mm": self.toe_overhang_mm,
        }


@dataclass(frozen=True)
class TemplateSample:
    """One head mask the template may be built from."""

    shot: PreparedShot
    frame: int
    mask: np.ndarray
    landmarks: dict[str, np.ndarray] | None


def bootstrap_landmarks(mask: np.ndarray) -> dict[str, np.ndarray] | None:
    """Heel = leftmost mask pixel, toe = rightmost, topline = top at their midpoint.

    ``head_outline.bootstrap_landmarks``: a crude seed that the self-build
    passes then re-read off the averaged template.
    """
    rows, cols = np.nonzero(mask)
    if rows.size < 8:
        return None
    left, right = int(cols.min()), int(cols.max())
    middle = int(round(0.5 * (left + right)))
    column = mask[:, middle]
    if not column.any():
        return None
    return {
        "heel": np.array([float(left), float(rows[cols == left].mean())]),
        "toe": np.array([float(right), float(rows[cols == right].mean())]),
        "topline": np.array([float(middle), float(np.flatnonzero(column)[0])]),
    }


def _grid(grid_mm_per_px: float) -> tuple[tuple[int, int], tuple[int, int]]:
    half = int(math.ceil(TEMPLATE_HALF_WIDTH_MM / grid_mm_per_px))
    above = int(math.ceil(TEMPLATE_ABOVE_MM / grid_mm_per_px))
    below = int(math.ceil(TEMPLATE_BELOW_MM / grid_mm_per_px))
    return (above + below + 1, 2 * half + 1), (half, above)


def _sample_to_grid(
    sample: TemplateSample,
    grid_mm_per_px: float,
    shape: tuple[int, int],
    origin: tuple[int, int],
    *,
    heel_open: bool,
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """One mask resampled onto the grid, cropped at the top and the toe only."""
    assert sample.landmarks is not None
    heel, toe, topline = (
        np.asarray(sample.landmarks[k], dtype=float) for k in ("heel", "toe", "topline")
    )
    anchor = np.array([0.5 * (heel[0] + toe[0]), topline[1]])
    step = grid_mm_per_px / sample.shot.mm_per_px(sample.frame)
    grid_y, grid_x = np.mgrid[0 : shape[0], 0 : shape[1]].astype(np.float32)
    map_x = (anchor[0] + (grid_x - origin[0]) * step).astype(np.float32)
    map_y = (anchor[1] + (grid_y - origin[1]) * step).astype(np.float32)
    warped = cv2.remap(
        sample.mask.astype(np.float32), map_x, map_y, cv2.INTER_LINEAR, borderValue=0.0
    )

    def to_grid(point: np.ndarray) -> np.ndarray:
        return (point - anchor) / step + np.asarray(origin, dtype=float)

    placed = {
        "heel": to_grid(heel),
        "toe": to_grid(toe),
        "topline": to_grid(topline),
        "midpoint": np.asarray(origin, dtype=float),
    }
    keep = (grid_x <= placed["toe"][0]) & (
        grid_y - origin[1] >= -CROP_TOP_MARGIN_MM / grid_mm_per_px
    )
    if not heel_open:
        keep = keep & (grid_x >= placed["heel"][0])
    return np.where(keep, warped, 0.0), placed


def _column_runs(above: np.ndarray) -> np.ndarray:
    keep = np.zeros_like(above)
    for column in range(above.shape[1]):
        rows = np.flatnonzero(above[:, column])
        if rows.size == 0:
            continue
        top = bottom = int(rows[0])
        while bottom + 1 < above.shape[0] and above[bottom + 1, column]:
            bottom += 1
        keep[top : bottom + 1, column] = True
    return keep


def _row_runs(above: np.ndarray) -> np.ndarray:
    keep = np.zeros_like(above)
    for row in range(above.shape[0]):
        columns = np.flatnonzero(above[row])
        if columns.size == 0:
            continue
        right = left = int(columns[-1])
        while left - 1 >= 0 and above[row, left - 1]:
            left -= 1
        keep[row, left : right + 1] = True
    return keep


def outline_from_occupancy(occupancy: np.ndarray, *, heel_from_data: bool) -> np.ndarray:
    """Column runs (the sole) intersected with row runs (the heel)."""
    above = occupancy >= OCCUPANCY_THRESHOLD
    columns = _column_runs(above)
    return columns & _row_runs(above) if heel_from_data else columns


def build_template(
    samples: Sequence[TemplateSample],
    grid_mm_per_px: float,
    *,
    heel_from_data: bool = True,
    club: str = "",
) -> HeadTemplate | None:
    """Mean occupancy of the samples; sole and heel edges from its fall-off.

    Pre-registered fallback (research iteration 3): if more than half the
    outline's rows still reach the grid's left edge, the heel never fell
    through 0.5, and the heel reverts to a crop at the landmark.
    """
    usable = [s for s in samples if s.landmarks is not None]
    if not usable:
        return None
    shape, origin = _grid(grid_mm_per_px)
    stack: list[np.ndarray] = []
    points: dict[str, list[np.ndarray]] = {}
    for sample in usable:
        warped, placed = _sample_to_grid(
            sample, grid_mm_per_px, shape, origin, heel_open=heel_from_data
        )
        stack.append(warped)
        for name, point in placed.items():
            points.setdefault(name, []).append(point)
    occupancy = np.mean(np.stack(stack), axis=0)
    outline = outline_from_occupancy(occupancy, heel_from_data=heel_from_data)
    if not outline.any():
        return None
    rows = outline.any(axis=1)
    left_edge = float(outline[:, 0][rows].mean()) if int(rows.sum()) else 0.0
    if heel_from_data and left_edge > FALLBACK_EDGE_FRACTION:
        return build_template(usable, grid_mm_per_px, heel_from_data=False, club=club)
    return HeadTemplate(
        outline=outline,
        occupancy=occupancy,
        landmarks={name: np.mean(np.stack(p), axis=0) for name, p in points.items()},
        grid_mm_per_px=grid_mm_per_px,
        origin=origin,
        n_samples=len(stack),
        heel_from_data=heel_from_data,
        club=club,
    )


def template_samples(shots: Iterable[PreparedShot]) -> list[TemplateSample]:
    """Every segmented head mask of every prepared shot, seeded from its extremes."""
    samples = []
    for shot in shots:
        if shot.masks is None:
            continue
        for frame, mask in sorted(shot.masks.masks.items()):
            samples.append(TemplateSample(shot, frame, mask, bootstrap_landmarks(mask)))
    return samples


def build_self_template(
    shots: Sequence[PreparedShot],
    *,
    club: str = "",
    passes: int = SELF_BUILD_PASSES,
    toe_calibration: bool = True,
) -> tuple[HeadTemplate | None, str]:
    """Build a club's outline from the session's OWN swings, with no marks.

    ``head_outline.build_template_self_built``: seed each sample from its own
    extremes, then re-read every sample's landmarks off the template placed on
    it, for up to ``passes`` builds; last, pull the toe landmark in by its
    median overhang past the slow frames' own toes. Returns the template (or
    None) and a reason.
    """
    samples = template_samples(shots)
    if not samples:
        return None, "no_head_masks_to_build_a_template_from"
    plates = [s.shot.plate_mm_per_px for s in samples]
    grid = float(np.median(plates)) / UPSAMPLE
    template = build_template(samples, grid, club=club)
    if template is None:
        return None, "template_did_not_build"
    movement: list[float] = []
    for _ in range(max(0, passes - 1)):
        refined = []
        for sample in samples:
            alignment = align_frame(sample.shot, template, sample.frame, sample.mask)
            if alignment is None:
                continue
            refined.append(
                replace(
                    sample,
                    landmarks={k: alignment.landmarks[k] for k in ("heel", "toe", "topline")},
                )
            )
        rebuilt = build_template(refined, grid, club=club) if refined else None
        if rebuilt is None:
            break
        moved = max(
            float(np.hypot(*(rebuilt.landmarks[name] - template.landmarks[name])))
            for name in LANDMARKS
        )
        movement.append(moved * grid)
        template, samples = rebuilt, refined
        if movement[-1] <= CONVERGENCE_MM:
            break
    template = replace(
        template,
        movement_mm=tuple(movement),
        converged=bool(movement) and movement[-1] <= CONVERGENCE_MM,
    )
    if toe_calibration:
        template = calibrate_toe_landmark(template, samples)
    return template, "ok"


def calibrate_toe_landmark(
    template: HeadTemplate, samples: Sequence[TemplateSample]
) -> HeadTemplate:
    """Pull the toe landmark in by its median overhang past the SLOW masks' toes.

    ``head_outline.calibrate_toe_landmark``: a mask is the head smeared over
    the exposure, and the averaged template inherits the smear at its toe.
    Stands down (template untouched, ``toe_calibrated`` False) with fewer than
    ``MIN_TOE_CALIBRATION_FRAMES`` slow frames.
    """
    slow = [
        sample
        for sample in samples
        if (speed := sample.shot.toe_speeds_mm_per_ms.get(sample.frame)) is not None
        and speed <= TOE_CALIBRATION_MAX_SPEED_MM_PER_MS
    ]
    if len(slow) < MIN_TOE_CALIBRATION_FRAMES:
        return template
    overhangs: list[float] = []
    for sample in slow:
        alignment = align_frame(sample.shot, template, sample.frame, sample.mask)
        cols = np.flatnonzero(sample.mask.any(axis=0))
        if alignment is None or cols.size == 0:
            continue
        overhang_px = float(alignment.landmarks["toe"][0]) - float(cols.max())
        overhangs.append(overhang_px * sample.shot.mm_per_px(sample.frame))
    if len(overhangs) < MIN_TOE_CALIBRATION_FRAMES:
        return template
    overhang_mm = float(np.median(overhangs))
    heel, toe = template.landmarks["heel"], template.landmarks["toe"]
    length = float(np.hypot(*(toe - heel)))
    if length <= 0.0:
        return template
    shifted = dict(template.landmarks)
    shifted["toe"] = toe - (toe - heel) / length * (overhang_mm / template.grid_mm_per_px)
    return replace(
        template,
        landmarks=shifted,
        toe_calibrated=True,
        toe_calibration_frames=len(overhangs),
        toe_overhang_mm=overhang_mm,
    )


# ---------------------------------------------------------------------------
# alignment
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Alignment:
    """One frame's placement of the outline, and the landmarks it reads off."""

    frame: int
    iou: float
    roll_deg: float
    step: float
    anchor: np.ndarray
    landmarks: dict[str, np.ndarray]
    on_roll_boundary: bool = False
    on_radius_boundary: bool = False


def _affine(
    template: HeadTemplate, step: float, angle_deg: float, anchor: Sequence[float]
) -> np.ndarray:
    """Template pixels -> image pixels, rotating and scaling about the origin."""
    radians = math.radians(angle_deg)
    cosine, sine = math.cos(radians), math.sin(radians)
    origin_x, origin_y = template.origin
    return np.array(
        [
            [step * cosine, -step * sine, anchor[0] - step * (cosine * origin_x - sine * origin_y)],
            [step * sine, step * cosine, anchor[1] - step * (sine * origin_x + cosine * origin_y)],
        ]
    )


def alignment_matrix(template: HeadTemplate, alignment: Alignment) -> np.ndarray:
    """The 2x3 template-to-image matrix of one placement."""
    return _affine(template, alignment.step, alignment.roll_deg, alignment.anchor)


def _patch_extent(
    template: HeadTemplate, step: float, angle: float
) -> tuple[np.ndarray, np.ndarray]:
    local = _affine(template, step, angle, (0.0, 0.0)) @ template.hull_points
    return local.min(axis=1), local.max(axis=1)


def _rasterise(
    template: HeadTemplate, step: float, angle: float, anchor: np.ndarray, shape: tuple[int, int]
) -> np.ndarray:
    matrix = _affine(template, step, angle, anchor)
    placed = cv2.warpAffine(
        template.outline_u8, matrix, (shape[1], shape[0]), flags=cv2.INTER_NEAREST
    )
    return placed > 0


def _local_iou(mask: np.ndarray, patch: np.ndarray, top_left: tuple[int, int]) -> float:
    """IoU inside the placed outline's own box (a global IoU is all shaft)."""
    row, col = top_left
    height, width = patch.shape
    if row < 0 or col < 0 or row + height > mask.shape[0] or col + width > mask.shape[1]:
        return math.nan
    window = mask[row : row + height, col : col + width]
    intersection = float((window & patch).sum())
    union = float(patch.sum()) + float(window.sum()) - intersection
    return intersection / union if union > 0 else math.nan


def _coarse_search(
    mask: np.ndarray,
    template: HeadTemplate,
    step: float,
    ball: tuple[float, float],
    radius_px: float,
) -> tuple[float, float, np.ndarray] | None:
    """Every roll, every translation, by FFT cross-correlation. Best IoU wins."""
    floating = mask.astype(np.float32)
    integral = cv2.integral(mask.astype(np.uint8))
    best_iou, best_angle, best_anchor = -1.0, 0.0, np.zeros(2)
    for angle in np.arange(-ROLL_LIMIT_DEG, ROLL_LIMIT_DEG + 1e-9, ROLL_STEP_DEG):
        low, high = _patch_extent(template, step, float(angle))
        anchor_in_patch = -low + PATCH_PAD_PX
        shape = (
            int(math.ceil(high[1] - low[1])) + 1 + 2 * PATCH_PAD_PX,
            int(math.ceil(high[0] - low[0])) + 1 + 2 * PATCH_PAD_PX,
        )
        if shape[0] >= mask.shape[0] or shape[1] >= mask.shape[1]:
            continue
        patch = _rasterise(template, step, float(angle), anchor_in_patch, shape)
        area = float(patch.sum())
        if area <= 0:
            continue
        intersection = signal.fftconvolve(floating, patch[::-1, ::-1].astype(np.float32), "valid")
        height, width = shape
        window = (
            integral[height:, width:]
            - integral[:-height, width:]
            - integral[height:, :-width]
            + integral[:-height, :-width]
        ).astype(np.float32)
        union = area + window - intersection
        with np.errstate(divide="ignore", invalid="ignore"):
            iou = np.where(union > 0, intersection / union, 0.0)
        rows, cols = np.mgrid[0 : iou.shape[0], 0 : iou.shape[1]]
        near = (cols + anchor_in_patch[0] - ball[0]) ** 2 + (
            rows + anchor_in_patch[1] - ball[1]
        ) ** 2 <= radius_px**2
        iou = np.where(near, iou, -1.0)
        index = int(np.argmax(iou))
        value = float(iou.ravel()[index])
        if value > best_iou:
            row, col = divmod(index, iou.shape[1])
            best_iou, best_angle = value, float(angle)
            best_anchor = np.array([col + anchor_in_patch[0], row + anchor_in_patch[1]])
    if best_iou <= 0.0:
        return None
    return best_iou, best_angle, best_anchor


def _score(
    mask: np.ndarray, template: HeadTemplate, step: float, angle: float, anchor: np.ndarray
) -> float:
    low, high = _patch_extent(template, step, angle)
    offset = anchor + low
    top_left = (int(math.floor(offset[1])), int(math.floor(offset[0])))
    anchor_in_patch = anchor - np.array([top_left[1], top_left[0]], dtype=float)
    shape = (
        int(math.ceil(high[1] - low[1])) + 2 + 2 * PATCH_PAD_PX,
        int(math.ceil(high[0] - low[0])) + 2 + 2 * PATCH_PAD_PX,
    )
    return _local_iou(mask, _rasterise(template, step, angle, anchor_in_patch, shape), top_left)


def align_frame(
    shot: PreparedShot, template: HeadTemplate, frame: int, mask: np.ndarray
) -> Alignment | None:
    """Scale by the depth model, then search translation and roll for best IoU."""
    step = template.grid_mm_per_px / shot.mm_per_px(frame)
    ball = (shot.ball.x, shot.ball.y)
    radius_px = MAX_ANCHOR_RADIUS_MM / shot.mm_per_px(frame)
    coarse = _coarse_search(mask, template, step, ball, radius_px)
    if coarse is None:
        return None
    best_iou, best_angle, best_anchor = coarse
    shifts = np.arange(-REFINE_SHIFT_PX, REFINE_SHIFT_PX + 1e-9, REFINE_SHIFT_STEP_PX)
    for _ in range(2):
        improved = False
        angles = np.arange(
            best_angle - REFINE_ROLL_DEG, best_angle + REFINE_ROLL_DEG + 1e-9, REFINE_ROLL_STEP_DEG
        )
        centre = best_anchor
        for angle in angles:
            if abs(angle) > ROLL_LIMIT_DEG + 1e-9:
                continue
            for delta_x in shifts:
                for delta_y in shifts:
                    anchor = centre + np.array([delta_x, delta_y])
                    value = _score(mask, template, step, float(angle), anchor)
                    if math.isfinite(value) and value > best_iou + 1e-9:
                        best_iou, best_angle, best_anchor = value, float(angle), anchor
                        improved = True
        if not improved:
            break
    matrix = _affine(template, step, best_angle, best_anchor)
    return Alignment(
        frame=frame,
        iou=best_iou,
        roll_deg=best_angle,
        step=step,
        anchor=best_anchor,
        landmarks={
            name: matrix[:, :2] @ template.landmarks[name] + matrix[:, 2] for name in LANDMARKS
        },
        on_roll_boundary=abs(abs(best_angle) - ROLL_LIMIT_DEG) < 1e-6,
        on_radius_boundary=abs(
            math.hypot(best_anchor[0] - ball[0], best_anchor[1] - ball[1]) - radius_px
        )
        < 1.0,
    )


def align_shot(
    shot: PreparedShot, template: HeadTemplate
) -> tuple[dict[int, Alignment], dict[int, str], dict[int, Alignment]]:
    """Align every masked frame, then apply the IoU, boundary and track gates.

    Returns (accepted, rejected reasons, every alignment). The track gate
    needs ``MIN_TRACK_FRAMES`` frames before contact, so at 120 fps it never
    runs; that is reported, not faked.
    """
    report = shot.masks
    rejected: dict[int, str] = dict(report.reasons) if report else {}
    alignments: dict[int, Alignment] = {}
    if report is None:
        return {}, rejected, alignments
    for frame, mask in sorted(report.masks.items()):
        alignment = align_frame(shot, template, frame, mask)
        if alignment is None:
            rejected[frame] = "no_placement_scored_above_zero"
        else:
            alignments[frame] = alignment
    accepted: dict[int, Alignment] = {}
    for frame, alignment in alignments.items():
        if alignment.iou < IOU_GATE:
            rejected[frame] = f"local_iou_{alignment.iou:.3f}_below_gate"
        elif alignment.on_roll_boundary:
            rejected[frame] = "search_finished_on_the_roll_boundary"
        elif alignment.on_radius_boundary:
            rejected[frame] = "search_finished_on_the_radius_boundary"
        else:
            accepted[frame] = alignment
    last_rest = shot.contact.last_rest_frame
    track = sorted(f for f in accepted if last_rest is not None and f <= last_rest)
    if len(track) >= MIN_TRACK_FRAMES:
        times = np.asarray(track, dtype=float)
        points = np.stack([accepted[f].landmarks["midpoint"] for f in track])
        fit_x = np.polyfit(times, points[:, 0], 1)
        fit_y = np.polyfit(times, points[:, 1], 1)
        for index, frame in enumerate(track):
            residual = math.hypot(
                points[index, 0] - np.polyval(fit_x, frame),
                points[index, 1] - np.polyval(fit_y, frame),
            )
            if residual > TRACK_GATE_MM / shot.mm_per_px(frame):
                del accepted[frame]
                rejected[frame] = f"midpoint_{residual:.1f}_px_off_the_swing_track"
    return accepted, rejected, alignments


# ---------------------------------------------------------------------------
# the reading
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ImpactLocation:  # pylint: disable=too-many-instance-attributes
    """One shot's heel-toe impact reading. EXPERIMENTAL and UNQUALIFIED.

    ``heel_toe_mm`` is the ball centre's offset from the face centre along the
    face, + toward the TOE for either hand. ``uncertainty_mm`` is a 1-sigma
    budget of the named random terms; it does NOT include the face-centre
    convention, which is systematic until a spray session calibrates it.
    """

    status: str
    reason: str
    label: str = EXPERIMENTAL_LABEL
    handedness: str = "right"
    contact_frame: int | None = None
    last_rest_frame: int | None = None
    contact_time_frames: float | None = None
    heel_toe_mm: float | None = None
    uncertainty_mm: float | None = None
    ball_from_toe_mm: float | None = None
    face_width_mm: float | None = None
    face_centre_convention: str = (
        f"aligned-outline heel-toe midpoint + {FACE_CENTRE_TOEWARD_MM:.0f} mm toe-ward "
        "(uncalibrated)"
    )
    diagnostics: dict[str, Any] = field(default_factory=dict)
    alignments: dict[int, Alignment] = field(default_factory=dict, repr=False)

    def as_dict(self) -> dict[str, Any]:
        """JSON-ready fields (the placements stay behind)."""
        return {
            "status": self.status,
            "reason": self.reason,
            "label": self.label,
            "handedness": self.handedness,
            "contact_frame": self.contact_frame,
            "last_rest_frame": self.last_rest_frame,
            "contact_time_frames": self.contact_time_frames,
            "heel_toe_mm": self.heel_toe_mm,
            "uncertainty_mm": self.uncertainty_mm,
            "ball_from_toe_mm": self.ball_from_toe_mm,
            "face_width_mm": self.face_width_mm,
            "face_centre_convention": self.face_centre_convention,
            "diagnostics": self.diagnostics,
        }


def reading_is_physical(ball_from_toe_mm: float) -> tuple[bool, str]:
    """Whether a contact this far from the toe can exist on a conforming iron."""
    if ball_from_toe_mm > TOE_SIDE_MARGIN_MM:
        return False, f"contact_{ball_from_toe_mm:.0f}_mm_beyond_the_toe"
    if -ball_from_toe_mm > MAX_IRON_BLADE_MM + BLADE_GATE_MARGIN_MM:
        return False, f"contact_{-ball_from_toe_mm:.0f}_mm_from_the_toe_is_beyond_any_iron_face"
    return True, "ok"


def _metric(shot: PreparedShot, point: np.ndarray, ratio: float) -> np.ndarray:
    """Image pixel at depth ratio ``ratio`` -> mm in the plane across the line of sight."""
    cx, cy = shot.camera.principal_point_px
    return np.array([point[0] - cx, point[1] - cy]) * shot.plate_mm_per_px * ratio


def _read(
    shot: PreparedShot,
    before: Alignment,
    after: Alignment,
    *,
    fraction: float,
    speed: float,
    face_centre_toeward_mm: float,
) -> dict[str, Any] | None:
    """Heel and toe interpolated to contact; the ball read against them."""
    last_rest = shot.contact.last_rest_frame
    assert last_rest is not None
    contact = last_rest + fraction
    weight = (contact - before.frame) / (after.frame - before.frame)
    ratio_before = shot.depth_ratio(before.frame, fraction=fraction, speed=speed)
    ratio_after = shot.depth_ratio(after.frame, fraction=fraction, speed=speed)
    points: dict[str, np.ndarray] = {}
    for name in ("heel", "toe"):
        first = _metric(shot, before.landmarks[name], ratio_before)
        second = _metric(shot, after.landmarks[name], ratio_after)
        points[name] = first + weight * (second - first)
    span = points["toe"] - points["heel"]
    length = float(np.hypot(*span))
    if length <= 0.0:
        return None
    unit = span / length
    centre = 0.5 * (points["heel"] + points["toe"]) + unit * face_centre_toeward_mm
    ball = _metric(shot, np.array([shot.ball.x, shot.ball.y]), 1.0)

    def centre_on(alignment: Alignment, ratio: float) -> float:
        heel = _metric(shot, alignment.landmarks["heel"], ratio)
        toe = _metric(shot, alignment.landmarks["toe"], ratio)
        return float(np.dot(0.5 * (heel + toe), unit))

    return {
        "heel_toe_mm": float(np.dot(ball - centre, unit)),
        "ball_from_toe_mm": float(np.dot(ball - points["toe"], unit)),
        "face_width_mm": length,
        "weight": weight,
        "ratio_before": ratio_before,
        "ratio_after": ratio_after,
        "heel_mm": points["heel"],
        "toe_mm": points["toe"],
        "centre_mm": centre,
        "centre_step_mm": centre_on(after, ratio_after) - centre_on(before, ratio_before),
    }


def _bracket(
    shot: PreparedShot, accepted: Mapping[int, Alignment]
) -> tuple[Alignment | None, Alignment | None]:
    last_rest = shot.contact.last_rest_frame
    assert last_rest is not None
    before = max((f for f in accepted if f <= last_rest), default=None)
    after = min((f for f in accepted if f > last_rest), default=None)
    return (
        accepted[before] if before is not None else None,
        accepted[after] if after is not None else None,
    )


def locate_impact(  # pylint: disable=too-many-return-statements
    shot: PreparedShot,
    template: HeadTemplate | None,
    *,
    face_centre_toeward_mm: float = FACE_CENTRE_TOEWARD_MM,
) -> ImpactLocation:
    """Align the template, interpolate the head to contact, read the ball. Fails closed."""
    contact = shot.contact
    diagnostics: dict[str, Any] = {
        "contact": contact.as_dict(),
        "fps": shot.fps,
        "analysis_flipped": shot.flipped,
        "roi_origin": list(shot.roi_origin),
        "camera": {
            "focal_px": shot.camera.focal_px,
            "source": shot.camera.source,
        },
        "ball_range_mm": round(shot.ball_range_mm, 1),
        "ball_range_source": shot.ball_range_source,
        "plate_mm_per_px": shot.plate_mm_per_px,
        "club_speed_mps": shot.club_speed_mps,
        "club_speed_source": shot.club_speed_source,
        "contact_fraction": shot.contact_fraction,
        "contact_fraction_source": shot.contact_fraction_source,
        "window": list(shot.window),
    }
    common = {
        "handedness": shot.handedness,
        "contact_frame": contact.contact_frame,
        "last_rest_frame": contact.last_rest_frame,
        "contact_time_frames": shot.contact_time,
    }
    if contact.status != "ok":
        return ImpactLocation("rejected", contact.reason, diagnostics=diagnostics, **common)
    if face_centre_toeward_mm != FACE_CENTRE_TOEWARD_MM:
        common["face_centre_convention"] = (
            f"aligned-outline heel-toe midpoint + {face_centre_toeward_mm:.1f} mm toe-ward"
        )
    if template is None:
        return ImpactLocation(
            "rejected", "no_template_for_this_club", diagnostics=diagnostics, **common
        )
    diagnostics["template"] = template.summary()
    accepted, rejected, alignments = align_shot(shot, template)
    diagnostics["frames"] = {
        int(f): {
            "iou": round(a.iou, 3),
            "roll_deg": a.roll_deg,
            "depth_ratio": round(shot.depth_ratio(f), 4),
            "accepted": f in accepted,
        }
        for f, a in sorted(alignments.items())
    }
    diagnostics["rejected_frames"] = {int(k): v for k, v in sorted(rejected.items())}
    if shot.masks is not None:
        diagnostics["ball_vetoes"] = sorted(int(k) for k in shot.masks.ball_vetoes)
    result = {"diagnostics": diagnostics, "alignments": alignments, **common}

    before, after = _bracket(shot, accepted)
    if before is None or after is None:
        side = "before" if before is None else "after"
        frame = contact.last_rest_frame if before is None else contact.contact_frame
        why = rejected.get(frame, "no_mask")
        return ImpactLocation(
            "rejected", f"no_accepted_frame_{side}_contact_{frame}_{why}", **result
        )
    span_s = (after.frame - before.frame) / shot.fps
    diagnostics["bracket_frames"] = [before.frame, after.frame]
    if span_s > MAX_BRACKET_S + 1e-9:
        return ImpactLocation(
            "rejected",
            f"bracket_{before.frame}_{after.frame}_spans_{span_s * 1000:.1f}_ms",
            **result,
        )

    fraction, speed = shot.contact_fraction, shot.club_speed_mps
    reading = _read(
        shot,
        before,
        after,
        fraction=fraction,
        speed=speed,
        face_centre_toeward_mm=face_centre_toeward_mm,
    )
    if reading is None:
        return ImpactLocation("rejected", "carried_outline_has_no_heel_toe_span", **result)
    physical, why = reading_is_physical(reading["ball_from_toe_mm"])
    if not physical:
        return ImpactLocation("rejected", why, **result)

    # --- the uncertainty budget -------------------------------------------------
    def reread(**changes: float) -> float:
        again = _read(
            shot,
            before,
            after,
            fraction=changes.get("fraction", fraction),
            speed=changes.get("speed", speed),
            face_centre_toeward_mm=face_centre_toeward_mm,
        )
        return reading["heel_toe_mm"] if again is None else again["heel_toe_mm"]

    fraction_sigma = (
        UNIFORM_FRACTION_SIGMA
        if shot.contact_fraction_source == "uniform_midpoint"
        else GIVEN_FRACTION_SIGMA
    )
    low, high = max(0.0, fraction - fraction_sigma), min(1.0, fraction + fraction_sigma)
    timing = abs(reread(fraction=high) - reread(fraction=low)) / max(
        (high - low) / fraction_sigma, 1e-9
    )
    speed_sigma = (
        ASSUMED_CLUB_SPEED_RELATIVE_SIGMA
        if shot.club_speed_source == "assumed_default"
        else MEASURED_CLUB_SPEED_RELATIVE_SIGMA
    )
    speed_term = (
        abs(reread(speed=speed * (1 + speed_sigma)) - reread(speed=speed * (1 - speed_sigma))) / 2.0
    )
    weight = reading["weight"]
    align = (
        ALIGN_SIGMA_PX
        * shot.plate_mm_per_px
        * math.hypot((1.0 - weight) * reading["ratio_before"], weight * reading["ratio_after"])
    )
    scale = abs(reading["heel_toe_mm"]) * BALL_DIAMETER_SIGMA_PX / shot.ball.diameter_px
    travelled_before = (
        speed * (contact.last_rest_frame + fraction - before.frame) * 1000.0 / shot.fps
    )
    travelled_after = (
        speed
        * POST_IMPACT_SPEED_FRACTION
        * (after.frame - contact.last_rest_frame - fraction)
        * 1000.0
        / shot.fps
    )
    arc_radius = ARC_RADIUS_MM / math.cos(math.radians(SWING_PLANE_DEG))
    arc = travelled_before * travelled_after / (2.0 * arc_radius)
    budget = {
        "alignment_mm": align,
        "contact_timing_mm": timing,
        "club_speed_mm": speed_term,
        "ball_scale_mm": scale,
        "arc_chord_mm": arc,
    }
    if contact.warning is not None:
        # A one-frame bracket shift moves the reading by about the head's
        # own step between frames; half of it as one sigma of a two-way doubt.
        budget["contact_frame_ambiguity_mm"] = abs(reading["centre_step_mm"]) / 2.0
    total = math.sqrt(sum(v * v for v in budget.values()))
    diagnostics.update(
        {
            "uncertainty_budget_mm": {k: round(v, 2) for k, v in budget.items()},
            "interpolation_weight": round(weight, 4),
            "depth_ratio_before": round(reading["ratio_before"], 4),
            "depth_ratio_after": round(reading["ratio_after"], 4),
            "centre_moved_between_bracket_frames_mm": round(reading["centre_step_mm"], 2),
            "heel_at_contact_mm": [round(v, 2) for v in reading["heel_mm"]],
            "toe_at_contact_mm": [round(v, 2) for v in reading["toe_mm"]],
            "centre_at_contact_mm": [round(v, 2) for v in reading["centre_mm"]],
            "not_in_uncertainty": [
                "face-centre convention (systematic until spray-calibrated)",
                "toe blur overhang beyond the toe calibration",
                "nominal focal length and principal point",
            ],
        }
    )
    return ImpactLocation(
        "ok",
        "ok",
        heel_toe_mm=reading["heel_toe_mm"],
        uncertainty_mm=total,
        ball_from_toe_mm=reading["ball_from_toe_mm"],
        face_width_mm=reading["face_width_mm"],
        **result,
    )


def analyse_shot(
    frames: np.ndarray,
    ball: RestingBall,
    *,
    fps: float,
    camera: ImpactCamera | None = None,
    handedness: str = "right",
    frames_mirrored: bool = False,
    club_speed_mps: float | None = None,
    ball_range_mm: float | None = None,
    contact_fraction: float | None = None,
    template: HeadTemplate | None = None,
    face_centre_toeward_mm: float = FACE_CENTRE_TOEWARD_MM,
) -> ImpactLocation:
    """One shot end to end. EXPERIMENTAL.

    With no ``template`` the outline is self-built from this shot's own frames
    alone -- two or three masks at 120 fps, so a session-level template
    (``build_self_template`` over every shot of the club) is the better input
    whenever there is a session.
    """
    shot = prepare_shot(
        frames,
        ball,
        fps=fps,
        camera=camera,
        handedness=handedness,
        frames_mirrored=frames_mirrored,
        club_speed_mps=club_speed_mps,
        ball_range_mm=ball_range_mm,
        contact_fraction=contact_fraction,
    )
    if template is None and shot.contact.status == "ok":
        template, _reason = build_self_template([shot])
    return locate_impact(shot, template, face_centre_toeward_mm=face_centre_toeward_mm)


# ---------------------------------------------------------------------------
# the picture a human checks
# ---------------------------------------------------------------------------


def render_overlay(
    shot: PreparedShot,
    template: HeadTemplate | None,
    location: ImpactLocation,
    *,
    tile_height: int = 260,
    text: Callable[[ImpactLocation], str] | None = None,
) -> np.ndarray:
    """The analysed frames side by side, as the SAVED picture shows them.

    Green: the head mask. Orange: the placed outline (red when the frame
    failed a gate). Blue dots heel/toe. Yellow: the resting ball. On the last
    tile, magenta: heel, toe and face centre interpolated to contact, drawn
    in the ball's plane. Render before measuring: this is how a reading is
    checked.
    """
    frames = shot.window or tuple(range(max(0, shot.frames.shape[0] - 3), shot.frames.shape[0]))
    plate = shot.plate_mm_per_px
    crop_x0 = max(0, int(shot.ball.x - OVERLAY_HALF_WIDTH_MM / plate))
    crop_x1 = min(shot.frames.shape[2], int(shot.ball.x + OVERLAY_HALF_WIDTH_MM / plate) + 1)
    crop_y0 = max(0, int(shot.ball.y - OVERLAY_ABOVE_MM / plate))
    crop_y1 = min(shot.frames.shape[1], int(shot.ball.y + OVERLAY_BELOW_MM / plate) + 1)
    scale = max(1, int(round(tile_height / max(crop_y1 - crop_y0, 1))))
    tiles = []
    accepted = {
        int(k) for k, v in location.diagnostics.get("frames", {}).items() if v.get("accepted")
    }
    for frame in frames:
        tile = cv2.cvtColor(shot.frames[frame], cv2.COLOR_GRAY2BGR)
        if shot.masks is not None and frame in shot.masks.masks:
            mask = shot.masks.masks[frame]
            tile[mask] = (0.6 * tile[mask] + 0.4 * np.array([0, 200, 0])).astype(np.uint8)
        tile = cv2.resize(tile, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
        centre = (int(round(shot.ball.x * scale)), int(round(shot.ball.y * scale)))
        cv2.circle(tile, centre, int(round(shot.ball.diameter_px * scale / 2)), (0, 220, 220), 1)
        alignment = location.alignments.get(frame)
        if template is not None and alignment is not None:
            matrix = alignment_matrix(template, alignment) * scale
            placed = cv2.warpAffine(
                template.outline.astype(np.uint8),
                matrix,
                (tile.shape[1], tile.shape[0]),
                flags=cv2.INTER_NEAREST,
            )
            contours, _ = cv2.findContours(placed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
            colour = (0, 140, 255) if frame in accepted else (0, 0, 255)
            cv2.drawContours(tile, contours, -1, colour, 1)
            for name in ("heel", "toe"):
                x, y = alignment.landmarks[name] * scale
                cv2.circle(tile, (int(round(x)), int(round(y))), 3, (255, 80, 0), -1)
        if frame == frames[-1] and location.status == "ok":
            cx, cy = shot.camera.principal_point_px
            plate = shot.plate_mm_per_px
            for key, radius in (
                ("heel_at_contact_mm", 3),
                ("toe_at_contact_mm", 3),
                ("centre_at_contact_mm", 5),
            ):
                mx, my = location.diagnostics[key]
                point = (
                    int(round((cx + mx / plate) * scale)),
                    int(round((cy + my / plate) * scale)),
                )
                cv2.circle(
                    tile, point, radius, (255, 0, 255), 1 if key != "centre_at_contact_mm" else 2
                )
        tile = tile[crop_y0 * scale : crop_y1 * scale, crop_x0 * scale : crop_x1 * scale]
        if shot.flipped:
            tile = tile[:, ::-1]
        tile = np.ascontiguousarray(tile)
        label = f"f{frame}"
        if frame == shot.contact.last_rest_frame:
            label += " last rest"
        elif frame == shot.contact.contact_frame:
            label += " ball moved"
        cv2.putText(
            tile, label, (4, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA
        )
        tiles.append(tile)
    if not tiles:
        tiles = [np.zeros((tile_height, tile_height, 3), np.uint8)]
    height = max(t.shape[0] for t in tiles)
    tiles = [np.pad(t, ((0, height - t.shape[0]), (0, 2), (0, 0))) for t in tiles]
    board = np.concatenate(tiles, axis=1)
    header = np.zeros((22, board.shape[1], 3), np.uint8)
    caption = text(location) if text else _caption(location)
    cv2.putText(
        header, caption, (4, 15), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA
    )
    return np.concatenate([header, board], axis=0)


def _caption(location: ImpactLocation) -> str:
    if location.status != "ok":
        return f"REJECTED: {location.reason}"
    return (
        f"EXPERIMENTAL heel-toe {location.heel_toe_mm:+.1f} +/- {location.uncertainty_mm:.1f} mm "
        f"(+ toe, {location.handedness}-handed), contact after f{location.last_rest_frame}"
    )

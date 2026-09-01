"""A clubhead outline built from the session's own masks, and placed per frame.

NO GROUND TRUTH EXISTS for anything in this module. The outline comes from the
pixels; the only reference it has ever been scored against is one annotator's
hand marks, which is a CONSISTENCY result and never an accuracy one.

Promoted from `research/empirical_template/{build_template,align_and_carry}_v3`
after the configuration below passed its pre-registered gate on 2026-08-29.
Three iterations of that research are compressed into four decisions, and each
of them was measured, not chosen:

  1. **The sole edge comes from the occupancy fall-off, not from a crop.** The
     first iteration cut every sample 48 mm below the marked topline, which
     made the outline's bottom edge a horizontal line the annotator drew. It
     cost a systematic +1.10 px topline bias, because a flat-bottomed outline
     slides down onto the sole shadow. Taking the bottom from where the mean
     occupancy falls through 0.5 dropped that to +0.16 px with no other change.

  2. **The heel edge comes from the same fall-off, in x.** With the heel side
     left uncut the outline runs up the hosel neck, which is exactly the
     structure the annotator was looking at when placing the heel mark. Heel
     p90 went 2.45 px -> 1.71 px, and it is the change that carried the gate.
     The cost is honest and named in `ClubOutlineTemplate.heel_reaches_hosel`:
     the outline is 30-35 mm wider than the club and its leftmost point is no
     longer the heel, so only the LANDMARK heel reading means anything.

  3. **The ball is vetoed AFTER contact and never before it.** Before contact
     the teed ball is stationary, so it is absent from the moving mask while
     the head sits on it, and vetoing punches a hole through the head. After
     contact -- and f_c + 1 is 0.3 ms after it -- the departing ball joins the
     moving component and `split_head` hands it back as part of the head. One
     shot locked its outline at +17 deg of roll on a ball blob because of it.

  4. **The scale is pinned by the radar range, not fitted.** The head images
     36 % larger six frames before contact than at it, and pinning the outline
     to `1581 / range_mm(frame)` tracks that with no free parameter and no size
     collapse.

ONE DEVIATION from the promoting brief, inherited from the research and kept
because the numbers came from it: the coarse translation search is GLOBAL (an
FFT cross-correlation gives every placement for the price of one), not the
brief's +-12 px. A +-12 px search needs a prior, and at f_c-6 the head is
20-30 px from the ball with the only prior being the marks, which are the test
set. The +-12 px is re-imposed downstream as a TRACK gate: a frame whose
aligned midpoint sits more than 12 px off the shot's own fitted line is failed
closed, which is the same fail-closed by a different route.
"""

# cv2's bindings are generated at import, so pylint cannot see any of them.
# pylint: disable=no-member

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import cv2
import numpy as np
from scipy import ndimage, signal

from openflight.camera.club_motion import ReferenceBall
from openflight.camera.clubpose.head_split import split_head
from openflight.camera.clubpose.projection import CAMERA_BALL_RANGE_MM

BALL_DIAMETER_MM = 42.67

# --- segmentation, from research/session_metrics/swing_plane.py -------------
MOVING_SIGMA = 4.0
MOVING_FLOOR = 18.0
COMPONENT_RADIUS_PX = 200.0
MIN_COMPONENT_AREA_PX = 40
MIN_HEAD_AREA_PX = 60
# The largest silhouette a clubhead can cast, in PHYSICAL area -- a pixel gate
# would be wrong by 85 % across the seven frames, because the head images 36 %
# wider six frames before contact than at it. A mid-iron head is about
# 110 x 55 mm seen from behind, so 11 000 mm^2 is 1.8x its own silhouette and
# anything past it has taken in turf. Over the 2026-08-25 session the masks
# measure a median 6 300 mm^2 and a maximum of 10 900 mm^2 (shot 016 f71, the
# known head-shaft-turf merge), so this gate is a coarse guard rather than the
# thing that catches that frame -- the IoU and USGA gates do.
MAX_HEAD_AREA_MM2 = 11_000.0
# Pre-swing frames, ending well before the club enters. Fifteen frames is 32 ms
# at the shipped 468 fps, by which point the head is out of the field.
BACKGROUND_START_FRAME = 8
BACKGROUND_LEAD_FRAMES = 15

# --- the post-contact flying-ball veto -------------------------------------
BALL_BRIGHTNESS_FRACTION = 0.8
BALL_SEARCH_SPAN = 2.0  # in teed-ball diameters, either side of the tee
BALL_VETO_PAD_PX = 1.0

# --- the template grid -----------------------------------------------------
UPSAMPLE = 4  # so a 0.25 px alignment refinement has something to land on
TEMPLATE_HALF_WIDTH = 120
TEMPLATE_ABOVE = 24
TEMPLATE_BELOW = 88
TEMPLATE_ORIGIN = (TEMPLATE_HALF_WIDTH, TEMPLATE_ABOVE)
TEMPLATE_SHAPE = (TEMPLATE_ABOVE + TEMPLATE_BELOW + 1, 2 * TEMPLATE_HALF_WIDTH + 1)
OCCUPANCY_THRESHOLD = 0.5
CROP_TOP_MARGIN_PX = 2.0
SIDE_MARGIN_PX = 0.0
# Pre-registered fallbacks, fixed before any error was read: if this fraction of
# the outline still reaches the grid's edge, the occupancy never fell through
# 0.5 on the way there and the edge reverts to a crop at the mark.
FALLBACK_EDGE_FRACTION = 0.5

# --- the blur filter on self-built templates -------------------------------
# The head's transverse image speed across the alignment window, measured on
# the 2026-08-25 session: 14-29 mm/frame five to three frames before contact,
# falling through ~7 mm/frame to ~0 at the contact instant (the arc's lateral
# turning point). A mask is the union of the head's positions across the
# exposure, so a fast frame's silhouette is smeared along the motion -- the
# session's masks span 118-200 mm against a 97 mm head -- and a template
# averaged over such masks inherits the smear: its toe landmark measured
# 3-10 mm beyond even the sharpest mask's toe, which shifted every impact
# reading 10-25 mm heel-ward and put 7 of 18 readings past the heel edge of
# any conforming face (2026-09-01 audit; the physics veto is that those shots
# left at 96-114 mph, which hosel contact cannot produce).
#
# The correction is a CALIBRATION OF THE TOE LANDMARK, not a change of shape:
# the outline, heel and topline still come from every frame, because a
# template built from the slow frames alone was tried and imports their own
# artefacts -- the stationary ball punches a notch in the crown and the shaft
# is fully merged into the heel at contact -- which failed the topline and
# heel gates on most frames (availability 11/21 against 18/21). Instead, the
# finished template is placed on each frame measured slower than this
# threshold, its toe landmark's overhang past that frame's own mask toe is
# taken, and the median overhang is subtracted from the landmark. The labels-
# built path is untouched: it is the pinned regression reference, and its
# marked frames sit at f_c-1/f_c+1 where the head is slowest.
MAX_TEMPLATE_SPEED_MM_PER_FRAME = 10.0
# Below this many slow frames the median would be a couple of masks' accident,
# so the calibration stands down and the report says so -- the pre-2026-09-01
# landmark, named rather than silent.
MIN_TOE_CALIBRATION_FRAMES = 4

# --- alignment -------------------------------------------------------------
ROLL_LIMIT_DEG = 20.0
ROLL_STEP_DEG = 1.0
REFINE_ROLL_DEG = 1.0
REFINE_ROLL_STEP_DEG = 0.25
REFINE_SHIFT_PX = 1.0
REFINE_SHIFT_STEP_PX = 0.25
MAX_ANCHOR_RADIUS_PX = 55.0
PATCH_PAD_PX = 2
IOU_GATE = 0.60
TRACK_GATE_PX = 12.0
MIN_TRACK_FRAMES = 3

# --- the specular topline ridge --------------------------------------------
RIDGE_SEARCH_PX = 3.0
RIDGE_STEP_PX = 0.25
RIDGE_SPAN_PX = 2.0  # the ridge is ~1 px wide; sample 2 px either side of it
RIDGE_MIN_COLUMNS = 4
RIDGE_WEIGHT = 0.5  # equal weight with the mask IoU, min-max normalised

LANDMARKS = ("heel", "toe", "topline", "midpoint")

# Blade length (heel to toe) at 7-iron, by category, from OFFICIAL sources: the
# Mizuno irons-comparison chart JSON and Srixon's `Iron_Shape_Comparison.pdf`.
# Used ONLY to size a bootstrap outline before a session has built its own, and
# never as an answer. No community or 3-D-print model is admissible here.
CATEGORY_BLADE_LENGTH_MM: dict[str, tuple[float, float]] = {
    "players": (74.0, 77.0),
    "players_cavity": (77.0, 81.0),
    "game_improvement": (85.0, 87.0),
}
DEFAULT_CATEGORY = "players_cavity"


# ---------------------------------------------------------------------------
# one swing's inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SwingFrames:
    """Everything one swing contributes: pixels, the teed ball, and timing.

    ``frames`` must already be UN-MIRRORED (`frames[:, :, ::-1]` on the shipped
    capture); the ball, the marks and the world frame all live in that
    orientation and nothing downstream re-checks it.
    """

    frames: np.ndarray
    ball: ReferenceBall
    fps: float
    contact_frame: float
    range_rate_ms: float
    club: str = ""
    name: str = ""

    @property
    def plate_mm_per_px(self) -> float:
        """Millimetres per pixel at the teed ball's range."""
        return BALL_DIAMETER_MM / self.ball.diameter_px

    def range_mm(self, frame: float) -> float:
        """Camera-to-head range, from the radar's rate anchored at contact."""
        return CAMERA_BALL_RANGE_MM - self.range_rate_ms * 1000.0 * (
            (self.contact_frame - frame) / self.fps
        )

    def mm_per_px(self, frame: float) -> float:
        """Millimetres per pixel at the head's own range on this frame."""
        return self.plate_mm_per_px * self.range_mm(frame) / CAMERA_BALL_RANGE_MM

    @property
    def align_frames(self) -> tuple[int, ...]:
        """f_c-6 to f_c+1, as the integer frames that exist."""
        last = int(math.floor(self.contact_frame))
        return tuple(range(max(0, last - 5), min(self.frames.shape[0], last + 2)))

    @property
    def background_slice(self) -> slice:
        """Pre-swing frames, well before the club enters the field."""
        stop = int(math.floor(self.contact_frame)) - BACKGROUND_LEAD_FRAMES
        return slice(BACKGROUND_START_FRAME, max(BACKGROUND_START_FRAME + 1, stop))


# ---------------------------------------------------------------------------
# masks
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MaskReport:
    """Per-frame head masks and, for every frame without one, why."""

    masks: dict[int, np.ndarray]
    reasons: dict[int, str]
    ball_vetoes: dict[int, tuple[float, float, float]]


def moving_mask(frames: np.ndarray, background: slice) -> np.ndarray:
    """Per-frame boolean mask of pixels that moved, with the head filled in."""
    median = np.median(frames[background], axis=0)
    sigma = frames[background].astype(float).std(axis=0)
    threshold = np.maximum(MOVING_SIGMA * sigma, MOVING_FLOOR)
    raw = np.abs(frames.astype(float) - median) > threshold
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    filled = np.empty_like(raw)
    for index in range(raw.shape[0]):
        closed = cv2.morphologyEx(raw[index].astype(np.uint8), cv2.MORPH_CLOSE, kernel)
        filled[index] = ndimage.binary_fill_holes(closed > 0)
    return filled


def _disk_kernel(radius: float) -> np.ndarray:
    half = max(1, int(round(radius)))
    grid_y, grid_x = np.mgrid[-half : half + 1, -half : half + 1]
    kernel = ((grid_x**2 + grid_y**2) <= radius**2).astype(np.float32)
    return kernel / max(kernel.sum(), 1.0)


def flying_ball(
    frame: np.ndarray, ball: ReferenceBall, background: np.ndarray
) -> tuple[float, float, float] | None:
    """The departing ball on a post-contact frame: brightest disk above the tee row.

    Returns ``(x, y, veto_radius)``, or None when nothing near the tee is as
    bright as the teed ball itself. That is FAIL CLOSED: a frame with no
    visible ball is left exactly as the mask recipe had it, rather than having
    a hole punched in it on a guess.
    """
    radius = ball.diameter_px / 2.0
    kernel = _disk_kernel(radius)
    response = cv2.filter2D(frame.astype(np.float32), -1, kernel, borderType=cv2.BORDER_REPLICATE)
    reference = cv2.filter2D(
        background.astype(np.float32), -1, kernel, borderType=cv2.BORDER_REPLICATE
    )
    teed = float(reference[int(round(ball.y)), int(round(ball.x))])
    grid_y, grid_x = np.mgrid[0 : response.shape[0], 0 : response.shape[1]]
    window = (np.abs(grid_x - ball.x) <= BALL_SEARCH_SPAN * ball.diameter_px) & (
        grid_y <= ball.y + radius
    )
    scored = np.where(window, response, -1.0)
    flat = int(np.argmax(scored))
    if float(scored.ravel()[flat]) < BALL_BRIGHTNESS_FRACTION * teed:
        return None
    row, column = divmod(flat, response.shape[1])
    return (float(column), float(row), radius + BALL_VETO_PAD_PX)


def head_masks(swing: SwingFrames, wanted: Iterable[int] | None = None) -> MaskReport:
    """The `split_head` head half of the largest moving component near the ball.

    No tee veto before contact: the teed ball is stationary and therefore
    absent from the moving mask, while the head sits on it, so a veto would
    punch a hole through the head. A FLYING-ball veto runs on frames strictly
    after contact, where the departing ball does join the moving component.

    Every frame without a mask carries a reason. A mask smaller than
    `MIN_HEAD_AREA_PX` or physically larger than `MAX_HEAD_AREA_MM2` is
    refused: the large case is the head merging with the turf shadow, which no
    amount of downstream gating recovers. The large gate is in MILLIMETRES,
    because the head images 36 % wider -- 85 % in area -- six frames before
    contact than at it, and a pixel gate would be wrong by that much.
    """
    background_slice = swing.background_slice
    moving = moving_mask(swing.frames, background_slice)
    background = np.median(swing.frames[background_slice], axis=0)
    grid_y, grid_x = np.mgrid[0 : swing.frames.shape[1], 0 : swing.frames.shape[2]]
    masks: dict[int, np.ndarray] = {}
    reasons: dict[int, str] = {}
    vetoes: dict[int, tuple[float, float, float]] = {}
    for index in swing.align_frames if wanted is None else wanted:
        if not 0 <= index < swing.frames.shape[0]:
            reasons[index] = "frame_out_of_range"
            continue
        current = moving[index].copy()
        if index > swing.contact_frame:
            found = flying_ball(swing.frames[index], swing.ball, background)
            if found is not None:
                x, y, radius = found
                current[((grid_x - x) ** 2 + (grid_y - y) ** 2) <= radius**2] = False
                vetoes[index] = found
        count, labels, stats, centroids = cv2.connectedComponentsWithStats(
            current.astype(np.uint8), 8
        )
        best: tuple[int, int] | None = None
        for label in range(1, count):
            area = int(stats[label, cv2.CC_STAT_AREA])
            distance = math.hypot(
                centroids[label][0] - swing.ball.x, centroids[label][1] - swing.ball.y
            )
            if area < MIN_COMPONENT_AREA_PX or distance > COMPONENT_RADIUS_PX:
                continue
            if best is None or area > best[0]:
                best = (area, label)
        if best is None:
            reasons[index] = "no_moving_component_near_the_ball"
            continue
        split = split_head((labels == best[1]).astype(np.uint8))
        if split is None:
            reasons[index] = "no_body_thick_enough_to_be_a_clubhead"
            continue
        head = split[0] > 0
        area = int(head.sum())
        if area < MIN_HEAD_AREA_PX:
            reasons[index] = f"head_mask_only_{area}_px"
            continue
        area_mm2 = area * swing.mm_per_px(index) ** 2
        if area_mm2 > MAX_HEAD_AREA_MM2:
            reasons[index] = f"head_mask_{area_mm2:.0f}_mm2_merged_with_turf"
            continue
        masks[index] = head
    return MaskReport(masks=masks, reasons=reasons, ball_vetoes=vetoes)


def mask_speeds(swing: SwingFrames, report: MaskReport) -> dict[int, float]:
    """Per-frame TOE-EDGE speed in MILLIMETRES PER FRAME, from the masks.

    The blur proxy for the self-built template filter. It is the toe-side
    extreme's motion -- the mask's maximum column, under the module's
    heel-left/toe-right convention -- and deliberately NOT the centroid: the
    centroid jumps 20+ mm/frame at contact from mask-shape changes alone (the
    ball hole, the shaft merge) and on the 2026-08-25 session it rated one
    7-iron frame in 52 as slow. The toe edge is the landmark the smear
    actually corrupts, and it decelerates smoothly into contact
    (29, 22, 15, 7, ~0 mm/frame on the audited shots).

    A frame's speed is the mean of its adjacent edge displacements per frame
    of gap, converted at that frame's own range. A frame with no adjacent
    mask has no estimate and is absent from the result -- absence is
    "unknown", never "slow".
    """
    edges: dict[int, float] = {}
    for frame, mask in report.masks.items():
        cols = np.flatnonzero(mask.any(axis=0))
        if cols.size:
            edges[frame] = float(cols.max())
    ordered = sorted(edges)
    gathered: dict[int, list[float]] = {}
    for first, second in zip(ordered, ordered[1:]):
        gap = float(second - first)
        step_px = abs(edges[second] - edges[first]) / gap
        gathered.setdefault(first, []).append(step_px * swing.mm_per_px(first))
        gathered.setdefault(second, []).append(step_px * swing.mm_per_px(second))
    return {frame: float(np.mean(values)) for frame, values in gathered.items()}


def shaft_line(swing: SwingFrames, frame: int) -> tuple[float, float] | None:
    """(slope, intercept) of the observed shaft in image coordinates, x = m*y + c.

    Fitted against ROW so a near-vertical shaft is well posed. Returns None
    when `split_head` found no shaft on this frame, which it often does not:
    the hosel neck drops out of the moving mask on about half the frames.
    """
    background_slice = swing.background_slice
    moving = moving_mask(swing.frames, background_slice)
    if not 0 <= frame < swing.frames.shape[0]:
        return None
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(
        moving[frame].astype(np.uint8), 8
    )
    best_area, best_label = 0, -1
    for label in range(1, count):
        area = int(stats[label, cv2.CC_STAT_AREA])
        distance = math.hypot(
            centroids[label][0] - swing.ball.x, centroids[label][1] - swing.ball.y
        )
        if area < MIN_COMPONENT_AREA_PX or distance > COMPONENT_RADIUS_PX:
            continue
        if area > best_area:
            best_area, best_label = area, label
    if best_label < 0:
        return None
    split = split_head((labels == best_label).astype(np.uint8))
    if split is None:
        return None
    rows, cols = np.nonzero(split[1])
    if rows.size < 8 or float(np.ptp(rows)) < 4.0:
        return None
    slope, intercept = np.polyfit(rows.astype(float), cols.astype(float), 1)
    return float(slope), float(intercept)


# ---------------------------------------------------------------------------
# the template
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClubOutlineTemplate:
    """One club's empirical outline, on a common grid at the ball's range.

    ``outline`` is the mean occupancy thresholded at 0.5, on a grid `UPSAMPLE`
    times finer than ``mm_per_px``. ``landmarks`` carries the two offsets the
    impact reading needs -- hosel-neck to midpoint, and topline to the ball's
    column -- expressed as points in template coordinates.

    ``source`` is one of "labels", "self-built" or "photo" and travels with
    every result, because a template built from an annotator's marks and one
    built from a golfer's own swings are not the same object.
    """

    club: str
    outline: np.ndarray
    occupancy: np.ndarray
    landmarks: dict[str, np.ndarray]
    mm_per_px: float
    n_samples: int
    source: str
    sample_iou: tuple[float, ...] = ()
    heel_reaches_hosel: bool = True
    left_edge_fraction: float = 0.0
    floor_fraction: float = 0.0
    excluded: str | None = None

    @property
    def mm_per_template_px(self) -> float:
        """Physical size of one template pixel, at the ball's range."""
        return self.mm_per_px / UPSAMPLE

    def extent_mm(self) -> tuple[float, float]:
        """(width, height) of the outline's bounding box, in millimetres."""
        rows, cols = np.nonzero(self.outline)
        if rows.size == 0:
            return (0.0, 0.0)
        return (
            float(np.ptp(cols) + 1) * self.mm_per_template_px,
            float(np.ptp(rows) + 1) * self.mm_per_template_px,
        )

    def save(self, path: str | Path) -> None:
        """Store the outline, the occupancy and the landmarks in one npz."""
        np.savez_compressed(
            Path(path),
            outline=self.outline,
            occupancy=self.occupancy,
            mm_per_px=np.asarray(self.mm_per_px),
            club=np.asarray(self.club),
            source=np.asarray(self.source),
            n_samples=np.asarray(self.n_samples),
            heel_reaches_hosel=np.asarray(self.heel_reaches_hosel),
            **{f"landmark_{name}": point for name, point in self.landmarks.items()},
        )

    @classmethod
    def load(cls, path: str | Path) -> "ClubOutlineTemplate":
        """Read back a template written by `save`."""
        with np.load(Path(path), allow_pickle=False) as data:
            landmarks = {
                key[len("landmark_") :]: np.asarray(data[key], dtype=float)
                for key in data.files
                if key.startswith("landmark_")
            }
            return cls(
                club=str(data["club"]),
                outline=np.asarray(data["outline"], dtype=bool),
                occupancy=np.asarray(data["occupancy"], dtype=float),
                landmarks=landmarks,
                mm_per_px=float(data["mm_per_px"]),
                n_samples=int(data["n_samples"]),
                source=str(data["source"]),
                heel_reaches_hosel=bool(data["heel_reaches_hosel"]),
            )


@dataclass(frozen=True)
class Landmarks:
    """One frame's heel, toe and topline in IMAGE pixels, however they were got."""

    heel: np.ndarray
    toe: np.ndarray
    topline: np.ndarray

    def as_dict(self) -> dict[str, np.ndarray]:
        """The three points, keyed the way the template stores them."""
        return {"heel": self.heel, "toe": self.toe, "topline": self.topline}


def bootstrap_landmarks(mask: np.ndarray) -> Landmarks | None:
    """Landmarks from a head mask's OWN extremes, with no marks and no template.

    This is how a session with no labels starts: the heel is the mask's
    leftmost pixel, the toe its rightmost, and the topline the top edge at the
    midpoint of the two. It is deliberately crude -- it is a seed for
    `build_template_self_built`, which then refines it across the session.
    """
    rows, cols = np.nonzero(mask)
    if rows.size < MIN_HEAD_AREA_PX:
        return None
    left, right = int(cols.min()), int(cols.max())
    heel_row = float(rows[cols == left].mean())
    toe_row = float(rows[cols == right].mean())
    middle = int(round(0.5 * (left + right)))
    column = mask[:, middle]
    if not column.any():
        return None
    return Landmarks(
        heel=np.array([float(left), heel_row]),
        toe=np.array([float(right), toe_row]),
        topline=np.array([float(middle), float(np.flatnonzero(column)[0])]),
    )


def _sample_to_template(
    swing: SwingFrames,
    mask: np.ndarray,
    frame: int,
    landmarks: Landmarks,
    reference_mm_per_px: float,
    *,
    heel_open: bool,
    side_margin_px: float = SIDE_MARGIN_PX,
    top_margin_px: float = CROP_TOP_MARGIN_PX,
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """One mask on the common grid, cropped on the TOP and the TOE side only.

    The crop is asymmetric on purpose. The toe side is cut at the toe landmark,
    the top a hair above the topline, and the heel side is not cut at all --
    that is decision 2 in the module docstring, and it is what lets the heel
    edge come from the data.
    """
    heel = np.asarray(landmarks.heel, dtype=float)
    toe = np.asarray(landmarks.toe, dtype=float)
    topline = np.asarray(landmarks.topline, dtype=float)
    anchor = np.array([0.5 * (heel[0] + toe[0]), topline[1]])

    step = reference_mm_per_px / (UPSAMPLE * swing.mm_per_px(frame))
    grid_y, grid_x = np.mgrid[0 : TEMPLATE_SHAPE[0], 0 : TEMPLATE_SHAPE[1]].astype(np.float32)
    local_x = (grid_x - TEMPLATE_ORIGIN[0]) * step
    local_y = (grid_y - TEMPLATE_ORIGIN[1]) * step
    map_x = (anchor[0] + local_x).astype(np.float32)
    map_y = (anchor[1] + local_y).astype(np.float32)
    warped = cv2.remap(mask.astype(np.float32), map_x, map_y, cv2.INTER_LINEAR, borderValue=0.0)

    def to_template(point: np.ndarray) -> np.ndarray:
        return (point - anchor) / step + np.asarray(TEMPLATE_ORIGIN, dtype=float)

    placed = {
        "heel": to_template(heel),
        "toe": to_template(toe),
        "topline": to_template(topline),
        "midpoint": np.asarray(TEMPLATE_ORIGIN, dtype=float),
    }
    keep = (grid_x <= placed["toe"][0] + side_margin_px / step) & (
        grid_y - TEMPLATE_ORIGIN[1] >= -top_margin_px / step
    )
    if not heel_open:
        keep = keep & (grid_x >= placed["heel"][0] - side_margin_px / step)
    return np.where(keep, warped, 0.0), placed


def _column_runs(above: np.ndarray) -> np.ndarray:
    """Per column: the run of True starting at that column's topmost True."""
    keep = np.zeros_like(above)
    for column in range(above.shape[1]):
        rows = np.flatnonzero(above[:, column])
        if rows.size == 0:
            continue
        top = int(rows[0])
        bottom = top
        while bottom + 1 < above.shape[0] and above[bottom + 1, column]:
            bottom += 1
        keep[top : bottom + 1, column] = True
    return keep


def _row_runs(above: np.ndarray) -> np.ndarray:
    """Per row: the run of True starting at that row's rightmost True, going LEFT."""
    keep = np.zeros_like(above)
    for row in range(above.shape[0]):
        columns = np.flatnonzero(above[row])
        if columns.size == 0:
            continue
        right = int(columns[-1])
        left = right
        while left - 1 >= 0 and above[row, left - 1]:
            left -= 1
        keep[row, left : right + 1] = True
    return keep


def outline_from_occupancy(occupancy: np.ndarray, *, heel_from_data: bool) -> np.ndarray:
    """Column runs (the sole) intersected with row runs (the heel)."""
    above = occupancy >= OCCUPANCY_THRESHOLD
    columns = _column_runs(above)
    if not heel_from_data:
        return columns
    return columns & _row_runs(above)


def build_template(
    samples: Sequence[tuple[SwingFrames, int, np.ndarray, Landmarks]],
    club: str,
    reference_mm_per_px: float,
    *,
    source: str,
    heel_from_data: bool = True,
    excluded: str | None = None,
) -> ClubOutlineTemplate | None:
    """Mean occupancy of every supplied sample, with a data-built sole and heel.

    Pre-registered fallback, fixed before any error was read: if more than
    `FALLBACK_EDGE_FRACTION` of the outline's occupied rows still reach the
    grid's left edge, the occupancy never fell through 0.5 on the way left, and
    the heel reverts to a crop at the landmark.
    """
    if not samples:
        return None
    stack: list[np.ndarray] = []
    landmark_stack: dict[str, list[np.ndarray]] = {}
    for swing, frame, mask, marks in samples:
        warped, placed = _sample_to_template(
            swing, mask, frame, marks, reference_mm_per_px, heel_open=heel_from_data
        )
        stack.append(warped)
        for name, point in placed.items():
            landmark_stack.setdefault(name, []).append(point)
    occupancy = np.mean(np.stack(stack), axis=0)
    outline = outline_from_occupancy(occupancy, heel_from_data=heel_from_data)

    occupied_rows = outline.any(axis=1)
    left_edge_fraction = (
        float(outline[:, 0][occupied_rows].mean()) if int(occupied_rows.sum()) else 0.0
    )
    if heel_from_data and left_edge_fraction > FALLBACK_EDGE_FRACTION:
        return build_template(
            samples,
            club,
            reference_mm_per_px,
            source=source,
            heel_from_data=False,
            excluded=excluded,
        )
    occupied_columns = outline.any(axis=0)
    floor_fraction = (
        float(outline[-1, :][occupied_columns].mean()) if int(occupied_columns.sum()) else 0.0
    )
    ious = []
    for warped in stack:
        binary = warped >= 0.5
        union = int((binary | outline).sum())
        ious.append(float((binary & outline).sum()) / union if union else math.nan)
    return ClubOutlineTemplate(
        club=club,
        outline=outline,
        occupancy=occupancy,
        landmarks={
            name: np.mean(np.stack(points), axis=0) for name, points in landmark_stack.items()
        },
        mm_per_px=reference_mm_per_px,
        n_samples=len(stack),
        source=source,
        sample_iou=tuple(ious),
        heel_reaches_hosel=heel_from_data,
        left_edge_fraction=left_edge_fraction,
        floor_fraction=floor_fraction,
        excluded=excluded,
    )


def build_template_from_labels(
    swings: Mapping[str, SwingFrames],
    marks: Mapping[tuple[str, int], Landmarks],
    club: str,
    reference_mm_per_px: float,
    *,
    exclude: str | None = None,
) -> ClubOutlineTemplate | None:
    """Build from hand-marked frames. REGRESSION ONLY -- see the module docstring.

    ``exclude`` leaves one swing out, so a swing being scored never contributed
    to the outline it is scored against. Every published figure for this
    extractor is leave-one-shot-out, and a template built without it means
    nothing.
    """
    samples = []
    for name in sorted(swings, key=lambda key: swings[key].name or key):
        if name == exclude or swings[name].club != club:
            continue
        report = head_masks(swings[name])
        for (mark_name, frame), landmarks in marks.items():
            if mark_name != name or frame not in report.masks:
                continue
            samples.append((swings[name], frame, report.masks[frame], landmarks))
    return build_template(samples, club, reference_mm_per_px, source="labels", excluded=exclude)


@dataclass(frozen=True)
class SelfBuildReport:
    """A self-built template, its convergence, and whether the toe was calibrated.

    ``toe_calibrated`` False on a template that DID build means the session
    had fewer than `MIN_TOE_CALIBRATION_FRAMES` slow frames and the toe
    landmark is the raw average -- the pre-2026-09-01 landmark, which carries
    the smeared toe. Read it before trusting absolute offsets.
    ``toe_overhang_mm`` is the median overhang that was subtracted: on the
    2026-08-25 session it is of order 5-9 mm.
    """

    template: ClubOutlineTemplate | None
    movement_px: tuple[float, ...] = ()
    converged: bool = False
    reason: str = "ok"
    toe_calibrated: bool = False
    toe_calibration_frames: int = 0
    toe_overhang_mm: float | None = None


def build_template_self_built(
    swings: Sequence[SwingFrames],
    club: str,
    reference_mm_per_px: float,
    *,
    passes: int = 3,
    convergence_px: float = 0.5,
    exclude: str | None = None,
    toe_calibration: bool = True,
) -> SelfBuildReport:
    """Build from a golfer's OWN swings, with no marks anywhere.

    The first pass seeds each sample's landmarks from that mask's own extremes
    (`bootstrap_landmarks`). Every later pass re-reads them off the template
    placed on that frame, so the crop stops being one frame's accident and
    becomes the session's average. Convergence is reported, not assumed: the
    movement of the mean landmark between passes is returned, and
    ``converged`` says whether the last pass moved it less than
    ``convergence_px``.

    Last, the toe landmark is calibrated against the frames measured slow
    (`calibrate_toe_landmark`, `MAX_TEMPLATE_SPEED_MM_PER_FRAME`), because the
    raw average inherits the motion smear of the fast frames.
    ``toe_calibration=False`` is the ablation, for tests and audits only.
    """
    frames: list[tuple[SwingFrames, int, np.ndarray]] = []
    slow_frames: list[tuple[SwingFrames, int, np.ndarray]] = []
    for swing in swings:
        if swing.name == exclude or (club and swing.club != club):
            continue
        report = head_masks(swing)
        speeds = mask_speeds(swing, report)
        for frame, mask in sorted(report.masks.items()):
            frames.append((swing, frame, mask))
            speed = speeds.get(frame)
            # No estimate is "unknown", never "slow".
            if speed is not None and speed <= MAX_TEMPLATE_SPEED_MM_PER_FRAME:
                slow_frames.append((swing, frame, mask))
    if not frames:
        return SelfBuildReport(None, reason="no_head_masks_in_the_session")

    seeded = [(swing, frame, mask, bootstrap_landmarks(mask)) for swing, frame, mask in frames]
    samples = [(s, f, m, marks) for s, f, m, marks in seeded if marks is not None]
    if not samples:
        return SelfBuildReport(None, reason="no_mask_carried_bootstrap_landmarks")

    template = build_template(samples, club, reference_mm_per_px, source="self-built")
    if template is None:
        return SelfBuildReport(None, reason="template_did_not_build")
    movement: list[float] = []
    for _ in range(max(0, passes - 1)):
        refined: list[tuple[SwingFrames, int, np.ndarray, Landmarks]] = []
        for swing, frame, mask, _seed in samples:
            alignment = align_frame(swing, template, frame, mask)
            if alignment is None:
                continue
            refined.append(
                (
                    swing,
                    frame,
                    mask,
                    Landmarks(
                        heel=alignment.landmarks["heel"],
                        toe=alignment.landmarks["toe"],
                        topline=alignment.landmarks["topline"],
                    ),
                )
            )
        if not refined:
            break
        rebuilt = build_template(refined, club, reference_mm_per_px, source="self-built")
        if rebuilt is None:
            break
        moved = max(
            float(np.hypot(*(rebuilt.landmarks[name] - template.landmarks[name])))
            for name in LANDMARKS
        )
        movement.append(moved * template.mm_per_template_px / max(template.mm_per_px, 1e-9))
        template, samples = rebuilt, refined
        if movement[-1] <= convergence_px:
            break

    calibration = calibrate_toe_landmark(template, slow_frames) if toe_calibration else None
    if calibration is not None:
        template = calibration[0]
    return SelfBuildReport(
        template=template,
        movement_px=tuple(movement),
        converged=bool(movement) and movement[-1] <= convergence_px,
        toe_calibrated=calibration is not None,
        toe_calibration_frames=len(slow_frames) if calibration is not None else 0,
        toe_overhang_mm=None if calibration is None else calibration[1],
    )


def calibrate_toe_landmark(
    template: ClubOutlineTemplate,
    slow_frames: Sequence[tuple[SwingFrames, int, np.ndarray]],
) -> tuple[ClubOutlineTemplate, float] | None:
    """Pull the toe landmark in by its median overhang past the SLOW masks' toes.

    The template is placed on every slow frame exactly as a reading would
    place it; the overhang is the placed toe landmark's column minus the
    mask's own maximum column, in millimetres at that frame's range. The
    median over the frames is subtracted from the landmark along the outline's
    heel-to-toe direction. Nothing else about the template changes, so every
    alignment gate sees the same shape it was validated on.

    Returns None -- no calibration, template untouched -- with fewer than
    `MIN_TOE_CALIBRATION_FRAMES` slow frames or placements.
    """
    if len(slow_frames) < MIN_TOE_CALIBRATION_FRAMES:
        return None
    overhangs: list[float] = []
    for swing, frame, mask in slow_frames:
        alignment = align_frame(swing, template, frame, mask)
        if alignment is None:
            continue
        cols = np.flatnonzero(mask.any(axis=0))
        if cols.size == 0:
            continue
        placed_toe_x = float(alignment.landmarks["toe"][0])
        overhangs.append((placed_toe_x - float(cols.max())) * swing.mm_per_px(frame))
    if len(overhangs) < MIN_TOE_CALIBRATION_FRAMES:
        return None
    overhang_mm = float(np.median(overhangs))
    heel, toe = template.landmarks["heel"], template.landmarks["toe"]
    span = toe - heel
    length = float(np.hypot(*span))
    if length <= 0.0:
        return None
    unit = span / length
    shifted = dict(template.landmarks)
    shifted["toe"] = toe - unit * (overhang_mm / template.mm_per_template_px)
    return replace(template, landmarks=shifted), overhang_mm


def build_template_from_address_photo(
    photo: np.ndarray, club: str, reference_mm_per_px: float
) -> ClubOutlineTemplate:
    """Build from ONE address frame, before the swing. Reserved, not implemented.

    The API is fixed here because the impact-zone convention depends on it: the
    face centre is currently the outline midpoint plus 8 mm toe-ward, which is
    a convention and not a measurement, and an address photo with the ball at a
    known place is what replaces it. Implementing it needs a session that
    provides such a frame, and the 2026-08-25 export does not.
    """
    raise NotImplementedError(
        "an address-photo template needs a session that captures the address "
        "frame with the ball at a known position; the 2026-08-25 export has "
        "none. See docs/clubface-impact-location.md, Stage 1."
    )


def bootstrap_outline_from_category(
    club: str, category: str = DEFAULT_CATEGORY, mm_per_px: float = 3.5732
) -> ClubOutlineTemplate:
    """A rectangle the size of a 7-iron of ``category``, to seed a first swing.

    The blade lengths come from official manufacturer data -- the Mizuno
    irons-comparison chart JSON and Srixon's `Iron_Shape_Comparison.pdf` -- and
    are used ONLY to size an outline before the session has built its own. It
    is never an answer, and the result it seeds is marked ``self-built``.
    """
    if category not in CATEGORY_BLADE_LENGTH_MM:
        raise ValueError(
            f"unknown club category {category!r}; expected one of "
            f"{sorted(CATEGORY_BLADE_LENGTH_MM)}"
        )
    low, high = CATEGORY_BLADE_LENGTH_MM[category]
    width_mm = 0.5 * (low + high)
    # A mid-iron face is 45-50 mm sole to crown; the seed is a plain rectangle.
    height_mm = 47.5
    per_px = mm_per_px / UPSAMPLE
    half_width = int(round(0.5 * width_mm / per_px))
    depth = int(round(height_mm / per_px))
    outline = np.zeros(TEMPLATE_SHAPE, dtype=bool)
    origin_x, origin_y = TEMPLATE_ORIGIN
    outline[origin_y : origin_y + depth, origin_x - half_width : origin_x + half_width] = True
    return ClubOutlineTemplate(
        club=club,
        outline=outline,
        occupancy=outline.astype(float),
        landmarks={
            "heel": np.array([float(origin_x - half_width), float(origin_y)]),
            "toe": np.array([float(origin_x + half_width), float(origin_y)]),
            "topline": np.array([float(origin_x), float(origin_y)]),
            "midpoint": np.array([float(origin_x), float(origin_y)]),
        },
        mm_per_px=mm_per_px,
        n_samples=0,
        source="self-built",
        heel_reaches_hosel=False,
    )


# ---------------------------------------------------------------------------
# alignment
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Alignment:
    """One frame's placement of the outline, and what it reads off."""

    frame: int
    iou: float
    roll_deg: float
    step: float
    anchor: np.ndarray
    landmarks: dict[str, np.ndarray] = field(default_factory=dict)
    mask_area: int = 0
    on_roll_boundary: bool = False
    on_radius_boundary: bool = False
    ridge_dy_px: float = 0.0


def _affine(step: float, angle_deg: float, anchor_x: float, anchor_y: float) -> np.ndarray:
    """Template pixels -> image pixels, rotating and scaling about the origin."""
    radians = math.radians(angle_deg)
    cosine, sine = math.cos(radians), math.sin(radians)
    origin_x, origin_y = TEMPLATE_ORIGIN
    return np.array(
        [
            [step * cosine, -step * sine, anchor_x - step * (cosine * origin_x - sine * origin_y)],
            [step * sine, step * cosine, anchor_y - step * (sine * origin_x + cosine * origin_y)],
        ]
    )


def _matrix(alignment: Alignment) -> np.ndarray:
    return _affine(
        alignment.step, alignment.roll_deg, float(alignment.anchor[0]), float(alignment.anchor[1])
    )


def _patch_extent(
    template: ClubOutlineTemplate, step: float, angle_deg: float
) -> tuple[np.ndarray, np.ndarray]:
    rows, cols = np.nonzero(template.outline)
    local = _affine(step, angle_deg, 0.0, 0.0) @ np.stack(
        [cols.astype(float), rows.astype(float), np.ones(cols.size)]
    )
    return local.min(axis=1), local.max(axis=1)


def _rasterise(
    template: ClubOutlineTemplate,
    step: float,
    angle_deg: float,
    anchor_in_patch: np.ndarray,
    shape: tuple[int, int],
) -> np.ndarray:
    matrix = _affine(step, angle_deg, float(anchor_in_patch[0]), float(anchor_in_patch[1]))
    return (
        cv2.warpAffine(
            template.outline.astype(np.uint8),
            matrix,
            (shape[1], shape[0]),
            flags=cv2.INTER_NEAREST,
        )
        > 0
    )


def _local_iou(mask: np.ndarray, patch: np.ndarray, top_left: tuple[int, int]) -> float:
    """IoU of the placed outline against the mask INSIDE the outline's own box.

    A GLOBAL IoU would be dominated by the shaft and hosel, which the template
    was cropped away from but which are still in the observed mask.
    """
    row, col = top_left
    height, width = patch.shape
    if row < 0 or col < 0 or row + height > mask.shape[0] or col + width > mask.shape[1]:
        return math.nan
    window = mask[row : row + height, col : col + width]
    intersection = float((window & patch).sum())
    union = float(patch.sum()) + float(window.sum()) - intersection
    return intersection / union if union > 0 else math.nan


def _coarse_search(
    mask: np.ndarray, template: ClubOutlineTemplate, step: float, ball: tuple[float, float]
) -> tuple[float, float, np.ndarray] | None:
    """Every roll, every translation, by FFT cross-correlation. Best IoU wins."""
    floating = mask.astype(np.float32)
    integral = cv2.integral(mask.astype(np.uint8))
    best_iou, best_angle = -1.0, 0.0
    best_anchor = np.zeros(2)
    angle = -ROLL_LIMIT_DEG
    while angle <= ROLL_LIMIT_DEG + 1e-9:
        low, high = _patch_extent(template, step, angle)
        anchor_in_patch = -low + PATCH_PAD_PX
        shape = (
            int(math.ceil(high[1] - low[1])) + 1 + 2 * PATCH_PAD_PX,
            int(math.ceil(high[0] - low[0])) + 1 + 2 * PATCH_PAD_PX,
        )
        if shape[0] >= mask.shape[0] or shape[1] >= mask.shape[1]:
            angle += ROLL_STEP_DEG
            continue
        patch = _rasterise(template, step, angle, anchor_in_patch, shape)
        area = float(patch.sum())
        if area <= 0:
            angle += ROLL_STEP_DEG
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
        ) ** 2 <= MAX_ANCHOR_RADIUS_PX**2
        iou = np.where(near, iou, -1.0)
        index = int(np.argmax(iou))
        value = float(iou.ravel()[index])
        if value > best_iou:
            row, col = divmod(index, iou.shape[1])
            best_iou, best_angle = value, angle
            best_anchor = np.array([col + anchor_in_patch[0], row + anchor_in_patch[1]])
        angle += ROLL_STEP_DEG
    if best_iou <= 0.0:
        return None
    return (best_iou, best_angle, best_anchor)


def _score(
    mask: np.ndarray,
    template: ClubOutlineTemplate,
    step: float,
    angle: float,
    anchor: np.ndarray,
) -> float:
    low, high = _patch_extent(template, step, angle)
    offset = anchor + low
    top_left = (int(math.floor(offset[1])), int(math.floor(offset[0])))
    anchor_in_patch = anchor - np.array([top_left[1], top_left[0]], dtype=float)
    shape = (
        int(math.ceil(high[1] - low[1])) + 2 + 2 * PATCH_PAD_PX,
        int(math.ceil(high[0] - low[0])) + 2 + 2 * PATCH_PAD_PX,
    )
    patch = _rasterise(template, step, angle, anchor_in_patch, shape)
    return _local_iou(mask, patch, top_left)


def align_frame(
    swing: SwingFrames,
    template: ClubOutlineTemplate,
    frame: int,
    mask: np.ndarray,
) -> Alignment | None:
    """Scale by the RADAR range, then search translation and roll for best IoU.

    The scale is not fitted. `swing.range_mm(frame)` walks the radar's own rate
    back from the taped ball range at contact, and the outline is resized by
    that ratio, which tracks a 36 % apparent-size change across seven frames
    with no free parameter.
    """
    step = template.mm_per_px / (UPSAMPLE * swing.mm_per_px(frame))
    ball = (swing.ball.x, swing.ball.y)
    coarse = _coarse_search(mask, template, step, ball)
    if coarse is None:
        return None
    best_iou, best_angle, best_anchor = coarse
    for _ in range(2):
        improved = False
        angles = np.arange(
            best_angle - REFINE_ROLL_DEG, best_angle + REFINE_ROLL_DEG + 1e-9, REFINE_ROLL_STEP_DEG
        )
        shifts = np.arange(-REFINE_SHIFT_PX, REFINE_SHIFT_PX + 1e-9, REFINE_SHIFT_STEP_PX)
        for angle in angles:
            if abs(angle) > ROLL_LIMIT_DEG:
                continue
            for delta_x in shifts:
                for delta_y in shifts:
                    anchor = best_anchor + np.array([delta_x, delta_y])
                    value = _score(mask, template, step, float(angle), anchor)
                    if math.isfinite(value) and value > best_iou + 1e-9:
                        best_iou, best_angle, best_anchor = value, float(angle), anchor
                        improved = True
        if not improved:
            break
    matrix = _affine(step, best_angle, float(best_anchor[0]), float(best_anchor[1]))
    return Alignment(
        frame=frame,
        iou=best_iou,
        roll_deg=best_angle,
        step=step,
        anchor=best_anchor,
        landmarks={
            name: matrix[:, :2] @ template.landmarks[name] + matrix[:, 2] for name in LANDMARKS
        },
        mask_area=int(mask.sum()),
        on_roll_boundary=abs(abs(best_angle) - ROLL_LIMIT_DEG) < 1e-6,
        on_radius_boundary=bool(
            abs(
                math.hypot(best_anchor[0] - ball[0], best_anchor[1] - ball[1])
                - MAX_ANCHOR_RADIUS_PX
            )
            < 1.0
        ),
    )


# ---------------------------------------------------------------------------
# the geometry of a placed outline
# ---------------------------------------------------------------------------


def placed_pixels(template: ClubOutlineTemplate, alignment: Alignment) -> np.ndarray:
    """Every outline pixel, in image coordinates. Shape (2, N)."""
    rows, cols = np.nonzero(template.outline)
    matrix = _matrix(alignment)
    return matrix[:, :2] @ np.stack([cols.astype(float), rows.astype(float)]) + matrix[:, 2:3]


def placed_extremes(template: ClubOutlineTemplate, alignment: Alignment) -> dict[str, np.ndarray]:
    """The placed outline's minimum-x and maximum-x points.

    Read this only when `ClubOutlineTemplate.heel_reaches_hosel` is False. With
    the heel edge taken from the data the outline runs up the hosel neck, so
    its leftmost point is the hosel and not the heel -- measured at -10.5 px,
    which is the length of that tail.
    """
    points = placed_pixels(template, alignment)
    return {
        "heel_extent": points[:, int(np.argmin(points[0]))],
        "toe_extent": points[:, int(np.argmax(points[0]))],
    }


def placed_top_edge(
    template: ClubOutlineTemplate, alignment: Alignment
) -> tuple[np.ndarray, np.ndarray]:
    """The placed outline's top edge as an (x, y) polyline, sorted by x."""
    cols = np.flatnonzero(template.outline.any(axis=0))
    if cols.size == 0:
        return np.empty(0), np.empty(0)
    rows = template.outline.argmax(axis=0)[cols]
    matrix = _matrix(alignment)
    points = matrix[:, :2] @ np.stack([cols.astype(float), rows.astype(float)]) + matrix[:, 2:3]
    order = np.argsort(points[0])
    return points[0][order], points[1][order]


def top_edge_at(xs: np.ndarray, ys: np.ndarray, x: float) -> float:
    """The top edge's row at one image column, or NaN outside the outline."""
    if xs.size == 0 or x < xs[0] or x > xs[-1]:
        return math.nan
    return float(np.interp(x, xs, ys))


# ---------------------------------------------------------------------------
# the specular topline ridge
# ---------------------------------------------------------------------------


def _sample_intensity(image: np.ndarray, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    return cv2.remap(
        image.astype(np.float32),
        xs.astype(np.float32).reshape(1, -1),
        ys.astype(np.float32).reshape(1, -1),
        cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE,
    )[0]


def _ridge_columns(
    swing: SwingFrames, template: ClubOutlineTemplate, alignment: Alignment
) -> tuple[np.ndarray, np.ndarray]:
    """Integer image columns on the TOE side, clear of the ball, and their top edge."""
    xs, ys = placed_top_edge(template, alignment)
    if xs.size == 0:
        return np.empty(0), np.empty(0)
    columns = np.arange(math.ceil(xs[0]), math.floor(xs[-1]) + 1, dtype=float)
    if columns.size == 0:
        return np.empty(0), np.empty(0)
    edge = np.interp(columns, xs, ys)
    midpoint_x = alignment.landmarks["midpoint"][0]
    side = 1.0 if alignment.landmarks["toe"][0] >= alignment.landmarks["heel"][0] else -1.0
    radius = swing.ball.diameter_px / 2.0
    keep = ((columns - midpoint_x) * side >= 0.0) & (np.abs(columns - swing.ball.x) > radius)
    return columns[keep], edge[keep]


def _ridge_response(image: np.ndarray, xs: np.ndarray, ys: np.ndarray) -> float:
    """Mean line response: bright ON the edge, dark both sides of it."""
    centre = _sample_intensity(image, xs, ys)
    above = _sample_intensity(image, xs, ys - RIDGE_SPAN_PX)
    below = _sample_intensity(image, xs, ys + RIDGE_SPAN_PX)
    return float(np.mean(centre - 0.5 * (above + below)))


def refine_on_ridge(
    swing: SwingFrames,
    template: ClubOutlineTemplate,
    alignment: Alignment,
    frame: int,
    mask: np.ndarray,
) -> Alignment:
    """Shift the placement vertically to fuse the mask IoU with the topline ridge.

    The topline is specular and sharp: on the toe-side columns of the last two
    frames it measures 199-203 counts against ~127 two pixels above and ~113
    two pixels below. Fusing that line response with the mask IoU cut the
    topline p90 from 2.32 px to 1.48 px. It saturates its own +-3 px window on
    some frames, so the window rather than the ridge is the binding constraint
    there, and `Alignment.ridge_dy_px` records how far it moved.
    """
    columns, edge = _ridge_columns(swing, template, alignment)
    if columns.size < RIDGE_MIN_COLUMNS:
        return alignment
    image = swing.frames[frame]
    offsets = np.arange(-RIDGE_SEARCH_PX, RIDGE_SEARCH_PX + 1e-9, RIDGE_STEP_PX)
    ridge = np.array([_ridge_response(image, columns, edge + dy) for dy in offsets])
    iou = np.array(
        [
            _score(
                mask,
                template,
                alignment.step,
                alignment.roll_deg,
                alignment.anchor + np.array([0.0, dy]),
            )
            for dy in offsets
        ]
    )
    finite = np.isfinite(iou) & np.isfinite(ridge)
    if finite.sum() < 3:
        return alignment

    def unit(values: np.ndarray) -> np.ndarray:
        span = np.nanmax(values[finite]) - np.nanmin(values[finite])
        if span <= 0:
            return np.zeros_like(values)
        return (values - np.nanmin(values[finite])) / span

    fused = np.where(finite, (1.0 - RIDGE_WEIGHT) * unit(iou) + RIDGE_WEIGHT * unit(ridge), -1.0)
    best = int(np.argmax(fused))
    delta = float(offsets[best])
    if abs(delta) < 1e-9:
        return alignment
    anchor = alignment.anchor + np.array([0.0, delta])
    matrix = _affine(alignment.step, alignment.roll_deg, float(anchor[0]), float(anchor[1]))
    return replace(
        alignment,
        anchor=anchor,
        iou=float(iou[best]),
        ridge_dy_px=delta,
        landmarks={
            name: matrix[:, :2] @ template.landmarks[name] + matrix[:, 2] for name in LANDMARKS
        },
    )


def align_swing(
    swing: SwingFrames,
    template: ClubOutlineTemplate,
    report: MaskReport,
    *,
    ridge: bool = True,
) -> tuple[dict[int, Alignment], dict[int, str]]:
    """Align every frame with a mask, then apply the acceptance gates.

    Three gates, all fail-closed and all pre-registered:
    local IoU below `IOU_GATE`; a search that finished ON the roll or radius
    boundary, which means the true placement is outside the box; and a
    midpoint more than `TRACK_GATE_PX` off the swing's own fitted line, which
    is the brief's +-12 px re-imposed as a track constraint.
    """
    alignments: dict[int, Alignment] = {}
    rejected: dict[int, str] = dict(report.reasons)
    for frame, mask in sorted(report.masks.items()):
        alignment = align_frame(swing, template, frame, mask)
        if alignment is None:
            rejected[frame] = "no_placement_scored_above_zero"
            continue
        if ridge:
            alignment = refine_on_ridge(swing, template, alignment, frame, mask)
        alignments[frame] = alignment

    accepted = {}
    for frame, alignment in alignments.items():
        if alignment.iou < IOU_GATE:
            rejected[frame] = f"local_iou_{alignment.iou:.3f}_below_gate"
        elif alignment.on_roll_boundary:
            rejected[frame] = "search_finished_on_the_roll_boundary"
        elif alignment.on_radius_boundary:
            rejected[frame] = "search_finished_on_the_radius_boundary"
        else:
            accepted[frame] = alignment

    track = sorted(f for f in accepted if f <= swing.contact_frame)
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
            if residual > TRACK_GATE_PX:
                del accepted[frame]
                rejected[frame] = f"midpoint_{residual:.1f}_px_off_the_swing_track"
    return accepted, rejected

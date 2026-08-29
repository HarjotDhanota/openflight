"""Iteration 2: align the data-built outline, refine on the topline ridge, carry.

NO GROUND TRUTH EXISTS. Every error here is a CONSISTENCY figure against the
maintainer's PASS-1 hand marks -- one human's reading of the same pixels, and
the marks also define the template's crop, origin and landmark convention.
Pass 2 is a second annotator with a different convention and is never read.

Three changes from `align_and_carry.py`, each switchable so the iteration-1
configuration can be re-run through this same code for an honest before/after:

  1. `--sole-mode occupancy` (default) takes the outline from
     `build_template_v2`, whose bottom edge is the per-column depth at which the
     mean occupancy falls through 0.5 rather than a horizontal 48 mm cut.
     `--sole-mode cut --top-margin 0` is iteration 1.
  2. `--ridge` (default) adds a VERTICAL refinement after the IoU search. The
     clubhead's topline is a specular ridge: measured on the toe-side columns
     of f71/f72, mean intensity is ~200 counts on the mask's top row against
     ~127 two pixels above and ~115 two pixels below. The refinement sweeps a
     vertical offset over +-3 px and maximises an equal-weight, min-max
     normalised fusion of (a) the local IoU against the mask and (b) that ridge
     response. Weights and window were fixed before any error was read.
     `--no-ridge` disables it.
  3. THE CARRY. The primary model is now interpolation between the aligned f71
     and f72 outlines at the acoustic contact instant; the cross-check is a
     quadratic over every accepted frame of f66-f72. The brief's linear carry
     is dropped -- iteration 1 measured its residual at 3-6 px, five to ten
     times the alignment error, because the projected midpoint decelerates as
     the club swings onto the optical axis.

TWO SCORING CONVENTIONS are reported side by side, because the pass-1 marks are
not where iteration 1 assumed:

  * RAW / landmark: the template's mean-mark landmark, transformed. This is
    iteration 1's convention.
  * DEFINITION-MATCHED: the toe mark is the head's WIDEST point (measured: a
    median 9.9 px BELOW the topline row), so it is scored against the aligned
    outline's maximum-x extent; the heel likewise against its minimum-x extent.
    The topline mark sits a median +11.0 px to the right of the ball centre --
    the ball's cap hides the crown directly above it -- and the crown slopes
    -0.33 px/px, so the topline is scored at THE MARK'S OWN COLUMN on the
    aligned outline's top edge, not at a single fixed point.

    uv run python research/empirical_template/align_and_carry_v2.py \
        --session /path/to/export --out research/empirical_template/results
"""

# pylint: disable=wrong-import-position,no-member,unsubscriptable-object

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from align_and_carry import (  # noqa: E402
    IOU_GATE,
    LANDMARKS,
    MIN_CARRY_FRAMES,
    PASS_AVAILABILITY,
    PASS_MEDIAN_PX,
    PASS_P90_PX,
    TRACK_GATE_PX,
    Alignment,
    _affine,
    _line_fit,
    _percentiles,
    _score,
    _write_csv,
    align_frame,
)
from build_template import (  # noqa: E402
    ALIGN_FRAMES,
    BACKGROUND_SLICE,
    CAMERA_BALL_RANGE_MM,
    COMPONENT_RADIUS_PX,
    DEFAULT_MARKS,
    MARK_FRAMES,
    MIN_COMPONENT_AREA_PX,
    MOVING_FLOOR,
    MOVING_SIGMA,
    ShotFrames,
    load_session,
    reference_scale,
    session_path,
)
from build_template_v2 import (  # noqa: E402
    CROP_TOP_MARGIN_PX,
    SOLE_MODES,
    TemplateV2,
    build_template_v2,
    load_marks_pass1,
)
from scipy import ndimage  # noqa: E402

from openflight.camera.clubpose.head_split import split_head  # noqa: E402

# Vertical refinement on the specular topline ridge. Fixed before scoring.
RIDGE_SEARCH_PX = 3.0
RIDGE_STEP_PX = 0.25
RIDGE_SPAN_PX = 2.0  # the ridge is ~1 px wide; sample 2 px either side of it
RIDGE_MIN_COLUMNS = 4
RIDGE_WEIGHT = 0.5  # equal weight with the mask IoU, min-max normalised

CARRY_FIT_FRAMES = ALIGN_FRAMES  # f66-f72, including f72: the quadratic cross-check
MIN_QUAD_FRAMES = 4

HOSEL_MIN_SHAFT_PX = 20

ROLL_LOCK_SHOTS = (4, 15, 21)
ROLL_LOCK_FRAME = 72
ROLL_LOCK_ZOOM = 8


# --------------------------------------------------------------------------
# geometry of the placed outline
# --------------------------------------------------------------------------


def _matrix(alignment: Alignment) -> np.ndarray:
    return _affine(
        alignment.step,
        alignment.roll_deg,
        float(alignment.anchor[0]),
        float(alignment.anchor[1]),
    )


def placed_pixels(template: TemplateV2, alignment: Alignment) -> np.ndarray:
    """Every outline pixel, in image coordinates. Shape (2, N)."""
    rows, cols = np.nonzero(template.outline)
    matrix = _matrix(alignment)
    return matrix[:, :2] @ np.stack([cols.astype(float), rows.astype(float)]) + matrix[:, 2:3]


def placed_extremes(template: TemplateV2, alignment: Alignment) -> dict[str, np.ndarray]:
    """The outline's minimum-x and maximum-x points: the heel and toe as marked.

    The pass-1 toe mark is the head's widest point, a median 9.9 px below the
    topline row, so the extent -- not a landmark on the topline row -- is what
    it should be compared with.
    """
    points = placed_pixels(template, alignment)
    return {
        "heel_extent": points[:, int(np.argmin(points[0]))],
        "toe_extent": points[:, int(np.argmax(points[0]))],
    }


def placed_top_edge(template: TemplateV2, alignment: Alignment) -> tuple[np.ndarray, np.ndarray]:
    """The outline's top edge as an (x, y) polyline in image coordinates."""
    outline = template.outline
    cols = np.flatnonzero(outline.any(axis=0))
    if cols.size == 0:
        return np.empty(0), np.empty(0)
    rows = outline.argmax(axis=0)[cols]
    matrix = _matrix(alignment)
    points = matrix[:, :2] @ np.stack([cols.astype(float), rows.astype(float)]) + matrix[:, 2:3]
    order = np.argsort(points[0])
    return points[0][order], points[1][order]


def top_edge_at(xs: np.ndarray, ys: np.ndarray, x: float) -> float:
    """The top edge's row at one image column, or NaN outside the outline."""
    if xs.size == 0 or x < xs[0] or x > xs[-1]:
        return math.nan
    return float(np.interp(x, xs, ys))


# --------------------------------------------------------------------------
# topline ridge refinement
# --------------------------------------------------------------------------


def _sample_intensity(image: np.ndarray, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    return cv2.remap(
        image.astype(np.float32),
        xs.astype(np.float32).reshape(1, -1),
        ys.astype(np.float32).reshape(1, -1),
        cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE,
    )[0]


def _ridge_columns(
    shot: ShotFrames, template: TemplateV2, alignment: Alignment
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
    radius = shot.ball.diameter_px / 2.0
    keep = ((columns - midpoint_x) * side >= 0.0) & (np.abs(columns - shot.ball.x) > radius)
    return columns[keep], edge[keep]


def _ridge_response(image: np.ndarray, xs: np.ndarray, ys: np.ndarray) -> float:
    """Mean line response: bright ON the edge, dark both sides of it."""
    centre = _sample_intensity(image, xs, ys)
    above = _sample_intensity(image, xs, ys - RIDGE_SPAN_PX)
    below = _sample_intensity(image, xs, ys + RIDGE_SPAN_PX)
    return float(np.mean(centre - 0.5 * (above + below)))


def refine_on_ridge(
    shot: ShotFrames, template: TemplateV2, alignment: Alignment, frame: int
) -> tuple[Alignment, dict[str, float]]:
    """Shift the placement vertically to fuse the mask IoU with the topline ridge.

    Returns the refined alignment and a diagnostic dict. If there are too few
    usable toe-side columns the alignment is returned untouched.
    """
    mask = shot.masks.get(frame)
    columns, edge = _ridge_columns(shot, template, alignment)
    diagnostic = {"ridge_columns": float(columns.size), "ridge_dy_px": 0.0}
    if mask is None or columns.size < RIDGE_MIN_COLUMNS:
        diagnostic["ridge_applied"] = 0.0
        return alignment, diagnostic
    image = shot.frames[frame]
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
        diagnostic["ridge_applied"] = 0.0
        return alignment, diagnostic

    def _unit(values: np.ndarray) -> np.ndarray:
        span = np.nanmax(values[finite]) - np.nanmin(values[finite])
        if span <= 0:
            return np.zeros_like(values)
        return (values - np.nanmin(values[finite])) / span

    fused = np.where(finite, (1.0 - RIDGE_WEIGHT) * _unit(iou) + RIDGE_WEIGHT * _unit(ridge), -1.0)
    best = int(np.argmax(fused))
    delta = float(offsets[best])
    diagnostic.update(
        {
            "ridge_applied": 1.0,
            "ridge_dy_px": delta,
            "ridge_peak_response": float(ridge[best]),
            "iou_before_ridge": float(iou[int(np.argmin(np.abs(offsets)))]),
            "iou_after_ridge": float(iou[best]),
        }
    )
    if abs(delta) < 1e-9:
        return alignment, diagnostic
    anchor = alignment.anchor + np.array([0.0, delta])
    matrix = _affine(alignment.step, alignment.roll_deg, float(anchor[0]), float(anchor[1]))
    return (
        replace(
            alignment,
            anchor=anchor,
            iou=float(iou[best]),
            landmarks={
                name: matrix[:, :2] @ template.landmarks[name] + matrix[:, 2] for name in LANDMARKS
            },
        ),
        diagnostic,
    )


# --------------------------------------------------------------------------
# hosel visibility
# --------------------------------------------------------------------------


def shaft_masks(shot: ShotFrames) -> dict[int, np.ndarray]:
    """The `split_head` SHAFT half for f66-f72, for the hosel-visibility split."""
    frames = shot.frames
    background = np.median(frames[BACKGROUND_SLICE], axis=0)
    sigma = frames[BACKGROUND_SLICE].astype(float).std(axis=0)
    threshold = np.maximum(MOVING_SIGMA * sigma, MOVING_FLOOR)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    out: dict[int, np.ndarray] = {}
    for index in ALIGN_FRAMES:
        if not 0 <= index < frames.shape[0]:
            continue
        raw = np.abs(frames[index].astype(float) - background) > threshold
        closed = cv2.morphologyEx(raw.astype(np.uint8), cv2.MORPH_CLOSE, kernel)
        moving = ndimage.binary_fill_holes(closed > 0)
        count, labels, stats, centroids = cv2.connectedComponentsWithStats(
            moving.astype(np.uint8), 8
        )
        best: tuple[int, int] | None = None
        for label in range(1, count):
            area = int(stats[label, cv2.CC_STAT_AREA])
            distance = math.hypot(
                centroids[label][0] - shot.ball.x, centroids[label][1] - shot.ball.y
            )
            if area < MIN_COMPONENT_AREA_PX or distance > COMPONENT_RADIUS_PX:
                continue
            if best is None or area > best[0]:
                best = (area, label)
        if best is None:
            continue
        split = split_head((labels == best[1]).astype(np.uint8))
        if split is None:
            continue
        out[index] = split[1] > 0
    return out


# --------------------------------------------------------------------------
# carry
# --------------------------------------------------------------------------


def carry(alignments: dict[int, Alignment], accepted: set[int], contact: float) -> dict[str, Any]:
    """Interpolate f71->f72 at contact (primary); quadratic over f66-f72 (check)."""
    result: dict[str, Any] = {}
    names = ("midpoint", "topline")
    if 71 in accepted and 72 in accepted:
        weight = contact - 71.0
        for name in names:
            first = alignments[71].landmarks[name]
            second = alignments[72].landmarks[name]
            point = first + weight * (second - first)
            result[f"pair_{name}_x"] = float(point[0])
            result[f"pair_{name}_y"] = float(point[1])
        result["pair_status"] = "ok"
    else:
        result["pair_status"] = "f71 or f72 not accepted"

    usable = sorted(f for f in CARRY_FIT_FRAMES if f in accepted)
    result["quad_frames"] = " ".join(str(f) for f in usable)
    result["n_quad"] = len(usable)
    if len(usable) >= MIN_QUAD_FRAMES:
        frames = np.asarray(usable, dtype=float)
        for name in names:
            points = np.stack([alignments[f].landmarks[name] for f in usable])
            fit_x = np.polyfit(frames, points[:, 0], 2)
            fit_y = np.polyfit(frames, points[:, 1], 2)
            residual = np.hypot(
                points[:, 0] - np.polyval(fit_x, frames), points[:, 1] - np.polyval(fit_y, frames)
            )
            result[f"quad_{name}_x"] = float(np.polyval(fit_x, contact))
            result[f"quad_{name}_y"] = float(np.polyval(fit_y, contact))
            result[f"quad_{name}_rms_px"] = float(np.sqrt(np.mean(residual**2)))
        result["quad_status"] = "ok"
    else:
        result["quad_status"] = f"only {len(usable)} accepted frames"
    for name in names:
        if f"pair_{name}_x" in result and f"quad_{name}_x" in result:
            result[f"disagree_{name}_x_px"] = result[f"pair_{name}_x"] - result[f"quad_{name}_x"]
            result[f"disagree_{name}_y_px"] = result[f"pair_{name}_y"] - result[f"quad_{name}_y"]
    return result


def _mark_point(mark: dict[str, Any], name: str) -> np.ndarray:
    if name == "midpoint":
        heel = np.asarray(mark["marks"]["heel"], dtype=float)
        toe = np.asarray(mark["marks"]["toe"], dtype=float)
        topline = np.asarray(mark["marks"]["topline"], dtype=float)
        return np.array([0.5 * (heel[0] + toe[0]), topline[1]])
    return np.asarray(mark["marks"][name], dtype=float)


# --------------------------------------------------------------------------
# renders
# --------------------------------------------------------------------------


def _draw_outline(tile: np.ndarray, template: TemplateV2, alignment: Alignment, colour) -> None:
    placed = cv2.warpAffine(
        template.outline.astype(np.uint8),
        _matrix(alignment),
        (tile.shape[1], tile.shape[0]),
        flags=cv2.INTER_NEAREST,
    )
    contours, _ = cv2.findContours(placed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    cv2.drawContours(tile, contours, -1, colour, 1)


def sheet(
    shot: ShotFrames,
    template: TemplateV2,
    alignments: dict[int, Alignment],
    accepted: set[int],
    marks: dict,
    path: Path,
) -> None:
    """f66-f72: mask green, outline orange (red = failed closed), marks ticked."""
    tiles: list[np.ndarray] = []
    x0 = max(0, int(shot.ball.x) - 60)
    y0 = max(0, int(shot.ball.y) - 42)
    for frame in ALIGN_FRAMES:
        tile = cv2.cvtColor(shot.frames[frame], cv2.COLOR_GRAY2BGR)
        mask = shot.masks.get(frame)
        if mask is not None:
            tile[mask] = (0.55 * tile[mask] + 0.45 * np.array([0, 220, 0])).astype(np.uint8)
        alignment = alignments.get(frame)
        if alignment is not None:
            _draw_outline(
                tile, template, alignment, (0, 165, 255) if frame in accepted else (0, 0, 255)
            )
            extremes = placed_extremes(template, alignment)
            for point, colour in (
                (extremes["heel_extent"], (255, 0, 255)),
                (extremes["toe_extent"], (0, 255, 255)),
            ):
                cv2.circle(tile, (int(round(point[0])), int(round(point[1]))), 1, colour, -1)
            cv2.circle(
                tile,
                tuple(int(round(v)) for v in alignment.landmarks["midpoint"]),
                1,
                (255, 255, 255),
                -1,
            )
        mark = marks.get((shot.shot, frame))
        if mark is not None:
            for name, colour in (
                ("heel", (255, 0, 255)),
                ("toe", (0, 255, 255)),
                ("topline", (255, 128, 0)),
            ):
                point = _mark_point(mark, name)
                cv2.drawMarker(
                    tile,
                    (int(round(point[0])), int(round(point[1]))),
                    colour,
                    cv2.MARKER_TILTED_CROSS,
                    5,
                    1,
                )
        cv2.circle(
            tile,
            (int(shot.ball.x), int(shot.ball.y)),
            max(1, int(shot.ball.diameter_px / 2)),
            (255, 0, 0),
            1,
        )
        label = f"{frame}"
        if alignment is not None:
            label += f" {alignment.iou:.2f} {alignment.roll_deg:+.1f}"
        cv2.putText(tile, label, (2, 10), cv2.FONT_HERSHEY_PLAIN, 0.7, (0, 255, 255), 1)
        tiles.append(tile[y0 : y0 + 78, x0 : x0 + 120])
    strip = np.hstack(tiles)
    banner = np.zeros((14, strip.shape[1], 3), np.uint8)
    cv2.putText(
        banner,
        f"{shot.shot}  {shot.club}  sole={template.sole_mode}",
        (3, 11),
        cv2.FONT_HERSHEY_PLAIN,
        0.8,
        (255, 255, 255),
        1,
    )
    cv2.imwrite(str(path), np.vstack([banner, strip]))


def roll_lock_render(
    shot: ShotFrames,
    template: TemplateV2,
    alignment: Alignment,
    frame: int,
    path: Path,
) -> None:
    """Three zoomed panels: raw, mask, and the found roll against a zero roll."""
    x0 = max(0, int(shot.ball.x) - 40)
    y0 = max(0, int(shot.ball.y) - 26)
    crop = (slice(y0, y0 + 52), slice(x0, x0 + 80))
    base = cv2.cvtColor(shot.frames[frame], cv2.COLOR_GRAY2BGR)
    mask = shot.masks.get(frame)

    raw = base.copy()
    masked = base.copy()
    if mask is not None:
        masked[mask] = (0.45 * masked[mask] + 0.55 * np.array([0, 220, 0])).astype(np.uint8)
    fitted = masked.copy()
    _draw_outline(fitted, template, alignment, (0, 165, 255))
    level = masked.copy()
    _draw_outline(level, template, replace(alignment, roll_deg=0.0), (255, 255, 0))

    panels = []
    for tile, caption in (
        (raw, "raw"),
        (masked, "head mask"),
        (fitted, f"outline @ roll {alignment.roll_deg:+.1f} deg"),
        (level, "outline @ roll 0 deg"),
    ):
        zoom = cv2.resize(
            tile[crop], None, fx=ROLL_LOCK_ZOOM, fy=ROLL_LOCK_ZOOM, interpolation=cv2.INTER_NEAREST
        )
        strip = np.zeros((16, zoom.shape[1], 3), np.uint8)
        cv2.putText(strip, caption, (4, 12), cv2.FONT_HERSHEY_PLAIN, 0.9, (255, 255, 255), 1)
        panels.append(np.vstack([strip, zoom]))
    grid = np.vstack([np.hstack(panels[:2]), np.hstack(panels[2:])])
    banner = np.zeros((18, grid.shape[1], 3), np.uint8)
    cv2.putText(
        banner,
        f"{shot.shot} f{frame}  IoU {alignment.iou:.3f}  roll {alignment.roll_deg:+.1f} deg",
        (4, 13),
        cv2.FONT_HERSHEY_PLAIN,
        1.0,
        (255, 255, 255),
        1,
    )
    cv2.imwrite(str(path), np.vstack([banner, grid]))


# --------------------------------------------------------------------------


def main() -> int:  # pylint: disable=too-many-locals,too-many-statements,too-many-branches
    """Align with the v2 outline, refine on the ridge, carry, score, render."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", default=None)
    parser.add_argument("--marks", default=str(DEFAULT_MARKS))
    parser.add_argument("--out", default=str(Path(__file__).resolve().parent / "results"))
    parser.add_argument("--exclude-shot", type=int, action="append", default=[1])
    parser.add_argument("--shot", type=int, action="append", default=None)
    parser.add_argument("--sole-mode", choices=SOLE_MODES, default="occupancy")
    parser.add_argument("--top-margin", type=float, default=CROP_TOP_MARGIN_PX)
    parser.add_argument("--ridge", dest="ridge", action="store_true", default=True)
    parser.add_argument("--no-ridge", dest="ridge", action="store_false")
    parser.add_argument("--roll-normalise", action="store_true")
    parser.add_argument("--no-sheets", action="store_true")
    parser.add_argument("--tag", default="v2")
    parser.add_argument("--roll-lock", type=int, action="append", default=None)
    parser.add_argument("--roll-lock-frame", type=int, default=ROLL_LOCK_FRAME)
    args = parser.parse_args()

    session = session_path(args.session)
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    sheets = out / f"sheets_{args.tag}"
    if not args.no_sheets:
        sheets.mkdir(parents=True, exist_ok=True)
    marks = load_marks_pass1(Path(args.marks).expanduser())
    shots = load_session(session, marks, set(args.exclude_shot))
    reference = reference_scale(shots)

    frame_rows: list[dict[str, Any]] = []
    shot_rows: list[dict[str, Any]] = []
    # (shot, club, hosel_visible, signed error) per axis and convention.
    signed: dict[str, list[tuple[str, str, bool, float]]] = {
        key: []
        for key in (
            "raw_heel_x",
            "raw_toe_x",
            "raw_topline_y",
            "def_heel_x",
            "def_toe_x",
            "def_topline_y",
            "preridge_topline_y",
        )
    }

    for shot in sorted(shots.values(), key=lambda s: s.number):
        if args.shot and shot.number not in set(args.shot):
            continue
        template = build_template_v2(
            shots,
            marks,
            shot.club,
            reference,
            roll_normalise=args.roll_normalise,
            exclude=shot.shot,
            sole_mode=args.sole_mode,
            top_margin_px=args.top_margin,
        )
        if template is None:
            continue
        shafts = shaft_masks(shot)
        alignments: dict[int, Alignment] = {}
        pre_ridge: dict[int, Alignment] = {}
        ridge_info: dict[int, dict[str, float]] = {}
        for frame in ALIGN_FRAMES:
            alignment = align_frame(shot, template, frame)
            if alignment is None:
                continue
            pre_ridge[frame] = alignment
            if args.ridge:
                alignment, info = refine_on_ridge(shot, template, alignment, frame)
                ridge_info[frame] = info
            alignments[frame] = alignment

        accepted = {
            f: a
            for f, a in alignments.items()
            if a.iou >= IOU_GATE and not a.on_roll_boundary and not a.on_radius_boundary
        }
        track = sorted(f for f in ALIGN_FRAMES if f <= 71 and f in accepted)
        track_residual: dict[int, float] = {}
        if len(track) >= MIN_CARRY_FRAMES:
            frames = np.asarray(track, dtype=float)
            points = np.stack([accepted[f].landmarks["midpoint"] for f in track])
            slope_x, intercept_x = _line_fit(frames, points[:, 0])
            slope_y, intercept_y = _line_fit(frames, points[:, 1])
            for index, frame in enumerate(track):
                track_residual[frame] = float(
                    math.hypot(
                        points[index, 0] - (slope_x * frame + intercept_x),
                        points[index, 1] - (slope_y * frame + intercept_y),
                    )
                )
            for frame, residual in track_residual.items():
                if residual > TRACK_GATE_PX:
                    del accepted[frame]
        accepted_frames = set(accepted)

        carried = carry(alignments, accepted_frames, shot.contact)
        mm_per_px = shot.plate_mm_per_px
        ball = np.array([shot.ball.x, shot.ball.y])

        row: dict[str, Any] = {
            "shot": shot.shot,
            "shot_number": shot.number,
            "club": shot.club,
            "contact_frame": shot.contact,
            "plate_mm_per_px": mm_per_px,
            "ball_x": shot.ball.x,
            "ball_y": shot.ball.y,
            "sole_mode": template.sole_mode,
            "template_n_samples": template.n_samples,
            "template_depth_median_mm": template.sole_depth_stats().get("median_mm", math.nan),
            "n_aligned": len(alignments),
            "n_accepted": len(accepted_frames),
            "accepted_frames": " ".join(str(f) for f in sorted(accepted_frames)),
            "median_iou": float(np.median([a.iou for a in alignments.values()]))
            if alignments
            else math.nan,
            "median_roll_deg": float(np.median([a.roll_deg for a in alignments.values()]))
            if alignments
            else math.nan,
            **{k: v for k, v in carried.items()},
        }

        for frame, alignment in sorted(alignments.items()):
            mark = marks.get((shot.shot, frame))
            shaft = shafts.get(frame)
            hosel_visible = bool(shaft is not None and int(shaft.sum()) >= HOSEL_MIN_SHAFT_PX)
            extremes = placed_extremes(template, alignment)
            top_x, top_y = placed_top_edge(template, alignment)
            entry: dict[str, Any] = {
                "shot": shot.shot,
                "club": shot.club,
                "frame": frame,
                "range_mm": shot.range_mm(frame),
                "scale_vs_contact": CAMERA_BALL_RANGE_MM / shot.range_mm(frame),
                "iou": alignment.iou,
                "iou_pre_ridge": pre_ridge[frame].iou,
                "roll_deg": alignment.roll_deg,
                "ridge_dy_px": ridge_info.get(frame, {}).get("ridge_dy_px", 0.0),
                "ridge_columns": ridge_info.get(frame, {}).get("ridge_columns", 0.0),
                "accepted": frame in accepted_frames,
                "track_residual_px": track_residual.get(frame, math.nan),
                "hosel_visible": hosel_visible,
                "shaft_area_px": int(shaft.sum()) if shaft is not None else 0,
                "mask_area_px": alignment.mask_area,
                "heel_extent_x": float(extremes["heel_extent"][0]),
                "heel_extent_y": float(extremes["heel_extent"][1]),
                "toe_extent_x": float(extremes["toe_extent"][0]),
                "toe_extent_y": float(extremes["toe_extent"][1]),
            }
            for name in LANDMARKS:
                point = alignment.landmarks[name]
                entry[f"{name}_x"] = float(point[0])
                entry[f"{name}_y"] = float(point[1])
            if mark is not None:
                for name in ("heel", "toe", "topline"):
                    truth = _mark_point(mark, name)
                    entry[f"{name}_mark_x"] = float(truth[0])
                    entry[f"{name}_mark_y"] = float(truth[1])
                mark_top = _mark_point(mark, "topline")
                entry["topline_at_mark_x"] = top_edge_at(top_x, top_y, float(mark_top[0]))
                pre_top_x, pre_top_y = placed_top_edge(template, pre_ridge[frame])
                entry["preridge_topline_at_mark_x"] = top_edge_at(
                    pre_top_x, pre_top_y, float(mark_top[0])
                )
                if frame in accepted_frames:
                    tagged = (shot.shot, shot.club, hosel_visible)
                    signed["raw_heel_x"].append(
                        (*tagged, float(alignment.landmarks["heel"][0] - entry["heel_mark_x"]))
                    )
                    signed["raw_toe_x"].append(
                        (*tagged, float(alignment.landmarks["toe"][0] - entry["toe_mark_x"]))
                    )
                    signed["raw_topline_y"].append(
                        (
                            *tagged,
                            float(alignment.landmarks["topline"][1] - entry["topline_mark_y"]),
                        )
                    )
                    signed["def_heel_x"].append(
                        (*tagged, float(entry["heel_extent_x"] - entry["heel_mark_x"]))
                    )
                    signed["def_toe_x"].append(
                        (*tagged, float(entry["toe_extent_x"] - entry["toe_mark_x"]))
                    )
                    if math.isfinite(entry["topline_at_mark_x"]):
                        signed["def_topline_y"].append(
                            (*tagged, float(entry["topline_at_mark_x"] - entry["topline_mark_y"]))
                        )
                    if math.isfinite(entry["preridge_topline_at_mark_x"]):
                        signed["preridge_topline_y"].append(
                            (
                                *tagged,
                                float(
                                    entry["preridge_topline_at_mark_x"] - entry["topline_mark_y"]
                                ),
                            )
                        )
            frame_rows.append(entry)

        for prefix in ("pair", "quad"):
            if f"{prefix}_midpoint_x" not in carried:
                continue
            offset = ball - np.array(
                [carried[f"{prefix}_midpoint_x"], carried[f"{prefix}_midpoint_y"]]
            )
            row[f"{prefix}_ball_minus_mid_x_px"] = float(offset[0])
            row[f"{prefix}_ball_minus_mid_y_px"] = float(offset[1])
            row[f"{prefix}_ball_minus_mid_x_mm"] = float(offset[0] * mm_per_px)
            row[f"{prefix}_ball_minus_mid_y_mm"] = float(offset[1] * mm_per_px)
            top_offset = float(ball[1] - carried[f"{prefix}_topline_y"])
            row[f"{prefix}_ball_minus_topline_y_px"] = top_offset
            row[f"{prefix}_ball_minus_topline_y_mm"] = top_offset * mm_per_px
        if "disagree_midpoint_x_px" in carried:
            for name in ("midpoint", "topline"):
                for axis in ("x", "y"):
                    key = f"disagree_{name}_{axis}_px"
                    if key in carried:
                        row[f"{key[:-3]}_mm"] = carried[key] * mm_per_px
        if all((shot.shot, f) in marks for f in MARK_FRAMES):
            weight = shot.contact - 71.0
            for name in ("midpoint", "topline"):
                first = _mark_point(marks[(shot.shot, 71)], name)
                second = _mark_point(marks[(shot.shot, 72)], name)
                point = first + weight * (second - first)
                offset = ball - point
                row[f"mark_ball_minus_{name}_x_px"] = float(offset[0])
                row[f"mark_ball_minus_{name}_y_px"] = float(offset[1])
                row[f"mark_ball_minus_{name}_x_mm"] = float(offset[0] * mm_per_px)
                row[f"mark_ball_minus_{name}_y_mm"] = float(offset[1] * mm_per_px)
            for prefix in ("pair", "quad"):
                if f"{prefix}_ball_minus_mid_x_px" not in row:
                    continue
                for axis, key in (("x", "x"), ("y", "y")):
                    row[f"{prefix}_minus_mark_{axis}_px"] = float(
                        row[f"{prefix}_ball_minus_mid_{key}_px"]
                        - row[f"mark_ball_minus_midpoint_{key}_px"]
                    )
                row[f"{prefix}_minus_mark_topline_y_px"] = float(
                    row[f"{prefix}_ball_minus_topline_y_px"] - row["mark_ball_minus_topline_y_px"]
                )
        shot_rows.append(row)

        if not args.no_sheets:
            sheet(shot, template, alignments, accepted_frames, marks, sheets / f"{shot.shot}.png")
        lock_shots = set(args.roll_lock or ROLL_LOCK_SHOTS)
        if shot.number in lock_shots and args.roll_lock_frame in alignments:
            roll_lock_render(
                shot,
                template,
                alignments[args.roll_lock_frame],
                args.roll_lock_frame,
                out / f"rolllock_{args.tag}_{shot.shot}_f{args.roll_lock_frame}.png",
            )
        print(
            f"{shot.shot:18s} aligned={len(alignments)} accepted={len(accepted_frames)} "
            f"medIoU={row['median_iou']:.3f} pair={carried.get('pair_status')} "
            f"acc={row['accepted_frames']}"
        )

    _write_csv(out / f"align_{args.tag}_per_frame.csv", frame_rows)
    _write_csv(out / f"align_{args.tag}_per_shot.csv", shot_rows)

    def stats(rows: list[tuple[str, str, bool, float]]) -> dict[str, Any]:
        return {
            **_percentiles([abs(v) for *_, v in rows]),
            "bias": float(np.median([v for *_, v in rows])) if rows else math.nan,
        }

    scores = {key: stats(rows) for key, rows in signed.items()}
    hosel_split = {
        key: {
            str(flag): stats([r for r in rows if r[2] is flag])
            for flag in (True, False)
            if any(r[2] is flag for r in rows)
        }
        for key, rows in signed.items()
        if key in ("raw_heel_x", "def_heel_x")
    }

    availability = sum(
        1
        for r in shot_rows
        if {66, 67, 68, 69, 70, 71}.issubset({int(f) for f in r["accepted_frames"].split()})
    )
    carried_delta: dict[str, Any] = {}
    carried_bias: dict[str, float] = {}
    for prefix in ("pair", "quad"):
        for axis in ("x", "y", "topline_y"):
            key = f"{prefix}_minus_mark_{axis}_px"
            values = [r[key] for r in shot_rows if key in r]
            carried_delta[f"{prefix}_{axis}"] = _percentiles([abs(v) for v in values])
            carried_bias[f"{prefix}_{axis}"] = float(np.median(values or [math.nan]))
    disagreement = {
        key: _percentiles([abs(r[key]) for r in shot_rows if key in r])
        for key in (
            "disagree_midpoint_x_px",
            "disagree_midpoint_y_px",
            "disagree_topline_x_px",
            "disagree_topline_y_px",
        )
    }

    def gate(prefix: str) -> dict[str, Any]:
        keys = (f"{prefix}_heel_x", f"{prefix}_toe_x", f"{prefix}_topline_y")
        checks = {
            k: {
                "median": scores[k].get("median", math.inf),
                "p90": scores[k].get("p90", math.inf),
                "median_pass": scores[k].get("median", math.inf) <= PASS_MEDIAN_PX,
                "p90_pass": scores[k].get("p90", math.inf) <= PASS_P90_PX,
            }
            for k in keys
        }
        passed = all(c["median_pass"] and c["p90_pass"] for c in checks.values()) and (
            availability >= PASS_AVAILABILITY
        )
        return {
            "checks": checks,
            "availability": availability,
            "result": "PASS" if passed else "FAIL",
        }

    report = {
        "caveat": (
            "CONSISTENCY ONLY. No ground truth exists. Scored against PASS-1 "
            "hand marks, which also define the template's crop, origin and "
            "landmark convention."
        ),
        "session": str(session),
        "configuration": {
            "sole_mode": args.sole_mode,
            "top_margin_px": args.top_margin,
            "ridge": args.ridge,
            "ridge_weight": RIDGE_WEIGHT,
            "ridge_search_px": RIDGE_SEARCH_PX,
            "roll_normalised": args.roll_normalise,
            "marks_pass": 1,
        },
        "counts": {
            "shots": len(shot_rows),
            "frames_aligned": len(frame_rows),
            "frames_accepted": int(sum(1 for r in frame_rows if r["accepted"])),
            "shots_with_all_f66_f71": availability,
            "marked_frames_scored": scores["raw_heel_x"].get("n", 0),
        },
        "iou": _percentiles([r["iou"] for r in frame_rows]),
        "iou_by_frame": {
            str(f): _percentiles([r["iou"] for r in frame_rows if r["frame"] == f])
            for f in ALIGN_FRAMES
        },
        "ridge_dy_px": _percentiles([abs(r["ridge_dy_px"]) for r in frame_rows]),
        "scores_vs_marks_px": scores,
        "scores_by_frame_px": {
            key: {
                str(frame): _percentiles(
                    [
                        abs(r[f"{col}"] - r[f"{mk}"])
                        for r in frame_rows
                        if r["frame"] == frame and r["accepted"] and f"{mk}" in r
                    ]
                )
                for frame in MARK_FRAMES
            }
            for key, col, mk in (
                ("raw_heel_x", "heel_x", "heel_mark_x"),
                ("raw_toe_x", "toe_x", "toe_mark_x"),
                ("raw_topline_y", "topline_y", "topline_mark_y"),
                ("def_heel_x", "heel_extent_x", "heel_mark_x"),
                ("def_toe_x", "toe_extent_x", "toe_mark_x"),
                ("def_topline_y", "topline_at_mark_x", "topline_mark_y"),
            )
        },
        "heel_by_hosel_visibility": hosel_split,
        "carry": {
            "minus_mark_px": carried_delta,
            "bias_px": carried_bias,
            "pair_vs_quad_disagreement_px": disagreement,
            "quad_fit_rms_px": _percentiles(
                [r["quad_midpoint_rms_px"] for r in shot_rows if "quad_midpoint_rms_px" in r]
            ),
        },
        "pre_registered_gate_raw_convention": gate("raw"),
        "pre_registered_gate_definition_matched": gate("def"),
    }
    (out / f"align_{args.tag}_summary.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )

    print(f"\n{report['caveat']}\n")
    print(f"config: sole={args.sole_mode} top-margin={args.top_margin} ridge={args.ridge}")
    print(
        f"frames aligned {report['counts']['frames_aligned']} "
        f"accepted {report['counts']['frames_accepted']}; "
        f"availability {availability}/{len(shot_rows)}"
    )
    for key in (
        "raw_heel_x",
        "raw_toe_x",
        "raw_topline_y",
        "def_heel_x",
        "def_toe_x",
        "def_topline_y",
        "preridge_topline_y",
    ):
        s = scores[key]
        if s.get("n"):
            print(
                f"  {key:20s} n={s['n']:2d} median {s['median']:5.2f}  p90 {s['p90']:5.2f}  "
                f"max {s['max']:5.2f}  bias {s['bias']:+5.2f}"
            )
    print("\ncarried-to-contact minus the marks' own interpolation (px)")
    for key, s in carried_delta.items():
        if s.get("n"):
            print(
                f"  {key:16s} n={s['n']:2d} median {s['median']:5.2f}  p90 {s['p90']:5.2f}  "
                f"bias {carried_bias[key]:+5.2f}"
            )
    print("\npair-minus-quadratic disagreement (px)")
    for key, s in disagreement.items():
        if s.get("n"):
            print(f"  {key:26s} n={s['n']:2d} median {s['median']:5.2f}  p90 {s['p90']:5.2f}")
    print(
        f"\ngate (raw convention):              {report['pre_registered_gate_raw_convention']['result']}"
    )
    print(
        f"gate (definition-matched):          {report['pre_registered_gate_definition_matched']['result']}"
    )
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

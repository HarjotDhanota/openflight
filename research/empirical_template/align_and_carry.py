"""Align the empirical outline per frame, carry it to contact, score the marks.

NO GROUND TRUTH EXISTS. The maintainer's hand marks are the only reference and
they are one human's reading of the same pixels, so every error below is a
CONSISTENCY figure between a data-built outline and an annotator -- never an
accuracy figure against a launch monitor.

Per shot S:

  1. The outline comes from `build_template.build_template` with S's own frames
     LEFT OUT, so the shot being scored never contributed to the shape it is
     scored against.
  2. On each of f66-f72 the outline is scaled by the radar's own range ramp --
     `1581 / range_mm(f)` times the plate-scale ratio -- and then translated and
     rolled to maximise IoU against that frame's head mask.
  3. The IoU is LOCAL: the mask is taken only inside the placed outline's own
     bounding box, which is exactly the box the template was cropped to when it
     was built. A global IoU would be dominated by the shaft and hosel, which
     the crop removed from the template but which are still in the mask.
  4. The aligned midpoint's image-plane motion is fitted linearly over f66-f71
     and evaluated at the acoustic contact instant.

THREE DEVIATIONS from the brief, all forced and all reported:

  * TRANSLATION SEARCH. The brief searches +-12 px, which needs a prior. There
    is none at f66-f70: the head is 20-30 px from the ball there and the marks,
    which would supply one, are the test set. The coarse stage is therefore a
    GLOBAL translation search (an FFT cross-correlation gives every placement
    for the price of one), restricted only to placements whose midpoint is
    within `MAX_ANCHOR_RADIUS_PX` of the ball. The +-12 px is re-imposed as a
    TRACK gate: a frame whose aligned midpoint sits more than 12 px off the
    shot's own fitted f66-f71 line is failed closed, which is the same
    fail-closed the brief asks for.
  * THE TEMPLATE HAS ONE INFORMATIVE EDGE. The 48 mm crop cuts the outline flat
    at the bottom and the +-2 px heel/toe margin cuts it flat at the sides, so
    three of the four boundaries are the crop, not the club. Only the topline
    and the heel/toe shoulders carry shape. This is a property of the brief's
    crop, and it caps what translation-plus-roll can resolve.
  * ROLL. Both template normalisations are available; the brief's
    (translate-and-scale only) bakes the session's mean roll into the outline,
    so the roll search reads deviation from that mean, not absolute roll.

Run from the repository root:

    uv run python research/empirical_template/align_and_carry.py \
        --session /path/to/export --out research/empirical_template/results
"""

# pylint: disable=wrong-import-position,no-member,unsubscriptable-object

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from scipy import signal

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_template import (  # noqa: E402
    ALIGN_FRAMES,
    CAMERA_BALL_RANGE_MM,
    DEFAULT_MARKS,
    MARK_FRAMES,
    TEMPLATE_ORIGIN,
    UPSAMPLE,
    ShotFrames,
    Template,
    build_template,
    load_marks,
    load_session,
    reference_scale,
    session_path,
)

ROLL_LIMIT_DEG = 20.0
ROLL_STEP_DEG = 1.0
REFINE_ROLL_DEG = 1.0
REFINE_ROLL_STEP_DEG = 0.25
REFINE_SHIFT_PX = 1.0
REFINE_SHIFT_STEP_PX = 0.25
# The head is never further than this from the teed ball over f66-f72; beyond it
# the only large moving thing is the golfer.
MAX_ANCHOR_RADIUS_PX = 55.0
PATCH_PAD_PX = 2

IOU_GATE = 0.60
TRACK_GATE_PX = 12.0
CARRY_FRAMES = tuple(f for f in ALIGN_FRAMES if f <= 71)
MIN_CARRY_FRAMES = 3
# Pre-registered pass criteria.
PASS_MEDIAN_PX = 1.0
PASS_P90_PX = 2.0
PASS_AVAILABILITY = 17

LANDMARKS = ("heel", "toe", "topline", "midpoint")
# Sheets are stored at native resolution: they are 320x200 grabs of a noisy
# sensor, and upscaling them triples the repository's weight without adding a
# pixel of evidence. Zoom the file, do not enlarge it here.
SHEET_SCALE = 1


@dataclass(frozen=True)
class Alignment:
    """One frame's placement of the outline, and what it reads off."""

    frame: int
    iou: float
    roll_deg: float
    step: float
    anchor: np.ndarray
    landmarks: dict[str, np.ndarray]
    mask_area: int
    template_area: int
    on_roll_boundary: bool
    on_radius_boundary: bool


def _rotation(angle_deg: float) -> tuple[float, float]:
    radians = math.radians(angle_deg)
    return math.cos(radians), math.sin(radians)


def _affine(step: float, angle_deg: float, anchor_x: float, anchor_y: float) -> np.ndarray:
    """Template pixels -> image pixels, rotating and scaling about the origin."""
    cosine, sine = _rotation(angle_deg)
    origin_x, origin_y = TEMPLATE_ORIGIN
    return np.array(
        [
            [
                step * cosine,
                -step * sine,
                anchor_x - step * (cosine * origin_x - sine * origin_y),
            ],
            [
                step * sine,
                step * cosine,
                anchor_y - step * (sine * origin_x + cosine * origin_y),
            ],
        ]
    )


def _apply(matrix: np.ndarray, point: np.ndarray) -> np.ndarray:
    return matrix[:, :2] @ point + matrix[:, 2]


def _patch_extent(
    template: Template, step: float, angle_deg: float
) -> tuple[np.ndarray, np.ndarray]:
    """Bounding box of the rotated, scaled outline relative to its own origin."""
    rows, cols = np.nonzero(template.outline)
    local = _affine(step, angle_deg, 0.0, 0.0) @ np.stack(
        [cols.astype(float), rows.astype(float), np.ones(cols.size)]
    )
    return local.min(axis=1), local.max(axis=1)


def _rasterise(
    template: Template, step: float, angle_deg: float, anchor_in_patch: np.ndarray, shape: tuple
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
    """IoU of the placed outline against the mask INSIDE the outline's own box."""
    row, col = top_left
    height, width = patch.shape
    if row < 0 or col < 0 or row + height > mask.shape[0] or col + width > mask.shape[1]:
        return math.nan
    window = mask[row : row + height, col : col + width]
    intersection = float((window & patch).sum())
    union = float(patch.sum()) + float(window.sum()) - intersection
    return intersection / union if union > 0 else math.nan


def _coarse_search(
    mask: np.ndarray, template: Template, step: float, ball: tuple[float, float]
) -> tuple[float, float, np.ndarray] | None:
    """Every roll, every translation, by FFT cross-correlation. Best IoU wins."""
    floating = mask.astype(np.float32)
    integral = cv2.integral(mask.astype(np.uint8))
    best: tuple[float, float, np.ndarray] | None = None
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
        anchor_x = cols + anchor_in_patch[0]
        anchor_y = rows + anchor_in_patch[1]
        near = (anchor_x - ball[0]) ** 2 + (anchor_y - ball[1]) ** 2 <= MAX_ANCHOR_RADIUS_PX**2
        iou = np.where(near, iou, -1.0)
        index = int(np.argmax(iou))
        value = float(iou.ravel()[index])
        if best is None or value > best[0]:
            row, col = divmod(index, iou.shape[1])
            best = (value, angle, np.array([col + anchor_in_patch[0], row + anchor_in_patch[1]]))
        angle += ROLL_STEP_DEG
    return best


def _score(
    mask: np.ndarray, template: Template, step: float, angle: float, anchor: np.ndarray
) -> float:
    low, _ = _patch_extent(template, step, angle)
    offset = anchor + low
    top_left = (int(math.floor(offset[1])), int(math.floor(offset[0])))
    anchor_in_patch = anchor - np.array([top_left[1], top_left[0]], dtype=float)
    high = _patch_extent(template, step, angle)[1]
    shape = (
        int(math.ceil(high[1] - low[1])) + 2 + 2 * PATCH_PAD_PX,
        int(math.ceil(high[0] - low[0])) + 2 + 2 * PATCH_PAD_PX,
    )
    patch = _rasterise(template, step, angle, anchor_in_patch, shape)
    return _local_iou(mask, patch, top_left)


def align_frame(shot: ShotFrames, template: Template, frame: int) -> Alignment | None:
    """Scale by the radar range, then search translation and roll for best IoU."""
    mask = shot.masks.get(frame)
    if mask is None:
        return None
    step = template.mm_per_px / (UPSAMPLE * shot.mm_per_px(frame))
    ball = (shot.ball.x, shot.ball.y)
    coarse = _coarse_search(mask, template, step, ball)
    if coarse is None or coarse[0] <= 0.0:
        return None
    best_iou, best_angle, best_anchor = coarse
    for _ in range(2):
        improved = False
        angles = np.arange(
            best_angle - REFINE_ROLL_DEG,
            best_angle + REFINE_ROLL_DEG + 1e-9,
            REFINE_ROLL_STEP_DEG,
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
        landmarks={name: _apply(matrix, template.landmarks[name]) for name in LANDMARKS},
        mask_area=int(mask.sum()),
        template_area=int(template.outline.sum()),
        on_roll_boundary=abs(abs(best_angle) - ROLL_LIMIT_DEG) < 1e-6,
        on_radius_boundary=bool(
            abs(
                math.hypot(best_anchor[0] - ball[0], best_anchor[1] - ball[1])
                - MAX_ANCHOR_RADIUS_PX
            )
            < 1.0
        ),
    )


def _line_fit(frames: np.ndarray, values: np.ndarray) -> tuple[float, float]:
    slope, intercept = np.polyfit(frames, values, 1)
    return float(slope), float(intercept)


def _poly_carry(
    frames: np.ndarray, points: np.ndarray, contact: float, degree: int
) -> tuple[np.ndarray, float, np.ndarray]:
    """Fit each image axis with a polynomial and evaluate it at contact."""
    fit_x = np.polyfit(frames, points[:, 0], degree)
    fit_y = np.polyfit(frames, points[:, 1], degree)
    residual = np.hypot(
        points[:, 0] - np.polyval(fit_x, frames), points[:, 1] - np.polyval(fit_y, frames)
    )
    carried = np.array([np.polyval(fit_x, contact), np.polyval(fit_y, contact)])
    return carried, float(np.sqrt(np.mean(residual**2))), residual


def carry_to_contact(
    alignments: dict[int, Alignment], accepted: set[int], contact: float
) -> dict[str, Any]:
    """Image-plane motion of the aligned midpoint, evaluated at the contact instant.

    The brief's model is LINEAR over f66-f71. That model is reported first and
    it is the one the pre-registered gate uses, but the residual is 3-6 px: the
    head's projected motion decelerates hard as it swings onto the optical axis,
    so a quadratic is fitted too and both are carried. The two-point carry from
    the two marked frames alone is the third comparison the brief asks for.
    """
    usable = sorted(f for f in CARRY_FRAMES if f in accepted)
    result: dict[str, Any] = {"carry_frames": usable, "n_carry": len(usable)}
    if len(usable) < MIN_CARRY_FRAMES:
        result["carry_status"] = f"only {len(usable)} aligned frames in f66-f71"
        return result
    frames = np.asarray(usable, dtype=float)
    points = np.stack([alignments[f].landmarks["midpoint"] for f in usable])
    linear, linear_rms, residual = _poly_carry(frames, points, contact, 1)
    slope_x, intercept_x = _line_fit(frames, points[:, 0])
    slope_y, intercept_y = _line_fit(frames, points[:, 1])
    result.update(
        {
            "carry_status": "ok",
            "midpoint_x_at_contact": float(linear[0]),
            "midpoint_y_at_contact": float(linear[1]),
            "midpoint_slope_x_px_frame": slope_x,
            "midpoint_slope_y_px_frame": slope_y,
            "carry_fit_rms_px": linear_rms,
            "carry_fit_max_px": float(residual.max()),
        }
    )
    if len(usable) >= 4:
        quadratic, quadratic_rms, _ = _poly_carry(frames, points, contact, 2)
        result.update(
            {
                "quad_midpoint_x_at_contact": float(quadratic[0]),
                "quad_midpoint_y_at_contact": float(quadratic[1]),
                "quad_carry_fit_rms_px": quadratic_rms,
            }
        )
    if 71 in accepted and 72 in accepted:
        first = alignments[71].landmarks["midpoint"]
        second = alignments[72].landmarks["midpoint"]
        pair = first + (contact - 71.0) * (second - first)
        result.update(
            {
                "pair_midpoint_x_at_contact": float(pair[0]),
                "pair_midpoint_y_at_contact": float(pair[1]),
            }
        )
    return result


def _mark_point(mark: dict[str, Any], name: str) -> np.ndarray:
    if name == "midpoint":
        heel = np.asarray(mark["marks"]["heel"], dtype=float)
        toe = np.asarray(mark["marks"]["toe"], dtype=float)
        topline = np.asarray(mark["marks"]["topline"], dtype=float)
        return np.array([0.5 * (heel[0] + toe[0]), topline[1]])
    return np.asarray(mark["marks"][name], dtype=float)


def _sheet(
    shot: ShotFrames,
    template: Template,
    alignments: dict[int, Alignment],
    accepted: set[int],
    marks: dict,
    path: Path,
) -> None:
    """f66-f72 with the mask green, the aligned outline orange and the marks ticked."""
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
            matrix = _affine(
                alignment.step,
                alignment.roll_deg,
                float(alignment.anchor[0]),
                float(alignment.anchor[1]),
            )
            placed = cv2.warpAffine(
                template.outline.astype(np.uint8),
                matrix,
                (tile.shape[1], tile.shape[0]),
                flags=cv2.INTER_NEAREST,
            )
            contours, _ = cv2.findContours(placed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
            colour = (0, 165, 255) if frame in accepted else (0, 0, 255)
            cv2.drawContours(tile, contours, -1, colour, 1)
            for name, mark_colour in (
                ("heel", (255, 0, 255)),
                ("toe", (0, 255, 255)),
                ("topline", (255, 128, 0)),
                ("midpoint", (255, 255, 255)),
            ):
                point = alignment.landmarks[name]
                cv2.circle(tile, (int(round(point[0])), int(round(point[1]))), 1, mark_colour, -1)
        mark = marks.get((shot.shot, frame))
        if mark is not None:
            for name, mark_colour in (
                ("heel", (255, 0, 255)),
                ("toe", (0, 255, 255)),
                ("topline", (255, 128, 0)),
            ):
                point = _mark_point(mark, name)
                cv2.drawMarker(
                    tile,
                    (int(round(point[0])), int(round(point[1]))),
                    mark_colour,
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
    height = max(t.shape[0] for t in tiles)
    width = max(t.shape[1] for t in tiles)
    tiles = [np.pad(t, ((0, height - t.shape[0]), (0, width - t.shape[1]), (0, 0))) for t in tiles]
    strip = np.hstack(tiles)
    banner = np.zeros((14, strip.shape[1], 3), np.uint8)
    cv2.putText(
        banner,
        f"{shot.shot}  {shot.club}",
        (3, 11),
        cv2.FONT_HERSHEY_PLAIN,
        0.8,
        (255, 255, 255),
        1,
    )
    sheet = cv2.resize(
        np.vstack([banner, strip]),
        None,
        fx=SHEET_SCALE,
        fy=SHEET_SCALE,
        interpolation=cv2.INTER_NEAREST,
    )
    cv2.imwrite(str(path), sheet)


def _percentiles(values: list[float]) -> dict[str, float | int]:
    finite = np.asarray([v for v in values if math.isfinite(v)], dtype=float)
    if finite.size == 0:
        return {"n": 0}
    return {
        "n": int(finite.size),
        "median": float(np.median(finite)),
        "p90": float(np.percentile(finite, 90)),
        "mean": float(finite.mean()),
        "max": float(finite.max()),
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    keys: list[str] = []
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:  # pylint: disable=too-many-locals,too-many-statements,too-many-branches
    """Align, carry, score, and write every sheet and table."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", default=None)
    parser.add_argument("--marks", default=str(DEFAULT_MARKS))
    parser.add_argument("--out", default=str(Path(__file__).resolve().parent / "results"))
    parser.add_argument("--exclude-shot", type=int, action="append", default=[1])
    parser.add_argument("--shot", type=int, action="append", default=None)
    parser.add_argument("--roll-normalise", action="store_true")
    parser.add_argument("--no-sheets", action="store_true")
    args = parser.parse_args()

    session = session_path(args.session)
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    sheets = out / "sheets"
    if not args.no_sheets:
        sheets.mkdir(parents=True, exist_ok=True)
    marks = load_marks(Path(args.marks).expanduser())
    shots = load_session(session, marks, set(args.exclude_shot))
    reference = reference_scale(shots)

    frame_rows: list[dict[str, Any]] = []
    shot_rows: list[dict[str, Any]] = []
    landmark_errors: dict[str, list[float]] = {name: [] for name in LANDMARKS}
    # Signed (predicted - marked) per axis, tagged by shot so that a
    # leave-one-shot-out bias can be removed without touching the held-out shot.
    signed: dict[str, list[tuple[str, str, float]]] = {"heel_x": [], "toe_x": [], "topline_y": []}

    for shot in sorted(shots.values(), key=lambda s: s.number):
        if args.shot and shot.number not in set(args.shot):
            continue
        template = build_template(
            shots,
            marks,
            shot.club,
            reference,
            roll_normalise=args.roll_normalise,
            exclude=shot.shot,
        )
        if template is None:
            continue
        alignments: dict[int, Alignment] = {}
        for frame in ALIGN_FRAMES:
            alignment = align_frame(shot, template, frame)
            if alignment is not None:
                alignments[frame] = alignment

        # Fail-closed pass 1: IoU and search boundaries.
        accepted = {
            f: a
            for f, a in alignments.items()
            if a.iou >= IOU_GATE and not a.on_roll_boundary and not a.on_radius_boundary
        }
        # Fail-closed pass 2: the +-12 px translation window, re-imposed as a
        # track gate against the shot's own fitted f66-f71 line.
        track = sorted(f for f in CARRY_FRAMES if f in accepted)
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

        carry = carry_to_contact(alignments, accepted_frames, shot.contact)
        mm_per_px = shot.plate_mm_per_px  # at the contact range, by construction

        row: dict[str, Any] = {
            "shot": shot.shot,
            "shot_number": shot.number,
            "club": shot.club,
            "contact_frame": shot.contact,
            "plate_mm_per_px": mm_per_px,
            "ball_x": shot.ball.x,
            "ball_y": shot.ball.y,
            "template_n_samples": template.n_samples,
            "n_masked": len(shot.masks),
            "n_aligned": len(alignments),
            "n_accepted": len(accepted_frames),
            "accepted_frames": " ".join(str(f) for f in sorted(accepted_frames)),
            "rejected_frames": " ".join(
                f"{f}:{'iou' if alignments[f].iou < IOU_GATE else 'boundary' if alignments[f].on_roll_boundary or alignments[f].on_radius_boundary else 'track'}"
                for f in sorted(set(alignments) - accepted_frames)
            ),
            "median_iou": float(np.median([a.iou for a in alignments.values()]))
            if alignments
            else math.nan,
            "median_roll_deg": float(np.median([a.roll_deg for a in alignments.values()]))
            if alignments
            else math.nan,
            **{k: v for k, v in carry.items() if k != "carry_frames"},
        }

        for frame, alignment in sorted(alignments.items()):
            mark = marks.get((shot.shot, frame))
            entry: dict[str, Any] = {
                "shot": shot.shot,
                "club": shot.club,
                "frame": frame,
                "range_mm": shot.range_mm(frame),
                "scale_vs_contact": CAMERA_BALL_RANGE_MM / shot.range_mm(frame),
                "step_px_per_template_px": alignment.step,
                "iou": alignment.iou,
                "roll_deg": alignment.roll_deg,
                "accepted": frame in accepted_frames,
                "track_residual_px": track_residual.get(frame, math.nan),
                "mask_area_px": alignment.mask_area,
            }
            for name in LANDMARKS:
                point = alignment.landmarks[name]
                entry[f"{name}_x"] = float(point[0])
                entry[f"{name}_y"] = float(point[1])
                if mark is not None:
                    truth = _mark_point(mark, name)
                    entry[f"{name}_mark_x"] = float(truth[0])
                    entry[f"{name}_mark_y"] = float(truth[1])
                    error = float(np.hypot(*(point - truth)))
                    entry[f"{name}_err_px"] = error
                    if frame in accepted_frames:
                        landmark_errors[name].append(error)
            if mark is not None and frame in accepted_frames:
                for axis, name, index in (
                    ("heel_x", "heel", 0),
                    ("toe_x", "toe", 0),
                    ("topline_y", "topline", 1),
                ):
                    signed[axis].append(
                        (
                            shot.shot,
                            shot.club,
                            float(
                                alignment.landmarks[name][index] - _mark_point(mark, name)[index]
                            ),
                        )
                    )
            frame_rows.append(entry)

        # Ball-minus-outline offsets, carried and single-frame.
        ball = np.array([shot.ball.x, shot.ball.y])
        for tag, prefix in (("", "carried"), ("quad_", "quad"), ("pair_", "pair")):
            key_x, key_y = f"{tag}midpoint_x_at_contact", f"{tag}midpoint_y_at_contact"
            if key_x not in carry:
                continue
            offset = ball - np.array([carry[key_x], carry[key_y]])
            row.update(
                {
                    f"{prefix}_ball_minus_mid_x_px": float(offset[0]),
                    f"{prefix}_ball_minus_mid_y_px": float(offset[1]),
                    f"{prefix}_ball_minus_mid_x_mm": float(offset[0] * mm_per_px),
                    f"{prefix}_ball_minus_mid_y_mm": float(offset[1] * mm_per_px),
                }
            )
        for frame in MARK_FRAMES:
            alignment = alignments.get(frame)
            if alignment is None:
                continue
            offset = ball - alignment.landmarks["midpoint"]
            row[f"f{frame}_ball_minus_mid_x_px"] = float(offset[0])
            row[f"f{frame}_ball_minus_mid_y_px"] = float(offset[1])
            top = ball - alignment.landmarks["topline"]
            row[f"f{frame}_ball_minus_topline_y_px"] = float(top[1])
            row[f"f{frame}_ball_minus_topline_y_mm"] = float(top[1] * mm_per_px)
        if all(f in marks for f in ((shot.shot, 71), (shot.shot, 72))):
            first = _mark_point(marks[(shot.shot, 71)], "midpoint")
            second = _mark_point(marks[(shot.shot, 72)], "midpoint")
            weight = shot.contact - 71.0
            mark_mid = first + weight * (second - first)
            offset = ball - mark_mid
            row.update(
                {
                    "mark_ball_minus_mid_x_px": float(offset[0]),
                    "mark_ball_minus_mid_y_px": float(offset[1]),
                    "mark_ball_minus_mid_x_mm": float(offset[0] * mm_per_px),
                    "mark_ball_minus_mid_y_mm": float(offset[1] * mm_per_px),
                }
            )
            for prefix in ("carried", "quad", "pair"):
                if f"{prefix}_ball_minus_mid_x_px" not in row:
                    continue
                row[f"{prefix}_minus_mark_x_px"] = float(
                    row[f"{prefix}_ball_minus_mid_x_px"] - row["mark_ball_minus_mid_x_px"]
                )
                row[f"{prefix}_minus_mark_y_px"] = float(
                    row[f"{prefix}_ball_minus_mid_y_px"] - row["mark_ball_minus_mid_y_px"]
                )
        shot_rows.append(row)
        if not args.no_sheets:
            _sheet(shot, template, alignments, accepted_frames, marks, sheets / f"{shot.shot}.png")
        print(
            f"{shot.shot:18s} aligned={len(alignments)} accepted={len(accepted_frames)} "
            f"medIoU={row['median_iou']:.3f} carry={carry.get('carry_status', '-')} "
            f"acc={row['accepted_frames']}"
        )

    _write_csv(out / "align_per_frame.csv", frame_rows)
    _write_csv(out / "align_per_shot.csv", shot_rows)

    availability = sum(
        1
        for r in shot_rows
        if len({int(f) for f in r["accepted_frames"].split()} & set(CARRY_FRAMES))
        == len(CARRY_FRAMES)
    )
    availability_min3 = sum(1 for r in shot_rows if r.get("carry_status") == "ok")
    scores = {
        "landmark_euclidean_px": {k: _percentiles(v) for k, v in landmark_errors.items()},
        "axis_px": {k: _percentiles([abs(v) for _, _, v in rows]) for k, rows in signed.items()},
        "axis_bias_px": {
            k: {
                "all": float(np.median([v for _, _, v in rows])),
                **{
                    club: float(np.median([v for _, c, v in rows if c == club]))
                    for club in sorted({c for _, c, _ in rows})
                },
            }
            for k, rows in signed.items()
        },
        "axis_px_loo_debiased": {
            k: _percentiles(
                [
                    abs(
                        value
                        - float(
                            np.median(
                                [v for s, c, v in rows if c == club and s != shot_name] or [0.0]
                            )
                        )
                    )
                    for shot_name, club, value in rows
                ]
            )
            for k, rows in signed.items()
        },
    }
    carried_delta = {
        f"{prefix}_{axis}": _percentiles(
            [
                abs(r[f"{prefix}_minus_mark_{axis}_px"])
                for r in shot_rows
                if f"{prefix}_minus_mark_{axis}_px" in r
            ]
        )
        for prefix in ("carried", "quad", "pair")
        for axis in ("x", "y")
    }
    carried_bias = {
        f"{prefix}_{axis}": float(
            np.median(
                [
                    r[f"{prefix}_minus_mark_{axis}_px"]
                    for r in shot_rows
                    if f"{prefix}_minus_mark_{axis}_px" in r
                ]
                or [math.nan]
            )
        )
        for prefix in ("carried", "quad", "pair")
        for axis in ("x", "y")
    }
    passed = (
        all(
            scores["axis_px"][k].get("median", math.inf) <= PASS_MEDIAN_PX
            and scores["axis_px"][k].get("p90", math.inf) <= PASS_P90_PX
            for k in signed
        )
        and availability >= PASS_AVAILABILITY
    )
    report = {
        "caveat": (
            "CONSISTENCY ONLY. No ground truth exists. Errors are against one "
            "annotator's hand marks on the same pixels; the marks also define "
            "the template's crop, origin and landmark convention."
        ),
        "session": str(session),
        "roll_normalised": args.roll_normalise,
        "reference_mm_per_px": reference,
        "gates": {
            "iou": IOU_GATE,
            "track_px": TRACK_GATE_PX,
            "roll_limit_deg": ROLL_LIMIT_DEG,
            "max_anchor_radius_px": MAX_ANCHOR_RADIUS_PX,
        },
        "counts": {
            "shots": len(shot_rows),
            "frames_aligned": len(frame_rows),
            "frames_accepted": int(sum(1 for r in frame_rows if r["accepted"])),
            "shots_with_all_f66_f71": availability,
            "shots_with_3plus_f66_f71": availability_min3,
        },
        "iou": _percentiles([r["iou"] for r in frame_rows]),
        "iou_by_frame": {
            str(f): _percentiles([r["iou"] for r in frame_rows if r["frame"] == f])
            for f in ALIGN_FRAMES
        },
        "scores_vs_marks": scores,
        "carried_minus_mark_offset_px": carried_delta,
        "carried_minus_mark_bias_px": carried_bias,
        "carry_fit_rms_px": _percentiles(
            [r["carry_fit_rms_px"] for r in shot_rows if "carry_fit_rms_px" in r]
        ),
        "quad_carry_fit_rms_px": _percentiles(
            [r["quad_carry_fit_rms_px"] for r in shot_rows if "quad_carry_fit_rms_px" in r]
        ),
        "pre_registered_pass": {
            "criteria": {
                "median_px": PASS_MEDIAN_PX,
                "p90_px": PASS_P90_PX,
                "availability_shots": PASS_AVAILABILITY,
            },
            "result": "PASS" if passed else "FAIL",
        },
    }
    (out / "align_summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"\n{report['caveat']}\n")
    print(
        f"frames aligned {report['counts']['frames_aligned']} "
        f"accepted {report['counts']['frames_accepted']}"
    )
    print(
        f"availability (all of f66-f71) {availability}/{len(shot_rows)} shots; "
        f"3+ frames {availability_min3}/{len(shot_rows)}"
    )
    for name, stats in scores["axis_px"].items():
        if stats.get("n"):
            debiased = scores["axis_px_loo_debiased"][name]
            print(
                f"  {name:10s} n={stats['n']:2d} median {stats['median']:5.2f} px  "
                f"p90 {stats['p90']:5.2f} px  max {stats['max']:5.2f} px  "
                f"| bias {scores['axis_bias_px'][name]['all']:+5.2f}  "
                f"LOO-debiased median {debiased['median']:5.2f} p90 {debiased['p90']:5.2f}"
            )
    for name, stats in scores["landmark_euclidean_px"].items():
        if stats.get("n"):
            print(
                f"  {name:10s} (2-D) n={stats['n']:2d} median {stats['median']:5.2f} px  "
                f"p90 {stats['p90']:5.2f} px"
            )
    print("\ncarried-to-contact offset minus the marks' interpolated offset (px)")
    for name, stats in carried_delta.items():
        if stats.get("n"):
            print(
                f"  {name:10s} n={stats['n']:2d} median |err| {stats['median']:5.2f}  "
                f"p90 {stats['p90']:5.2f}  bias {carried_bias[name]:+5.2f}"
            )
    print(
        f"  carry fit rms px: linear median "
        f"{report['carry_fit_rms_px'].get('median', float('nan')):.2f}, quadratic median "
        f"{report['quad_carry_fit_rms_px'].get('median', float('nan')):.2f}"
    )
    print(f"\npre-registered gate: {report['pre_registered_pass']['result']}")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

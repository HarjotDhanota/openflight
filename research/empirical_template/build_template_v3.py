"""Iteration 3: the HEEL edge from the data, and a flying-ball veto after contact.

NO GROUND TRUTH EXISTS. Pass-1 hand marks only, via
`build_template_v2.load_marks_pass1`, which is the only loader iteration 3 uses.

Iteration 2 left one gate criterion failing -- heel x at p90 -- and showed why:
three of the four template edges were the annotator's crop, and iteration 2
replaced only the bottom one. The heel edge was a vertical cut 2.1 px outside
the heel mark, so the outline's minimum-x extent measured that cut and nothing
else (measured bias -1.94 px, which is the crop margin itself).

Two changes:

  1. THE SIDE CROP MOVES TO THE MARKS AND THE HEEL EDGE COMES FROM THE DATA.
     `SIDE_MARGIN_PX` is 0, so the toe side is cut at the toe mark exactly; the
     heel side is not cut at all. The outline is then the intersection of two
     occupancy runs: per COLUMN, the run of mean occupancy >= 0.5 starting at
     that column's topmost occupied row (iteration 2's sole), and per ROW, the
     run starting at that row's rightmost occupied column and walking LEFT (the
     new heel). Where the mean occupancy falls through 0.5 on the way left is
     the club's own heel, if it falls at all.

     Pre-registered fallback, fixed before any error was read: if more than
     `FALLBACK_EDGE_FRACTION` of the outline's occupied rows still reach the
     template grid's left edge, the occupancy never fell off in x, and the build
     reverts to a crop at the heel mark and says so.

  2. THE TEE / FLYING-BALL VETO NOW RUNS AFTER CONTACT. `build_template.
     head_masks` skips the veto entirely, on the stated grounds that "every
     frame requested here is at or before contact". Contact is f71.853 and f72
     is 0.147 frames (0.31 ms at 467.6 fps) AFTER it, so on f72 the ball is
     moving, enters the moving mask, and `split_head` hands it back as part of
     the head -- measured on `shot_021_9-iron` f72, where the outline locked at
     +17 deg on a ball blob. `head_masks_v3` finds the departing ball as the
     brightest disk-shaped response at the teed ball's own diameter, restricted
     to blob centres no lower than the tee row, accepts it only if it is at
     least `BALL_BRIGHTNESS_FRACTION` as bright as the teed ball on the
     background median, and vetoes that disk -- on post-contact frames only.
     Frames at or before contact are untouched, because there the ball is
     stationary and absent from the moving mask while the head sits on it.

    uv run python research/empirical_template/build_template_v3.py \
        --session /path/to/export --out research/empirical_template/results
"""

# pylint: disable=wrong-import-position,no-member,unsubscriptable-object

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_template import (  # noqa: E402
    ALIGN_FRAMES,
    BACKGROUND_SLICE,
    COMPONENT_RADIUS_PX,
    DEFAULT_MARKS,
    MARK_FRAMES,
    MIN_COMPONENT_AREA_PX,
    MIN_HEAD_AREA_PX,
    OCCUPANCY_THRESHOLD,
    TEMPLATE_ORIGIN,
    TEMPLATE_SHAPE,
    UPSAMPLE,
    ReferenceBall,
    ShotFrames,
    Template,
    contact_frame,
    detect_reference_ball,
    moving_mask,
    reference_scale,
    render_template,
    session_path,
    split_head,
)
from build_template_v2 import (  # noqa: E402
    CROP_TOP_MARGIN_PX,
    FACE_HEIGHT_BAND_MM,
    load_marks_pass1,
)

# The toe side is cut at the toe mark; the heel side is not cut at all.
SIDE_MARGIN_PX = 0.0

# Pre-registered fallback: if this fraction of occupied rows still reaches the
# grid's left edge, the occupancy never fell through 0.5 in x and the heel
# reverts to a crop at the mark.
FALLBACK_EDGE_FRACTION = 0.5

# The departing ball must be at least this bright, relative to the teed ball on
# the background median, before its disk is vetoed. Fail closed, not open.
BALL_BRIGHTNESS_FRACTION = 0.8
BALL_SEARCH_SPAN = 2.0  # in teed-ball diameters, either side of the tee
BALL_VETO_PAD_PX = 1.0

HEEL_MODES = ("occupancy", "crop")


@dataclass(frozen=True)
class TemplateV3(Template):
    """A `Template` that records where its bottom AND heel edges came from."""

    sole_mode: str = "occupancy"
    heel_mode: str = "occupancy"
    heel_fell_off: bool = False
    left_edge_fraction: float = 0.0
    floor_fraction: float = 0.0
    column_depth_mm: tuple[float, ...] = ()
    row_width_mm: tuple[float, ...] = ()

    def depth_stats(self) -> dict[str, float]:
        """Median / p10 / p90 of the per-column head depth, in mm."""
        return _spread(self.column_depth_mm)

    def width_stats(self) -> dict[str, float]:
        """Median / p10 / p90 of the per-row head width, in mm."""
        return _spread(self.row_width_mm)


def _spread(values: Iterable[float]) -> dict[str, float]:
    array = np.asarray(list(values), dtype=float)
    array = array[array > 0]
    if array.size == 0:
        return {}
    return {
        "median_mm": float(np.median(array)),
        "p10_mm": float(np.percentile(array, 10)),
        "p90_mm": float(np.percentile(array, 90)),
        "max_mm": float(array.max()),
        "n": int(array.size),
    }


# --------------------------------------------------------------------------
# masks: the flying-ball veto
# --------------------------------------------------------------------------


def _disk_kernel(radius: float) -> np.ndarray:
    half = max(1, int(round(radius)))
    grid_y, grid_x = np.mgrid[-half : half + 1, -half : half + 1]
    kernel = ((grid_x**2 + grid_y**2) <= radius**2).astype(np.float32)
    return kernel / max(kernel.sum(), 1.0)


def flying_ball(
    frames: np.ndarray, index: int, ball: ReferenceBall, background: np.ndarray
) -> tuple[float, float, float] | None:
    """The departing ball on a post-contact frame: brightest disk above the tee row.

    Returns (x, y, veto_radius), or None when nothing near the tee is as bright
    as the teed ball itself -- fail closed, so a frame with no visible ball is
    left exactly as `head_masks` had it.
    """
    radius = ball.diameter_px / 2.0
    kernel = _disk_kernel(radius)
    response = cv2.filter2D(
        frames[index].astype(np.float32), -1, kernel, borderType=cv2.BORDER_REPLICATE
    )
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


def head_masks_v3(
    frames: np.ndarray,
    ball: ReferenceBall,
    wanted: Iterable[int],
    contact: float,
    *,
    ball_veto: bool = True,
) -> tuple[dict[int, np.ndarray], dict[int, tuple[float, float, float]]]:
    """`build_template.head_masks` plus a flying-ball veto on post-contact frames."""
    moving = moving_mask(frames)
    background = np.median(frames[BACKGROUND_SLICE], axis=0)
    grid_y, grid_x = np.mgrid[0 : frames.shape[1], 0 : frames.shape[2]]
    masks: dict[int, np.ndarray] = {}
    vetoes: dict[int, tuple[float, float, float]] = {}
    for index in wanted:
        if not 0 <= index < frames.shape[0]:
            continue
        current = moving[index].copy()
        if ball_veto and index > contact:
            found = flying_ball(frames, index, ball, background)
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
            distance = math.hypot(centroids[label][0] - ball.x, centroids[label][1] - ball.y)
            if area < MIN_COMPONENT_AREA_PX or distance > COMPONENT_RADIUS_PX:
                continue
            if best is None or area > best[0]:
                best = (area, label)
        if best is None:
            continue
        split = split_head((labels == best[1]).astype(np.uint8))
        if split is None or int(split[0].sum()) < MIN_HEAD_AREA_PX:
            continue
        masks[index] = split[0] > 0
    return masks, vetoes


def load_shot_v3(session: Path, shot: str, row: dict[str, str], *, ball_veto: bool) -> ShotFrames:
    """`build_template.load_shot` with the post-contact flying-ball veto applied."""
    shot_dir = session / "shots" / shot
    with (shot_dir / "camera_metadata.json").open(encoding="utf-8") as handle:
        metadata = json.load(handle)
    with np.load(shot_dir / "frames.npz") as capture:
        frames = capture["frames"][:, :, ::-1].astype(np.uint8)
    ball = detect_reference_ball(frames)
    contact = contact_frame(metadata)
    masks, _ = head_masks_v3(frames, ball, ALIGN_FRAMES, contact, ball_veto=ball_veto)
    return ShotFrames(
        shot=shot,
        number=int(shot.split("_", 2)[1]),
        club=row["club"],
        frames=frames,
        ball=ball,
        fps=float(metadata["delivered_fps"]),
        contact=contact,
        range_rate_ms=float(row["iwr_club_path_range_rate_ms"]),
        masks=masks,
    )


def load_session_v3(
    session: Path,
    marks: dict[tuple[str, int], dict[str, Any]],
    exclude: set[int],
    *,
    ball_veto: bool = True,
) -> dict[str, ShotFrames]:
    """Every marked shot, segmented with the iteration-3 mask recipe."""
    import csv  # pylint: disable=import-outside-toplevel

    with (session / "shots.csv").open(newline="", encoding="utf-8") as handle:
        rows = {int(row["shot_number"]): row for row in csv.DictReader(handle)}
    shots: dict[str, ShotFrames] = {}
    for shot in sorted({name for name, _ in marks}):
        number = int(shot.split("_", 2)[1])
        if number in exclude:
            continue
        shots[shot] = load_shot_v3(session, shot, rows[number], ball_veto=ball_veto)
    return shots


# --------------------------------------------------------------------------
# the template
# --------------------------------------------------------------------------


def sample_to_template_v3(
    shot: ShotFrames,
    frame: int,
    mark: dict[str, Any],
    reference_mm_per_px: float,
    *,
    roll_normalise: bool,
    heel_open: bool,
    side_margin_px: float = SIDE_MARGIN_PX,
    top_margin_px: float = CROP_TOP_MARGIN_PX,
) -> tuple[np.ndarray, dict[str, np.ndarray]] | None:
    """One mask on the common grid, cropped on the top and the TOE side only.

    The crop is asymmetric now: the toe side is cut at the toe mark plus
    `side_margin_px`, and the heel side is either cut at the heel mark minus the
    same margin (`heel_open=False`) or not cut at all.
    """
    mask = shot.masks.get(frame)
    if mask is None:
        return None
    heel = np.asarray(mark["marks"]["heel"], dtype=float)
    toe = np.asarray(mark["marks"]["toe"], dtype=float)
    topline = np.asarray(mark["marks"]["topline"], dtype=float)
    anchor = np.array([0.5 * (heel[0] + toe[0]), topline[1]])

    step = reference_mm_per_px / (UPSAMPLE * shot.mm_per_px(frame))
    angle = math.atan2(toe[1] - heel[1], toe[0] - heel[0]) if roll_normalise else 0.0
    cosine, sine = math.cos(angle), math.sin(angle)

    grid_y, grid_x = np.mgrid[0 : TEMPLATE_SHAPE[0], 0 : TEMPLATE_SHAPE[1]].astype(np.float32)
    local_x = (grid_x - TEMPLATE_ORIGIN[0]) * step
    local_y = (grid_y - TEMPLATE_ORIGIN[1]) * step
    map_x = (anchor[0] + cosine * local_x - sine * local_y).astype(np.float32)
    map_y = (anchor[1] + sine * local_x + cosine * local_y).astype(np.float32)
    warped = cv2.remap(mask.astype(np.float32), map_x, map_y, cv2.INTER_LINEAR, borderValue=0.0)

    def to_template(point: np.ndarray) -> np.ndarray:
        delta = point - anchor
        local = np.array(
            [cosine * delta[0] + sine * delta[1], -sine * delta[0] + cosine * delta[1]]
        )
        return local / step + np.asarray(TEMPLATE_ORIGIN, dtype=float)

    landmarks = {
        "heel": to_template(heel),
        "toe": to_template(toe),
        "topline": to_template(topline),
        "midpoint": np.asarray(TEMPLATE_ORIGIN, dtype=float),
    }
    keep = (grid_x <= landmarks["toe"][0] + side_margin_px / step) & (
        grid_y - TEMPLATE_ORIGIN[1] >= -top_margin_px / step
    )
    if not heel_open:
        keep = keep & (grid_x >= landmarks["heel"][0] - side_margin_px / step)
    return np.where(keep, warped, 0.0), landmarks


def _column_runs(above: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per column: the run of True starting at the column's topmost True."""
    keep = np.zeros_like(above)
    depth = np.zeros(above.shape[1], dtype=float)
    for column in range(above.shape[1]):
        rows = np.flatnonzero(above[:, column])
        if rows.size == 0:
            continue
        top = int(rows[0])
        bottom = top
        while bottom + 1 < above.shape[0] and above[bottom + 1, column]:
            bottom += 1
        keep[top : bottom + 1, column] = True
        depth[column] = bottom - top + 1
    return keep, depth


def _row_runs(above: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per row: the run of True starting at the row's rightmost True, walking LEFT."""
    keep = np.zeros_like(above)
    width = np.zeros(above.shape[0], dtype=float)
    for row in range(above.shape[0]):
        columns = np.flatnonzero(above[row])
        if columns.size == 0:
            continue
        right = int(columns[-1])
        left = right
        while left - 1 >= 0 and above[row, left - 1]:
            left -= 1
        keep[row, left : right + 1] = True
        width[row] = right - left + 1
    return keep, width


def outline_from_occupancy(
    occupancy: np.ndarray, *, heel_from_data: bool, threshold: float = OCCUPANCY_THRESHOLD
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Column runs (the sole) intersected with row runs (the heel)."""
    above = occupancy >= threshold
    columns, depth = _column_runs(above)
    if not heel_from_data:
        return columns, depth, np.zeros(above.shape[0])
    rows, width = _row_runs(above)
    return columns & rows, depth, width


def build_template_v3(
    shots: dict[str, ShotFrames],
    marks: dict[tuple[str, int], dict[str, Any]],
    club: str,
    reference_mm_per_px: float,
    *,
    roll_normalise: bool = False,
    exclude: str | None = None,
    heel_mode: str = "occupancy",
    side_margin_px: float = SIDE_MARGIN_PX,
    top_margin_px: float = CROP_TOP_MARGIN_PX,
) -> TemplateV3 | None:
    """Mean occupancy for one club, with a data-built sole AND a data-built heel."""
    if heel_mode not in HEEL_MODES:
        raise ValueError(f"heel_mode must be one of {HEEL_MODES}")
    heel_open = heel_mode == "occupancy"
    stack: list[np.ndarray] = []
    keys: list[tuple[str, int]] = []
    landmark_stack: dict[str, list[np.ndarray]] = {}
    for shot in sorted(shots.values(), key=lambda s: s.number):
        if shot.club != club or shot.shot == exclude:
            continue
        for frame in MARK_FRAMES:
            mark = marks.get((shot.shot, frame))
            if mark is None:
                continue
            sample = sample_to_template_v3(
                shot,
                frame,
                mark,
                reference_mm_per_px,
                roll_normalise=roll_normalise,
                heel_open=heel_open,
                side_margin_px=side_margin_px,
                top_margin_px=top_margin_px,
            )
            if sample is None:
                continue
            warped, landmarks = sample
            stack.append(warped)
            keys.append((shot.shot, frame))
            for name, point in landmarks.items():
                landmark_stack.setdefault(name, []).append(point)
    if not stack:
        return None
    occupancy = np.mean(np.stack(stack), axis=0)
    outline, depth, width = outline_from_occupancy(occupancy, heel_from_data=heel_open)

    occupied_rows = outline.any(axis=1)
    left_edge_fraction = (
        float(outline[:, 0][occupied_rows].mean()) if int(occupied_rows.sum()) else 0.0
    )
    occupied_columns = outline.any(axis=0)
    floor_fraction = (
        float(outline[-1, :][occupied_columns].mean()) if int(occupied_columns.sum()) else 0.0
    )
    fell_off = left_edge_fraction <= FALLBACK_EDGE_FRACTION
    if heel_open and not fell_off:
        # Pre-registered fallback: the heel-side shadow never averaged away.
        rebuilt = build_template_v3(
            shots,
            marks,
            club,
            reference_mm_per_px,
            roll_normalise=roll_normalise,
            exclude=exclude,
            heel_mode="crop",
            side_margin_px=side_margin_px,
            top_margin_px=top_margin_px,
        )
        if rebuilt is not None:
            fields = (
                "club",
                "outline",
                "occupancy",
                "landmarks",
                "mm_per_px",
                "n_samples",
                "sample_iou",
                "roll_normalised",
                "excluded",
                "sole_mode",
                "column_depth_mm",
                "row_width_mm",
                "floor_fraction",
            )
            return TemplateV3(
                **{field: getattr(rebuilt, field) for field in fields},
                heel_mode="crop-fallback",
                heel_fell_off=False,
                left_edge_fraction=left_edge_fraction,
            )

    mm_per_template_px = reference_mm_per_px / UPSAMPLE
    ious: list[tuple[str, int, float]] = []
    for (shot_name, frame), warped in zip(keys, stack, strict=True):
        binary = warped >= 0.5
        union = int((binary | outline).sum())
        ious.append(
            (shot_name, frame, float((binary & outline).sum()) / union if union else math.nan)
        )
    return TemplateV3(
        club=club,
        outline=outline,
        occupancy=occupancy,
        landmarks={
            name: np.mean(np.stack(points), axis=0) for name, points in landmark_stack.items()
        },
        mm_per_px=reference_mm_per_px,
        n_samples=len(stack),
        sample_iou=ious,
        roll_normalised=roll_normalise,
        excluded=exclude,
        sole_mode="occupancy",
        heel_mode=heel_mode,
        heel_fell_off=fell_off,
        left_edge_fraction=left_edge_fraction,
        floor_fraction=floor_fraction,
        column_depth_mm=tuple((depth * mm_per_template_px).tolist()),
        row_width_mm=tuple((width * mm_per_template_px).tolist()),
    )


def main() -> int:
    """Build both heel modes for both clubs, render them, report the edges."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", default=None)
    parser.add_argument("--marks", default=str(DEFAULT_MARKS))
    parser.add_argument("--out", default=str(Path(__file__).resolve().parent / "results"))
    parser.add_argument("--exclude-shot", type=int, action="append", default=[1])
    parser.add_argument("--roll-normalise", action="store_true")
    parser.add_argument("--side-margin", type=float, default=SIDE_MARGIN_PX)
    parser.add_argument("--no-ball-veto", dest="ball_veto", action="store_false", default=True)
    args = parser.parse_args()

    session = session_path(args.session)
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    marks = load_marks_pass1(Path(args.marks).expanduser())
    shots = load_session_v3(session, marks, set(args.exclude_shot), ball_veto=args.ball_veto)
    reference = reference_scale(shots)
    print(f"pass-1 marks {len(marks)}; {len(shots)} shots; reference {reference:.4f} mm/px")

    report: dict[str, Any] = {
        "caveat": (
            "CONSISTENCY ONLY. No ground truth exists. Scored against pass-1 "
            "hand marks, which also define the crop, origin and landmark "
            "convention."
        ),
        "session": str(session),
        "marks_pass": 1,
        "ball_veto": args.ball_veto,
        "side_margin_px": args.side_margin,
        "reference_mm_per_px": reference,
        "face_height_band_mm": list(FACE_HEIGHT_BAND_MM),
        "templates": {},
    }
    for heel_mode in HEEL_MODES:
        report["templates"][heel_mode] = {}
        for club in sorted({s.club for s in shots.values()}):
            template = build_template_v3(
                shots,
                marks,
                club,
                reference,
                roll_normalise=args.roll_normalise,
                heel_mode=heel_mode,
                side_margin_px=args.side_margin,
            )
            if template is None:
                continue
            width_mm, height_mm = template.extent_mm()
            ious = np.asarray([iou for _, _, iou in template.sample_iou], dtype=float)
            report["templates"][heel_mode][club] = {
                "n_samples": template.n_samples,
                "heel_mode_effective": template.heel_mode,
                "heel_fell_off_before_edge": template.heel_fell_off,
                "left_edge_fraction": template.left_edge_fraction,
                "floor_fraction": template.floor_fraction,
                "bbox_width_mm": width_mm,
                "bbox_height_mm": height_mm,
                "column_depth": template.depth_stats(),
                "row_width": template.width_stats(),
                "mean_sample_iou": float(np.nanmean(ious)),
                "median_sample_iou": float(np.nanmedian(ious)),
                "min_sample_iou": float(np.nanmin(ious)),
                "landmarks": {k: v.tolist() for k, v in template.landmarks.items()},
            }
            tag = f"v3_{heel_mode}"
            render_template(template, out / f"template_{tag}_{club}.png")
            np.savez_compressed(
                out / f"template_{tag}_{club}.npz",
                outline=template.outline,
                occupancy=template.occupancy,
                column_depth_mm=np.asarray(template.column_depth_mm),
                row_width_mm=np.asarray(template.row_width_mm),
                **{f"landmark_{k}": v for k, v in template.landmarks.items()},
                mm_per_px=np.asarray(reference),
            )
            print(
                f"{heel_mode:10s} {club:7s} n={template.n_samples:2d} "
                f"bbox {width_mm:6.1f} x {height_mm:5.1f} mm  "
                f"row width median {template.width_stats().get('median_mm', float('nan')):5.1f} mm "
                f"left-edge {template.left_edge_fraction:.2f}  "
                f"effective={template.heel_mode}  IoU {np.nanmean(ious):.3f}"
            )

    (out / "template_v3_summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\n{report['caveat']}")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

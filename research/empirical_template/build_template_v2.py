"""Iteration 2 of the empirical clubhead outline: the SOLE comes from the data.

NO GROUND TRUTH EXISTS. The only reference is the maintainer's hand marks,
`openflight/contact_marks.jsonl` **pass 1** (annotator `harjot`). Pass 2 lines
are a different annotator with a visibly different convention (toe y differs by
a median 8.7 px, topline x by 10.9 px) and are NEVER read here. Iteration 1's
`build_template.load_marks` keyed on `(shot, frame)` with no pass filter, so it
takes whichever line comes LAST in the file; that was safe when the file held
one pass and is not safe now. `load_marks_pass1` below is the fix.

What changes from `build_template.py`:

  1. THE BOTTOM EDGE IS NO LONGER A CROP. Iteration 1 cut every sample flat at
     `CROP_DEPTH_MM = 48` below the topline row, so the template's height was
     exactly 48.2 mm on all four builds -- the crop, not the club. Here the
     sample is cropped only on the top (topline row minus a 2 px margin) and on
     the sides (heel/toe +- 2 px). The bottom is left open all the way to the
     template grid's floor, and the outline is then taken PER COLUMN as the run
     of occupancy >= 0.5 that starts at that column's topmost occupied row. The
     depth at which the mean occupancy falls through 0.5 is the club's own sole
     -- if it falls at all.
  2. A PRE-REGISTERED FALLBACK. The sole shadow is attached to the head in
     every single-frame mask (measured: 48-78 mm of continuous mask below the
     topline row on f71/f72). The construction only works if the shadow's
     extent VARIES shot to shot, so that averaging drives it below 0.5 while
     the head itself stays above. The rule, fixed before the numbers were read:
     if more than `FALLBACK_FLOOR_FRACTION` of the outline's columns are still
     occupied at the grid floor, the occupancy never fell off, and the build
     falls back to the 48 mm cut and says so.

Everything else -- the un-mirroring, the ball detection, the mask recipe, the
radar range ramp, the resampling to 1581 mm, the leave-one-shot-out hook -- is
`build_template.py` unchanged and imported, not copied.

    uv run python research/empirical_template/build_template_v2.py \
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
from typing import Any

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_template import (  # noqa: E402
    CROP_DEPTH_MM,
    CROP_MARGIN_PX,
    DEFAULT_MARKS,
    MARK_FRAMES,
    OCCUPANCY_THRESHOLD,
    TEMPLATE_ORIGIN,
    TEMPLATE_SHAPE,
    UPSAMPLE,
    ShotFrames,
    Template,
    load_session,
    reference_scale,
    render_template,
    session_path,
)

# Margin kept ABOVE the marked topline row so the crown edge is data, not crop.
# Same 2 px the sides already use. The teed ball sits above the head here and is
# stationary, so it is absent from the moving mask and cannot leak in.
CROP_TOP_MARGIN_PX = 2.0

# Pre-registered fallback: if this fraction of occupied columns still reaches
# the template grid's floor, the occupancy never fell through 0.5 and the
# 48 mm cut is used instead.
FALLBACK_FLOOR_FRACTION = 0.5

# A mid-iron face is about 45-50 mm tall. This is the sanity band the measured
# template height is REPORTED against; it is not enforced anywhere.
FACE_HEIGHT_BAND_MM = (45.0, 50.0)

SOLE_MODES = ("occupancy", "cut")


@dataclass(frozen=True)
class TemplateV2(Template):
    """A `Template` that also carries where its bottom edge came from."""

    sole_mode: str = "cut"
    sole_fell_off: bool = False
    floor_fraction: float = 0.0
    column_depth_mm: tuple[float, ...] = ()

    def sole_depth_stats(self) -> dict[str, float]:
        """Median / p10 / p90 / max of the per-column head depth, in mm."""
        depths = np.asarray(self.column_depth_mm, dtype=float)
        depths = depths[depths > 0]
        if depths.size == 0:
            return {}
        return {
            "median_mm": float(np.median(depths)),
            "p10_mm": float(np.percentile(depths, 10)),
            "p90_mm": float(np.percentile(depths, 90)),
            "max_mm": float(depths.max()),
            "n_columns": int(depths.size),
        }


def load_marks_pass1(
    path: Path, pass_index: int = 1, annotator: str = "harjot"
) -> dict[tuple[str, int], dict[str, Any]]:
    """The maintainer's hand marks, PASS 1 ONLY, keyed by (shot, frame).

    `build_template.load_marks` has no pass filter and last-line-wins, so with
    a second pass in the file it silently scores against the other annotator.
    """
    marks: dict[tuple[str, int], dict[str, Any]] = {}
    seen: set[tuple[str, int]] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("skipped"):
            continue
        if record.get("pass_index") != pass_index or record.get("annotator") != annotator:
            continue
        key = (record["shot"], int(record["frame"]))
        if key in seen:
            raise SystemExit(f"duplicate pass-{pass_index} mark for {key}")
        seen.add(key)
        marks[key] = record
    if not marks:
        raise SystemExit(f"no pass-{pass_index} marks by {annotator!r} in {path}")
    return marks


def sample_to_template(
    shot: ShotFrames,
    frame: int,
    mark: dict[str, Any],
    reference_mm_per_px: float,
    *,
    roll_normalise: bool,
    bottom_crop: bool,
    top_margin_px: float = CROP_TOP_MARGIN_PX,
) -> tuple[np.ndarray, dict[str, np.ndarray]] | None:
    """One (shot, frame) mask on the common grid, with the bottom crop optional.

    Identical to `build_template.sample_to_template` except that the bottom of
    the crop box is a flag, and the top of it carries a 2 px margin so the crown
    edge is the club's rather than the annotator's row.
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

    half_span = 0.5 * float(np.hypot(*(toe - heel))) + CROP_MARGIN_PX
    keep = (np.abs(grid_x - TEMPLATE_ORIGIN[0]) <= half_span / step) & (
        grid_y - TEMPLATE_ORIGIN[1] >= -top_margin_px / step
    )
    if bottom_crop:
        depth_px = CROP_DEPTH_MM / shot.mm_per_px(frame)
        keep = keep & (grid_y - TEMPLATE_ORIGIN[1] <= depth_px / step)
    warped = np.where(keep, warped, 0.0)

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
    return warped, landmarks


def outline_from_columns(
    occupancy: np.ndarray, threshold: float = OCCUPANCY_THRESHOLD
) -> tuple[np.ndarray, np.ndarray]:
    """Per column, the run of `occupancy >= threshold` starting at its top.

    Returns the outline and the per-column depth in template pixels (0 where
    the column is empty). The bottom of each run is where the mean occupancy
    falls through the threshold -- the club's own sole edge, if the shadow
    below it is inconsistent enough between shots to average away.
    """
    above = occupancy >= threshold
    outline = np.zeros_like(above)
    depth = np.zeros(above.shape[1], dtype=float)
    for column in range(above.shape[1]):
        rows = np.flatnonzero(above[:, column])
        if rows.size == 0:
            continue
        top = int(rows[0])
        bottom = top
        while bottom + 1 < above.shape[0] and above[bottom + 1, column]:
            bottom += 1
        outline[top : bottom + 1, column] = True
        depth[column] = bottom - top + 1
    return outline, depth


def build_template_v2(
    shots: dict[str, ShotFrames],
    marks: dict[tuple[str, int], dict[str, Any]],
    club: str,
    reference_mm_per_px: float,
    *,
    roll_normalise: bool = False,
    exclude: str | None = None,
    sole_mode: str = "occupancy",
    top_margin_px: float = CROP_TOP_MARGIN_PX,
) -> TemplateV2 | None:
    """Mean occupancy of every marked mask for one club, with a data-built sole.

    `sole_mode='cut'` reproduces iteration 1 exactly (48 mm horizontal bottom,
    outline = occupancy >= 0.5). `sole_mode='occupancy'` leaves the bottom open
    and reads the sole off the occupancy map, falling back to the cut if the
    occupancy never falls through 0.5 before the grid floor.
    """
    if sole_mode not in SOLE_MODES:
        raise ValueError(f"sole_mode must be one of {SOLE_MODES}")
    bottom_crop = sole_mode == "cut"
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
            sample = sample_to_template(
                shot,
                frame,
                mark,
                reference_mm_per_px,
                roll_normalise=roll_normalise,
                bottom_crop=bottom_crop,
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

    if sole_mode == "cut":
        outline = occupancy >= OCCUPANCY_THRESHOLD
        _, depth = outline_from_columns(occupancy)
    else:
        outline, depth = outline_from_columns(occupancy)
    occupied = outline.any(axis=0)
    floor_fraction = float(outline[-1, :][occupied].mean()) if int(occupied.sum()) else 0.0
    fell_off = floor_fraction <= FALLBACK_FLOOR_FRACTION
    effective_mode = sole_mode
    if sole_mode == "occupancy" and not fell_off:
        # Pre-registered fallback: the shadow never averaged away.
        effective_mode = "cut-fallback"
        rebuilt = build_template_v2(
            shots,
            marks,
            club,
            reference_mm_per_px,
            roll_normalise=roll_normalise,
            exclude=exclude,
            sole_mode="cut",
            top_margin_px=top_margin_px,
        )
        if rebuilt is not None:
            return TemplateV2(
                club=rebuilt.club,
                outline=rebuilt.outline,
                occupancy=rebuilt.occupancy,
                landmarks=rebuilt.landmarks,
                mm_per_px=rebuilt.mm_per_px,
                n_samples=rebuilt.n_samples,
                sample_iou=rebuilt.sample_iou,
                roll_normalised=rebuilt.roll_normalised,
                excluded=rebuilt.excluded,
                sole_mode=effective_mode,
                sole_fell_off=False,
                floor_fraction=floor_fraction,
                column_depth_mm=rebuilt.column_depth_mm,
            )
    mm_per_template_px = reference_mm_per_px / UPSAMPLE
    ious: list[tuple[str, int, float]] = []
    for (shot_name, frame), warped in zip(keys, stack, strict=True):
        binary = warped >= 0.5
        union = int((binary | outline).sum())
        ious.append(
            (shot_name, frame, float((binary & outline).sum()) / union if union else math.nan)
        )
    return TemplateV2(
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
        sole_mode=effective_mode,
        sole_fell_off=fell_off,
        floor_fraction=floor_fraction,
        column_depth_mm=tuple((depth * mm_per_template_px).tolist()),
    )


def main() -> int:
    """Build both sole modes for both clubs, render them, and report heights."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", default=None)
    parser.add_argument("--marks", default=str(DEFAULT_MARKS))
    parser.add_argument("--out", default=str(Path(__file__).resolve().parent / "results"))
    parser.add_argument("--exclude-shot", type=int, action="append", default=[1])
    parser.add_argument("--roll-normalise", action="store_true")
    parser.add_argument("--top-margin", type=float, default=CROP_TOP_MARGIN_PX)
    args = parser.parse_args()

    session = session_path(args.session)
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    marks = load_marks_pass1(Path(args.marks).expanduser())
    shots = load_session(session, marks, set(args.exclude_shot))
    reference = reference_scale(shots)
    print(f"pass-1 marks {len(marks)}; {len(shots)} shots; reference {reference:.4f} mm/px")

    report: dict[str, Any] = {
        "caveat": (
            "CONSISTENCY ONLY. No ground truth exists. Scored against pass-1 "
            "hand marks, which also define the crop, the origin and the "
            "landmark convention."
        ),
        "session": str(session),
        "marks_pass": 1,
        "n_marks": len(marks),
        "reference_mm_per_px": reference,
        "crop_top_margin_px": CROP_TOP_MARGIN_PX,
        "fallback_floor_fraction": FALLBACK_FLOOR_FRACTION,
        "face_height_band_mm": list(FACE_HEIGHT_BAND_MM),
        "templates": {},
    }

    for sole_mode in SOLE_MODES:
        report["templates"][sole_mode] = {}
        for club in sorted({s.club for s in shots.values()}):
            template = build_template_v2(
                shots,
                marks,
                club,
                reference,
                roll_normalise=args.roll_normalise,
                sole_mode=sole_mode,
                top_margin_px=args.top_margin,
            )
            if template is None:
                continue
            width_mm, height_mm = template.extent_mm()
            ious = np.asarray([iou for _, _, iou in template.sample_iou], dtype=float)
            stats = template.sole_depth_stats()
            in_band = (
                FACE_HEIGHT_BAND_MM[0] <= stats.get("median_mm", -1.0) <= FACE_HEIGHT_BAND_MM[1]
            )
            report["templates"][sole_mode][club] = {
                "n_samples": template.n_samples,
                "sole_mode_effective": template.sole_mode,
                "sole_fell_off_before_floor": template.sole_fell_off,
                "floor_fraction": template.floor_fraction,
                "bbox_width_mm": width_mm,
                "bbox_height_mm": height_mm,
                "column_depth": stats,
                "median_depth_in_face_band": bool(in_band),
                "mean_sample_iou": float(np.nanmean(ious)),
                "median_sample_iou": float(np.nanmedian(ious)),
                "min_sample_iou": float(np.nanmin(ious)),
                "worst_samples": [
                    {"shot": s, "frame": f, "iou": v}
                    for s, f, v in sorted(template.sample_iou, key=lambda r: r[2])[:5]
                ],
                "landmarks": {k: v.tolist() for k, v in template.landmarks.items()},
            }
            tag = f"v2_{sole_mode}"
            render_template(template, out / f"template_{tag}_{club}.png")
            np.savez_compressed(
                out / f"template_{tag}_{club}.npz",
                outline=template.outline,
                occupancy=template.occupancy,
                column_depth_mm=np.asarray(template.column_depth_mm),
                **{f"landmark_{k}": v for k, v in template.landmarks.items()},
                mm_per_px=np.asarray(reference),
            )
            print(
                f"{sole_mode:10s} {club:7s} n={template.n_samples:2d} "
                f"bbox {width_mm:6.1f} x {height_mm:5.1f} mm  "
                f"column depth median {stats.get('median_mm', float('nan')):5.1f} "
                f"(p10 {stats.get('p10_mm', float('nan')):4.1f} "
                f"p90 {stats.get('p90_mm', float('nan')):4.1f}) mm  "
                f"floor {template.floor_fraction:.2f}  "
                f"effective={template.sole_mode}  IoU {np.nanmean(ious):.3f}"
            )

    (out / "template_v2_summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\n{report['caveat']}")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

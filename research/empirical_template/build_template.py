"""Build a clubhead OUTLINE TEMPLATE per club from the session's own masks.

NO GROUND TRUTH EXISTS. Nothing here is scored against a launch monitor. The
only reference is the maintainer's hand marks, which are one human's reading of
the same pixels: every figure downstream is a CONSISTENCY result between a
data-built outline and a human annotator, never an accuracy result.

The GrabCAD mesh is not used. The outline comes from the data:

  1. Per shot, frames are un-mirrored (`frames[:, :, ::-1]`), the teed ball is
     found by `club_motion.detect_reference_ball`, and the per-shot plate scale
     is 42.67 mm / diameter_px -- the millimetres per pixel at the BALL's range.
  2. The head mask is the `research/session_metrics/swing_plane.py` recipe:
     background median of f8:56, `|diff| > max(4 sigma, 18)`, 3x3 close plus
     hole fill, largest moving component within 200 px of the ball, split by
     `clubpose.head_split.split_head`. The tee veto is NOT applied before
     impact -- the teed ball is stationary and so is absent from the moving
     mask, while the head sits on it, and vetoing punches a hole through the
     head. Frames 71 and 72 are both pre-contact-ish (contact is 71.85), so no
     veto runs at all on the template frames.
  3. For each (shot, frame in {71, 72}) that the maintainer marked, the mask is
     cropped to the head box the marks define: x between heel and toe with a
     2 px margin, y from the topline row down by 48 mm. That drops the ball cap
     above the topline and most of the sole shadow.
  4. The crop is translated so the heel-toe midpoint on the topline row is the
     origin, and resampled to a common plate scale -- the 9-iron median -- at
     `UPSAMPLE` times that scale, with the radar's own range for the frame
     folded in so every sample is an outline AS SEEN AT 1581 mm.
  5. The samples are averaged into an OCCUPANCY MAP; the template is that map
     thresholded at 0.5. The map's blur IS the label/mask noise.

The marks define the crop and the origin, so the template is not independent of
the annotator's convention. `template_for` therefore supports LEAVE-ONE-SHOT-OUT
so that the shot being aligned never contributed to the outline it is scored
against.

TWO NORMALISATIONS are built and reported. The brief's is translation and scale
only, so the club's own roll (the heel-toe line is 5-10 deg off horizontal, and
the amount varies shot to shot) blurs the average. The variant additionally
de-rotates each sample by its heel-to-toe line angle. Both mean-IoU figures are
printed; `--roll-normalise` selects which one the alignment stage consumes.

Run from the repository root:

    uv run python research/empirical_template/build_template.py \
        --session /path/to/export --out research/empirical_template/results
"""

# pylint: disable=wrong-import-position,no-member,unsubscriptable-object

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from scipy import ndimage  # noqa: E402

from openflight.camera.club_motion import ReferenceBall, detect_reference_ball  # noqa: E402
from openflight.camera.clubpose.head_split import split_head  # noqa: E402

BALL_DIAMETER_MM = 42.67
BALL_TO_UNIT_M = 1.575
SPEED_OF_SOUND_M_S = 343.0
CAMERA_BALL_RANGE_MM = 1581.0

BACKGROUND_SLICE = slice(8, 56)
MOVING_SIGMA = 4.0
MOVING_FLOOR = 18.0
COMPONENT_RADIUS_PX = 200.0
MIN_COMPONENT_AREA_PX = 40
MIN_HEAD_AREA_PX = 60

MARK_FRAMES = (71, 72)
ALIGN_FRAMES = tuple(range(66, 73))

# Crop that isolates the head from the ball cap above the topline and the sole
# shadow below it, expressed the way the marks are: pixels sideways, mm down.
CROP_MARGIN_PX = 2.0
CROP_DEPTH_MM = 48.0

# The template grid is UPSAMPLE times finer than the reference plate scale, so
# that 0.25 px alignment refinement has something to land on.
UPSAMPLE = 4
TEMPLATE_HALF_WIDTH = 120
TEMPLATE_ABOVE = 24
TEMPLATE_BELOW = 88
TEMPLATE_ORIGIN = (TEMPLATE_HALF_WIDTH, TEMPLATE_ABOVE)
TEMPLATE_SHAPE = (TEMPLATE_ABOVE + TEMPLATE_BELOW + 1, 2 * TEMPLATE_HALF_WIDTH + 1)

OCCUPANCY_THRESHOLD = 0.5

DEFAULT_MARKS = ROOT.parent / "openflight" / "contact_marks.jsonl"


@dataclass(frozen=True)
class ShotFrames:
    """Everything one shot contributes, loaded once."""

    shot: str
    number: int
    club: str
    frames: np.ndarray
    ball: ReferenceBall
    fps: float
    contact: float
    range_rate_ms: float
    masks: dict[int, np.ndarray]

    @property
    def plate_mm_per_px(self) -> float:
        """Millimetres per pixel at the teed ball's range (1581 mm)."""
        return BALL_DIAMETER_MM / self.ball.diameter_px

    def range_mm(self, frame: float) -> float:
        """Camera-to-head range from the radar's own ramp, anchored at contact."""
        return CAMERA_BALL_RANGE_MM - self.range_rate_ms * 1000.0 * (
            (self.contact - frame) / self.fps
        )

    def mm_per_px(self, frame: float) -> float:
        """Millimetres per pixel at the head's range on this frame."""
        return self.plate_mm_per_px * self.range_mm(frame) / CAMERA_BALL_RANGE_MM


@dataclass(frozen=True)
class Template:
    """One club's empirical outline plus the landmarks it carries."""

    club: str
    outline: np.ndarray
    occupancy: np.ndarray
    landmarks: dict[str, np.ndarray]
    mm_per_px: float
    n_samples: int
    sample_iou: list[tuple[str, int, float]]
    roll_normalised: bool
    excluded: str | None

    @property
    def mm_per_template_px(self) -> float:
        """Physical size of one template pixel, at 1581 mm range."""
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


def contact_frame(metadata: dict[str, Any]) -> float:
    """Frame index of ball contact, from the acoustic trigger's flight time."""
    return int(metadata["pre_trigger_frames"]) - BALL_TO_UNIT_M / SPEED_OF_SOUND_M_S * float(
        metadata["delivered_fps"]
    )


def moving_mask(frames: np.ndarray) -> np.ndarray:
    """Per-frame boolean mask of pixels that moved, with the head filled in."""
    background = np.median(frames[BACKGROUND_SLICE], axis=0)
    sigma = frames[BACKGROUND_SLICE].astype(float).std(axis=0)
    threshold = np.maximum(MOVING_SIGMA * sigma, MOVING_FLOOR)
    raw = np.abs(frames.astype(float) - background) > threshold
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    filled = np.empty_like(raw)
    for index in range(raw.shape[0]):
        closed = cv2.morphologyEx(raw[index].astype(np.uint8), cv2.MORPH_CLOSE, kernel)
        filled[index] = ndimage.binary_fill_holes(closed > 0)
    return filled


def head_masks(
    frames: np.ndarray, ball: ReferenceBall, wanted: Iterable[int]
) -> dict[int, np.ndarray]:
    """The `split_head` head half of the largest moving component near the ball.

    No tee veto and no ball-flight veto: every frame requested here is at or
    before contact, where the teed ball is stationary and therefore absent from
    the moving mask anyway.
    """
    moving = moving_mask(frames)
    masks: dict[int, np.ndarray] = {}
    for index in wanted:
        if not 0 <= index < frames.shape[0]:
            continue
        count, labels, stats, centroids = cv2.connectedComponentsWithStats(
            moving[index].astype(np.uint8), 8
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
    return masks


def load_marks(path: Path) -> dict[tuple[str, int], dict[str, Any]]:
    """The maintainer's hand marks, keyed by (shot, frame). READ ONLY."""
    marks: dict[tuple[str, int], dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("skipped"):
            continue
        marks[(record["shot"], int(record["frame"]))] = record
    return marks


def load_shot(session: Path, shot: str, row: dict[str, str]) -> ShotFrames:
    """Frames, ball, radar rate and the f66-f72 head masks for one shot."""
    shot_dir = session / "shots" / shot
    with (shot_dir / "camera_metadata.json").open(encoding="utf-8") as handle:
        metadata = json.load(handle)
    with np.load(shot_dir / "frames.npz") as capture:
        frames = capture["frames"][:, :, ::-1].astype(np.uint8)
    ball = detect_reference_ball(frames)
    return ShotFrames(
        shot=shot,
        number=int(shot.split("_", 2)[1]),
        club=row["club"],
        frames=frames,
        ball=ball,
        fps=float(metadata["delivered_fps"]),
        contact=contact_frame(metadata),
        range_rate_ms=float(row["iwr_club_path_range_rate_ms"]),
        masks=head_masks(frames, ball, ALIGN_FRAMES),
    )


def sample_to_template(
    shot: ShotFrames,
    frame: int,
    mark: dict[str, Any],
    reference_mm_per_px: float,
    roll_normalise: bool,
) -> tuple[np.ndarray, dict[str, np.ndarray]] | None:
    """One (shot, frame) mask resampled onto the common template grid.

    Returns the occupancy contribution in [0, 1] and the marks expressed in
    template coordinates, or None if the frame has no head mask.
    """
    mask = shot.masks.get(frame)
    if mask is None:
        return None
    heel = np.asarray(mark["marks"]["heel"], dtype=float)
    toe = np.asarray(mark["marks"]["toe"], dtype=float)
    topline = np.asarray(mark["marks"]["topline"], dtype=float)
    anchor = np.array([0.5 * (heel[0] + toe[0]), topline[1]])

    # Shot pixels per template pixel: the reference grid is `reference_mm_per_px
    # / UPSAMPLE` mm wide at 1581 mm, and this frame's pixels are
    # `mm_per_px(frame)` mm wide at the head's own range.
    step = reference_mm_per_px / (UPSAMPLE * shot.mm_per_px(frame))
    angle = math.atan2(toe[1] - heel[1], toe[0] - heel[0]) if roll_normalise else 0.0
    cosine, sine = math.cos(angle), math.sin(angle)

    grid_y, grid_x = np.mgrid[0 : TEMPLATE_SHAPE[0], 0 : TEMPLATE_SHAPE[1]].astype(np.float32)
    local_x = (grid_x - TEMPLATE_ORIGIN[0]) * step
    local_y = (grid_y - TEMPLATE_ORIGIN[1]) * step
    map_x = (anchor[0] + cosine * local_x - sine * local_y).astype(np.float32)
    map_y = (anchor[1] + sine * local_x + cosine * local_y).astype(np.float32)
    warped = cv2.remap(mask.astype(np.float32), map_x, map_y, cv2.INTER_LINEAR, borderValue=0.0)

    # The crop, applied in template coordinates so it travels with the rotation.
    half_span = 0.5 * float(np.hypot(*(toe - heel))) + CROP_MARGIN_PX
    depth_px = CROP_DEPTH_MM / shot.mm_per_px(frame)
    keep = (
        (np.abs(grid_x - TEMPLATE_ORIGIN[0]) <= half_span / step)
        & (grid_y - TEMPLATE_ORIGIN[1] >= 0.0)
        & (grid_y - TEMPLATE_ORIGIN[1] <= depth_px / step)
    )
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


def reference_scale(shots: dict[str, ShotFrames], club: str = "9-iron") -> float:
    """The common plate scale: the median over the named club's shots."""
    scales = [s.plate_mm_per_px for s in shots.values() if s.club == club]
    if not scales:
        scales = [s.plate_mm_per_px for s in shots.values()]
    return float(np.median(scales))


def build_template(
    shots: dict[str, ShotFrames],
    marks: dict[tuple[str, int], dict[str, Any]],
    club: str,
    reference_mm_per_px: float,
    *,
    roll_normalise: bool,
    exclude: str | None = None,
) -> Template | None:
    """Mean occupancy of every marked mask for one club, thresholded at 0.5."""
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
            sample = sample_to_template(shot, frame, mark, reference_mm_per_px, roll_normalise)
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
    outline = occupancy >= OCCUPANCY_THRESHOLD
    ious: list[tuple[str, int, float]] = []
    for (shot_name, frame), warped in zip(keys, stack, strict=True):
        binary = warped >= 0.5
        union = int((binary | outline).sum())
        ious.append(
            (shot_name, frame, float((binary & outline).sum()) / union if union else math.nan)
        )
    return Template(
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
    )


def render_template(template: Template, path: Path) -> None:
    """Occupancy map on the left, thresholded outline on the right."""
    heat = cv2.applyColorMap(
        (np.clip(template.occupancy, 0.0, 1.0) * 255).astype(np.uint8), cv2.COLORMAP_VIRIDIS
    )
    outline = cv2.cvtColor((template.outline * 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)
    contours, _ = cv2.findContours(
        template.outline.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE
    )
    cv2.drawContours(heat, contours, -1, (0, 165, 255), 1)
    for panel in (heat, outline):
        for name, colour in (
            ("heel", (255, 0, 255)),
            ("toe", (0, 255, 255)),
            ("topline", (255, 128, 0)),
            ("midpoint", (0, 0, 255)),
        ):
            point = template.landmarks[name]
            cv2.drawMarker(
                panel,
                (int(round(point[0])), int(round(point[1]))),
                colour,
                cv2.MARKER_TILTED_CROSS,
                7,
                1,
            )
    sheet = np.hstack([heat, np.full((heat.shape[0], 4, 3), 60, np.uint8), outline])
    sheet = cv2.resize(sheet, None, fx=3, fy=3, interpolation=cv2.INTER_NEAREST)
    width_mm, height_mm = template.extent_mm()
    label = (
        f"{template.club}  n={template.n_samples}  "
        f"{width_mm:.0f} x {height_mm:.0f} mm  "
        f"{'roll-normalised' if template.roll_normalised else 'brief (translate+scale)'}"
    )
    banner = np.zeros((22, sheet.shape[1], 3), np.uint8)
    cv2.putText(banner, label, (4, 15), cv2.FONT_HERSHEY_PLAIN, 1.0, (255, 255, 255), 1)
    cv2.imwrite(str(path), np.vstack([banner, sheet]))


def load_session(
    session: Path, marks: dict[tuple[str, int], dict[str, Any]], exclude: set[int]
) -> dict[str, ShotFrames]:
    """Every marked shot in the export, loaded and segmented."""
    import csv  # pylint: disable=import-outside-toplevel

    with (session / "shots.csv").open(newline="", encoding="utf-8") as handle:
        rows = {int(row["shot_number"]): row for row in csv.DictReader(handle)}
    wanted = sorted({shot for shot, _ in marks})
    shots: dict[str, ShotFrames] = {}
    for shot in wanted:
        number = int(shot.split("_", 2)[1])
        if number in exclude:
            continue
        shots[shot] = load_shot(session, shot, rows[number])
    return shots


def session_path(value: str | None) -> Path:
    """The filtered session export, from the flag or the environment."""
    supplied = value or os.environ.get("OPENFLIGHT_SESSION")
    if not supplied:
        raise SystemExit("provide --session or set OPENFLIGHT_SESSION")
    path = Path(supplied).expanduser().resolve()
    if not (path / "shots.csv").is_file() or not (path / "shots").is_dir():
        raise SystemExit(f"session export is missing shots.csv or shots/: {path}")
    return path


def main() -> int:
    """Build, render and summarise the per-club empirical outline."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", default=None)
    parser.add_argument("--marks", default=str(DEFAULT_MARKS))
    parser.add_argument("--out", default=str(Path(__file__).resolve().parent / "results"))
    parser.add_argument("--exclude-shot", type=int, action="append", default=[1])
    args = parser.parse_args()

    session = session_path(args.session)
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    marks = load_marks(Path(args.marks).expanduser())
    shots = load_session(session, marks, set(args.exclude_shot))
    reference = reference_scale(shots)
    print(f"reference plate scale (9-iron median) {reference:.4f} mm/px")

    report: dict[str, Any] = {
        "caveat": (
            "CONSISTENCY ONLY. No ground truth exists. The reference is one "
            "annotator's hand marks on the same pixels, and those marks also "
            "define the template's crop and origin."
        ),
        "session": str(session),
        "reference_mm_per_px": reference,
        "upsample": UPSAMPLE,
        "shots": {
            s.shot: {
                "club": s.club,
                "plate_mm_per_px": s.plate_mm_per_px,
                "ball": [s.ball.x, s.ball.y, s.ball.diameter_px],
                "contact_frame": s.contact,
                "range_rate_ms": s.range_rate_ms,
                "mask_frames": sorted(s.masks),
                "range_mm": {str(f): s.range_mm(f) for f in ALIGN_FRAMES},
            }
            for s in sorted(shots.values(), key=lambda s: s.number)
        },
        "templates": {},
    }

    for roll_normalise in (False, True):
        tag = "roll_normalised" if roll_normalise else "brief"
        report["templates"][tag] = {}
        for club in sorted({s.club for s in shots.values()}):
            template = build_template(shots, marks, club, reference, roll_normalise=roll_normalise)
            if template is None:
                continue
            width_mm, height_mm = template.extent_mm()
            ious = np.asarray([iou for _, _, iou in template.sample_iou], dtype=float)
            report["templates"][tag][club] = {
                "n_samples": template.n_samples,
                "width_mm": width_mm,
                "height_mm": height_mm,
                "width_px_at_ball_range": width_mm / reference,
                "height_px_at_ball_range": height_mm / reference,
                "mean_sample_iou": float(np.nanmean(ious)),
                "median_sample_iou": float(np.nanmedian(ious)),
                "min_sample_iou": float(np.nanmin(ious)),
                "worst_samples": [
                    {"shot": s, "frame": f, "iou": v}
                    for s, f, v in sorted(template.sample_iou, key=lambda r: r[2])[:5]
                ],
                "occupancy_fraction_ambiguous": float(
                    ((template.occupancy > 0.15) & (template.occupancy < 0.85)).sum()
                    / max(1, int((template.occupancy > 0.15).sum()))
                ),
                "landmarks": {k: v.tolist() for k, v in template.landmarks.items()},
            }
            render_template(template, out / f"template_{tag}_{club}.png")
            np.savez_compressed(
                out / f"template_{tag}_{club}.npz",
                outline=template.outline,
                occupancy=template.occupancy,
                **{f"landmark_{k}": v for k, v in template.landmarks.items()},
                mm_per_px=np.asarray(reference),
            )
            print(
                f"{tag:16s} {club:7s} n={template.n_samples:2d} "
                f"{width_mm:6.1f} x {height_mm:5.1f} mm  "
                f"mean sample IoU {np.nanmean(ious):.3f}  "
                f"median {np.nanmedian(ious):.3f}  min {np.nanmin(ious):.3f}"
            )

    (out / "template_summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\n{report['caveat']}")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Render what the support-plane gate is actually looking at, one PNG per shot.

DIAGNOSTIC ONLY. Nothing here feeds the pipeline and nothing here is a
measurement: it draws the ball, the plane the ball rests on, the aligned
outline and the topline the extractor read at the ball's column, so a human can
see whether that topline sits on the club, on the ball's highlight, or on the
turf shadow.

Run it against the 2026-08-25 export (`tests/_session_export` finds it):

    uv run python scripts/analysis/diagnostics/render_support_plane.py --out DIR

Every shot gets the contact frame with the full overlay, and the frames either
side of contact with the same overlay minus the template, so the mask's top
edge at the ball's column can be judged without the outline drawn over it.
"""

# `_session_export` is reached through the sys.path insert below, and PIL's
# constants are C-level attributes: both are astroid false positives, the same
# situation `head_outline` disables for cv2.
# pylint: disable=import-error,no-member

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "tests"))

import _session_export as session  # noqa: E402  pylint: disable=wrong-import-position

from openflight.camera.clubpose import (  # noqa: E402  pylint: disable=wrong-import-position
    head_outline as outline,
    impact_zone as zone,
)
from openflight.rig_geometry import (  # noqa: E402  pylint: disable=wrong-import-position
    RigGeometry,
    solve_setup,
)

SCALE = 4
LABEL_HEIGHT = 96
BALL_COLOUR = (80, 200, 255)
SUPPORT_COLOUR = (255, 90, 90)
CENTRE_COLOUR = (255, 170, 60)
OUTLINE_COLOUR = (110, 255, 140)
TOPLINE_COLOUR = (255, 60, 255)
SILHOUETTE_COLOUR = (255, 255, 90)


def _canvas(frame: np.ndarray) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.fromarray(np.asarray(frame, dtype=np.uint8), mode="L").convert("RGB")
    image = image.resize((image.width * SCALE, image.height * SCALE), Image.NEAREST)
    canvas = Image.new("RGB", (image.width, image.height + LABEL_HEIGHT), (12, 12, 16))
    canvas.paste(image, (0, 0))
    return canvas, ImageDraw.Draw(canvas)


def _cross(draw: ImageDraw.ImageDraw, x: float, y: float, colour, arm: int = 7) -> None:
    px, py = x * SCALE, y * SCALE
    draw.line((px - arm, py, px + arm, py), fill=colour, width=2)
    draw.line((px, py - arm, px, py + arm), fill=colour, width=2)


def _draw_ball(draw: ImageDraw.ImageDraw, ball, width: int) -> tuple[float, float]:
    radius = float(ball.diameter_px) / 2.0
    draw.ellipse(
        (
            (ball.x - radius) * SCALE,
            (ball.y - radius) * SCALE,
            (ball.x + radius) * SCALE,
            (ball.y + radius) * SCALE,
        ),
        outline=BALL_COLOUR,
        width=2,
    )
    support_row = float(ball.y) + radius
    draw.line((0, support_row * SCALE, width, support_row * SCALE), fill=SUPPORT_COLOUR, width=2)
    draw.line((0, float(ball.y) * SCALE, width, float(ball.y) * SCALE), fill=CENTRE_COLOUR, width=1)
    draw.line(
        (float(ball.x) * SCALE, 0, float(ball.x) * SCALE, LABEL_HEIGHT), fill=(70, 70, 90), width=1
    )
    return support_row, radius


def _mask_bottom_at(mask, column: float) -> float | None:
    """The mask's lowest row at one image column, or None where it has none."""
    if mask is None:
        return None
    index = int(round(column))
    if index < 0 or index >= mask.shape[1]:
        return None
    rows = np.flatnonzero(mask[:, index])
    return None if rows.size == 0 else float(rows[-1])


def _mask_top_at(mask, column: float) -> float | None:
    if mask is None:
        return None
    index = int(round(column))
    if index < 0 or index >= mask.shape[1]:
        return None
    rows = np.flatnonzero(mask[:, index])
    return None if rows.size == 0 else float(rows[0])


def _draw_template(draw: ImageDraw.ImageDraw, template, alignment) -> None:
    matrix = outline._matrix(alignment)  # pylint: disable=protected-access
    rows, cols = np.nonzero(template.outline)
    points = matrix[:, :2] @ np.stack([cols.astype(float), rows.astype(float)]) + matrix[:, 2:3]
    for x, y in zip(points[0][::7], points[1][::7]):
        draw.point((x * SCALE, y * SCALE), fill=OUTLINE_COLOUR)
    edge_x, edge_y = outline.placed_top_edge(template, alignment)
    if edge_x.size > 1:
        draw.line(
            [(x * SCALE, y * SCALE) for x, y in zip(edge_x, edge_y)],
            fill=OUTLINE_COLOUR,
            width=2,
        )


def _label(draw: ImageDraw.ImageDraw, top: int, lines) -> None:
    for index, line in enumerate(lines):
        draw.text((8, top + 6 + index * 14), line, fill=(230, 230, 240))


def _fmt(value, digits: int = 1) -> str:
    return "None" if value is None else f"{value:.{digits}f}"


def render(
    swing,
    template,
    report,
    result,
    alignments,
    out_dir: Path,
) -> list[Path]:
    """Every PNG for one shot: the contact frame, then the frames either side."""
    written: list[Path] = []
    contact = int(round(swing.contact_frame))
    plate = swing.plate_mm_per_px
    for frame, with_template in ((contact - 1, False), (contact, True), (contact + 1, False)):
        if frame < 0 or frame >= swing.frames.shape[0]:
            continue
        canvas, draw = _canvas(swing.frames[frame])
        support_row, _ = _draw_ball(draw, swing.ball, canvas.width)
        alignment = alignments.get(frame)
        mask = report.masks.get(frame)
        top_edge_y = None
        if with_template and alignment is not None:
            _draw_template(draw, template, alignment)
            edge_x, edge_y = outline.placed_top_edge(template, alignment)
            if edge_x.size:
                top_edge_y = outline.top_edge_at(edge_x, edge_y, float(swing.ball.x))
                if math.isfinite(top_edge_y):
                    _cross(draw, float(swing.ball.x), top_edge_y, TOPLINE_COLOUR)
        mask_top = _mask_top_at(mask, swing.ball.x)
        mask_bottom = _mask_bottom_at(mask, swing.ball.x)
        if mask_top is not None:
            _cross(draw, float(swing.ball.x), mask_top, SILHOUETTE_COLOUR, arm=5)
        if mask_bottom is not None:
            _cross(draw, float(swing.ball.x), mask_bottom, SILHOUETTE_COLOUR, arm=5)
        lines = [
            f"{swing.name}  frame {frame}"
            + (f" (contact {swing.contact_frame:.2f})" if with_template else ""),
            f"mm_per_px {plate:.3f}   mat_gate {result.mat_gate_status}",
            (
                f"topline_above_support {_fmt(result.topline_above_support_mm)} mm   "
                f"sole_above_support {_fmt(result.sole_above_support_mm)} mm   "
                f"face_height {_fmt(result.face_height_mm)} mm"
            ),
            (
                f"template topline row {_fmt(top_edge_y, 2)}   mask top row "
                f"{_fmt(mask_top, 2)}   mask bottom row {_fmt(mask_bottom, 2)}   "
                f"support row {support_row:.2f}   ball centre row {swing.ball.y:.2f}"
            ),
            "cyan ball | red support plane | orange ball centre | green outline | "
            "magenta topline@ball | yellow mask top/bottom@ball",
        ]
        _label(draw, canvas.height - LABEL_HEIGHT, lines)
        suffix = "" if with_template else f"_f{frame}"
        path = out_dir / f"{swing.name}{suffix}.png"
        canvas.save(path)
        written.append(path)
    return written


def main() -> int:
    """Render the requested shots and write a summary next to them."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--shots", nargs="*", default=None, help="shot numbers, e.g. 3 16")
    args = parser.parse_args()

    names = session.shot_names()
    if not names:
        print("no session export found; nothing to render")
        return 1
    wanted = None
    if args.shots:
        wanted = {int(value) for value in args.shots}

    swings = {name: session.swing(name) for name in names}
    marks = session.pass_one_marks()
    reports = {name: outline.head_masks(swing) for name, swing in swings.items()}
    nines = [s.plate_mm_per_px for s in swings.values() if s.club == "9-iron"]
    reference = float(np.median(nines or [s.plate_mm_per_px for s in swings.values()]))
    rig = RigGeometry.test_rig()

    args.out.mkdir(parents=True, exist_ok=True)
    summary: list[str] = []
    written: list[Path] = []
    for name, swing in sorted(swings.items()):
        number = int(name.split("_", 2)[1])
        if wanted is not None and number not in wanted:
            continue
        samples = [
            (other, frame, mask, marks[(other.name, frame)])
            for other_name, other in swings.items()
            if other_name != name and other.club == swing.club
            for frame, mask in reports[other_name].masks.items()
            if (other_name, frame) in marks
        ]
        template = outline.build_template(
            samples, swing.club, reference, source="labels", excluded=name
        )
        if template is None:
            summary.append(f"{name}: no leave-one-out template")
            continue
        accepted, _ = outline.align_swing(swing, template, reports[name])
        result = zone.extract_impact_zone(
            swing,
            template,
            report=reports[name],
            setup=solve_setup(swing.ball, rig),
            rig=rig,
        )
        written.extend(render(swing, template, reports[name], result, accepted, args.out))
        summary.append(
            f"{name}\tstatus={result.status}\tgate={result.mat_gate_status}\t"
            f"topline_above_support_mm={_fmt(result.topline_above_support_mm, 2)}\t"
            f"sole_above_support_mm={_fmt(result.sole_above_support_mm, 2)}\t"
            f"contact_above_support_mm={_fmt(result.contact_above_support_mm, 2)}\t"
            f"face_height_mm={_fmt(result.face_height_mm, 2)}\t"
            f"face_height_template_mm={_fmt(result.face_height_template_mm, 2)}\t"
            f"face_height_assumed_mm={_fmt(result.face_height_assumed_mm, 2)}\t"
            f"face_height_source={result.face_height_source}\t"
            f"mm_per_px={swing.plate_mm_per_px:.4f}\t"
            f"detail={result.mat_gate_detail}"
        )
    (args.out / "summary.txt").write_text("\n".join(summary) + "\n", encoding="utf-8")
    for path in written:
        print(path)
    print(args.out / "summary.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

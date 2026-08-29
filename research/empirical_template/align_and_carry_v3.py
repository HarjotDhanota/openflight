"""Iteration 3: align the v3 outline, carry it, and read the impact zone.

NO GROUND TRUTH EXISTS. Pass-1 hand marks only (`load_marks_pass1`), which are
one human's reading of the same pixels and which also define the template's
crop, origin and landmark convention. Nothing here is scored against a launch
monitor, and the "impact zone" below is a CONSISTENCY reading, not a
measurement of where the ball actually struck the face.

What changes from `align_and_carry_v2.py`:

  * masks and template come from `build_template_v3`: the flying-ball veto runs
    on post-contact frames, the side crop sits at the marks (0 px margin), and
    `--heel-mode occupancy` lets the heel edge come from the occupancy fall-off
    in x the way the sole comes from the fall-off in y.
  * every scored quantity is carried to contact by the quadratic over the
    accepted frames of f66-f72, with the f71/f72 interpolation as the check --
    not just the midpoint. That includes the outline's own heel and toe extents
    and the top edge AT THE BALL'S COLUMN, which is what an impact-zone reading
    needs and which no earlier iteration carried.
  * the roll ramp is reported per shot: a straight-line fit of the searched roll
    against frame over f66-f72. It is an OBSERVABLE WITH NO TRUTH -- nothing in
    this session measures clubface rotation independently.
  * if the pre-registered gate passes, the impact zone is read out per shot and
    put beside the hand marks' own zone, under one convention: face centre 8 mm
    toe-ward of the outline midpoint along the heel-to-toe direction, and ball
    depth below the topline measured at the ball's own column.

    uv run python research/empirical_template/align_and_carry_v3.py --tag v3
"""

# pylint: disable=wrong-import-position,no-member,unsubscriptable-object

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from align_and_carry import (  # noqa: E402
    IOU_GATE,
    MIN_CARRY_FRAMES,
    PASS_AVAILABILITY,
    PASS_MEDIAN_PX,
    PASS_P90_PX,
    TRACK_GATE_PX,
    Alignment,
    _line_fit,
    _percentiles,
    _write_csv,
    align_frame,
)
from align_and_carry_v2 import (  # noqa: E402
    _mark_point,
    placed_extremes,
    placed_top_edge,
    refine_on_ridge,
    roll_lock_render,
    sheet,
    top_edge_at,
)
from build_template import (  # noqa: E402
    ALIGN_FRAMES,
    DEFAULT_MARKS,
    MARK_FRAMES,
    reference_scale,
    session_path,
)
from build_template_v2 import CROP_TOP_MARGIN_PX, load_marks_pass1  # noqa: E402
from build_template_v3 import (  # noqa: E402
    HEEL_MODES,
    SIDE_MARGIN_PX,
    build_template_v3,
    load_session_v3,
)

MIN_QUAD_FRAMES = 4
# The impact-zone convention, fixed before any number was read: the face centre
# sits this far toe-ward of the outline midpoint along the heel-to-toe line.
FACE_CENTRE_TOEWARD_MM = 8.0
CARRY_FRAMES = ALIGN_FRAMES

# Everything carried to contact. Each is a scalar measured per accepted frame.
OBSERVABLES = (
    "mid_x",
    "mid_y",
    "heel_x",
    "heel_y",
    "toe_x",
    "toe_y",
    "topline_x",
    "topline_y",
    "heel_extent_x",
    "toe_extent_x",
    "topline_at_ball_x",
)


def observables(shot, template, alignment: Alignment) -> dict[str, float]:
    """Every scalar this iteration carries, read off one aligned frame."""
    extremes = placed_extremes(template, alignment)
    edge_x, edge_y = placed_top_edge(template, alignment)
    values = {
        "mid_x": float(alignment.landmarks["midpoint"][0]),
        "mid_y": float(alignment.landmarks["midpoint"][1]),
        "heel_x": float(alignment.landmarks["heel"][0]),
        "heel_y": float(alignment.landmarks["heel"][1]),
        "toe_x": float(alignment.landmarks["toe"][0]),
        "toe_y": float(alignment.landmarks["toe"][1]),
        "topline_x": float(alignment.landmarks["topline"][0]),
        "topline_y": float(alignment.landmarks["topline"][1]),
        "heel_extent_x": float(extremes["heel_extent"][0]),
        "toe_extent_x": float(extremes["toe_extent"][0]),
        "topline_at_ball_x": top_edge_at(edge_x, edge_y, float(shot.ball.x)),
    }
    return values


def _carry(frames: np.ndarray, values: np.ndarray, contact: float, degree: int) -> float:
    fit = np.polyfit(frames, values, degree)
    return float(np.polyval(fit, contact))


def carry_all(
    per_frame: dict[int, dict[str, float]], accepted: set[int], contact: float
) -> dict[str, Any]:
    """Quadratic carry of every observable, with the f71/f72 pair as the check."""
    usable = sorted(f for f in CARRY_FRAMES if f in accepted)
    out: dict[str, Any] = {
        "carry_frames": " ".join(str(f) for f in usable),
        "n_carry": len(usable),
    }
    for name in OBSERVABLES:
        series = [(f, per_frame[f][name]) for f in usable if math.isfinite(per_frame[f][name])]
        if len(series) >= MIN_QUAD_FRAMES:
            frames = np.asarray([f for f, _ in series], dtype=float)
            values = np.asarray([v for _, v in series], dtype=float)
            out[f"quad_{name}"] = _carry(frames, values, contact, 2)
        if 71 in accepted and 72 in accepted:
            first, second = per_frame[71][name], per_frame[72][name]
            if math.isfinite(first) and math.isfinite(second):
                out[f"pair_{name}"] = first + (contact - 71.0) * (second - first)
        if f"quad_{name}" in out and f"pair_{name}" in out:
            out[f"disagree_{name}"] = out[f"quad_{name}"] - out[f"pair_{name}"]
    return out


def impact_zone(
    heel: np.ndarray,
    toe: np.ndarray,
    topline_at_ball: float,
    ball: np.ndarray,
    mm_per_px: float,
) -> dict[str, float]:
    """Ball position relative to the face, under one fixed convention.

    Face centre is `FACE_CENTRE_TOEWARD_MM` toe-ward of the heel-toe midpoint
    along the heel-to-toe direction; the vertical reading is how far the ball
    centre sits below the topline AT THE BALL'S OWN COLUMN.
    """
    span = toe - heel
    length = float(np.hypot(*span))
    if length <= 0:
        return {}
    unit = span / length
    midpoint = 0.5 * (heel + toe)
    centre = midpoint + unit * (FACE_CENTRE_TOEWARD_MM / mm_per_px)
    return {
        "face_width_mm": length * mm_per_px,
        "impact_toeward_mm": float(np.dot(ball - centre, unit)) * mm_per_px,
        "impact_below_topline_mm": (float(ball[1]) - topline_at_ball) * mm_per_px,
    }


def marks_zone(
    marks: dict, shot_name: str, contact: float, ball: np.ndarray, mm_per_px: float
) -> dict[str, float]:
    """The same reading from the hand marks alone, interpolated f71 -> f72.

    The topline mark sits a median +11 px toe-ward of the ball because the
    ball's cap hides the crown above it, so the topline at the ball's column is
    the heel-to-topline line extrapolated back -- the `analyse_marks` convention.
    """
    if not all((shot_name, f) in marks for f in MARK_FRAMES):
        return {}
    weight = contact - 71.0
    points = {}
    for name in ("heel", "toe", "topline"):
        first = _mark_point(marks[(shot_name, 71)], name)
        second = _mark_point(marks[(shot_name, 72)], name)
        points[name] = first + weight * (second - first)
    heel, toe, topline = points["heel"], points["toe"], points["topline"]
    run = topline[0] - heel[0]
    if abs(run) < 1e-6:
        return {}
    slope = (topline[1] - heel[1]) / run
    at_ball = topline[1] + slope * (float(ball[0]) - topline[0])
    zone = impact_zone(heel, toe, at_ball, ball, mm_per_px)
    zone["topline_at_ball_y"] = at_ball
    return zone


def main() -> int:  # pylint: disable=too-many-locals,too-many-statements,too-many-branches
    """Align, carry every observable, score, and read the impact zone."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", default=None)
    parser.add_argument("--marks", default=str(DEFAULT_MARKS))
    parser.add_argument("--out", default=str(Path(__file__).resolve().parent / "results"))
    parser.add_argument("--exclude-shot", type=int, action="append", default=[1])
    parser.add_argument("--shot", type=int, action="append", default=None)
    parser.add_argument("--heel-mode", choices=HEEL_MODES, default="crop")
    parser.add_argument("--side-margin", type=float, default=SIDE_MARGIN_PX)
    parser.add_argument("--top-margin", type=float, default=CROP_TOP_MARGIN_PX)
    parser.add_argument("--ridge", dest="ridge", action="store_true", default=True)
    parser.add_argument("--no-ridge", dest="ridge", action="store_false")
    parser.add_argument("--no-ball-veto", dest="ball_veto", action="store_false", default=True)
    parser.add_argument("--roll-normalise", action="store_true")
    parser.add_argument("--no-sheets", action="store_true")
    parser.add_argument("--tag", default="v3")
    parser.add_argument("--roll-lock", type=int, action="append", default=[4, 15, 21])
    parser.add_argument("--roll-lock-frame", type=int, default=72)
    args = parser.parse_args()

    session = session_path(args.session)
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    sheets = out / f"sheets_{args.tag}"
    if not args.no_sheets:
        sheets.mkdir(parents=True, exist_ok=True)
    marks = load_marks_pass1(Path(args.marks).expanduser())
    shots = load_session_v3(session, marks, set(args.exclude_shot), ball_veto=args.ball_veto)
    reference = reference_scale(shots)

    frame_rows: list[dict[str, Any]] = []
    shot_rows: list[dict[str, Any]] = []
    signed: dict[str, list[tuple[str, str, float]]] = {
        key: []
        for key in (
            "raw_heel_x",
            "raw_toe_x",
            "raw_topline_y",
            "def_heel_x",
            "def_toe_x",
            "def_topline_y",
        )
    }
    roll_fits: list[dict[str, Any]] = []

    for shot in sorted(shots.values(), key=lambda s: s.number):
        if args.shot and shot.number not in set(args.shot):
            continue
        template = build_template_v3(
            shots,
            marks,
            shot.club,
            reference,
            roll_normalise=args.roll_normalise,
            exclude=shot.shot,
            heel_mode=args.heel_mode,
            side_margin_px=args.side_margin,
            top_margin_px=args.top_margin,
        )
        if template is None:
            continue
        alignments: dict[int, Alignment] = {}
        ridge_dy: dict[int, float] = {}
        for frame in ALIGN_FRAMES:
            alignment = align_frame(shot, template, frame)
            if alignment is None:
                continue
            if args.ridge:
                alignment, info = refine_on_ridge(shot, template, alignment, frame)
                ridge_dy[frame] = info.get("ridge_dy_px", 0.0)
            alignments[frame] = alignment

        accepted = {
            f: a
            for f, a in alignments.items()
            if a.iou >= IOU_GATE and not a.on_roll_boundary and not a.on_radius_boundary
        }
        track = sorted(f for f in ALIGN_FRAMES if f <= 71 and f in accepted)
        if len(track) >= MIN_CARRY_FRAMES:
            frames = np.asarray(track, dtype=float)
            points = np.stack([accepted[f].landmarks["midpoint"] for f in track])
            slope_x, intercept_x = _line_fit(frames, points[:, 0])
            slope_y, intercept_y = _line_fit(frames, points[:, 1])
            for index, frame in enumerate(track):
                residual = math.hypot(
                    points[index, 0] - (slope_x * frame + intercept_x),
                    points[index, 1] - (slope_y * frame + intercept_y),
                )
                if residual > TRACK_GATE_PX:
                    del accepted[frame]
        accepted_frames = set(accepted)

        per_frame = {f: observables(shot, template, a) for f, a in alignments.items()}
        carried = carry_all(per_frame, accepted_frames, shot.contact)
        mm_per_px = shot.plate_mm_per_px
        ball = np.array([shot.ball.x, shot.ball.y])

        # Roll ramp: an observable with no truth.
        roll_frames = sorted(accepted_frames)
        roll_row: dict[str, Any] = {"roll_n": len(roll_frames)}
        if len(roll_frames) >= 4:
            xs = np.asarray(roll_frames, dtype=float)
            ys = np.asarray([alignments[f].roll_deg for f in roll_frames], dtype=float)
            slope, intercept = _line_fit(xs, ys)
            residual = ys - (slope * xs + intercept)
            roll_row.update(
                {
                    "roll_slope_deg_per_frame": slope,
                    "roll_slope_deg_per_ms": slope * shot.fps / 1000.0,
                    "roll_at_f66": float(slope * 66 + intercept),
                    "roll_at_f72": float(slope * 72 + intercept),
                    "roll_fit_rms_deg": float(np.sqrt(np.mean(residual**2))),
                }
            )
            roll_fits.append({"shot": shot.shot, **roll_row})

        row: dict[str, Any] = {
            "shot": shot.shot,
            "shot_number": shot.number,
            "club": shot.club,
            "contact_frame": shot.contact,
            "plate_mm_per_px": mm_per_px,
            "ball_x": shot.ball.x,
            "ball_y": shot.ball.y,
            "heel_mode": template.heel_mode,
            "template_n_samples": template.n_samples,
            "n_aligned": len(alignments),
            "n_accepted": len(accepted_frames),
            "accepted_frames": " ".join(str(f) for f in sorted(accepted_frames)),
            "median_iou": float(np.median([a.iou for a in alignments.values()]))
            if alignments
            else math.nan,
            **roll_row,
            **carried,
        }

        for frame, alignment in sorted(alignments.items()):
            mark = marks.get((shot.shot, frame))
            values = per_frame[frame]
            entry: dict[str, Any] = {
                "shot": shot.shot,
                "club": shot.club,
                "frame": frame,
                "iou": alignment.iou,
                "roll_deg": alignment.roll_deg,
                "ridge_dy_px": ridge_dy.get(frame, 0.0),
                "accepted": frame in accepted_frames,
                "mask_area_px": alignment.mask_area,
                **values,
            }
            if mark is not None:
                for name in ("heel", "toe", "topline"):
                    truth = _mark_point(mark, name)
                    entry[f"{name}_mark_x"] = float(truth[0])
                    entry[f"{name}_mark_y"] = float(truth[1])
                edge_x, edge_y = placed_top_edge(template, alignment)
                entry["topline_at_mark_x"] = top_edge_at(
                    edge_x, edge_y, float(_mark_point(mark, "topline")[0])
                )
                if frame in accepted_frames:
                    tag = (shot.shot, shot.club)
                    signed["raw_heel_x"].append((*tag, values["heel_x"] - entry["heel_mark_x"]))
                    signed["raw_toe_x"].append((*tag, values["toe_x"] - entry["toe_mark_x"]))
                    signed["raw_topline_y"].append(
                        (*tag, values["topline_y"] - entry["topline_mark_y"])
                    )
                    signed["def_heel_x"].append(
                        (*tag, values["heel_extent_x"] - entry["heel_mark_x"])
                    )
                    signed["def_toe_x"].append((*tag, values["toe_extent_x"] - entry["toe_mark_x"]))
                    if math.isfinite(entry["topline_at_mark_x"]):
                        signed["def_topline_y"].append(
                            (*tag, entry["topline_at_mark_x"] - entry["topline_mark_y"])
                        )
            frame_rows.append(entry)

        # Impact zone, from the carried outline and from the marks.
        for prefix in ("quad", "pair"):
            keys = [f"{prefix}_{n}" for n in ("heel_x", "heel_y", "toe_x", "toe_y")]
            if not all(k in carried for k in keys) or f"{prefix}_topline_at_ball_x" not in carried:
                continue
            zone = impact_zone(
                np.array([carried[f"{prefix}_heel_x"], carried[f"{prefix}_heel_y"]]),
                np.array([carried[f"{prefix}_toe_x"], carried[f"{prefix}_toe_y"]]),
                carried[f"{prefix}_topline_at_ball_x"],
                ball,
                mm_per_px,
            )
            for key, value in zone.items():
                row[f"{prefix}_{key}"] = value
        reference_zone = marks_zone(marks, shot.shot, shot.contact, ball, mm_per_px)
        for key, value in reference_zone.items():
            row[f"mark_{key}"] = value
        for key in ("impact_toeward_mm", "impact_below_topline_mm", "face_width_mm"):
            if f"quad_{key}" in row and f"mark_{key}" in row:
                row[f"quad_minus_mark_{key}"] = row[f"quad_{key}"] - row[f"mark_{key}"]
        shot_rows.append(row)

        if not args.no_sheets:
            sheet(shot, template, alignments, accepted_frames, marks, sheets / f"{shot.shot}.png")
        if shot.number in set(args.roll_lock) and args.roll_lock_frame in alignments:
            roll_lock_render(
                shot,
                template,
                alignments[args.roll_lock_frame],
                args.roll_lock_frame,
                out / f"rolllock_{args.tag}_{shot.shot}_f{args.roll_lock_frame}.png",
            )
        print(
            f"{shot.shot:18s} aligned={len(alignments)} accepted={len(accepted_frames)} "
            f"medIoU={row['median_iou']:.3f} roll {row.get('roll_at_f66', float('nan')):+5.1f}"
            f"->{row.get('roll_at_f72', float('nan')):+5.1f} deg  acc={row['accepted_frames']}"
        )

    _write_csv(out / f"align_{args.tag}_per_frame.csv", frame_rows)
    _write_csv(out / f"align_{args.tag}_per_shot.csv", shot_rows)

    def stats(rows: list[tuple[str, str, float]]) -> dict[str, Any]:
        return {
            **_percentiles([abs(v) for *_, v in rows]),
            "bias": float(np.median([v for *_, v in rows])) if rows else math.nan,
        }

    scores = {key: stats(rows) for key, rows in signed.items()}
    availability = sum(
        1
        for r in shot_rows
        if {66, 67, 68, 69, 70, 71}.issubset({int(f) for f in r["accepted_frames"].split()})
    )

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
        return {"checks": checks, "result": "PASS" if passed else "FAIL"}

    roll_slopes = [r["roll_slope_deg_per_frame"] for r in roll_fits]
    roll_summary = {
        "n_shots": len(roll_fits),
        "slope_deg_per_frame": _percentiles(roll_slopes),
        "slope_median_deg_per_frame": float(np.median(roll_slopes)) if roll_slopes else math.nan,
        "slope_iqr_deg_per_frame": (
            float(np.percentile(roll_slopes, 75) - np.percentile(roll_slopes, 25))
            if roll_slopes
            else math.nan
        ),
        "slope_median_deg_per_ms": float(np.median([r["roll_slope_deg_per_ms"] for r in roll_fits]))
        if roll_fits
        else math.nan,
        "roll_at_f66_median": float(np.median([r["roll_at_f66"] for r in roll_fits]))
        if roll_fits
        else math.nan,
        "roll_at_f72_median": float(np.median([r["roll_at_f72"] for r in roll_fits]))
        if roll_fits
        else math.nan,
        "fit_rms_deg": _percentiles([r["roll_fit_rms_deg"] for r in roll_fits]),
        "shots_with_positive_slope": int(sum(1 for s in roll_slopes if s > 0)),
        "note": (
            "OBSERVABLE WITH NO TRUTH. Nothing in this session measures clubface "
            "rotation independently; this is the roll the IoU search chose, "
            "fitted against frame."
        ),
    }

    zone_keys = ("impact_toeward_mm", "impact_below_topline_mm", "face_width_mm")
    zones = {
        key: {
            "quad": _percentiles([r[f"quad_{key}"] for r in shot_rows if f"quad_{key}" in r]),
            "mark": _percentiles([r[f"mark_{key}"] for r in shot_rows if f"mark_{key}" in r]),
            "quad_minus_mark_abs": _percentiles(
                [
                    abs(r[f"quad_minus_mark_{key}"])
                    for r in shot_rows
                    if f"quad_minus_mark_{key}" in r
                ]
            ),
            "quad_minus_mark_bias": float(
                np.median(
                    [
                        r[f"quad_minus_mark_{key}"]
                        for r in shot_rows
                        if f"quad_minus_mark_{key}" in r
                    ]
                    or [math.nan]
                )
            ),
        }
        for key in zone_keys
    }

    report = {
        "caveat": (
            "CONSISTENCY ONLY. No ground truth exists. Scored against PASS-1 "
            "hand marks. The impact zone is a consistency reading, not a "
            "measurement of where the ball struck."
        ),
        "session": str(session),
        "configuration": {
            "heel_mode": args.heel_mode,
            "side_margin_px": args.side_margin,
            "top_margin_px": args.top_margin,
            "ridge": args.ridge,
            "ball_veto": args.ball_veto,
            "marks_pass": 1,
            "face_centre_toeward_mm": FACE_CENTRE_TOEWARD_MM,
        },
        "counts": {
            "shots": len(shot_rows),
            "frames_aligned": len(frame_rows),
            "frames_accepted": int(sum(1 for r in frame_rows if r["accepted"])),
            "shots_with_all_f66_f71": availability,
            "marked_frames_scored": scores["raw_heel_x"].get("n", 0),
        },
        "iou": _percentiles([r["iou"] for r in frame_rows]),
        "scores_vs_marks_px": scores,
        "pre_registered_gate_raw_convention": gate("raw"),
        "pre_registered_gate_definition_matched": gate("def"),
        "roll_ramp": roll_summary,
        "impact_zone": zones,
    }
    (out / f"align_{args.tag}_summary.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )

    print(f"\n{report['caveat']}\n")
    print(
        f"config: heel={args.heel_mode} side-margin={args.side_margin} "
        f"ridge={args.ridge} ball-veto={args.ball_veto}"
    )
    print(
        f"frames aligned {report['counts']['frames_aligned']} "
        f"accepted {report['counts']['frames_accepted']}; availability {availability}"
    )
    for key, value in scores.items():
        if value.get("n"):
            print(
                f"  {key:16s} n={value['n']:2d} median {value['median']:5.2f}  "
                f"p90 {value['p90']:5.2f}  max {value['max']:5.2f}  bias {value['bias']:+5.2f}"
            )
    print(f"\ngate (raw):              {report['pre_registered_gate_raw_convention']['result']}")
    print(
        f"gate (definition-matched): {report['pre_registered_gate_definition_matched']['result']}"
    )
    print(
        f"\nroll ramp: median {roll_summary['slope_median_deg_per_frame']:+.2f} deg/frame "
        f"({roll_summary['slope_median_deg_per_ms']:+.2f} deg/ms), IQR "
        f"{roll_summary['slope_iqr_deg_per_frame']:.2f}, "
        f"f66 {roll_summary['roll_at_f66_median']:+.1f} -> f72 "
        f"{roll_summary['roll_at_f72_median']:+.1f} deg, fit rms median "
        f"{roll_summary['fit_rms_deg'].get('median', float('nan')):.1f} deg"
    )
    print("\nimpact zone at contact (mm)")
    for key, value in zones.items():
        if value["quad"].get("n"):
            print(
                f"  {key:26s} outline {value['quad']['median']:+6.1f}  "
                f"marks {value['mark']['median']:+6.1f}  "
                f"|diff| median {value['quad_minus_mark_abs']['median']:5.1f} "
                f"p90 {value['quad_minus_mark_abs']['p90']:5.1f} "
                f"bias {value['quad_minus_mark_bias']:+5.1f}"
            )
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

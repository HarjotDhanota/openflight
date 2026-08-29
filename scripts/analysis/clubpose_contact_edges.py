"""Replay the contact-edge spike on an exported OpenFlight session.

Run from the repository root:

    uv run python scripts/analysis/clubpose_contact_edges.py \
        --session /path/to/export --out /path/to/output
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from openflight.camera.club_motion import (  # noqa: E402
    BALL_DIAMETER_MM,
    ReferenceBall,
    detect_reference_ball,
)
from openflight.camera.clubpose.contact_edges import (  # noqa: E402
    find_contact_edges,
)

_BALL_TO_UNIT_M = 1.575
_SPEED_OF_SOUND_M_S = 343.0
_CROP_HALF_WIDTH = 40
_CROP_ABOVE_BALL = 24
_CROP_BELOW_BALL = 16
_SHEET_SCALE = 8
_SHEET_COLUMNS = 3


def _session_path(value: str | None, parser: argparse.ArgumentParser) -> Path:
    supplied = value or os.environ.get("OPENFLIGHT_SESSION")
    if not supplied:
        parser.error("provide --session or set OPENFLIGHT_SESSION")
    session = Path(supplied).expanduser().resolve()
    if not (session / "shots.csv").is_file() or not (session / "shots").is_dir():
        parser.error(f"session export is missing shots.csv or shots/: {session}")
    return session


def _shot_clubs(session: Path) -> dict[int, str]:
    with (session / "shots.csv").open(newline="", encoding="utf-8") as handle:
        return {
            int(row["shot_number"]): row["club"]
            for row in csv.DictReader(handle)
            if row.get("shot_number")
        }


def _shot_number(path: Path) -> int:
    return int(path.name.split("_", 2)[1])


def _mat_level(background: np.ndarray, ball: ReferenceBall) -> float:
    height, width = background.shape
    x0 = max(0, int(math.floor(ball.x - 32.0)))
    x1 = min(width, int(math.ceil(ball.x + 32.0)) + 1)
    y0 = max(0, int(math.floor(ball.y - 14.0)))
    y1 = min(height, int(math.ceil(ball.y + 3.0)) + 1)
    if x1 <= x0 or y1 <= y0:
        raise ValueError("ball-centred mat window is outside the frame")
    return float(np.median(background[y0:y1, x0:x1]))


def _contact_indices(metadata: dict[str, Any]) -> tuple[float, int, int]:
    trigger = int(metadata["pre_trigger_frames"])
    fps = float(metadata["delivered_fps"])
    contact = trigger - _BALL_TO_UNIT_M / _SPEED_OF_SOUND_M_S * fps
    return contact, math.floor(contact), math.ceil(contact)


def _load_shot(path: Path, club: str) -> dict[str, Any]:
    with (path / "camera_metadata.json").open(encoding="utf-8") as handle:
        metadata = json.load(handle)
    with np.load(path / "frames.npz") as capture:
        frames = capture["frames"][:, :, ::-1]
    ball = detect_reference_ball(frames)
    background = np.median(frames[8:56], axis=0)
    mat_level = _mat_level(background, ball)
    contact, first_index, second_index = _contact_indices(metadata)
    frame_results: dict[str, dict[str, Any]] = {}
    frame_images: dict[int, np.ndarray] = {}
    for frame_index in (first_index, second_index):
        result = find_contact_edges(frames[frame_index], ball, mat_level)
        frame_results[str(frame_index)] = asdict(result)
        frame_images[frame_index] = frames[frame_index]
    return {
        "shot_number": _shot_number(path),
        "shot": path.name,
        "club": club,
        "ball": asdict(ball),
        "plate_scale_mm_per_px": BALL_DIAMETER_MM / ball.diameter_px,
        "mat_level": mat_level,
        "contact_frame": contact,
        "frame_indices": [first_index, second_index],
        "frames": frame_results,
        "_images": frame_images,
    }


def _extract_crop(frame: np.ndarray, ball: dict[str, Any]) -> tuple[np.ndarray, int, int]:
    centre_x = int(round(float(ball["x"])))
    centre_y = int(round(float(ball["y"])))
    x0 = max(0, centre_x - _CROP_HALF_WIDTH)
    x1 = min(frame.shape[1], centre_x + _CROP_HALF_WIDTH)
    y0 = max(0, centre_y - _CROP_ABOVE_BALL)
    y1 = min(frame.shape[0], centre_y + _CROP_BELOW_BALL)
    crop = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_GRAY2BGR)
    return crop, x0, y0


def _draw_measurements(
    crop: np.ndarray,
    crop_x: int,
    crop_y: int,
    ball: dict[str, Any],
    result: dict[str, Any],
) -> np.ndarray:
    scaled = cv2.resize(
        crop,
        None,
        fx=_SHEET_SCALE,
        fy=_SHEET_SCALE,
        interpolation=cv2.INTER_NEAREST,
    )
    ball_x = float(ball["x"])
    slope = result["topline_slope"]
    row_at_ball = result["topline_row_at_ball"]
    if slope is not None and row_at_ball is not None:
        left_x = float(crop_x)
        right_x = float(crop_x + crop.shape[1] - 1)
        left_y = float(row_at_ball) + float(slope) * (left_x - ball_x)
        right_y = float(row_at_ball) + float(slope) * (right_x - ball_x)
        cv2.line(
            scaled,
            (0, int(round((left_y - crop_y) * _SHEET_SCALE))),
            (
                scaled.shape[1] - 1,
                int(round((right_y - crop_y) * _SHEET_SCALE)),
            ),
            (255, 255, 0),
            2,
            cv2.LINE_AA,
        )
    for field, colour in (("heel_x", (0, 0, 255)), ("toe_x", (0, 255, 255))):
        edge_x = result[field]
        if edge_x is None or row_at_ball is None or slope is None:
            continue
        edge_y = float(row_at_ball) + float(slope) * (float(edge_x) - ball_x)
        point = (
            int(round((float(edge_x) - crop_x) * _SHEET_SCALE)),
            int(round((edge_y - crop_y) * _SHEET_SCALE)),
        )
        cv2.drawMarker(scaled, point, colour, cv2.MARKER_CROSS, 28, 3, cv2.LINE_AA)
    return scaled


def _render_sheet(records: list[dict[str, Any]], frame_index: int, output_path: Path) -> None:
    panels: list[np.ndarray] = []
    for record in records:
        frame = record["_images"][frame_index]
        crop, crop_x, crop_y = _extract_crop(frame, record["ball"])
        result = record["frames"][str(frame_index)]
        panel = _draw_measurements(crop, crop_x, crop_y, record["ball"], result)
        label = f"{record['shot']}  {result['status']}  {result['reason']}"
        cv2.rectangle(panel, (0, 0), (panel.shape[1] - 1, 27), (0, 0, 0), -1)
        cv2.putText(
            panel,
            label,
            (6, 19),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.48,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
        panels.append(panel)
    panel_height = max(panel.shape[0] for panel in panels)
    panel_width = max(panel.shape[1] for panel in panels)
    rows = math.ceil(len(panels) / _SHEET_COLUMNS)
    sheet = np.zeros((rows * panel_height, _SHEET_COLUMNS * panel_width, 3), np.uint8)
    for index, panel in enumerate(panels):
        row, column = divmod(index, _SHEET_COLUMNS)
        sheet[
            row * panel_height : row * panel_height + panel.shape[0],
            column * panel_width : column * panel_width + panel.shape[1],
        ] = panel
    if not cv2.imwrite(str(output_path), sheet):
        raise OSError(f"could not write {output_path}")


def _frame(record: dict[str, Any], index: int) -> dict[str, Any]:
    return record["frames"][str(record["frame_indices"][index])]


def _statistics(records: list[dict[str, Any]]) -> dict[str, Any]:
    width_checks: dict[str, Any] = {}
    width_flags: list[dict[str, Any]] = []
    for club in sorted({str(record["club"]) for record in records}):
        club_records = [record for record in records if record["club"] == club]
        widths = [
            float(frame["width_px"])
            for record in club_records
            for frame in (_frame(record, 0), _frame(record, 1))
            if frame["width_px"] is not None
        ]
        median = float(np.median(widths)) if widths else None
        for record in club_records:
            for frame_index in (0, 1):
                width = _frame(record, frame_index)["width_px"]
                if width is not None and median is not None and abs(width - median) > 3.0:
                    width_flags.append(
                        {
                            "shot": record["shot"],
                            "frame": record["frame_indices"][frame_index],
                            "width_px": width,
                            "club_median_px": median,
                        }
                    )
        width_checks[club] = {
            "mean_px": float(np.mean(widths)) if widths else None,
            "sd_px": float(np.std(widths, ddof=1)) if len(widths) > 1 else None,
            "n_frames": len(widths),
            "pass": bool(widths) and float(np.std(widths, ddof=1)) <= 1.5,
        }

    consistency_rows: list[dict[str, Any]] = []
    consistency_passes = 0
    availability = 0
    for record in records:
        first, second = _frame(record, 0), _frame(record, 1)
        complete = first["status"] == "ok" and second["status"] == "ok"
        availability += int(complete)
        delta_width = None
        delta_topline = None
        passed = False
        if complete:
            delta_toe = float(second["toe_x"] - first["toe_x"])
            delta_heel = float(second["heel_x"] - first["heel_x"])
            delta_width = delta_toe - delta_heel
            delta_topline = float(second["topline_row_at_ball"] - first["topline_row_at_ball"])
            passed = abs(delta_width) <= 1.0
            consistency_passes += int(passed)
        consistency_rows.append(
            {
                "shot": record["shot"],
                "delta_toe_minus_delta_heel_px": delta_width,
                "delta_topline_px": delta_topline,
                "pass": passed,
            }
        )

    reference = {
        71: {"heel_x": 165.0, "toe_x": 191.0, "topline_row_at_ball": 141.0},
        72: {"heel_x": 163.0, "toe_x": 190.0, "topline_row_at_ball": 140.0},
    }
    shot_014 = next(record for record in records if record["shot_number"] == 14)
    hand_errors: dict[str, dict[str, float | None]] = {}
    hand_pass = True
    for frame_index, marks in reference.items():
        result = shot_014["frames"].get(str(frame_index), {})
        errors: dict[str, float | None] = {}
        for field, mark in marks.items():
            value = result.get(field)
            error = None if value is None else abs(float(value) - mark)
            errors[field] = error
            hand_pass = hand_pass and error is not None and error <= 1.5
        hand_errors[str(frame_index)] = errors

    return {
        "width": width_checks,
        "width_flags": width_flags,
        "two_frame": {
            "passing_shots": consistency_passes,
            "total_shots": len(records),
            "fraction": consistency_passes / len(records),
            "pass": consistency_passes / len(records) >= 0.8,
            "per_shot": consistency_rows,
        },
        "hand_mark_shot_014": {"errors_px": hand_errors, "pass": hand_pass},
        "availability": {
            "complete_shots": availability,
            "total_shots": len(records),
            "pass": availability >= 17,
        },
    }


def _print_table(checks: dict[str, Any], output: Path) -> None:
    print("\nSection 3 pre-registered checks")
    print("check | result | threshold | pass")
    print("--- | --- | --- | ---")
    for club, result in checks["width"].items():
        summary = (
            f"mean {result['mean_px']:.3f} px, sd {result['sd_px']:.3f} px, n={result['n_frames']}"
        )
        print(f"width {club} | {summary} | sd <= 1.5 px | {result['pass']}")
    two_frame = checks["two_frame"]
    print(
        "two-frame consistency | "
        f"{two_frame['passing_shots']}/{two_frame['total_shots']} "
        f"({two_frame['fraction']:.1%}) | >= 80% | {two_frame['pass']}"
    )
    hand = checks["hand_mark_shot_014"]
    hand_values = [
        value
        for errors in hand["errors_px"].values()
        for value in errors.values()
        if value is not None
    ]
    maximum = max(hand_values) if hand_values else float("nan")
    print(f"shot 014 hand marks | max error {maximum:.3f} px | <= 1.5 px | {hand['pass']}")
    availability = checks["availability"]
    print(
        "availability | "
        f"{availability['complete_shots']}/{availability['total_shots']} | "
        f">= 17/21 | {availability['pass']}"
    )
    print(f"look | inspect {output / 'sheet_f71.png'} and {output / 'sheet_f72.png'}")
    if checks["width_flags"]:
        print("\nWidth flags (>3 px from club median):")
        for flag in checks["width_flags"]:
            print(
                f"  {flag['shot']} f{flag['frame']}: {flag['width_px']:.3f} px "
                f"(club median {flag['club_median_px']:.3f} px)"
            )
    print("\nPer-shot two-frame deltas:")
    print("shot | delta toe - delta heel (px) | delta topline (px) | pass")
    print("--- | --- | --- | ---")
    for row in two_frame["per_shot"]:
        delta_width = row["delta_toe_minus_delta_heel_px"]
        delta_topline = row["delta_topline_px"]
        width_text = "unavailable" if delta_width is None else f"{delta_width:.3f}"
        topline_text = "unavailable" if delta_topline is None else f"{delta_topline:.3f}"
        print(f"{row['shot']} | {width_text} | {topline_text} | {row['pass']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", help="export root; defaults to OPENFLIGHT_SESSION")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    session = _session_path(args.session, parser)
    output = args.out.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    clubs = _shot_clubs(session)
    paths = sorted((session / "shots").glob("shot_*"), key=_shot_number)
    paths = [path for path in paths if path.is_dir() and _shot_number(path) != 1]
    records = [_load_shot(path, clubs[_shot_number(path)]) for path in paths]
    if len(records) != 21:
        parser.error(f"expected 21 usable shots after excluding shot_001, found {len(records)}")

    first_index, second_index = records[0]["frame_indices"]
    if any(record["frame_indices"] != [first_index, second_index] for record in records):
        parser.error("contact-bracketing frame indices differ across shots")
    _render_sheet(records, first_index, output / f"sheet_f{first_index}.png")
    _render_sheet(records, second_index, output / f"sheet_f{second_index}.png")

    checks = _statistics(records)
    serializable_records = [
        {key: value for key, value in record.items() if key != "_images"} for record in records
    ]
    payload = {
        "session": str(session),
        "mirroring": "frames[:, :, ::-1]",
        "contact_formula": "trigger - 1.575 / 343 * delivered_fps",
        "final_thresholds": {
            "topline_bright_dn": "max(205, mat + 50)",
            "topline_body_dn": "mat - 35",
            "toe_dark_dn": "mat - 35",
            "shaft_bright_dn": "max(210, mat + 55)",
        },
        "shots": serializable_records,
        "checks": checks,
    }
    (output / "contact_edges.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    _print_table(checks, output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Replay translation-only clubhead outline alignment on an exported session.

Run from the repository root:

    uv run python scripts/analysis/clubpose_outline_align.py \
        --session /path/to/export --mesh /path/to/poc_7iron.npz --out /path/to/output
"""

# Imports follow the repository-root path bootstrap; OpenCV and np.load expose
# runtime members that Pylint's static inference cannot see.
# pylint: disable=wrong-import-position,no-member,unsubscriptable-object

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from dataclasses import fields
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
from openflight.camera.clubpose.angles import STATIC_LOFT_DEG  # noqa: E402
from openflight.camera.clubpose.fit import measured_camera  # noqa: E402
from openflight.camera.clubpose.mesh import load_normalized_mesh  # noqa: E402
from openflight.camera.clubpose.outline_align import (  # noqa: E402
    OutlineAlignment,
    ShaftLine,
    align_outline,
    detect_shaft_line,
    outline_offsets,
)

_BALL_TO_UNIT_M = 1.575
_SPEED_OF_SOUND_M_S = 343.0
_CONTACT_RANGE_MM = 1_581.0
_SHEET_SCALE = 8
_SHEET_COLUMNS = 3
_CROP_HALF_WIDTH = 32
_CROP_HALF_HEIGHT = 24


def _session_path(value: str | None, parser: argparse.ArgumentParser) -> Path:
    supplied = value or os.environ.get("OPENFLIGHT_SESSION")
    if not supplied:
        parser.error("provide --session or set OPENFLIGHT_SESSION")
    session = Path(supplied).expanduser().resolve()
    if not (session / "shots.csv").is_file() or not (session / "shots").is_dir():
        parser.error(f"session export is missing shots.csv or shots/: {session}")
    return session


def _mesh_path(value: Path, parser: argparse.ArgumentParser) -> Path:
    path = value.expanduser().resolve()
    if not path.is_file():
        parser.error(f"normalized mesh asset does not exist: {path}")
    if path.suffix.lower() != ".npz":
        parser.error(
            "--mesh must be the normalized NPZ produced by "
            "scripts/analysis/download_club_mesh.py --local-iron <STL>"
        )
    return path


def _shot_rows(session: Path) -> dict[int, dict[str, str]]:
    with (session / "shots.csv").open(newline="", encoding="utf-8") as handle:
        return {
            int(row["shot_number"]): row for row in csv.DictReader(handle) if row.get("shot_number")
        }


def _shot_number(path: Path) -> int:
    return int(path.name.split("_", 2)[1])


def _float(value: str | None) -> float | None:
    try:
        parsed = float(value) if value not in (None, "") else None
    except ValueError:
        return None
    return parsed if parsed is not None and math.isfinite(parsed) else None


def _mat_level(background: np.ndarray, ball: ReferenceBall) -> float:
    height, width = background.shape
    x0 = max(0, int(math.floor(ball.x - 32.0)))
    x1 = min(width, int(math.ceil(ball.x + 32.0)) + 1)
    y0 = max(0, int(math.floor(ball.y - 14.0)))
    y1 = min(height, int(math.ceil(ball.y + 3.0)) + 1)
    if x1 <= x0 or y1 <= y0:
        raise ValueError("ball-centred mat window is outside the frame")
    return float(np.median(background[y0:y1, x0:x1]))


def _contact_frame(metadata: dict[str, Any]) -> float:
    trigger = int(metadata["pre_trigger_frames"])
    fps = float(metadata["delivered_fps"])
    return trigger - _BALL_TO_UNIT_M / _SPEED_OF_SOUND_M_S * fps


def _range_mm(frame_index: int, contact_frame: float, fps: float, range_rate_ms: float) -> float:
    return _CONTACT_RANGE_MM - range_rate_ms * 1000.0 * (contact_frame - frame_index) / fps


def _loft(row: dict[str, str]) -> tuple[float, str]:
    status = row.get("experimental_fused_status", "")
    attack = _float(row.get("experimental_fused_attack_angle_deg"))
    if status in {"approach_high", "approach_mixed"} and attack is not None:
        return STATIC_LOFT_DEG + attack, "static_plus_fused_attack"
    return STATIC_LOFT_DEG, "static_fallback_attack_status"


def _alignment_record(result: OutlineAlignment) -> dict[str, Any]:
    return {
        item.name: getattr(result, item.name) for item in fields(result) if item.name != "template"
    }


def _shaft_record(line: ShaftLine) -> dict[str, Any]:
    return {
        "center_xy": line.center_xy,
        "direction_xy": line.direction_xy,
        "angle_deg": line.angle_deg,
        "status": line.status,
        "reason": line.reason,
    }


def _load_shot(path: Path, row: dict[str, str], mesh, camera) -> dict[str, Any]:
    with (path / "camera_metadata.json").open(encoding="utf-8") as handle:
        metadata = json.load(handle)
    with np.load(path / "frames.npz") as capture:
        frames = capture["frames"][:, :, ::-1]
    ball = detect_reference_ball(frames)
    background = np.median(frames[8:56], axis=0)
    mat_level = _mat_level(background, ball)
    contact = _contact_frame(metadata)
    fps = float(metadata["delivered_fps"])
    first, second = math.floor(contact), math.ceil(contact)
    range_rate = _float(row.get("iwr_club_path_range_rate_ms"))
    if range_rate is None:
        raise ValueError(f"shot {_shot_number(path):03d} has no radar range rate")
    loft_deg, loft_source = _loft(row)
    frame_results: dict[str, dict[str, Any]] = {}
    runtime: dict[int, dict[str, Any]] = {}
    for frame_index in (first - 1, first, second):
        shaft = detect_shaft_line(frames[frame_index], ball, mat_level)
        frame_range = _range_mm(frame_index, contact, fps, range_rate)
        result = align_outline(
            frames[frame_index],
            ball,
            mesh,
            camera,
            frame_range,
            shaft.angle_deg,
            loft_deg,
            shaft_line=shaft,
        )
        runtime[frame_index] = {
            "frame": frames[frame_index],
            "shaft": shaft,
            "alignment": result,
            "range_mm": frame_range,
        }
        frame_results[str(frame_index)] = {
            "range_mm": frame_range,
            "shaft": _shaft_record(shaft),
            "alignment": _alignment_record(result),
        }
    return {
        "shot_number": _shot_number(path),
        "shot": path.name,
        "club": row["club"],
        "ball": {
            "x": ball.x,
            "y": ball.y,
            "diameter_px": ball.diameter_px,
            "area_px": ball.area_px,
        },
        "plate_scale_mm_per_px": BALL_DIAMETER_MM / ball.diameter_px,
        "mat_level": mat_level,
        "contact_frame": contact,
        "frame_indices": [first, second],
        "range_rate_ms": range_rate,
        "loft_deg": loft_deg,
        "loft_source": loft_source,
        "frames": frame_results,
        "_ball": ball,
        "_runtime": runtime,
    }


def _extract_crop(frame: np.ndarray, ball: ReferenceBall) -> tuple[np.ndarray, int, int]:
    centre_x = int(round(ball.x))
    centre_y = int(round(ball.y))
    x0 = max(0, centre_x - _CROP_HALF_WIDTH)
    x1 = min(frame.shape[1], centre_x + _CROP_HALF_WIDTH)
    y0 = max(0, centre_y - _CROP_HALF_HEIGHT)
    y1 = min(frame.shape[0], centre_y + _CROP_HALF_HEIGHT)
    return cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_GRAY2BGR), x0, y0


def _scaled_point(x: float, y: float, crop_x: int, crop_y: int) -> tuple[int, int]:
    return (
        int(round((x - crop_x) * _SHEET_SCALE)),
        int(round((y - crop_y) * _SHEET_SCALE)),
    )


def _draw_alignment(
    crop: np.ndarray,
    crop_x: int,
    crop_y: int,
    ball: ReferenceBall,
    result: OutlineAlignment,
) -> np.ndarray:
    scaled = cv2.resize(
        crop,
        None,
        fx=_SHEET_SCALE,
        fy=_SHEET_SCALE,
        interpolation=cv2.INTER_NEAREST,
    )
    cv2.circle(
        scaled,
        _scaled_point(ball.x, ball.y, crop_x, crop_y),
        int(round(ball.diameter_px / 2.0 * _SHEET_SCALE)),
        (0, 255, 0),
        2,
        cv2.LINE_AA,
    )
    if result.template is None or result.dx is None or result.dy is None:
        return scaled
    points = result.template.boundary_xy + np.asarray([result.dx, result.dy])
    for x, y in points:
        px, py = _scaled_point(float(x), float(y), crop_x, crop_y)
        if 0 <= px < scaled.shape[1] and 0 <= py < scaled.shape[0]:
            cv2.circle(scaled, (px, py), 2, (255, 255, 0), -1)
    offsets = outline_offsets(result.template, ball, result.dx, result.dy)
    top = offsets["topline_row_at_ball"]
    marks = (
        (offsets["heel_x"], top, (0, 0, 255)),
        (offsets["toe_x"], top, (0, 255, 255)),
        (offsets["midpoint_x"], top, (255, 0, 255)),
    )
    for x, y, colour in marks:
        cv2.drawMarker(
            scaled,
            _scaled_point(x, y, crop_x, crop_y),
            colour,
            cv2.MARKER_CROSS,
            24,
            3,
            cv2.LINE_AA,
        )
    return scaled


def _render_sheet(records: list[dict[str, Any]], frame_index: int, output_path: Path) -> None:
    panels: list[np.ndarray] = []
    for record in records:
        ball = record["_ball"]
        runtime = record["_runtime"][frame_index]
        crop, crop_x, crop_y = _extract_crop(runtime["frame"], ball)
        result = runtime["alignment"]
        panel = _draw_alignment(crop, crop_x, crop_y, ball, result)
        residual = "-" if result.residual_px is None else f"{result.residual_px:.2f}"
        label = f"{record['shot']}  {result.status}:{result.reason}  r={residual}"
        cv2.rectangle(panel, (0, 0), (panel.shape[1] - 1, 27), (0, 0, 0), -1)
        cv2.putText(
            panel,
            label,
            (5, 19),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.43,
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


def _run_sensitivity(record: dict[str, Any], mesh, camera) -> dict[str, Any]:
    output: dict[str, Any] = {}
    ball = record["_ball"]
    for frame_index in record["frame_indices"]:
        runtime = record["_runtime"][frame_index]
        nominal = runtime["alignment"]
        if nominal.status != "ok":
            output[str(frame_index)] = {"status": "nominal_failed", "variants": {}}
            continue
        variants = {
            "loft_minus_5": {"loft": record["loft_deg"] - 5.0},
            "loft_plus_5": {"loft": record["loft_deg"] + 5.0},
            "face_minus_10": {"loft": record["loft_deg"], "face": -10.0},
            "face_plus_10": {"loft": record["loft_deg"], "face": 10.0},
            "lie_minus_3": {
                "loft": record["loft_deg"],
                "lie": nominal.lie_used_deg - 3.0,
            },
            "lie_plus_3": {
                "loft": record["loft_deg"],
                "lie": nominal.lie_used_deg + 3.0,
            },
        }
        variant_records: dict[str, Any] = {}
        for name, settings in variants.items():
            result = align_outline(
                runtime["frame"],
                ball,
                mesh,
                camera,
                runtime["range_mm"],
                runtime["shaft"].angle_deg,
                settings["loft"],
                shaft_line=runtime["shaft"],
                face_angle_deg=settings.get("face", 0.0),
                lie_override_deg=settings.get("lie"),
            )
            movement = None
            if result.status == "ok":
                movement = math.hypot(
                    result.ball_minus_midpoint_px - nominal.ball_minus_midpoint_px,
                    result.ball_minus_topline_px - nominal.ball_minus_topline_px,
                )
            variant_records[name] = {
                "status": result.status,
                "reason": result.reason,
                "offset_movement_px": movement,
            }
        output[str(frame_index)] = {"status": "ok", "variants": variant_records}
    return output


def _checks(  # pylint: disable=too-many-branches
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    consistency_rows: list[dict[str, Any]] = []
    consistency_passes = 0
    availability = 0
    residuals: list[float] = []
    for record in records:
        first, second = record["frame_indices"]
        reference = first - 1
        align0 = record["_runtime"][reference]["alignment"]
        align1 = record["_runtime"][first]["alignment"]
        align2 = record["_runtime"][second]["alignment"]
        complete = align1.status == "ok" and align2.status == "ok"
        availability += int(complete)
        for result in (align1, align2):
            if result.status == "ok":
                residuals.append(float(result.residual_px))
        error_x = error_y = error_norm = None
        passed = False
        if complete and align0.status == "ok":
            predicted_dx = align1.dx + (align1.dx - align0.dx)
            predicted_dy = align1.dy + (align1.dy - align0.dy)
            predicted = outline_offsets(
                align2.template, record["_ball"], predicted_dx, predicted_dy
            )
            error_x = predicted["ball_minus_midpoint_px"] - align2.ball_minus_midpoint_px
            error_y = predicted["ball_minus_topline_px"] - align2.ball_minus_topline_px
            error_norm = math.hypot(error_x, error_y)
            passed = error_norm <= 1.0
            consistency_passes += int(passed)
        consistency_rows.append(
            {
                "shot": record["shot"],
                "horizontal_error_px": error_x,
                "vertical_error_px": error_y,
                "error_norm_px": error_norm,
                "pass": passed,
            }
        )

    shot_014 = next(record for record in records if record["shot_number"] == 14)
    hand_reference = {
        71: {"midpoint_x": (165.0 + 191.0) / 2.0, "topline_row_at_ball": 141.0},
        72: {"midpoint_x": (163.0 + 190.0) / 2.0, "topline_row_at_ball": 140.0},
    }
    hand_errors: dict[str, Any] = {}
    hand_pass = True
    for frame_index, expected in hand_reference.items():
        result = shot_014["_runtime"][frame_index]["alignment"]
        errors: dict[str, float | None] = {}
        for key, target in expected.items():
            value = getattr(result, key)
            error = None if value is None else abs(float(value) - target)
            errors[key] = error
            hand_pass = hand_pass and error is not None and error <= 1.5
        hand_errors[str(frame_index)] = errors

    sensitivity_groups = {
        "loft_5_deg": ("loft_minus_5", "loft_plus_5"),
        "face_10_deg": ("face_minus_10", "face_plus_10"),
        "lie_3_deg": ("lie_minus_3", "lie_plus_3"),
    }
    sensitivity_checks: dict[str, Any] = {}
    for group, names in sensitivity_groups.items():
        values: list[float] = []
        missing = 0
        for record in records:
            for frame in record["sensitivity"].values():
                if frame["status"] != "ok":
                    continue
                for name in names:
                    movement = frame["variants"][name]["offset_movement_px"]
                    if movement is None:
                        missing += 1
                    else:
                        values.append(float(movement))
        maximum = max(values) if values else None
        sensitivity_checks[group] = {
            "max_offset_movement_px": maximum,
            "n_variants": len(values),
            "missing_variants": missing,
            "pass": bool(values) and missing == 0 and maximum <= 1.0,
        }

    fraction = consistency_passes / len(records)
    median_residual = float(np.median(residuals)) if residuals else None
    return {
        "two_frame_consistency": {
            "passing_shots": consistency_passes,
            "total_shots": len(records),
            "fraction": fraction,
            "pass": fraction >= 0.8,
            "per_shot": consistency_rows,
        },
        "hand_mark_shot_014": {"errors_px": hand_errors, "pass": hand_pass},
        "availability": {
            "complete_shots": availability,
            "total_shots": len(records),
            "pass": availability >= 17,
        },
        "sensitivity": sensitivity_checks,
        "residual": {
            "median_px": median_residual,
            "n_frames": len(residuals),
            "pass": median_residual is not None and median_residual <= 1.0,
        },
    }


def _print_table(checks: dict[str, Any], output: Path) -> None:
    print("\nSection 2 pre-registered checks")
    print("check | result | threshold | pass")
    print("--- | --- | --- | ---")
    consistency = checks["two_frame_consistency"]
    print(
        "two-frame consistency | "
        f"{consistency['passing_shots']}/{consistency['total_shots']} "
        f"({consistency['fraction']:.1%}) | >= 80% within 1 px | {consistency['pass']}"
    )
    hand = checks["hand_mark_shot_014"]
    hand_values = [
        value
        for frame in hand["errors_px"].values()
        for value in frame.values()
        if value is not None
    ]
    hand_max = max(hand_values) if hand_values else float("nan")
    print(f"shot 014 hand marks | max error {hand_max:.3f} px | <= 1.5 px | {hand['pass']}")
    availability = checks["availability"]
    print(
        "availability | "
        f"{availability['complete_shots']}/{availability['total_shots']} | "
        f">= 17/21 | {availability['pass']}"
    )
    for name, result in checks["sensitivity"].items():
        maximum = result["max_offset_movement_px"]
        text = "unavailable" if maximum is None else f"max {maximum:.3f} px"
        print(f"{name} sensitivity | {text} | <= 1 px | {result['pass']}")
    residual = checks["residual"]
    median = residual["median_px"]
    text = "unavailable" if median is None else f"median {median:.3f} px"
    print(f"accepted residual | {text}, n={residual['n_frames']} | <= 1 px | {residual['pass']}")
    print(f"look | inspect {output / 'sheet_f71.png'} and {output / 'sheet_f72.png'}")


def main() -> int:
    """Replay the session, render evidence sheets, and write the result JSON."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", help="export root; defaults to OPENFLIGHT_SESSION")
    parser.add_argument("--mesh", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    session = _session_path(args.session, parser)
    mesh_path = _mesh_path(args.mesh, parser)
    output = args.out.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    mesh, mesh_metadata, mesh_sha256 = load_normalized_mesh(str(mesh_path))
    camera = measured_camera(320, 200)
    rows = _shot_rows(session)
    paths = sorted((session / "shots").glob("shot_*"), key=_shot_number)
    paths = [path for path in paths if path.is_dir() and _shot_number(path) != 1]
    records = [_load_shot(path, rows[_shot_number(path)], mesh, camera) for path in paths]
    if len(records) != 21:
        parser.error(f"expected 21 usable shots after excluding shot_001, found {len(records)}")
    first, second = records[0]["frame_indices"]
    if any(record["frame_indices"] != [first, second] for record in records):
        parser.error("contact-bracketing frame indices differ across shots")

    _render_sheet(records, first, output / f"sheet_f{first}.png")
    _render_sheet(records, second, output / f"sheet_f{second}.png")
    for record in records:
        record["sensitivity"] = _run_sensitivity(record, mesh, camera)
    checks = _checks(records)
    serializable = [
        {key: value for key, value in record.items() if not key.startswith("_")}
        for record in records
    ]
    payload = {
        "session": str(session),
        "mirroring": "frames[:, :, ::-1]",
        "contact_formula": "trigger - 1.575 / 343 * delivered_fps",
        "range_formula": (
            "1581 - iwr_club_path_range_rate_ms * 1000 * "
            "(contact_frame - frame_index) / delivered_fps"
        ),
        "camera": {"name": camera.name, "fx": camera.fx, "fy": camera.fy},
        "mesh": {
            "path": str(mesh_path),
            "asset_sha256": mesh_sha256,
            "source_uid": mesh.source_uid,
            "source_sha256": mesh.source_sha256,
            "metadata": mesh_metadata,
            "nine_iron_caveat": "9-iron shots use the 690CB 7-iron mesh",
        },
        "translation_approximation": "<0.1 px error for +/-10 px at 1.5 m",
        "transverse_carry": (
            "f72 nominal range render plus dx/dy increment measured from f70 to f71"
        ),
        "shots": serializable,
        "checks": checks,
    }
    (output / "outline_align.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    _print_table(checks, output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

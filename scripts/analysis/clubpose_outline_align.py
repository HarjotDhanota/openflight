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
    linear_motion_carry,
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


def _template_record(result: OutlineAlignment) -> dict[str, Any] | None:
    template = result.template
    if template is None:
        return None
    return {
        "center_xy": [template.center_x, template.center_y],
        "height_px": template.height_px,
        "boundary_candidate_count": template.boundary_candidate_count,
        "boundary_kept_count": template.boundary_kept_count,
        "boundary_fraction_kept": template.boundary_fraction_kept,
    }


def _shaft_record(line: ShaftLine) -> dict[str, Any]:
    return {
        "center_xy": line.center_xy,
        "direction_xy": line.direction_xy,
        "angle_deg": line.angle_deg,
        "status": line.status,
        "reason": line.reason,
    }


def _longest_true_run(values: np.ndarray) -> tuple[int, int] | None:
    indices = np.flatnonzero(values)
    if indices.size == 0:
        return None
    splits = np.flatnonzero(np.diff(indices) > 1) + 1
    runs = np.split(indices, splits)
    longest = max(runs, key=len)
    return int(longest[0]), int(longest[-1])


def _sanity_metrics(
    frame: np.ndarray,
    ball: ReferenceBall,
    result: OutlineAlignment,
    mat_level: float,
) -> dict[str, float | int | None]:
    template = result.template
    if template is None or result.dx is None or result.dy is None:
        return {
            "template_topline_row_px": None,
            "brightest_ridge_row_px": None,
            "topline_minus_ridge_px": None,
            "template_height_px": None,
            "template_height_mm": None,
            "observed_dark_height_px": None,
            "observed_dark_height_mm": None,
        }
    half_width = max(1.0, (template.toe_x - template.midpoint_x) / 2.0)
    nominal_x0 = max(0, int(math.ceil(template.midpoint_x + half_width)))
    nominal_x1 = min(template.mask.shape[1], int(math.floor(template.toe_x)) + 1)
    x0 = max(0, int(math.ceil(nominal_x0 + result.dx)))
    x1 = min(frame.shape[1], int(math.floor(nominal_x1 + result.dx)))
    top_rows = template.topline_ys[
        (template.topline_xs >= nominal_x0) & (template.topline_xs < nominal_x1)
    ]
    mask_rows = np.flatnonzero(np.any(template.mask[:, nominal_x0:nominal_x1], axis=1))
    top = None if top_rows.size == 0 else float(np.median(top_rows) + result.dy)
    template_height = None if mask_rows.size == 0 else float(mask_rows[-1] - mask_rows[0] + 1)
    scale = BALL_DIAMETER_MM / ball.diameter_px
    if top is None or template_height is None:
        return {
            "template_topline_row_px": top,
            "brightest_ridge_row_px": None,
            "topline_minus_ridge_px": None,
            "template_height_px": template_height,
            "template_height_mm": None if template_height is None else template_height * scale,
            "observed_dark_height_px": None,
            "observed_dark_height_mm": None,
        }
    y0 = max(0, int(math.floor(top - 4.0)))
    y1 = min(frame.shape[0], int(math.ceil(top + template_height + 5.0)))
    if x1 <= x0 or y1 <= y0:
        return {
            "template_topline_row_px": top,
            "brightest_ridge_row_px": None,
            "topline_minus_ridge_px": None,
            "template_height_px": template_height,
            "template_height_mm": template_height * scale,
            "observed_dark_height_px": None,
            "observed_dark_height_mm": None,
        }
    band = frame[y0:y1, x0:x1].astype(float)
    ridge_scores = np.percentile(band, 90.0, axis=1)
    ridge = float(y0 + int(np.argmax(ridge_scores)))
    dark_floor = float(np.percentile(band, 10.0))
    dark_cutoff = (float(mat_level) + dark_floor) / 2.0
    dark_rows = np.median(band, axis=1) < dark_cutoff
    run = _longest_true_run(dark_rows)
    observed_height = None if run is None else float(run[1] - run[0] + 1)
    return {
        "toe_band_x0": x0,
        "toe_band_x1": x1 - 1,
        "template_topline_row_px": top,
        "brightest_ridge_row_px": ridge,
        "topline_minus_ridge_px": top - ridge,
        "template_height_px": template_height,
        "template_height_mm": template_height * scale,
        "observed_dark_height_px": observed_height,
        "observed_dark_height_mm": None if observed_height is None else observed_height * scale,
    }


def _carry_results(
    frame_indices: list[int],
    results: list[OutlineAlignment],
    ball: ReferenceBall,
    target_frame: float,
) -> dict[str, Any]:
    if any(
        result.status != "ok" or result.template is None or result.dx is None or result.dy is None
        for result in results
    ):
        return {
            "status": "failed",
            "reason": "three_frame_alignment_incomplete",
            "velocity_px_per_frame": None,
            "spread_px": None,
            "frames": {},
        }
    centres = np.asarray(
        [
            [result.template.center_x + result.dx, result.template.center_y + result.dy]
            for result in results
        ],
        dtype=float,
    )
    velocity, carries = linear_motion_carry(
        np.asarray(frame_indices, dtype=float), centres, target_frame=target_frame
    )
    scale = BALL_DIAMETER_MM / ball.diameter_px
    carried: dict[str, Any] = {}
    vectors: list[list[float]] = []
    for frame_index, result, carry in zip(frame_indices, results, carries, strict=True):
        offsets = outline_offsets(
            result.template,
            ball,
            float(result.dx + carry[0]),
            float(result.dy + carry[1]),
        )
        vectors.append([offsets["ball_minus_midpoint_px"], offsets["ball_minus_topline_px"]])
        carried[str(frame_index)] = {
            "carry_dx_px": float(carry[0]),
            "carry_dy_px": float(carry[1]),
            **offsets,
            "ball_minus_midpoint_mm": offsets["ball_minus_midpoint_px"] * scale,
            "ball_minus_topline_mm": offsets["ball_minus_topline_px"] * scale,
        }
    pairwise = [
        math.dist(vectors[first], vectors[second])
        for first in range(len(vectors))
        for second in range(first + 1, len(vectors))
    ]
    return {
        "status": "ok",
        "reason": "ok",
        "target_frame": float(target_frame),
        "velocity_px_per_frame": velocity.tolist(),
        "spread_px": max(pairwise),
        "frames": carried,
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
    first = math.floor(contact)
    frame_indices = [first - 2, first - 1, first]
    range_rate = _float(row.get("iwr_club_path_range_rate_ms"))
    if range_rate is None:
        raise ValueError(f"shot {_shot_number(path):03d} has no radar range rate")
    loft_deg, loft_source = _loft(row)
    frame_results: dict[str, dict[str, Any]] = {}
    runtime: dict[int, dict[str, Any]] = {}
    for frame_index in frame_indices:
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
            "sanity": _sanity_metrics(frames[frame_index], ball, result, mat_level),
        }
        frame_results[str(frame_index)] = {
            "range_mm": frame_range,
            "shaft": _shaft_record(shaft),
            "alignment": _alignment_record(result),
            "template": _template_record(result),
            "sanity": runtime[frame_index]["sanity"],
        }
    carry = _carry_results(
        frame_indices,
        [runtime[index]["alignment"] for index in frame_indices],
        ball,
        contact,
    )
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
        "frame_indices": frame_indices,
        "range_rate_ms": range_rate,
        "loft_deg": loft_deg,
        "loft_source": loft_source,
        "frames": frame_results,
        "carry_to_contact": carry,
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
        sanity = runtime["sanity"]
        ridge = sanity["brightest_ridge_row_px"]
        observed_height = sanity["observed_dark_height_px"]
        template_height = sanity["template_height_px"]
        kept = None if result.template is None else result.template.boundary_fraction_kept
        detail = (
            f"top/ridge={sanity['template_topline_row_px']}/{ridge}  "
            f"height={template_height}/{observed_height}px  "
            f"keep={'-' if kept is None else f'{kept:.0%}'}"
        )
        cv2.rectangle(panel, (0, 0), (panel.shape[1] - 1, 47), (0, 0, 0), -1)
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
        cv2.putText(
            panel,
            detail,
            (5, 39),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.38,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
        panels.append(panel)
    _write_panel_sheet(panels, output_path)


def _write_panel_sheet(panels: list[np.ndarray], output_path: Path) -> None:
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


def _render_contact_sheet(records: list[dict[str, Any]], output_path: Path) -> None:
    panels: list[np.ndarray] = []
    colours = ((255, 255, 0), (255, 0, 255), (0, 255, 255))
    for record in records:
        ball = record["_ball"]
        last_frame = record["frame_indices"][-1]
        crop, crop_x, crop_y = _extract_crop(record["_runtime"][last_frame]["frame"], ball)
        panel = cv2.resize(
            crop,
            None,
            fx=_SHEET_SCALE,
            fy=_SHEET_SCALE,
            interpolation=cv2.INTER_NEAREST,
        )
        cv2.circle(
            panel,
            _scaled_point(ball.x, ball.y, crop_x, crop_y),
            int(round(ball.diameter_px / 2.0 * _SHEET_SCALE)),
            (0, 255, 0),
            2,
            cv2.LINE_AA,
        )
        carry = record["carry_to_contact"]
        if carry["status"] == "ok":
            for frame_index, colour in zip(record["frame_indices"], colours, strict=True):
                result = record["_runtime"][frame_index]["alignment"]
                carried = carry["frames"][str(frame_index)]
                dx = result.dx + carried["carry_dx_px"]
                dy = result.dy + carried["carry_dy_px"]
                points = result.template.boundary_xy + np.asarray([dx, dy])
                for x, y in points:
                    px, py = _scaled_point(float(x), float(y), crop_x, crop_y)
                    if 0 <= px < panel.shape[1] and 0 <= py < panel.shape[0]:
                        cv2.circle(panel, (px, py), 2, colour, -1)
                cv2.drawMarker(
                    panel,
                    _scaled_point(
                        carried["midpoint_x"],
                        carried["topline_row_at_ball"],
                        crop_x,
                        crop_y,
                    ),
                    colour,
                    cv2.MARKER_CROSS,
                    20,
                    2,
                    cv2.LINE_AA,
                )
        spread = "-" if carry["spread_px"] is None else f"{carry['spread_px']:.2f}px"
        label = f"{record['shot']}  carry:{carry['status']}  spread={spread}"
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
    _write_panel_sheet(panels, output_path)


def _run_sensitivity(record: dict[str, Any], mesh, camera) -> dict[str, Any]:
    if record["carry_to_contact"]["status"] != "ok":
        return {"status": "nominal_failed", "variants": {}}
    ball = record["_ball"]
    variants = {
        "loft_minus_5": {"loft": record["loft_deg"] - 5.0},
        "loft_plus_5": {"loft": record["loft_deg"] + 5.0},
        "face_minus_10": {"loft": record["loft_deg"], "face": -10.0},
        "face_plus_10": {"loft": record["loft_deg"], "face": 10.0},
        "lie_minus_3": {"loft": record["loft_deg"], "lie_delta": -3.0},
        "lie_plus_3": {"loft": record["loft_deg"], "lie_delta": 3.0},
    }
    variant_records: dict[str, Any] = {}
    nominal_carry = record["carry_to_contact"]
    for name, settings in variants.items():
        results: list[OutlineAlignment] = []
        for frame_index in record["frame_indices"]:
            runtime = record["_runtime"][frame_index]
            nominal = runtime["alignment"]
            lie = None
            if "lie_delta" in settings:
                lie = nominal.lie_used_deg + settings["lie_delta"]
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
                lie_override_deg=lie,
            )
            results.append(result)
        variant_carry = _carry_results(
            record["frame_indices"], results, ball, record["contact_frame"]
        )
        movement = None
        if variant_carry["status"] == "ok":
            movement = max(
                math.hypot(
                    variant_carry["frames"][str(frame_index)]["ball_minus_midpoint_px"]
                    - nominal_carry["frames"][str(frame_index)]["ball_minus_midpoint_px"],
                    variant_carry["frames"][str(frame_index)]["ball_minus_topline_px"]
                    - nominal_carry["frames"][str(frame_index)]["ball_minus_topline_px"],
                )
                for frame_index in record["frame_indices"]
            )
        variant_records[name] = {
            "status": variant_carry["status"],
            "reason": variant_carry["reason"],
            "max_carried_offset_movement_px": movement,
        }
    return {"status": "ok", "variants": variant_records}


def _interval_error_px(value_mm: float, low_mm: float, high_mm: float, scale: float) -> float:
    if value_mm < low_mm:
        return (low_mm - value_mm) / scale
    if value_mm > high_mm:
        return (value_mm - high_mm) / scale
    return 0.0


def _checks(  # pylint: disable=too-many-branches
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    consistency_rows: list[dict[str, Any]] = []
    consistency_passes = 0
    availability = 0
    residuals: list[float] = []
    kept_fractions: list[float] = []
    topline_deltas: list[float] = []
    template_heights: list[float] = []
    observed_heights: list[float] = []
    for record in records:
        carry = record["carry_to_contact"]
        complete = carry["status"] == "ok"
        availability += int(complete)
        spread = carry["spread_px"]
        passed = complete and spread <= 1.0
        consistency_passes += int(passed)
        consistency_rows.append({"shot": record["shot"], "spread_px": spread, "pass": passed})
        for frame_index in record["frame_indices"]:
            runtime = record["_runtime"][frame_index]
            result = runtime["alignment"]
            if result.status == "ok":
                residuals.append(float(result.residual_px))
            if result.template is not None:
                kept_fractions.append(result.template.boundary_fraction_kept)
            sanity = runtime["sanity"]
            if sanity["topline_minus_ridge_px"] is not None:
                topline_deltas.append(float(sanity["topline_minus_ridge_px"]))
            if sanity["template_height_px"] is not None:
                template_heights.append(float(sanity["template_height_px"]))
            if sanity["observed_dark_height_px"] is not None:
                observed_heights.append(float(sanity["observed_dark_height_px"]))

    shot_014 = next(record for record in records if record["shot_number"] == 14)
    hand_rows: dict[str, Any] = {}
    hand_pass = shot_014["carry_to_contact"]["status"] == "ok"
    if hand_pass:
        scale = shot_014["plate_scale_mm_per_px"]
        for frame_index in shot_014["frame_indices"]:
            carried = shot_014["carry_to_contact"]["frames"][str(frame_index)]
            midpoint_error = _interval_error_px(
                carried["ball_minus_midpoint_mm"], -24.0, -18.0, scale
            )
            topline_error = _interval_error_px(carried["ball_minus_topline_mm"], 16.0, 19.0, scale)
            hand_rows[str(frame_index)] = {
                "ball_minus_midpoint_mm": carried["ball_minus_midpoint_mm"],
                "ball_minus_topline_mm": carried["ball_minus_topline_mm"],
                "midpoint_interval_error_px": midpoint_error,
                "topline_interval_error_px": topline_error,
            }
            hand_pass = hand_pass and max(midpoint_error, topline_error) <= 1.5

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
            sensitivity = record["sensitivity"]
            if sensitivity["status"] != "ok":
                continue
            for name in names:
                movement = sensitivity["variants"][name]["max_carried_offset_movement_px"]
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
    return {
        "three_frame_consistency": {
            "passing_shots": consistency_passes,
            "total_shots": len(records),
            "fraction": fraction,
            "pass": fraction >= 0.8,
            "per_shot": consistency_rows,
        },
        "hand_mark_shot_014": {"frames": hand_rows, "pass": hand_pass},
        "availability": {
            "complete_shots": availability,
            "total_shots": len(records),
            "pass": availability >= 17,
        },
        "sensitivity": sensitivity_checks,
        "accepted_residual_diagnostic": {
            "median_px": float(np.median(residuals)) if residuals else None,
            "n_frames": len(residuals),
        },
        "boundary_fraction_kept": {
            "median": float(np.median(kept_fractions)),
            "minimum": min(kept_fractions),
            "maximum": max(kept_fractions),
            "n_templates": len(kept_fractions),
        },
        "sanity": {
            "topline_minus_ridge_median_px": float(np.median(topline_deltas)),
            "template_height_median_px": float(np.median(template_heights)),
            "observed_dark_height_median_px": float(np.median(observed_heights)),
        },
    }


def _print_table(checks: dict[str, Any], output: Path) -> None:
    print("\nIteration 2 pre-registered checks")
    print("check | result | threshold | pass")
    print("--- | --- | --- | ---")
    consistency = checks["three_frame_consistency"]
    print(
        "three-frame carried consistency | "
        f"{consistency['passing_shots']}/{consistency['total_shots']} "
        f"({consistency['fraction']:.1%}) | >= 80% within 1 px | {consistency['pass']}"
    )
    hand = checks["hand_mark_shot_014"]
    hand_values = [
        max(row["midpoint_interval_error_px"], row["topline_interval_error_px"])
        for row in hand["frames"].values()
    ]
    hand_max = max(hand_values) if hand_values else float("nan")
    print(f"shot 014 hand intervals | max error {hand_max:.3f} px | <= 1.5 px | {hand['pass']}")
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
    kept = checks["boundary_fraction_kept"]
    print(
        "non-sole boundary retained | "
        f"median {kept['median']:.1%}, range {kept['minimum']:.1%}-{kept['maximum']:.1%} | "
        "report | True"
    )
    print(
        "look | inspect "
        f"{output / 'sheet_f69.png'}, {output / 'sheet_f70.png'}, "
        f"{output / 'sheet_f71.png'}, and {output / 'sheet_contact.png'}"
    )


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
    frame_indices = records[0]["frame_indices"]
    if any(record["frame_indices"] != frame_indices for record in records):
        parser.error("pre-contact alignment frame indices differ across shots")

    for frame_index in frame_indices:
        _render_sheet(records, frame_index, output / f"sheet_f{frame_index}.png")
    _render_contact_sheet(records, output / "sheet_contact.png")
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
        "template_boundary": (
            "ball-overlap pixels excluded, then downward-dominant sole normals discarded"
        ),
        "transverse_carry": (
            "linear fit of aligned template bbox centres on f69/f70/f71; each observed "
            "outline carried with fitted px/frame velocity to exact f_contact"
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

"""The unit's camera tilt offset, calibrated from the median of validated camera-radar pairs.

World pitch = LIS3DH pitch now + a fixed offset (the camera's pitch on the enclosure plus
the principal point's row), stored per unit and lens relative to the LIS3DH reading so the
enclosure's tilt is never counted twice. Out-of-bound solves are refused, never stored.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np

from openflight.constants import BALL_DIAMETER_M

SCHEMA = "openflight.camera_vertical_offset.v1"
FILE_NAME = "camera-vertical-offset.json"
MAX_OFFSET_DEG = 8.0
# a later validated pair more than this far from the stored offset is flagged
CHECK_TOLERANCE_DEG = 1.5
# searched well past the bound, so an implausible solve is refused with its value
SEARCH_SPAN_DEG = 25.0
# what one ball's geometry leaves uncertain besides the radar's own range
PIXEL_UNCERTAINTY_PX = 1.0
LENS_HEIGHT_UNCERTAINTY_M = 0.005
COMPOSITION = "pitch = lis3dh_camera_pitch + offset (same lateral axis; roll not applied)"
LABEL = "unit_calibration_from_validated_pairs"
# validated pairs that must agree (within CHECK_TOLERANCE_DEG of their median) to calibrate
MIN_PAIRS = 2
# this many checks in a row disagreeing on the same side re-calibrate from their median
RECALIBRATE_AFTER = 2
# The setup's nominal camera's angular uncertainty: the camera's vertical before the unit's
# tilt is calibrated (about 3 deg, indoor-test-1), and at least this much after.
UNCALIBRATED_ANGULAR_UNCERTAINTY_DEG = 3.0
CALIBRATED_ANGULAR_UNCERTAINTY_DEG = 0.5


def camera_tilt_policy() -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "solve": "pitch_whose_ray_meets_the_radar_distance_at_the_ball_centre_height",
        "offset": "solved_pitch_minus_lis3dh_pitch_at_the_solve",
        "composition": COMPOSITION,
        "bound_deg": MAX_OFFSET_DEG,
        "check_tolerance_deg": CHECK_TOLERANCE_DEG,
        "min_pairs": MIN_PAIRS,
        "recalibrate_after": RECALIBRATE_AFTER,
        "stored": "per_unit_and_lens_local_calibration_file_not_the_rig_file",
        "updates": "median_of_agreeing_validated_pairs_calibrates_later_pairs_check",
        "label": LABEL,
    }


def _height_error(
    camera_at_pitch: Callable[[float], Any],
    pitch_deg: float,
    pixel: Sequence[float],
    radar_range_m: float,
    ball_center_height_m: float,
) -> float:
    """How far above the ball's centre the ray meets the radar's distance, at this pitch."""
    camera = camera_at_pitch(pitch_deg)
    ray = np.asarray(camera.ray_model.rays(np.asarray(pixel, dtype=float)), dtype=float)
    lens = np.asarray(camera.camera_origin_lfu, dtype=float)
    offset = np.asarray(camera.radar_origin_lfu, dtype=float) - lens
    along = float(np.dot(ray, offset))
    discriminant = along * along - float(np.dot(offset, offset)) + radar_range_m**2
    if discriminant < 0.0:
        raise ValueError("the radar's distance is shorter than the lens-to-radar offset")
    distance = along + math.sqrt(discriminant)
    return float(lens[2] + distance * ray[2] - ball_center_height_m)


def _solve_pitch(
    camera_at_pitch: Callable[[float], Any],
    pixel: Sequence[float],
    radar_range_m: float,
    ball_center_height_m: float,
    around_deg: float,
) -> float:
    low, high = around_deg - SEARCH_SPAN_DEG, around_deg + SEARCH_SPAN_DEG
    f_low = _height_error(camera_at_pitch, low, pixel, radar_range_m, ball_center_height_m)
    f_high = _height_error(camera_at_pitch, high, pixel, radar_range_m, ball_center_height_m)
    if f_low * f_high > 0.0:
        raise ValueError("no camera pitch within reach puts this ball at the radar's distance")
    for _ in range(60):
        middle = 0.5 * (low + high)
        f_middle = _height_error(
            camera_at_pitch, middle, pixel, radar_range_m, ball_center_height_m
        )
        if f_low * f_middle <= 0.0:
            high = middle
        else:
            low, f_low = middle, f_middle
    return 0.5 * (low + high)


def solve_vertical_offset(  # pylint: disable=too-many-arguments
    camera_at_pitch: Callable[[float], Any],
    pixel: Sequence[float],
    *,
    radar_range_m: float,
    radar_uncertainty_m: float,
    lis3dh_pitch_deg: float,
    ball_center_height_m: float = BALL_DIAMETER_M / 2.0,
) -> dict:
    """The camera's vertical offset from one ball the radar's distance confirmed.

    ``camera_at_pitch(pitch_deg)`` builds the nominal camera at a trial pitch (the
    rig's lens height and origins, the LIS3DH's roll convention).
    """
    pixel = [float(value) for value in pixel]
    pitch = _solve_pitch(
        camera_at_pitch, pixel, radar_range_m, ball_center_height_m, lis3dh_pitch_deg
    )
    step = max(float(radar_uncertainty_m), 0.005)
    parts = {
        "radar_range_deg": abs(
            _solve_pitch(camera_at_pitch, pixel, radar_range_m + step, ball_center_height_m, pitch)
            - pitch
        )
        * float(radar_uncertainty_m)
        / step,
        "pixel_row_deg": abs(
            _solve_pitch(
                camera_at_pitch,
                [pixel[0], pixel[1] + PIXEL_UNCERTAINTY_PX],
                radar_range_m,
                ball_center_height_m,
                pitch,
            )
            - pitch
        ),
        "lens_height_deg": abs(
            _solve_pitch(
                camera_at_pitch,
                pixel,
                radar_range_m,
                ball_center_height_m - LENS_HEIGHT_UNCERTAINTY_M,
                pitch,
            )
            - pitch
        ),
    }
    return {
        "offset_deg": pitch - float(lis3dh_pitch_deg),
        "pitch_deg": pitch,
        "lis3dh_pitch_deg": float(lis3dh_pitch_deg),
        "uncertainty_deg": math.sqrt(sum(value * value for value in parts.values())),
        "uncertainty_parts_deg": parts,
        "radar_range_m": float(radar_range_m),
        "pixel_px": pixel,
    }


def _read(path: Path | None) -> dict | None:
    if path is None:
        return None
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, Mapping) or payload.get("schema") != SCHEMA:
        return None
    return dict(payload)


def load_calibration(path: Path | None) -> dict | None:
    """The unit's stored offset, or None (uncalibrated, pending pairs, or unreadable)."""
    payload = _read(path)
    if payload is None or payload.get("status") != "calibrated":
        return None
    offset = payload.get("offset_deg")
    if isinstance(offset, bool) or not isinstance(offset, (int, float)):
        return None
    if not math.isfinite(float(offset)) or abs(float(offset)) > MAX_OFFSET_DEG:
        return None
    return payload


def _write(path: Path, payload: Mapping) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(dict(payload), handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _median(values: Sequence[float]) -> float:
    return float(np.median(np.asarray(values, dtype=float)))


def _agreeing(entries: Sequence[Mapping]) -> list[Mapping]:
    """The pairs within half the check tolerance of their median."""
    if not entries:
        return []
    middle = _median([float(entry["offset_deg"]) for entry in entries])
    return [
        entry
        for entry in entries
        if abs(float(entry["offset_deg"]) - middle) <= CHECK_TOLERANCE_DEG / 2.0
    ]


def _calibrated(
    payload: dict,
    support: Sequence[Mapping],
    now: str,
    method: str,
    over: Sequence[Mapping] | None = None,
) -> dict:
    """The calibration: the median over ``over`` (default ``support``)."""
    offsets = [float(entry["offset_deg"]) for entry in (over if over is not None else support)]
    spread = float(np.std(offsets)) if len(offsets) > 1 else 0.0
    single = [float(entry.get("uncertainty_deg") or 0.0) for entry in support]
    return {
        **payload,
        "label": LABEL,
        "status": "calibrated",
        "method": method,
        "offset_deg": _median(offsets),
        "uncertainty_deg": math.hypot(
            spread, (_median(single) if single else 0.0) / math.sqrt(max(len(support), 1))
        ),
        "calibrated_at_utc": now,
        "support": [entry.get("epoch_id") for entry in support],
        "checks": [],
    }


def record_pair(
    path: Path,
    solved: Mapping,
    *,
    facts: Mapping | None = None,
    method: str = "median_of_validated_pairs",
) -> dict:
    """Add a validated pair: calibrate from the median, check, or re-calibrate.

    Returns ``action``: pending, calibrated, checked (warns past ``CHECK_TOLERANCE_DEG``),
    recalibrated (``RECALIBRATE_AFTER`` same-side disagreements) or refused (not stored).
    """
    path = Path(path)
    offset = float(solved["offset_deg"])
    now = datetime.now(timezone.utc).isoformat()
    entry = {
        **{key: solved.get(key) for key in ("offset_deg", "uncertainty_deg", "pitch_deg")},
        "lis3dh_pitch_deg": solved.get("lis3dh_pitch_deg"),
        "at_utc": now,
        **dict(facts or {}),
    }
    if not math.isfinite(offset) or abs(offset) > MAX_OFFSET_DEG:
        return {
            "action": "refused",
            "offset_deg": offset,
            "warning": (
                f"The camera's tilt solved at {offset:+.1f} deg, beyond +-{MAX_OFFSET_DEG:g} "
                "deg: it was not stored. The ball, the radar's distance or the lens height "
                "is probably wrong; check the unit stands on the hitting surface."
            ),
        }
    payload = _read(path) or {
        "schema": SCHEMA,
        "status": "pending",
        "bound_deg": MAX_OFFSET_DEG,
        "min_pairs": MIN_PAIRS,
        "composition": COMPOSITION,
        "policy": camera_tilt_policy(),
        "pairs": [],
        "previous": [],
    }
    payload["pairs"] = [*list(payload.get("pairs") or []), entry]
    stored = load_calibration(path)
    if stored is None:
        support = _agreeing(payload["pairs"])
        if len(support) < MIN_PAIRS:
            payload["status"] = "pending"
            _write(path, payload)
            spread = (
                max(float(item["offset_deg"]) for item in payload["pairs"])
                - min(float(item["offset_deg"]) for item in payload["pairs"])
                if len(payload["pairs"]) > 1
                else 0.0
            )
            return {
                "action": "pending",
                "offset_deg": offset,
                "pairs": len(payload["pairs"]),
                "pairs_needed": MIN_PAIRS - len(support),
                "warning": (
                    f"The setups' camera tilts disagree by {spread:.1f} deg: another setup "
                    "is needed before the tilt is calibrated."
                    if len(payload["pairs"]) >= MIN_PAIRS
                    else None
                ),
                "path": str(path),
            }
        calibrated = _calibrated(payload, support, now, method, over=payload["pairs"])
        _write(path, calibrated)
        return {
            "action": "calibrated",
            "offset_deg": calibrated["offset_deg"],
            "pairs": len(support),
            "warning": None,
            "path": str(path),
        }
    residual = offset - float(stored["offset_deg"])
    disagrees = abs(residual) > CHECK_TOLERANCE_DEG
    checks = [*list(stored.get("checks") or []), {**entry, "residual_deg": residual}]
    recent = checks[-RECALIBRATE_AFTER:]
    if (
        disagrees
        and len(recent) == RECALIBRATE_AFTER
        and all(abs(float(item["residual_deg"])) > CHECK_TOLERANCE_DEG for item in recent)
        and len({math.copysign(1.0, float(item["residual_deg"])) for item in recent}) == 1
    ):
        previous = {
            key: stored.get(key)
            for key in ("offset_deg", "uncertainty_deg", "calibrated_at_utc", "support")
        }
        payload = {
            **stored,
            "pairs": payload["pairs"],
            "previous": [*list(stored.get("previous") or []), previous],
        }
        calibrated = _calibrated(payload, recent, now, "median_of_recent_disagreeing_pairs")
        _write(path, calibrated)
        return {
            "action": "recalibrated",
            "offset_deg": calibrated["offset_deg"],
            "residual_deg": residual,
            "warning": (
                f"The last {RECALIBRATE_AFTER} setups put the camera's tilt at "
                f"{calibrated['offset_deg']:+.1f} deg, not the stored "
                f"{float(previous['offset_deg']):+.1f} deg: the tilt was re-calibrated. The "
                "camera may have moved in the enclosure."
            ),
            "path": str(path),
        }
    warning = (
        f"This setup's ball puts the camera's tilt at {offset:+.1f} deg; the unit's "
        f"calibration says {float(stored['offset_deg']):+.1f} deg: it disagrees by "
        f"{abs(residual):.1f} deg. The unit may be raised or tilted differently, or the "
        "calibration is stale."
        if disagrees
        else None
    )
    _write(path, {**stored, "pairs": payload["pairs"], "checks": checks})
    return {
        "action": "checked",
        "offset_deg": float(stored["offset_deg"]),
        "residual_deg": residual,
        "warning": warning,
        "path": str(path),
    }


def applied_pitch(lis3dh_pitch_deg: float, calibration: Mapping | None) -> dict:
    """The camera's pitch this session: the LIS3DH's, composed with the unit's offset."""
    lis3dh = float(lis3dh_pitch_deg)
    if calibration is None:
        return {
            "pitch_deg": lis3dh,
            "lis3dh_pitch_deg": lis3dh,
            "offset_deg": None,
            "status": "uncalibrated",
            "composition": COMPOSITION,
        }
    offset = float(calibration["offset_deg"])
    return {
        "pitch_deg": lis3dh + offset,
        "lis3dh_pitch_deg": lis3dh,
        "offset_deg": offset,
        "uncertainty_deg": calibration.get("uncertainty_deg"),
        "status": "calibrated",
        "label": LABEL,
        "calibrated_at_utc": calibration.get("calibrated_at_utc"),
        "composition": COMPOSITION,
    }


SWING_POSE_SCHEMA = "openflight.camera_swing_pose.v1"


def _for_camera(inclinometer: Mapping | None) -> bool:
    """Whether this swing's LIS3DH reading was handed to the camera."""
    return isinstance(inclinometer, Mapping) and "camera" in (inclinometer.get("applied_to") or ())


def _stable_pitch(inclinometer: Mapping | None) -> float | None:
    """This swing's own calibrated LIS3DH pitch, when the camera took its reading."""
    if not _for_camera(inclinometer):
        return None
    pitch = inclinometer.get("calibrated_pitch_deg")
    if isinstance(pitch, bool) or not isinstance(pitch, (int, float)):
        return None
    return float(pitch) if math.isfinite(float(pitch)) else None


def swing_camera_pose(
    *,
    rig: Any,
    image_width_px: int,
    inclinometer: Mapping | None,
    calibration: Mapping | None,
) -> dict:
    """The camera pose one swing measures with, composed exactly as the setup's.

    Pitch: the rig's boresight pitch moved by this swing's LIS3DH departure from the
    rig's expected reading, plus the unit's offset; focal length: the rig's for this mode.
    Without all three inputs the pose says why and stays uncalibrated. Roll goes
    through ``camera_roll`` and is applied only when its gate is on.
    """
    from openflight.camera.ball_pixels import mode_focal_px  # noqa: PLC0415

    pose: dict[str, Any] = {
        "schema": SWING_POSE_SCHEMA,
        "status": "uncalibrated",
        "pitch_deg": None,
        "focal_px": None,
        "composition": COMPOSITION,
        "roll": _swing_roll(rig, inclinometer),
    }
    reasons = []
    if calibration is None:
        reasons.append("no unit camera tilt calibration (P8-5)")
    if rig is None:
        reasons.append("no rig geometry")
    lis3dh_pitch = _stable_pitch(inclinometer)
    if lis3dh_pitch is None:
        reasons.append("no stable LIS3DH reading for this shot")
    if reasons:
        pose["reason"] = "; ".join(reasons) + ": pitch and focal length inferred per shot"
        return pose
    expected = rig.expected_inclinometer_orientation().pitch_deg or 0.0
    camera_pitch = float(rig.boresight_pitch_deg) + (lis3dh_pitch - float(expected))
    applied = applied_pitch(camera_pitch, calibration)
    pose.update(
        {
            "status": "calibrated",
            "label": LABEL,
            "pitch_deg": applied["pitch_deg"],
            "lis3dh_camera_pitch_deg": camera_pitch,
            "offset_deg": applied["offset_deg"],
            "uncertainty_deg": applied.get("uncertainty_deg"),
            "calibrated_at_utc": applied.get("calibrated_at_utc"),
            "focal_px": mode_focal_px(int(image_width_px), rig),
            "focal_source": "rig_focal_for_the_mode",
        }
    )
    return pose


def _swing_roll(rig: Any, inclinometer: Mapping | None) -> dict:
    """This swing's LIS3DH roll, through the one convention setup and swings share."""
    from openflight.camera import camera_roll  # noqa: PLC0415

    applied = camera_roll.roll_applied()
    gravity = (
        tuple(inclinometer.get(axis) for axis in ("x_g", "y_g", "z_g"))
        if _for_camera(inclinometer)
        else (None, None, None)
    )
    measured = (
        camera_roll.lis3dh_roll_deg(*gravity)
        if all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in gravity)
        else None
    )
    expected = (rig.expected_inclinometer_orientation().roll_deg or 0.0) if rig is not None else 0.0
    record = {
        "measured_deg": measured,
        "expected_deg": expected,
        "applied": False,
        "roll_correction_deg": 0.0,
        "status": "not_applied_pending_c8",
    }
    if not applied:
        return record
    if measured is None:
        record["status"] = "not_applied_no_lis3dh_reading"
        return record
    camera_roll_deg = camera_roll.applied_camera_roll_deg(measured, expected, apply=True)
    record.update(
        applied=True,
        status="applied",
        roll_correction_deg=camera_roll.nominal_roll_correction_deg(camera_roll_deg),
    )
    return record


def with_swing_pose(geometry: Any, pose: Mapping | None) -> Any:
    """A nominal camera geometry with this swing's calibrated pose, if it has one.

    A calibrated optical model carries its own pose and is left as it is.
    """
    if pose is None or getattr(geometry, "calibrated_model", None) is not None:
        return geometry
    from dataclasses import replace  # noqa: PLC0415

    changes: dict[str, Any] = {}
    roll = pose.get("roll") or {}
    if roll.get("applied"):
        changes["roll_correction_deg"] = float(geometry.roll_correction_deg) + float(
            roll["roll_correction_deg"]
        )
    if pose.get("status") == "calibrated":
        changes["setup_pitch_deg"] = float(pose["pitch_deg"])
        changes["setup_focal_px"] = float(pose["focal_px"])
    return replace(geometry, **changes) if changes else geometry

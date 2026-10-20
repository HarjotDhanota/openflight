"""The unit's camera tilt, calibrated from the median of validated pairs (P8-5).

The nominal camera model takes its pitch from the LIS3DH and puts the principal
point at the image centre. Neither is the camera's true vertical: the camera is
mounted in the enclosure at a small pitch of its own, and the lens's centre sits
some rows off the image's middle. At 95 mm above the ground both move a ball's
row a long way (indoor-test-1: the floor met the door about 50 px lower
than the level, image-centred model predicts, about 3 deg).

Each ball the camera and radar agree on (P8-4) measures the two together as one
vertical offset: the pitch the camera must have for the ball's row to be where
the radar's distance, the lens height and the ball's radius put it, less the
LIS3DH's pitch at the time. One pair never calibrates the unit: the offset is the
median of at least ``MIN_PAIRS`` validated pairs that agree (a single wrong pair,
indoor-test-1's, once calibrated a unit for good). It is stored per unit
and per lens, beside the unit's other local settings (``~/.config/openflight/``),
never in the shared rig file.

**Composition.** The camera's pitch in the world is the enclosure's pitch (the
LIS3DH, read each session) composed with the camera's fixed pitch on the
enclosure plus the principal point's row (this offset): world <- enclosure <-
camera. Both are rotations about the same lateral axis (the LIS3DH roll is
recorded but not applied until C8, camera_roll), so they compose by adding their angles:
``pitch = LIS3DH pitch now + offset``. The offset is stored relative to the
LIS3DH reading it was solved at, so a unit set down at another tilt the next day
gets that day's LIS3DH pitch plus the same offset, never the first day's whole
solved pitch added on top of today's reading, which would count the enclosure's
tilt twice. The principal point's row is treated as a pitch: exact at the
image's middle column, and within a few hundredths of a degree across the rows
a ball at address uses.

The offset is bounded (``MAX_OFFSET_DEG``); a solve beyond it is refused with a
warning, since it means the ball, the radar's distance or the lens height is
wrong, not the camera. Later validated pairs check it and warn when they
disagree; ``RECALIBRATE_AFTER`` disagreeing checks in a row, on the same side,
re-calibrate from their median and say so; ``reset_calibration`` starts over (a
lens change). Nothing is replaced silently, and there is no typed input.
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

SCHEMA = "openflight.camera_vertical_offset.v1"
FILE_NAME = "camera-vertical-offset.json"
MAX_OFFSET_DEG = 8.0
# a later validated pair more than this far from the stored offset is flagged
CHECK_TOLERANCE_DEG = 1.5
# the solve searches this far either side of the LIS3DH's pitch, well past the
# bound, so an implausible solve is refused with its value rather than lost
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


def default_calibration_path() -> Path:
    """Where this unit keeps its offset: with its other local settings, not the rig file."""
    return Path.home() / ".config" / "openflight" / FILE_NAME


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
    ball_center_height_m: float = 0.04267 / 2.0,
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


def calibration_status(path: Path | None) -> dict:
    """What the unit's tilt file says: calibrated, pending pairs, or uncalibrated."""
    payload = _read(path)
    if payload is None:
        return {"status": "uncalibrated", "pairs": 0, "pairs_needed": MIN_PAIRS}
    pairs = list(payload.get("pairs") or [])
    calibrated = load_calibration(path) is not None
    return {
        "status": "calibrated" if calibrated else "pending",
        "offset_deg": payload.get("offset_deg") if calibrated else None,
        "pairs": len(pairs),
        "pairs_needed": 0 if calibrated else max(0, MIN_PAIRS - len(pairs)),
        "calibrated_at_utc": payload.get("calibrated_at_utc") if calibrated else None,
    }


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
    """The pairs that agree with their median: within half the check tolerance of it,
    so any two of them lie within the tolerance of each other."""
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
    """The calibration: the median over ``over`` (every pair by default), once
    ``support`` (the pairs that agree) is large enough."""
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

    ``method`` names the calibration: ``median_of_validated_pairs`` for setup pairs
    (before 13 Oct), ``median_of_swing_pairs`` for swings (each swing's IWR launch
    point and the resting ball's row, openflight.camera.swing_tilt).

    Returns what happened: ``pending`` (too few agreeing pairs yet),
    ``calibrated`` (the median of ``MIN_PAIRS`` or more that agree), ``checked``
    (a later pair, with a warning when it disagrees by more than
    ``CHECK_TOLERANCE_DEG``), ``recalibrated`` (``RECALIBRATE_AFTER`` checks in a
    row disagreed on the same side) or ``refused`` (beyond ``MAX_OFFSET_DEG``,
    not stored).
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


def reset_calibration(path: Path | None, *, reason: str = "reset") -> dict:
    """Forget the unit's tilt (a lens change, or the tester asks); it is calibrated again."""
    payload = _read(path)
    if path is None or payload is None:
        return {"action": "nothing_to_reset", "previous_offset_deg": None}
    archive = Path(path).with_name(
        f"{Path(path).stem}.reset-{int(datetime.now().timestamp())}.json"
    )
    _write(archive, {**payload, "reset_reason": reason})
    Path(path).unlink(missing_ok=True)
    return {
        "action": "reset",
        "previous_offset_deg": payload.get("offset_deg"),
        "previous_status": payload.get("status"),
        "archived_to": str(archive),
        "reason": reason,
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


def _stable_pitch(inclinometer: Mapping | None) -> float | None:
    """This swing's own calibrated LIS3DH pitch, when its reading was stable."""
    if not isinstance(inclinometer, Mapping) or inclinometer.get("applied") is not True:
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
    """The camera pose one swing measures with, composed exactly as the setup's (D36 item 4).

    The camera's LIS3DH pitch is the rig's boresight pitch moved by how far this
    swing's own reading departs from the placement the rig expects (the tester's
    ``camera_pitch_deg``); ``applied_pitch`` composes it with the unit's offset;
    the focal length is the rig's for this mode (``ball_pixels.mode_focal_px``).
    Both are once-measured calibration. Without the unit's calibration, a stable
    reading or a rig, the pose says why and the estimators keep inferring the
    pitch and focal length from the resting ball, labelled uncalibrated.

    The roll (item 4c) goes through ``camera_roll`` as the setup's does: this
    swing's LIS3DH roll less the rig's expected one, as a clockwise image-roll
    correction. While the gate (``camera_roll.LIS3DH_ROLL_APPLIED``, C8) is off
    it is recorded and not applied.
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
        if isinstance(inclinometer, Mapping) and inclinometer.get("applied") is True
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

    The roll correction is added to the geometry's configured one, and only once
    the gate applies it. A calibrated optical model carries its own pose (its roll
    goes through the same gate) and is left as it is.
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

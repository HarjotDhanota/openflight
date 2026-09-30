"""The unit's camera tilt, calibrated from the first validated pair (P8-5).

The nominal camera model takes its pitch from the LIS3DH and puts the principal
point at the image centre. Neither is the camera's true vertical: the camera is
mounted in the enclosure at a small pitch of its own, and the lens's centre sits
some rows off the image's middle. At 95 mm above the ground both move a ball's
row a long way (harjot-indoor-test-1: the floor met the door about 50 px lower
than the level, image-centred model predicts, about 3 deg).

The first ball the camera and radar agree on (P8-4) measures the two together as
one vertical offset: the pitch the camera must have for the ball's row to be
where the radar's distance, the lens height and the ball's radius put it, less
the LIS3DH's pitch at the time. It is stored per unit, beside the unit's other
local settings (``~/.config/openflight/``), never in the shared rig file.

**Composition.** The camera's pitch in the world is the enclosure's pitch (the
LIS3DH, read each session) composed with the camera's fixed pitch on the
enclosure plus the principal point's row (this offset): world <- enclosure <-
camera. Both are rotations about the same lateral axis (the LIS3DH roll is
recorded but not applied, camera_roll), so they compose by adding their angles:
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
disagree; nothing is replaced silently, and there is no typed input.
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
LABEL = "unit_calibration_from_a_validated_pair"


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
        "stored": "per_unit_local_calibration_file_not_the_rig_file",
        "updates": "first_validated_pair_calibrates_later_pairs_check",
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


def load_calibration(path: Path | None) -> dict | None:
    """The unit's stored offset, or None (uncalibrated, or an unreadable file)."""
    if path is None:
        return None
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, Mapping) or payload.get("schema") != SCHEMA:
        return None
    offset = payload.get("offset_deg")
    if isinstance(offset, bool) or not isinstance(offset, (int, float)):
        return None
    if not math.isfinite(float(offset)) or abs(float(offset)) > MAX_OFFSET_DEG:
        return None
    return dict(payload)


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


def record_pair(path: Path, solved: Mapping, *, facts: Mapping | None = None) -> dict:
    """Calibrate from a validated pair, or check the stored offset against it.

    Returns what happened: ``calibrated`` (the first pair), ``checked`` (a later
    one, with a warning when it disagrees by more than ``CHECK_TOLERANCE_DEG``) or
    ``refused`` (beyond ``MAX_OFFSET_DEG``, not stored).
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
    stored = load_calibration(path)
    if stored is None:
        payload = {
            "schema": SCHEMA,
            "label": LABEL,
            "status": "calibrated",
            "offset_deg": offset,
            "uncertainty_deg": solved.get("uncertainty_deg"),
            "calibrated_at_utc": now,
            "source": entry,
            "bound_deg": MAX_OFFSET_DEG,
            "composition": COMPOSITION,
            "policy": camera_tilt_policy(),
            "checks": [],
        }
        _write(path, payload)
        return {"action": "calibrated", "offset_deg": offset, "warning": None, "path": str(path)}
    residual = offset - float(stored["offset_deg"])
    warning = (
        f"This setup's ball puts the camera's tilt at {offset:+.1f} deg; the unit's "
        f"calibration says {float(stored['offset_deg']):+.1f} deg: it disagrees by "
        f"{abs(residual):.1f} deg. The unit may be raised or tilted differently, or the "
        "calibration is stale."
        if abs(residual) > CHECK_TOLERANCE_DEG
        else None
    )
    stored["checks"] = [*list(stored.get("checks") or []), {**entry, "residual_deg": residual}]
    _write(path, stored)
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

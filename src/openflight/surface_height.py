"""The hitting surface's height above the enclosure's base, solved from one shot.

Nothing is typed in: the shot's own measured IWR launch-point range and its resting
ball's pixel give the lens height above the surface the ball rests on (the setup's
ray-meets-range geometry, ``reference_ball_range.ray_meets_radar_range``), and so the
surface's height above the base the unit stands on.

Sigma has two parts. The per-shot part (radar range, the LIS3DH reading, the ball's
centroid) averages down over shots; the camera's shared part (where its optical axis
points: an uncalibrated principal point and the camera-to-LIS3DH mount) does not.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

import numpy as np

from openflight.camera.ground_patch import SIZE_TOLERANCE
from openflight.constants import BALL_DIAMETER_M

SCHEMA = "openflight.surface_height.v1"
# Sanity bounds on the surface above the base: a unit on a 30 cm box, or a mat 30 cm up,
# is the most any legitimate setup does; beyond it the object or the range is wrong.
SURFACE_HEIGHT_BOUNDS_M = (-0.30, 0.30)
# Where the optical axis points without a checkerboard calibration: the principal
# point taken at the image centre (an M12 lens centred to +-0.1 mm is +-33 px of a 3 um
# sensor, ~1 deg at 6 mm) and the camera's mount on the LIS3DH's plane. The setup's
# radar lens-height solve states the same 1 deg (``patch_pair_lens_height``).
UNCALIBRATED_ANGULAR_SIGMA_DEG = 1.0
# The LIS3DH pitch reading's own scatter when the shot's record carries none.
DEFAULT_PITCH_READING_SIGMA_DEG = 0.1
# The resting ball's centroid, per shot.
CENTROID_SIGMA_PX = 0.5
# The only range that may solve the surface: the shot's own IWR launch point, measured
# (never the camera prior, which would be circular, nor two competing walks).
RANGE_SOURCE = "iwr_post_impact"
MEASURED_LABEL = "measured"

PER_SHOT_SOURCE = "per_shot"


def _finite(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _refused(code: str, reason: str, inputs: Mapping[str, Any], **extra: Any) -> dict:
    return {
        "schema": SCHEMA,
        "status": "refused",
        "reason_code": code,
        "reason": reason,
        "surface_height_m": None,
        "sigma_m": None,
        "random_sigma_m": None,
        "systematic_sigma_m": None,
        "lens_above_surface_m": None,
        "inputs": dict(inputs),
        **extra,
    }


def solve_shot(  # pylint: disable=too-many-arguments,too-many-locals,too-many-return-statements
    *,
    range_m: float | None,
    range_sigma_m: float | None,
    range_source: str | None,
    range_label: str | None,
    ball_px: Sequence[float] | None,
    ball_diameter_px: float | None,
    focal_px: float | None,
    image_size_px: Sequence[int],
    pitch_deg: float | None,
    roll_correction_deg: float,
    rig_lens_height_m: float,
    radar_offset_lfu_m: Sequence[float],
    angular_sigma_deg: float = UNCALIBRATED_ANGULAR_SIGMA_DEG,
    pitch_reading_sigma_deg: float | None = None,
    size_tolerance: float = SIZE_TOLERANCE,
) -> dict:
    """One shot's hitting-surface height above the base, or why it has none.

    ``pitch_deg`` is the camera's pitch from its LIS3DH and the rig's boresight, never
    one composed with a tilt calibrated from ball pairs (those assume the rig's lens
    height, so the surface would come back as the base). ``radar_offset_lfu_m`` is the
    IWR's phase centre from the lens (lateral, forward, up).
    """
    inputs = {
        "range_m": range_m,
        "range_sigma_m": range_sigma_m,
        "range_source": range_source,
        "range_label": range_label,
        "ball_px": list(ball_px) if ball_px is not None else None,
        "ball_diameter_px": ball_diameter_px,
        "focal_px": focal_px,
        "image_size_px": list(image_size_px),
        "principal_point": "image_centre",
        "pitch_deg": pitch_deg,
        "roll_correction_deg": roll_correction_deg,
        "rig_lens_height_m": rig_lens_height_m,
        "radar_offset_lfu_m": list(radar_offset_lfu_m),
        "angular_sigma_deg": angular_sigma_deg,
    }
    if not _finite(range_m):
        return _refused("no_iwr_range", "the shot has no IWR launch-point range", inputs)
    if range_source != RANGE_SOURCE:
        return _refused(
            "range_not_iwr_measured",
            f"the shot's range is from {range_source or 'nowhere'}, not its own IWR launch "
            "point; a camera range would solve the surface from itself",
            inputs,
        )
    if range_label != MEASURED_LABEL:
        return _refused(
            "range_label_not_measured",
            f"the IWR launch point is {range_label or 'unlabelled'}, not measured",
            inputs,
        )
    if ball_px is None or not _finite(ball_diameter_px) or float(ball_diameter_px) <= 0.0:
        return _refused("no_resting_ball", "no resting ball in the shot's own frames", inputs)
    if not _finite(pitch_deg):
        return _refused("no_camera_pitch", "no stable LIS3DH camera pitch for the shot", inputs)
    if not _finite(focal_px) or float(focal_px) <= 0.0:
        return _refused("no_focal_length", "no focal length for this camera mode", inputs)

    from openflight.camera.reference_ball_range import (  # noqa: PLC0415
        BallPlaneCamera,
        ray_meets_radar_range,
    )

    lens = np.array([0.0, 0.0, float(rig_lens_height_m)])
    camera = BallPlaneCamera.nominal(
        focal_px=float(focal_px),
        image_width_px=int(image_size_px[0]),
        image_height_px=int(image_size_px[1]),
        pitch_deg=float(pitch_deg),
        roll_correction_deg=float(roll_correction_deg),
        mirror_horizontal=False,
        camera_origin_lfu=lens,
        radar_origin_lfu=lens + np.asarray(radar_offset_lfu_m, dtype=float),
        angular_uncertainty_deg=float(angular_sigma_deg),
        focal_relative_uncertainty=0.08,
    )
    try:
        ray, distance = ray_meets_radar_range(camera, ball_px, float(range_m))
    except ValueError as error:
        return _refused("range_misses_the_ray", f"the radar range misses the ray: {error}", inputs)
    if distance <= 0.0:
        return _refused(
            "range_misses_the_ray", "the radar range meets the ray behind the lens", inputs
        )

    # the wrong object: its size must agree with the size its range implies
    expected = float(focal_px) * BALL_DIAMETER_M / distance
    observed = float(ball_diameter_px)
    relative_range = float(range_sigma_m or 0.0) / float(range_m)
    tolerance = math.hypot(size_tolerance, relative_range)
    sizes = {"expected_diameter_px": expected, "observed_diameter_px": observed}
    if not 1.0 / (1.0 + tolerance) <= observed / expected <= 1.0 + tolerance:
        return _refused(
            "ball_size_disagrees_with_range",
            f"the resting ball is {observed:.1f} px across; at {distance:.2f} m it should be "
            f"{expected:.1f} px (+-{tolerance * 100:.0f} %): not the ball the radar ranged",
            inputs,
            ball_distance_m=distance,
            **sizes,
        )

    down = -float(ray[2])
    radius = BALL_DIAMETER_M / 2.0
    lens_above = radius + distance * down
    surface = float(rig_lens_height_m) - lens_above
    reading = (
        float(pitch_reading_sigma_deg)
        if _finite(pitch_reading_sigma_deg)
        else DEFAULT_PITCH_READING_SIGMA_DEG
    )
    random_sigma = math.sqrt(
        (float(range_sigma_m or 0.0) * down) ** 2
        + (distance * math.sin(math.radians(reading))) ** 2
        + (distance * CENTROID_SIGMA_PX / float(focal_px)) ** 2
    )
    systematic_sigma = distance * math.sin(math.radians(float(angular_sigma_deg)))
    solved = {
        "ball_distance_m": distance,
        **sizes,
    }
    if lens_above <= 0.0:
        return _refused(
            "lens_below_surface",
            f"the solve puts the lens {-lens_above * 1000:.0f} mm below the surface it sees",
            inputs,
            **solved,
        )
    low, high = SURFACE_HEIGHT_BOUNDS_M
    if not low <= surface <= high:
        return _refused(
            "surface_height_out_of_bounds",
            f"the solve puts the surface {surface * 1000:.0f} mm from the base, outside "
            f"{low * 1000:.0f} to {high * 1000:.0f} mm",
            inputs,
            **solved,
        )
    return {
        "schema": SCHEMA,
        "status": "solved",
        "reason_code": None,
        "reason": None,
        "surface_height_m": surface,
        "sigma_m": math.hypot(random_sigma, systematic_sigma),
        "random_sigma_m": random_sigma,
        "systematic_sigma_m": systematic_sigma,
        "lens_above_surface_m": lens_above,
        "inputs": inputs,
        **solved,
    }


__all__ = [
    "MEASURED_LABEL",
    "PER_SHOT_SOURCE",
    "RANGE_SOURCE",
    "SIZE_TOLERANCE",
    "SURFACE_HEIGHT_BOUNDS_M",
    "UNCALIBRATED_ANGULAR_SIGMA_DEG",
    "solve_shot",
]

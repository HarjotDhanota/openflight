"""The hitting surface's height above the enclosure's base, solved on every shot.

Nothing is typed in: each shot's own measured IWR launch-point range and its resting
ball's pixel give the lens height above the surface the ball rests on (the setup's
ray-meets-range geometry, ``reference_ball_range.ray_meets_radar_range``), and so the
surface's height above the base the unit stands on. The session combines the shots
(median and MAD outlier rejection, then inverse variance) with the setup's own solve
when there is one.

Heights handed to every consumer are above the HITTING SURFACE: the lens and the radar
move down by the solved height (the radar may come out negative, below the surface)
and the ball's centre stays one radius up. The radar's floor bounce comes from the
lower of the base and the surface, in front of the unit (``reflecting_plane_depth_m``,
how far that plane lies below the hitting surface).

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
# A solved height is used whenever its 1-sigma is at most this: a 15 mm surface error
# moves the radar's vertical launch ~0.5 deg (0.33 deg per 10 mm, Oct 27 replay) and a
# 1.6 m ball's depression ~0.5 deg, inside both sensors' own noise.
SURFACE_HEIGHT_MAX_SIGMA_M = 0.015
# A looser solve is still used when it puts the surface more than this many sigma from
# the base (the rig's assumption): the rig height is then the worse of the two. This
# replaces the setup's fixed 60 mm gross-error floor.
NOMINAL_REJECTION_SIGMAS = 2.0
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
# Outlier rejection: a shot further than this many sigma from the median (sigma = the
# larger of the MAD's and its own per-shot sigma) is not combined.
OUTLIER_SIGMAS = 3.0
MAD_TO_SIGMA = 1.4826
# The only range that may solve the surface: the shot's own IWR launch point, measured
# (never the camera prior, which would be circular, nor two competing walks).
RANGE_SOURCE = "iwr_post_impact"
MEASURED_LABEL = "measured"

PER_SHOT_SOURCE = "per_shot"
SOLVED_PER_SHOT = "solved_per_shot"
SOLVED_SESSION = "solved_session"
RIG_NOMINAL = "rig_nominal"
REFERENCE = "hitting_surface"


def policy() -> dict[str, Any]:
    """Every constant, for the session record."""
    return {
        "schema": SCHEMA,
        "max_sigma_m": SURFACE_HEIGHT_MAX_SIGMA_M,
        "nominal_rejection_sigmas": NOMINAL_REJECTION_SIGMAS,
        "bounds_m": list(SURFACE_HEIGHT_BOUNDS_M),
        "uncalibrated_angular_sigma_deg": UNCALIBRATED_ANGULAR_SIGMA_DEG,
        "default_pitch_reading_sigma_deg": DEFAULT_PITCH_READING_SIGMA_DEG,
        "centroid_sigma_px": CENTROID_SIGMA_PX,
        "size_tolerance": SIZE_TOLERANCE,
        "outlier_sigmas": OUTLIER_SIGMAS,
        "range": f"{RANGE_SOURCE}_{MEASURED_LABEL}_only",
        "aggregate": "median_mad_outliers_then_inverse_variance_shared_sigma_not_averaged",
        "reflecting_plane": "lower_of_base_and_hitting_surface",
    }


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


def _label(item: Mapping) -> dict:
    return {
        key: item.get(key)
        for key in (
            "shot_number",
            "source",
            "surface_height_m",
            "sigma_m",
            "random_sigma_m",
            "systematic_sigma_m",
        )
    }


def aggregate(contributions: Sequence[Mapping]) -> dict:
    """The session's surface height from every accepted solve, with each one's part."""
    solved = [item for item in contributions if item.get("status") == "solved"]
    reasons = [
        {
            "shot_number": item.get("shot_number"),
            "reason_code": item.get("reason_code"),
            "reason": item.get("reason"),
        }
        for item in contributions
        if item.get("status") != "solved"
    ]
    base = {
        "schema": SCHEMA,
        "method": "median_mad_outliers_then_inverse_variance",
        "reasons": reasons,
    }
    if not solved:
        return {
            **base,
            "status": "none",
            "surface_height_m": None,
            "sigma_m": None,
            "random_sigma_m": None,
            "systematic_sigma_m": None,
            "n": 0,
            "n_rejected": 0,
            "contributions": [],
        }
    heights = np.array([float(item["surface_height_m"]) for item in solved])
    random = np.array([max(float(item.get("random_sigma_m") or 0.0), 1e-6) for item in solved])
    median = float(np.median(heights))
    spread = MAD_TO_SIGMA * float(np.median(np.abs(heights - median)))
    keep = np.abs(heights - median) <= OUTLIER_SIGMAS * np.maximum(spread, random)
    weights = 1.0 / random[keep] ** 2
    value = float(np.dot(weights, heights[keep]) / weights.sum())
    random_sigma = float(1.0 / math.sqrt(weights.sum()))
    shared = [float(item.get("systematic_sigma_m") or 0.0) for item, k in zip(solved, keep) if k]
    systematic_sigma = float(np.median(shared))
    return {
        **base,
        "status": "solved",
        "surface_height_m": value,
        "sigma_m": math.hypot(random_sigma, systematic_sigma),
        "random_sigma_m": random_sigma,
        "systematic_sigma_m": systematic_sigma,
        "n": int(keep.sum()),
        "n_rejected": int((~keep).sum()),
        "contributions": [
            {**_label(item), "state": "used" if k else "outlier"} for item, k in zip(solved, keep)
        ],
    }


def use_solved(offset_m: float, sigma_m: float) -> tuple[bool, str]:
    """Whether a solved surface height replaces the rig's (the surface on the base)."""
    if sigma_m <= SURFACE_HEIGHT_MAX_SIGMA_M:
        return (
            True,
            f"sigma {sigma_m * 1000:.0f} mm is within {SURFACE_HEIGHT_MAX_SIGMA_M * 1000:.0f} mm",
        )
    if abs(offset_m) > NOMINAL_REJECTION_SIGMAS * sigma_m:
        return True, (
            f"{offset_m * 1000:+.0f} mm is more than {NOMINAL_REJECTION_SIGMAS:g} sigma "
            f"({sigma_m * 1000:.0f} mm) from the base"
        )
    return False, (
        f"solved {offset_m * 1000:+.0f} +- {sigma_m * 1000:.0f} mm: sigma above "
        f"{SURFACE_HEIGHT_MAX_SIGMA_M * 1000:.0f} mm and within {NOMINAL_REJECTION_SIGMAS:g} "
        "sigma of the base, so the rig's heights stand"
    )


def heights_in_force(
    combined: Mapping,
    *,
    rig_lens_height_m: float,
    rig_radar_height_m: float,
    ball_radius_m: float,
    shot_number: int | None = None,
) -> dict:
    """The lens, radar and ball heights above the hitting surface every consumer uses.

    Labelled ``solved_per_shot`` when the only solve used is this shot's own,
    ``solved_session`` for the session's combination, the setup's own source when only
    the setup solved, else ``rig_nominal`` with the reasons nothing was used.
    """
    surface, sigma, source, reason = 0.0, None, RIG_NOMINAL, None
    if combined.get("status") == "solved":
        used, why = use_solved(float(combined["surface_height_m"]), float(combined["sigma_m"]))
        if used:
            surface, sigma, reason = (
                float(combined["surface_height_m"]),
                float(combined["sigma_m"]),
                why,
            )
            parts = [
                item for item in combined.get("contributions") or () if item["state"] == "used"
            ]
            sources = {item.get("source") for item in parts}
            if sources == {PER_SHOT_SOURCE} and {item.get("shot_number") for item in parts} == {
                shot_number
            }:
                source = SOLVED_PER_SHOT
            elif PER_SHOT_SOURCE in sources:
                source = SOLVED_SESSION
            else:
                source = str(next(iter(sources)) or SOLVED_SESSION)
        else:
            reason = why
    else:
        codes = sorted(
            {item.get("reason_code") or "unknown" for item in combined.get("reasons") or ()}
        )
        reason = (
            "no shot solved the surface (" + ", ".join(codes) + ")"
            if codes
            else "no shot has solved the surface yet"
        )
    return {
        "schema": SCHEMA,
        "reference": REFERENCE,
        "source": source,
        "reason": reason,
        "surface_height_above_base_m": surface,
        "surface_height_sigma_m": sigma,
        "lens_height_m": float(rig_lens_height_m) - surface,
        "radar_height_m": float(rig_radar_height_m) - surface,
        "ball_height_m": float(ball_radius_m),
        "ball_height_basis": "one_radius_above_the_hitting_surface",
        "reflecting_plane_depth_m": max(surface, 0.0),
        "n": int(combined.get("n") or 0),
    }


__all__ = [
    "MEASURED_LABEL",
    "NOMINAL_REJECTION_SIGMAS",
    "PER_SHOT_SOURCE",
    "RANGE_SOURCE",
    "RIG_NOMINAL",
    "SIZE_TOLERANCE",
    "SOLVED_PER_SHOT",
    "SOLVED_SESSION",
    "SURFACE_HEIGHT_BOUNDS_M",
    "SURFACE_HEIGHT_MAX_SIGMA_M",
    "UNCALIBRATED_ANGULAR_SIGMA_DEG",
    "aggregate",
    "heights_in_force",
    "policy",
    "solve_shot",
    "use_solved",
]

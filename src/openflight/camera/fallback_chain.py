"""The best-available-source chains for the ball, shared by the kiosk and its replays.

Ball range: the shot's own IWR launch point (measured), else the camera prior the setup
or the shot's resting-ball look gave (estimated), else the resting ball's own size in
this clip (``resting_ball_range``, experimental). Launch: the radar's (or a measured
camera's), else the camera fallback (``ball_launch_fallback``, experimental). Each is a
``source_chain`` resolution, so every route's refusal is kept and a failing route never
takes the shot down; a later gap is closed by adding a route here.

The kiosk (``server``) and the replays call these same functions, so one shot gives
the same values live and replayed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any, Callable, Mapping, MutableMapping, Optional

import numpy as np

from ..horizontal_frame import implausible_launch_reason
from ..source_chain import Candidate, Resolution, Route, resolve
from ..source_labels import (
    CAMERA_FALLBACK_SOURCE,
    INTRINSICS_CALIBRATED,
    IWR_COMPETING_NOTE,
    IWR_PARTIAL_NOTE,
    LABEL_ESTIMATED,
    LABEL_EXPERIMENTAL,
    LABEL_IWR_PARTIAL,
    LABEL_MEASURED,
    WEAK_HORIZONTAL_SOURCES,
    HorizontalOption,
    launch_label,
    prefers_horizontal,
    shot_launch_label,
)
from . import ball_launch_fallback, resting_ball_range
from .geometry import intersect_radar_range_sphere
from .lens_profiles import lens_record_focal_px

IWR_RANGE_SOURCE = "iwr_post_impact"
PRIOR_RANGE_SOURCE = "camera_prior"
SIZE_RANGE_SOURCE = resting_ball_range.SOURCE
# The resting ball's pitch inference against an uncalibrated principal point: about
# 35 px of an M12 lens's centring at 2000 px focal length.
RESTING_BALL_PITCH_SD_DEG = 1.0
CALIBRATED_PITCH_SD_DEG = 0.5
# Launch sources the radar route stands on (a measured camera's included).
MEASURED_VERTICAL_SOURCES = ("radar", "camera")
# mode_focal's source for a checkerboard-calibrated lens (lens_profiles: intrinsics
# "calibrated"); resting_ball_range calls the same focal "unit_calibration".
CALIBRATED_FOCAL_SOURCE = "lens_calibrated"
SIZE_CALIBRATED_FOCAL_SOURCE = "unit_calibration"


def size_range_label(focal_source: Optional[str], *, tape_checked: bool = False) -> str:
    """The resting ball's size range's label (Q6, 25 Oct 2026): estimated through a
    calibrated lens, experimental through a nominal one. ``tape_checked`` is the hook for
    the future validation flag: a calibrated size range checked against a tape is
    measured. Nothing sets it yet."""
    if focal_source != SIZE_CALIBRATED_FOCAL_SOURCE:
        return LABEL_EXPERIMENTAL
    return LABEL_MEASURED if tape_checked else LABEL_ESTIMATED


def finite(value: Any) -> bool:
    """A real, finite number (not a bool)."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


# ---------------------------------------------------------------- ball range


def ball_range_resolution(
    *,
    shot_range: Mapping[str, Any],
    prior: Any = None,
    size_route: Optional[Callable[[], Any]] = None,
    range_prior_rejection: Optional[str] = None,
) -> Resolution:
    """The shot's ball range: its IWR launch point, the camera prior, its resting ball.

    ``shot_range`` holds what the IWR stage left on the shot (``ball_range_m``,
    ``ball_range_source``, ``ball_range_uncertainty_m``, ``ball_range_reason``);
    ``prior`` a ``LaunchRangePrior`` the IWR never consulted (its capture failed or
    there is no IWR); ``size_route`` gives the resting-ball record, or why not.
    """

    def iwr():
        if shot_range.get("ball_range_source") == IWR_RANGE_SOURCE and finite(
            shot_range.get("ball_range_m")
        ):
            # the IWR range keeps its own label and says why: a partial dump
            # (iwr_partial) or two tracks competing for the launch point (estimated)
            label = shot_range.get("ball_range_label")
            label = label if label in (LABEL_IWR_PARTIAL, LABEL_ESTIMATED) else LABEL_MEASURED
            default = IWR_PARTIAL_NOTE if label == LABEL_IWR_PARTIAL else IWR_COMPETING_NOTE
            return Candidate(
                float(shot_range["ball_range_m"]),
                label,
                IWR_RANGE_SOURCE,
                uncertainty=_number_or_none(shot_range.get("ball_range_uncertainty_m")),
                note=(shot_range.get("ball_range_reason") or default)
                if label != LABEL_MEASURED
                else None,
            )
        return shot_range.get("ball_range_reason") or "no IWR launch point on this shot"

    def camera_prior():
        if range_prior_rejection:
            return range_prior_rejection
        if shot_range.get("ball_range_source") == PRIOR_RANGE_SOURCE and finite(
            shot_range.get("ball_range_m")
        ):
            return Candidate(
                float(shot_range["ball_range_m"]),
                LABEL_ESTIMATED,
                PRIOR_RANGE_SOURCE,
                uncertainty=_number_or_none(shot_range.get("ball_range_uncertainty_m")),
            )
        if prior is not None and finite(getattr(prior, "range_m", None)):
            return Candidate(
                float(prior.range_m),
                LABEL_ESTIMATED,
                PRIOR_RANGE_SOURCE,
                uncertainty=float(prior.uncertainty_m),
                note=f"camera prior ({getattr(prior, 'source', 'unnamed')})",
            )
        return "no camera prior: the setup saved no ball range and the shot's look had none"

    def ball_size():
        if size_route is None:
            return "no camera frames for this shot"
        record = size_route()
        if not isinstance(record, Mapping):
            return str(record or "the resting ball's size gave nothing")
        if record.get("status") != "measured_from_size":
            return record.get("reason") or str(record.get("status"))
        return Candidate(
            float(record["slant_range_m"]),
            size_range_label(record.get("focal_source")),
            SIZE_RANGE_SOURCE,
            uncertainty=float(record["uncertainty_m"]),
            note=record.get("note"),
            details=dict(record),
        )

    return resolve(
        "ball_range",
        [
            Route(IWR_RANGE_SOURCE, iwr),
            Route(PRIOR_RANGE_SOURCE, camera_prior),
            Route(SIZE_RANGE_SOURCE, ball_size),
        ],
    )


def _number_or_none(value: Any) -> Optional[float]:
    return float(value) if finite(value) else None


def apply_ball_range(shot: Any, resolution: Resolution) -> None:
    """Put the resolved range on the shot, with its source, label, uncertainty and why."""
    sources = dict(getattr(shot, "input_sources", None) or {})
    record = resolution.to_dict()
    if resolution.details:
        record["details"] = dict(resolution.details)
    sources["ball_range"] = record
    shot.input_sources = sources
    if not resolution.present:
        shot.ball_range_m = shot.ball_range_source = shot.ball_range_uncertainty_m = None
        shot.ball_range_label = "not_measured"
        shot.ball_range_reason = resolution.reason
        return
    earlier = "; ".join(f"{name}: {why}" for name, why in resolution.tried)
    shot.ball_range_m = float(resolution.value)
    shot.ball_range_source = resolution.source
    shot.ball_range_uncertainty_m = resolution.uncertainty
    shot.ball_range_label = resolution.label
    shot.ball_range_reason = (
        f"{earlier}; {resolution.route} stands in"
        if earlier and resolution.tried
        # a weaker-than-measured IWR winner keeps why (a partial dump, competing tracks)
        else resolution.note
        if resolution.route == IWR_RANGE_SOURCE and resolution.label != LABEL_MEASURED
        else None
    )


def size_range_route(  # pylint: disable=too-many-arguments
    archive: Optional[Mapping[str, Any]],
    *,
    seed: Optional[Mapping[str, Any]],
    lens_focal_px: Optional[float],
    calibrated_focal_px: Optional[float],
    camera_origin_lfu,
    radar_origin_lfu,
    ball_height_m: Optional[float],
    roll_correction_deg: float,
    horizontal_pixel_sign: float = 1.0,
) -> Callable[[], Any]:
    """The resting-ball-size route over one clip, run only when the chain reaches it."""

    def route():
        if archive is None or "frames" not in archive:
            return "no camera frames for this shot"
        if not finite(ball_height_m):
            return "no ball height for the resting ball"
        frames = np.asarray(archive["frames"])
        measure = resting_ball_measure(archive, seed)
        if measure.found is None:
            return "no resting ball: neither the shot's look, the setup nor a search found it"
        return resting_ball_range.resting_ball_range(
            frames,
            first_post_index=first_post_index(archive, len(frames)),
            frame_interval_s=frame_interval_s(archive),
            seed=measure.found,
            size=measure.size,
            lens_focal_px=lens_focal_px,
            calibrated_focal_px=calibrated_focal_px,
            camera_origin_lfu=np.asarray(camera_origin_lfu, dtype=float),
            radar_origin_lfu=np.asarray(radar_origin_lfu, dtype=float),
            ball_height_m=float(ball_height_m),
            image_width_px=int(frames.shape[-1]),
            image_height_px=int(frames.shape[-2]),
            roll_correction_deg=float(roll_correction_deg),
            horizontal_pixel_sign=float(horizontal_pixel_sign),
        )

    return route


def frame_interval_s(archive: Mapping[str, Any]) -> float:
    """The clip's median frame interval, from its sensor (else host) timestamps."""
    stamps = archive.get("sensor_timestamp_ns")
    stamps = np.asarray(stamps if stamps is not None else archive.get("host_timestamp_ns", []))
    if stamps.size < 2:
        return 1.0 / 120.0
    return float(np.median(np.diff(stamps.astype(np.int64)))) / 1e9


def resting_ball_seed(
    frames: np.ndarray, seed: Optional[Mapping[str, Any]], first_post: int
) -> Optional[tuple[float, float, float]]:
    """Where to look for the resting ball: the shot's look or the setup's ball, else the
    scene detector's ball in the hitting zone (the gate a clip without a setup uses)."""
    if seed and all(finite(seed.get(key)) for key in ("x", "y", "diameter_px")):
        return float(seed["x"]), float(seed["y"]), float(seed["diameter_px"])
    from . import ball_pixels  # noqa: PLC0415
    from .ball_flight import reference_ball_gate  # noqa: PLC0415
    from .club_motion import detect_reference_ball  # noqa: PLC0415

    height, width = frames.shape[-2:]
    gate = reference_ball_gate(None, width, height)
    region = tuple(int(round(value)) for value in gate["region_px"])
    try:
        found = detect_reference_ball(
            frames[: max(3, first_post - 4)],
            pixel_scale=ball_pixels.pixel_scale(width),
            roi=region,
        )
    except (RuntimeError, ValueError):
        return None
    smallest, largest = gate["diameter_px"]
    if not smallest <= found.diameter_px <= largest:
        return None
    return float(found.x), float(found.y), float(found.diameter_px)


# The resting ball measured on a clip, kept on the shot's in-memory archive under this key
# (``keep_resting_ball_measure``): the kiosk takes it while the IWR's dump is still
# arriving, and the range chain and the camera fallback read it instead of measuring again.
RESTING_BALL_KEY = "_resting_ball_measure"


@dataclass(frozen=True)
class RestingBallMeasure:
    """This clip's resting ball: where it was looked for (``seed``, the caller's x, y,
    diameter or None), where it was found and its measured size (None when not found)."""

    seed: Optional[tuple[float, float, float]]
    found: Optional[tuple[float, float, float]]
    size: Optional[resting_ball_range.RestingBallSize]


def ball_reference_check(measure: Optional[RestingBallMeasure]) -> dict:
    """Compare the saved reference with this clip's independently fitted outline."""
    record = {"status": "unavailable", "range_prior_rejection": None}
    if measure is None or measure.seed is None or measure.size is None:
        return {**record, "reason": "no saved reference and fitted outline to compare"}
    from .ball_flight import EXPECTED_SIZE_RATIO  # noqa: PLC0415

    x, y, diameter = measure.seed
    size = measure.size
    if (
        diameter <= 0
        or not all(finite(value) for value in (x, y, diameter, size.x, size.y, size.diameter_px))
        or size.diameter_px <= 0
    ):
        return {**record, "reason": "invalid reference or fitted outline"}
    distance = math.hypot(size.x - x, size.y - y)
    ratio = size.diameter_px / diameter
    # Use the existing scene/impact detector agreement bounds, without relaxing them.
    position_limit = max(8.0, 0.75 * diameter)
    low, high = EXPECTED_SIZE_RATIO
    consistent = distance <= position_limit and low <= ratio <= high
    return {
        "status": "consistent" if consistent else "conflict",
        "reference": {"x": x, "y": y, "diameter_px": diameter},
        "observed": {
            "x": size.x,
            "y": size.y,
            "diameter_px": size.diameter_px,
            "diameter_sd_px": size.diameter_sd_px,
            "windows": size.windows,
        },
        "centre_distance_px": distance,
        "diameter_ratio": ratio,
        "position_limit_px": position_limit,
        "diameter_ratio_limits": [low, high],
        "range_prior_rejection": None
        if consistent
        else ("saved ball reference conflicts with the shot clip's fitted outline"),
    }


def _seed_key(seed: Optional[Mapping[str, Any]]) -> Optional[tuple[float, float, float]]:
    if seed and all(finite(seed.get(key)) for key in ("x", "y", "diameter_px")):
        return float(seed["x"]), float(seed["y"]), float(seed["diameter_px"])
    return None


def resting_ball_measure(
    archive: Mapping[str, Any], seed: Optional[Mapping[str, Any]]
) -> RestingBallMeasure:
    """The resting ball on this clip from ``seed`` (``resting_ball_seed``, then
    ``measure_resting_ball_size``): the one kept on the archive for the same seed, else
    measured now."""
    key = _seed_key(seed)
    kept = archive.get(RESTING_BALL_KEY)
    if isinstance(kept, RestingBallMeasure) and kept.seed == key:
        return kept
    frames = np.asarray(archive["frames"])
    first = first_post_index(archive, len(frames))
    found = resting_ball_seed(frames, seed, first)
    size = (
        None
        if found is None
        else resting_ball_range.measure_resting_ball_size(
            frames, first_post_index=first, seed=found, frame_interval_s=frame_interval_s(archive)
        )
    )
    return RestingBallMeasure(seed=key, found=found, size=size)


def keep_resting_ball_measure(
    archive: MutableMapping[str, Any], seed: Optional[Mapping[str, Any]]
) -> RestingBallMeasure:
    """Measure this clip's resting ball now and keep it on the archive for the shot."""
    measure = resting_ball_measure(archive, seed)
    archive[RESTING_BALL_KEY] = measure
    return measure


def first_post_index(archive: Mapping[str, Any], frame_count: int) -> int:
    """The first frame exposed after the trigger (by exposure, else by arrival)."""
    from .triggered_buffer import exposure_trigger_index  # noqa: PLC0415

    by_exposure = exposure_trigger_index(archive, frame_count)
    if by_exposure is not None:
        return by_exposure + 1
    return int(np.asarray(archive.get("pre_trigger_count", frame_count // 2)))


# ---------------------------------------------------------------- the fallback stage


def mode_focal(
    width_px: int, *, lens: Optional[Mapping[str, Any]], rig: Any
) -> tuple[Optional[float], Optional[str], float]:
    """This mode's focal length, its source and relative 1-sigma: the lens record's (its
    checkerboard calibration's when it has one: the calibration's own stated sigma, else
    +-2 %; else the listing's, +-8 %), else the rig file's (listed: +-8 %)."""
    focal = lens_record_focal_px(lens, width_px)
    if focal is not None and lens.get("intrinsics") == INTRINSICS_CALIBRATED:
        stated = lens.get("focal_rel_sd")
        sigma = (
            float(stated)
            if finite(stated) and float(stated) > 0
            else resting_ball_range.CALIBRATED_FOCAL_REL_SD
        )
        return focal, CALIBRATED_FOCAL_SOURCE, sigma
    if focal is not None:
        return focal, "lens_nominal", resting_ball_range.NOMINAL_FOCAL_REL_SD
    if rig is not None and finite(getattr(rig, "focal_px", None)):
        from .ball_pixels import mode_focal_px  # noqa: PLC0415

        return mode_focal_px(width_px, rig), "rig_nominal", resting_ball_range.NOMINAL_FOCAL_REL_SD
    return None, None, resting_ball_range.NOMINAL_FOCAL_REL_SD


def fallback_inputs(
    *,
    focal: tuple,
    ops_offset_mm: Any,
    camera_origin_lfu,
    target_line: Optional[Mapping[str, Any]],
    lis3dh_camera_pitch_deg: Optional[float],
) -> dict:
    """The camera fallback's inputs besides the geometry, frozen into the shot's context:
    the focal length and its uncertainty, the OPS's place, the aim's uncertainty and the
    LIS3DH's camera pitch (a check on the resting ball's pitch)."""
    from ..rig_geometry import camera_rdf_offset_to_target_lfu  # noqa: PLC0415

    focal_px, focal_source, focal_sd = focal
    ops = None
    if ops_offset_mm is not None:
        lateral, forward, up = camera_rdf_offset_to_target_lfu(ops_offset_mm)
        origin = [float(value) for value in camera_origin_lfu]
        ops = [origin[0] + lateral, origin[1] + forward, origin[2] + up]
    line = target_line or {}
    aim_sd = line.get("total_uncertainty_deg") if line.get("status") == "set" else None
    return {
        "focal_px": focal_px,
        "focal_source": focal_source,
        "focal_rel_sd": focal_sd,
        "ops_lfu": ops,
        "horizontal_offset_sd_deg": float(aim_sd) if finite(aim_sd) else 0.0,
        "lis3dh_camera_pitch_deg": lis3dh_camera_pitch_deg
        if finite(lis3dh_camera_pitch_deg)
        else None,
    }


def _camera_distance_from_slant(
    camera: ball_launch_fallback.PinholeCamera,
    rest_px,
    slant_m: float,
    radar_origin,
    ball_height_m: float,
    infer_pitch: bool,
) -> tuple[float, ball_launch_fallback.PinholeCamera]:
    """The lens-to-ball distance whose point is ``slant_m`` from the radar (and the pitch)."""
    distance = slant_m
    for _ in range(6):
        if infer_pitch:
            pitch = ball_launch_fallback.resting_ball_pitch_deg(
                camera, rest_px, distance, ball_height_m
            )
            camera = replace(camera, pitch_deg=pitch)
        ray = camera.rays(np.asarray(rest_px, dtype=float))
        point = intersect_radar_range_sphere(
            ray[None, :],
            slant_m,
            camera_origin_lfu=np.asarray(camera.origin_lfu, dtype=float),
            radar_origin_lfu=np.asarray(radar_origin, dtype=float),
        )[0]
        distance = float(np.linalg.norm(point - np.asarray(camera.origin_lfu, dtype=float)))
    return distance, camera


def run_ball_fallback(  # pylint: disable=too-many-locals
    context: Mapping[str, Any],
    archive: Mapping[str, Any],
    *,
    geometry: Any,
) -> dict:
    """The camera fallback's launch for one frozen context and its clip, as a plain record."""
    inputs = context.get("camera_fallback")
    if not isinstance(inputs, Mapping):
        return {"status": "not_run", "reason": "this context predates the camera fallback"}
    frames = np.asarray(archive["frames"])
    stamps = archive.get("sensor_timestamp_ns")
    stamps = np.asarray(stamps if stamps is not None else archive["host_timestamp_ns"])
    exposure = archive.get("exposure_us")
    exposure = np.asarray(exposure if exposure is not None else 0.0, dtype=float)
    first_post = first_post_index(archive, len(frames))
    ball_range = context.get("ball_range") or {}
    details = ball_range.get("details") or {}
    if details.get("status") == "measured_from_size":
        rest_px, rest_d = tuple(details["rest_px"]), float(details["diameter_px"])
    else:
        measure = resting_ball_measure(archive, context.get("setup_ball"))
        if measure.found is None:
            return {"status": "withheld_no_resting_ball", "reason": "no resting ball found"}
        size = measure.size
        if size is None:
            return {
                "status": "withheld_no_resting_ball",
                "reason": "no lit resting ball measured before impact",
            }
        rest_px, rest_d = (size.x, size.y), size.diameter_px
    pose = context.get("camera_pose") or {}
    calibrated = pose.get("status") == "calibrated" and finite(pose.get("pitch_deg"))
    focal = inputs.get("focal_px")
    if not finite(focal):
        return {"status": "withheld_no_focal_length", "reason": "no focal length for this mode"}
    roll = float(geometry.roll_correction_deg)
    roll_record = pose.get("roll") or {}
    if roll_record.get("applied") and finite(roll_record.get("roll_correction_deg")):
        roll += float(roll_record["roll_correction_deg"])
    camera = ball_launch_fallback.PinholeCamera(
        focal_px=float(focal),
        width=int(geometry.image_width_px),
        height=int(geometry.image_height_px),
        pitch_deg=float(pose["pitch_deg"]) if calibrated else 0.0,
        roll_correction_deg=roll,
        horizontal_pixel_sign=float(geometry.horizontal_pixel_sign),
        origin_lfu=(
            float(geometry.camera_lateral_offset_m),
            float(geometry.camera_forward_offset_m),
            float(geometry.camera_height_m),
        ),
    )
    radar_origin = (0.0, 0.0, float(geometry.radar_height_m))
    if details.get("status") == "measured_from_size":
        distance = float(details["camera_distance_m"])
        distance_sd = float(details["size_sd_m"])
        distance_source = SIZE_RANGE_SOURCE
    else:
        distance, _camera = _camera_distance_from_slant(
            camera,
            rest_px,
            float(geometry.tee_slant_range_m),
            radar_origin,
            float(geometry.ball_height_m),
            infer_pitch=not calibrated,
        )
        distance_sd = float(ball_range.get("uncertainty_m") or 0.05)
        distance_source = str(ball_range.get("source") or "tee_range")
    if calibrated:
        pitch_source, pitch_sd = "calibrated", float(pose.get("uncertainty_deg") or 0.5)
    else:
        pitch_source = ball_launch_fallback.PITCH_FROM_RESTING_BALL
        inferred = ball_launch_fallback.resting_ball_pitch_deg(
            camera, rest_px, distance, float(geometry.ball_height_m)
        )
        lis3dh = inputs.get("lis3dh_camera_pitch_deg")
        gap = abs(inferred - float(lis3dh)) if finite(lis3dh) else 0.0
        pitch_sd = max(RESTING_BALL_PITCH_SD_DEG, gap)
    fallback_geometry = ball_launch_fallback.FallbackGeometry(
        camera=camera,
        rest_px=tuple(float(value) for value in rest_px),
        rest_diameter_px=float(rest_d),
        ball_distance_m=distance,
        ball_distance_sd_m=distance_sd,
        distance_source=distance_source,
        focal_rel_sd=float(inputs.get("focal_rel_sd") or 0.0),
        pitch_source=pitch_source,
        pitch_sd_deg=pitch_sd,
        ball_height_m=float(geometry.ball_height_m),
        ops_lfu=tuple(inputs["ops_lfu"]) if inputs.get("ops_lfu") else None,
        horizontal_offset_deg=geometry.ball_horizontal_output_offset_deg,
        horizontal_offset_sd_deg=float(inputs.get("horizontal_offset_sd_deg") or 0.0),
    )
    ops = context.get("ops_ball_speed_mph")
    result = ball_launch_fallback.estimate_camera_ball_launch(
        frames,
        stamps,
        exposure_us=exposure,
        first_post_index=first_post,
        geometry=fallback_geometry,
        ops_radial_mph=float(ops) if finite(ops) and fallback_geometry.ops_lfu else None,
    ).to_dict()
    result["inputs"] = {
        "first_post_index": first_post,
        "focal_source": inputs.get("focal_source"),
        "distance_source": distance_source,
        "pitch_source": pitch_source,
        "pitch_sd_deg": pitch_sd,
        "lis3dh_camera_pitch_deg": inputs.get("lis3dh_camera_pitch_deg"),
    }
    return result


# ---------------------------------------------------------------- launch


def launch_resolutions(shot: Any, fallback: Optional[Mapping[str, Any]]) -> dict[str, Resolution]:
    """The vertical and horizontal launch: the shot's sensors, else the camera fallback.

    Each value carries the policy's label (``source_labels.shot_launch_label``): the
    camera fallback is estimated through a calibrated lens, else experimental; a
    low-coherence or low-consensus horizontal is experimental (Q3). The horizontal routes
    are ranked by ``source_labels.prefers_horizontal`` (Q5): an experimental value never
    outranks one that is not, attached sigmas decide where both have one, else the
    radar's wins.
    """
    accepted = isinstance(fallback, Mapping) and fallback.get("status") == "accepted"
    why_not = (
        "the camera fallback did not run"
        if not isinstance(fallback, Mapping)
        else fallback.get("reason") or str(fallback.get("status"))
    )
    fallback_label = launch_label(
        CAMERA_FALLBACK_SOURCE, intrinsics=getattr(shot, "camera_lens_intrinsics", None)
    )

    def fallback_route(key: str, sd_key: str):
        def route():
            if not accepted or not finite(fallback.get(key)):
                return why_not
            return Candidate(
                float(fallback[key]),
                fallback_label,
                CAMERA_FALLBACK_SOURCE,
                uncertainty=_number_or_none(fallback.get(sd_key)),
                note=f"camera fallback ({fallback.get('n_points')} ball points)",
            )

        return route

    def vertical_sensor():
        source = getattr(shot, "launch_angle_vertical_source", None)
        if source in MEASURED_VERTICAL_SOURCES and finite(shot.launch_angle_vertical):
            label, note = shot_launch_label(shot, "vertical")
            return Candidate(float(shot.launch_angle_vertical), label, source, note=note)
        return getattr(shot, "launch_angle_vertical_reason", None) or (
            "no radar vertical launch on this shot"
        )

    def sensor_horizontal(*, weak: bool):
        def route():
            source = getattr(shot, "launch_angle_horizontal_source", None)
            value = getattr(shot, "launch_angle_horizontal", None)
            if source in (None, "estimated", CAMERA_FALLBACK_SOURCE) or not finite(value):
                return getattr(shot, "launch_angle_horizontal_reason", None) or (
                    "no sensor horizontal launch on this shot"
                )
            implausible = implausible_launch_reason(value)
            if implausible is not None:
                return f"{source} {implausible}"
            if (source in WEAK_HORIZONTAL_SOURCES) != weak:
                return "a weak sensor horizontal (its own route)" if not weak else "not weak"
            label, note = shot_launch_label(shot, "horizontal")
            return Candidate(float(value), label, source, note=note)

        return route

    horizontal_routes = [
        Route("sensor", sensor_horizontal(weak=False)),
        Route(CAMERA_FALLBACK_SOURCE, fallback_route("horizontal_deg", "horizontal_sd_deg")),
        # a low-coherence or low-consensus horizontal: shown, labelled experimental
        Route("weak_sensor", sensor_horizontal(weak=True)),
    ]
    return {
        "launch_vertical": resolve(
            "launch_vertical",
            [
                Route("radar", vertical_sensor),
                Route(CAMERA_FALLBACK_SOURCE, fallback_route("vertical_deg", "vertical_sd_deg")),
            ],
        ),
        "launch_horizontal": resolve("launch_horizontal", _ranked(horizontal_routes)),
    }


def _ranked(routes: list[Route]) -> list[Route]:
    """The routes with the one ``prefers_horizontal`` picks among those that give a value
    first; the others keep their order behind it (they run only if it fails)."""
    best, best_option = None, None
    for route in routes:
        try:
            outcome = route.produce()
        except Exception:  # pylint: disable=broad-exception-caught
            continue  # resolve() records the error when it runs the route
        if not isinstance(outcome, Candidate) or not finite(outcome.value):
            continue
        option = HorizontalOption(
            label=outcome.label,
            sigma_deg=outcome.uncertainty,
            radar=str(outcome.source or "").startswith("radar"),
        )
        if prefers_horizontal(option, over=best_option):
            best, best_option = route, option
    if best is None:
        return routes
    return [best, *(route for route in routes if route is not best)]


__all__ = [
    "apply_ball_range",
    "ball_range_resolution",
    "fallback_inputs",
    "first_post_index",
    "keep_resting_ball_measure",
    "mode_focal",
    "launch_resolutions",
    "resting_ball_measure",
    "RestingBallMeasure",
    "run_ball_fallback",
    "size_range_route",
]

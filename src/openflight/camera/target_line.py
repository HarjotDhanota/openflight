"""The target line: a camera aim point dragged on the live view at every setup (D34).

TrackMan 4's "Target Image" is the model: the ball sits in a square on the
picture, the operator marks the target, and optionally the line where the floor
meets the screen. Here the ball is the confirmed ground patch's centre (P8-1),
and the tester drags an **aim point** onto the target on the far net, screen or
flag in the live 1280x800 view. The line from the ball towards it is the target
line; its yaw is the zero every horizontal angle is measured from.

**Yaw.** The azimuth, in the ground plane, of the line from the ball (the patch
centre, on the ground) to the aim point, relative to the camera's boresight. It
is computed through the same camera model and tilt the patch is drawn with (the
rig file, the LIS3DH pitch and the unit's camera tilt, P8-5), so the pitch is
applied. **Sign:** positive when the target line lies to the RIGHT of the unit's
boresight, as seen from behind the unit (the shared contract with D36).

**Far or near.** An aim point on a far flag is a direction: the line from the
ball runs parallel to the aim pixel's ray (``direction``). An aim point on a net
or screen a few metres away is not quite one: a ball aside of the lens sees the
target at a different azimuth from the lens (parallax). When the tester also
marks the floor line (where the floor meets the screen), the aim point is put on
that screen, dropped to the floor, and the line runs from the ball to that floor
point (``floor_point``). Without a floor line the aim is taken as a direction,
the method is recorded, and the parallax the direction leaves at the net's range
is added to the uncertainty at the hand-off.

**Roll.** The LIS3DH roll is recorded but not applied to the camera
(``camera_roll.LIS3DH_ROLL_APPLIED``). A camera rolled by r turns the picture
about its centre, so an aim point d rows from the centre moves about d * sin(r)
columns: at the horizon row nothing, 1.4 m up a screen 4.5 m out (about 270 rows
above the centre) about 0.9 deg for a 3 deg roll. That error is carried in the
uncertainty (``roll_deg``), using the patch's roll pad (the reading, at least
1 deg, or 3 deg without one). An aim point low on the screen, near where it meets
the floor, keeps it small.

**Lens.** The nominal model is a distortion-free pinhole; the stock 2.8 mm lens
has a barrel of about 16 % at the 1280 frame's edge (field frames, 30 Sept). It
is not corrected (no calibration measures it), only carried in the uncertainty
(``lens_distortion_deg``): small near the middle of the picture, so the unit
should be turned to face the target roughly.

**Camera to IWR.** The rig file has no yaw term between the camera and the IWR:
they share a boresight in azimuth by design (the IWR is pitched 10 deg up, which
does not turn it sideways; ``iwr_offset_mm`` puts it straight below the lens).
So one yaw applies to both, and both kiosk flags get the same value. A measured
camera-to-IWR yaw, when the rig file has one, enters ``iwr_yaw_deg`` here.

**Kiosk flags.** ``--camera-capture-horizontal-offset-deg`` and
``--iwr6843-azimuth-offset-deg`` are each *added* to the measured angle and are
documented as the boresight's azimuth from the target line (the IWR flag:
"positive means boresight points right of the target line"). That is minus the
target line's yaw, so the flags get ``kiosk_offset_deg = -yaw_deg``: a ball
launched straight down the target line then reads 0.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any, Callable, Mapping, Sequence

import numpy as np

from openflight.camera import camera_roll, ground_patch

SCHEMA = "openflight.tester_target_line.v1"
SOURCE = "aim_point"
SIGN_CONVENTION = "positive_target_line_right_of_unit_boresight_seen_from_behind"
KIOSK_OFFSET_CONVENTION = (
    "kiosk_offset_deg = -yaw_deg: both flags take the boresight's azimuth from the target "
    "line and are added to measured horizontal angles"
)
CAMERA_FLAG = "--camera-capture-horizontal-offset-deg"
IWR_FLAG = "--iwr6843-azimuth-offset-deg"
# The rig file carries no camera-to-IWR yaw: a shared boresight by design, not measured.
CAMERA_TO_IWR_YAW_DEG = 0.0
CAMERA_TO_IWR_BASIS = "rig_file_has_no_yaw_term_shared_boresight_by_design_not_measured"
# How well a mouse puts the aim on the target, in 1280x800 pixels: the floor.
AIM_PIXEL_UNCERTAINTY_PX = 2.0
# A finger on the preview: this many screen pixels, times the preview's frame pixels
# per screen pixel (``display_scale``, the page sends it). Without it a finger on
# the Pi's own preview is assumed: 20-40 frame px (13 Oct audit), this much.
TOUCH_UNCERTAINTY_SCREEN_PX = 12.5
DEFAULT_TOUCH_UNCERTAINTY_FRAME_PX = 30.0
FLOOR_LINE_PIXEL_UNCERTAINTY_PX = 2.0
# A re-confirmed aim point (or floor line end) that moved less than this has not moved.
AIM_MOVE_TOLERANCE_PX = 4.0
# The stock lens's barrel, as a one-term radial model at 1280 px wide (Outdoors-test-5,
# 30 Sept: about 16 % at the horizontal edge). Carried as uncertainty only.
STOCK_LENS_BARREL_K1_PER_PX2_AT_1280 = 4.0e-7
# Beyond this the aim point sits far out in the picture, where the lens is least known.
LARGE_YAW_WARNING_DEG = 15.0
# A floor line shorter than this on the ground cannot give the screen's direction.
MIN_FLOOR_LINE_M = 0.05
# The target must lie at least this far beyond the ball along the boresight.
MIN_TARGET_BEYOND_BALL_M = 0.05
# The target distances a direction's parallax is shown for: a net, a screen, a flag.
PARALLAX_REFERENCE_DISTANCES_M = (3.0, 5.0, 10.0, 100.0)
# The alignment stick (Part B) agrees when within this many combined uncertainties.
STICK_AGREEMENT_SIGMA = 2.0


def target_line_policy() -> dict[str, Any]:
    """Every constant of the target line, for the setup's evidence."""
    return {
        "schema": SCHEMA,
        "sign_convention": SIGN_CONVENTION,
        "kiosk_offset_convention": KIOSK_OFFSET_CONVENTION,
        "yaw": "ground_plane_azimuth_from_the_patch_centre_to_the_aim_point_vs_the_boresight",
        "methods": {
            "direction": "the line from the ball parallel to the aim pixel's ray",
            "floor_point": "the aim point put on the floor line's screen, dropped to the floor",
        },
        "aim_pixel_uncertainty_px": AIM_PIXEL_UNCERTAINTY_PX,
        "touch_uncertainty_screen_px": TOUCH_UNCERTAINTY_SCREEN_PX,
        "default_touch_uncertainty_frame_px": DEFAULT_TOUCH_UNCERTAINTY_FRAME_PX,
        "fallback_in_a_perturbation": "unbounded_with_a_warning",
        "shown_uncertainty": "total_uncertainty_deg_with_parallax_at_the_target_distance",
        "floor_line_pixel_uncertainty_px": FLOOR_LINE_PIXEL_UNCERTAINTY_PX,
        "move_tolerance_px": AIM_MOVE_TOLERANCE_PX,
        "stock_lens_barrel_k1_per_px2_at_1280": STOCK_LENS_BARREL_K1_PER_PX2_AT_1280,
        "large_yaw_warning_deg": LARGE_YAW_WARNING_DEG,
        "camera_to_iwr_yaw_deg": CAMERA_TO_IWR_YAW_DEG,
        "camera_to_iwr_basis": CAMERA_TO_IWR_BASIS,
        "roll": "recorded_not_applied_carried_as_uncertainty"
        if not camera_roll.LIS3DH_ROLL_APPLIED
        else "applied",
    }


@dataclass(frozen=True)
class TargetLineSolution:
    """One solve: the yaw, how it was found, and the direction yaw beside it."""

    yaw_deg: float
    method: str
    direction_yaw_deg: float
    floor_point_lfu_m: tuple[float, float] | None = None
    aim_height_m: float | None = None
    fallback_reason: str | None = None


def _ray(camera, pixel: Sequence[float]) -> np.ndarray:
    ray = np.asarray(camera.ray_model.rays(np.asarray(pixel, dtype=float)), dtype=float)
    if ray.shape != (3,) or not np.all(np.isfinite(ray)):
        raise ValueError("the camera model returned an invalid ray")
    return ray


def _ground_point(camera, pixel: Sequence[float]) -> np.ndarray | None:
    """Where the pixel's ray meets the floor, or None at or above the horizon."""
    origin = np.asarray(camera.camera_origin_lfu, dtype=float)
    ray = _ray(camera, pixel)
    if ray[2] >= -1e-9:
        return None
    return origin + (-origin[2] / ray[2]) * ray


def _azimuth_deg(lateral: float, forward: float) -> float:
    return math.degrees(math.atan2(float(lateral), float(forward)))


def solve_yaw(  # pylint: disable=too-many-return-statements
    camera,
    ball_lfu: Sequence[float],
    aim_px: Sequence[float],
    floor_line_px: Sequence[Sequence[float]] | None = None,
) -> TargetLineSolution:
    """The target line's yaw from the ball (``ball_lfu``: lateral, forward on the floor)."""
    ray = _ray(camera, aim_px)
    if ray[1] <= 1e-6:
        raise ValueError("the aim point's ray does not point forward")
    direction = _azimuth_deg(ray[0], ray[1])
    as_direction = TargetLineSolution(direction, "direction", direction)
    if floor_line_px is None:
        return as_direction
    ends = [_ground_point(camera, end) for end in floor_line_px]
    if any(end is None for end in ends):
        return replace(as_direction, fallback_reason="floor_line_at_or_above_the_horizon")
    first, second = ends  # type: ignore[misc]
    along = np.asarray([second[0] - first[0], second[1] - first[1], 0.0])
    if float(np.hypot(along[0], along[1])) < MIN_FLOOR_LINE_M:
        return replace(as_direction, fallback_reason="floor_line_too_short")
    normal = np.asarray([-along[1], along[0], 0.0])
    denominator = float(np.dot(normal, ray))
    if abs(denominator) < 1e-9:
        return replace(as_direction, fallback_reason="aim_ray_parallel_to_the_screen")
    origin = np.asarray(camera.camera_origin_lfu, dtype=float)
    distance = float(np.dot(normal, first - origin)) / denominator
    if distance <= 0.0:
        return replace(as_direction, fallback_reason="screen_behind_the_lens")
    on_screen = origin + distance * ray
    ball = (float(ball_lfu[0]), float(ball_lfu[1]))
    if on_screen[1] - ball[1] < MIN_TARGET_BEYOND_BALL_M:
        return replace(as_direction, fallback_reason="screen_not_beyond_the_ball")
    return TargetLineSolution(
        yaw_deg=_azimuth_deg(on_screen[0] - ball[0], on_screen[1] - ball[1]),
        method="floor_point",
        direction_yaw_deg=direction,
        floor_point_lfu_m=(float(on_screen[0]), float(on_screen[1])),
        aim_height_m=float(on_screen[2]),
    )


class _RemappedRays:  # pylint: disable=too-few-public-methods
    """A camera whose pixels are moved before its own ray model sees them.

    How a wrong pitch, an unapplied roll, a wrong focal length or the lens's
    barrel moves a pixel, applied to any model (nominal or calibrated).
    """

    def __init__(self, model, remap: Callable[[np.ndarray], np.ndarray]):
        self._model = model
        self._remap = remap

    def rays(self, pixels_px: Any) -> np.ndarray:
        """The model's rays for the moved pixels."""
        pixels = np.asarray(pixels_px, dtype=float)
        return self._model.rays(self._remap(pixels.reshape(-1, 2)).reshape(pixels.shape))


def _perturbed(camera, remap: Callable[[np.ndarray], np.ndarray]):
    return replace(camera, ray_model=_RemappedRays(camera.ray_model, remap))


def _centre(camera) -> np.ndarray:
    return np.asarray([camera.image_width_px / 2.0, camera.image_height_px / 2.0])


class _PitchedRays:  # pylint: disable=too-few-public-methods
    """A camera pitched ``degrees`` further up: its rays turned about the lateral axis."""

    def __init__(self, model, degrees: float):
        self._model = model
        angle = math.radians(degrees)
        self._turn = np.asarray(
            [
                [1.0, 0.0, 0.0],
                [0.0, math.cos(angle), -math.sin(angle)],
                [0.0, math.sin(angle), math.cos(angle)],
            ]
        )

    def rays(self, pixels_px: Any) -> np.ndarray:
        """The model's rays, turned."""
        rays = np.asarray(self._model.rays(pixels_px), dtype=float)
        return rays @ self._turn.T


def _pitched(camera, degrees: float):
    return replace(camera, ray_model=_PitchedRays(camera.ray_model, degrees))


def _rolled(camera, degrees: float):
    centre = _centre(camera)
    angle = math.radians(degrees)
    turn = np.asarray([[math.cos(angle), -math.sin(angle)], [math.sin(angle), math.cos(angle)]])
    return _perturbed(camera, lambda pixels: (pixels - centre) @ turn.T + centre)


def _scaled(camera, factor: float):
    centre = _centre(camera)
    return _perturbed(camera, lambda pixels: (pixels - centre) * factor + centre)


def _undistorted(camera):
    """The camera if the stock lens's barrel were taken out of its pixels."""
    centre = _centre(camera)
    k1 = STOCK_LENS_BARREL_K1_PER_PX2_AT_1280 * (1280.0 / camera.image_width_px) ** 2

    def remap(pixels: np.ndarray) -> np.ndarray:
        offset = pixels - centre
        radius2 = np.sum(offset * offset, axis=-1, keepdims=True)
        return centre + offset * (1.0 + k1 * radius2)

    return _perturbed(camera, remap)


def _spread(base: float, values: Sequence[float]) -> float:
    return max((abs(value - base) for value in values), default=0.0)


def _roll_pad_deg(roll_deg: float | None) -> float:
    """The roll the camera model does not apply: the patch's pad, or 1 deg once applied."""
    if camera_roll.LIS3DH_ROLL_APPLIED:
        return ground_patch.MIN_ROLL_PAD_DEG
    return ground_patch.unapplied_roll_pad_deg(roll_deg)


def aim_pixel_uncertainty(display_scale: float | None) -> tuple[float, str]:
    """How far the aim pixel may be off, in frame pixels, and on what basis."""
    if display_scale is None:
        return DEFAULT_TOUCH_UNCERTAINTY_FRAME_PX, "touch_on_the_pi_preview_assumed"
    scale = float(display_scale)
    if not math.isfinite(scale) or scale <= 0.0:
        raise ValueError("display_scale must be a positive number (frame px per screen px)")
    return max(AIM_PIXEL_UNCERTAINTY_PX, TOUCH_UNCERTAINTY_SCREEN_PX * scale), (
        "touch_screen_px_times_display_scale"
    )


def _uncertainty(  # pylint: disable=too-many-arguments,too-many-locals
    camera,
    ball: tuple[float, float],
    aim: list[float],
    floor: list[list[float]] | None,
    solved: TargetLineSolution,
    *,
    tilt_pad_deg: float,
    roll_pad_deg: float,
    aim_px_uncertainty: float = AIM_PIXEL_UNCERTAINTY_PX,
) -> dict[str, Any]:
    unbounded: set[str] = set()

    def spread(name: str, solutions: list[TargetLineSolution]) -> float:
        """The part's spread; a perturbation that changes the method leaves it unbounded:
        a fallback is a different estimate, never a tighter one."""
        if any(item.method != solved.method for item in solutions):
            unbounded.add(name)
        return _spread(solved.yaw_deg, [item.yaw_deg for item in solutions])

    def sol(model=camera, aim_px=aim, floor_px=floor) -> TargetLineSolution:
        return solve_yaw(model, ball, aim_px, floor_px)

    step = float(aim_px_uncertainty)
    parts = {
        "aim_pixel_deg": spread(
            "aim_pixel_deg",
            [
                sol(aim_px=[aim[0] + dx, aim[1] + dy])
                for dx, dy in ((step, 0.0), (-step, 0.0), (0.0, step), (0.0, -step))
            ],
        ),
        "tilt_deg": spread(
            "tilt_deg", [sol(_pitched(camera, sign * tilt_pad_deg)) for sign in (1.0, -1.0)]
        ),
        "roll_deg": spread(
            "roll_deg", [sol(_rolled(camera, sign * roll_pad_deg)) for sign in (1.0, -1.0)]
        ),
        "focal_deg": spread(
            "focal_deg",
            [
                sol(_scaled(camera, 1.0 + sign * float(camera.focal_relative_uncertainty)))
                for sign in (1.0, -1.0)
            ],
        ),
    }
    nominal = str(camera.source).startswith("nominal")
    parts["lens_distortion_deg"] = (
        spread("lens_distortion_deg", [sol(_undistorted(camera))]) if nominal else 0.0
    )
    if solved.method == "floor_point" and floor is not None:
        moved = []
        for index in range(2):
            for sign in (1.0, -1.0):
                shifted = [list(end) for end in floor]
                shifted[index][1] += sign * FLOOR_LINE_PIXEL_UNCERTAINTY_PX
                moved.append(sol(floor_px=shifted))
        parts["floor_line_px_deg"] = spread("floor_line_px_deg", moved)
    bounded = not unbounded
    return {
        "aim_px": round(step, 3),
        "parts_deg": {key: round(value, 4) for key, value in parts.items()},
        "bounded": bounded,
        "unbounded_parts": sorted(unbounded),
        "total_deg": (
            round(math.sqrt(sum(value * value for value in parts.values())), 4) if bounded else None
        ),
        "tilt_pad_deg": float(tilt_pad_deg),
        "roll_pad_deg": float(roll_pad_deg),
        "roll_applied": bool(camera_roll.LIS3DH_ROLL_APPLIED),
        "roll_note": (
            "the LIS3DH roll is recorded but not applied to the camera; the yaw an "
            "unapplied roll of roll_pad_deg would move is roll_deg"
        ),
        "lens_distortion": (
            "stock_lens_barrel_estimate_not_applied" if nominal else "in_the_calibrated_model"
        ),
        "parallax": (
            "none: the floor point puts the target at its distance"
            if solved.method == "floor_point"
            else "added at the hand-off from ball_offset_from_aim_ray_m and the net's range"
        ),
    }


def _pixel(value: Any, camera, name: str) -> list[float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 2:
        raise ValueError(f"{name} must be two numbers")
    x, y = (float(item) for item in value)
    if not (math.isfinite(x) and math.isfinite(y)):
        raise ValueError(f"{name} must be two finite numbers")
    if not (0.0 <= x <= camera.image_width_px and 0.0 <= y <= camera.image_height_px):
        raise ValueError(
            f"{name} must lie in the {camera.image_width_px}x{camera.image_height_px} picture"
        )
    return [x, y]


def parse_floor_line(value: Any, camera) -> list[list[float]] | None:
    """The floor line's two ends, in this camera's pixels, or None when not marked."""
    if value is None:
        return None
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 2:
        raise ValueError("floor_line_px must be two points")
    return [_pixel(end, camera, "floor_line_px") for end in value]


def _ball_offset_from_aim_ray_m(camera, ball: tuple[float, float], aim: list[float]) -> float:
    """How far aside of the aim ray (in the ground plane) the ball sits: the parallax arm."""
    ray = _ray(camera, aim)
    heading = np.asarray([ray[0], ray[1]]) / math.hypot(ray[0], ray[1])
    origin = np.asarray(camera.camera_origin_lfu, dtype=float)
    offset = np.asarray([ball[0] - origin[0], ball[1] - origin[1]])
    return float(heading[0] * offset[1] - heading[1] * offset[0])


def default_aim_px(camera) -> list[float]:
    """Straight down the boresight, on the horizon: where a new setup's aim starts."""
    from openflight.camera.reference_ball_range import project_to_pixel  # noqa: PLC0415

    origin = np.asarray(camera.camera_origin_lfu, dtype=float)
    return list(project_to_pixel(camera, origin + np.asarray([0.0, 1000.0, 0.0])))


def target_line_record(  # pylint: disable=too-many-arguments
    camera,
    ball_lfu: Sequence[float],
    aim_px: Any,
    floor_line_px: Any = None,
    *,
    tilt_pad_deg: float,
    roll_deg: float | None,
    camera_tilt: Mapping[str, Any] | None = None,
    display_scale: float | None = None,
    target_distance_m: float | None = None,
    target_distance_basis: str | None = None,
) -> dict[str, Any]:
    """The target line as it is stored, shown and handed on.

    ``ball_lfu`` is the box's ball on the floor (rig frame); ``tilt_pad_deg`` the
    camera tilt's uncertainty; ``roll_deg`` the LIS3DH roll the camera does not
    apply (against the rig's expected placement). ``display_scale`` (frame px per
    screen px) sizes the touch's own uncertainty; ``target_distance_m`` is how far
    the target is taken to be beyond the ball, for the direction's parallax, which
    ``total_uncertainty_deg`` (the one +- the page shows) includes.
    """
    aim = _pixel(aim_px, camera, "aim_px")
    floor = parse_floor_line(floor_line_px, camera)
    ball = (float(ball_lfu[0]), float(ball_lfu[1]))
    solved = solve_yaw(camera, ball, aim, floor)
    roll_pad = _roll_pad_deg(roll_deg)
    aim_uncertainty, aim_basis = aim_pixel_uncertainty(display_scale)
    uncertainty = _uncertainty(
        camera,
        ball,
        aim,
        floor,
        solved,
        tilt_pad_deg=tilt_pad_deg,
        roll_pad_deg=roll_pad,
        aim_px_uncertainty=aim_uncertainty,
    )
    uncertainty["aim_px_basis"] = aim_basis
    uncertainty["display_scale"] = display_scale
    warnings = []
    if not uncertainty["bounded"]:
        warnings.append(
            "At this lens height the camera's tilt cannot bound where the floor line puts "
            "the target: the aim has no honest +-. Aim at a far target instead, or skip "
            "the floor line."
        )
    if abs(solved.yaw_deg) > LARGE_YAW_WARNING_DEG:
        side = "right" if solved.yaw_deg > 0 else "left"
        warnings.append(
            f"The target is {abs(solved.yaw_deg):.0f} deg {side} of where the unit points: "
            "turn the unit to face the target, then set the aim point again."
        )
    if solved.fallback_reason is not None:
        warnings.append(
            "The floor line could not place the target "
            f"({solved.fallback_reason.replace('_', ' ')}): the aim point is taken as a direction."
        )
    iwr_yaw = solved.yaw_deg - CAMERA_TO_IWR_YAW_DEG
    offset_m = _ball_offset_from_aim_ray_m(camera, ball, aim)
    parallax = (
        0.0
        if solved.method != "direction"
        else (
            math.degrees(math.atan2(abs(offset_m), float(target_distance_m)))
            if target_distance_m is not None and float(target_distance_m) > 0.0
            else None
        )
    )
    total = uncertainty["total_deg"]
    shown = (
        round(math.hypot(float(total), float(parallax)), 4)
        if total is not None and parallax is not None
        else None
    )
    return {
        "schema": SCHEMA,
        "source": SOURCE,
        "sign_convention": SIGN_CONVENTION,
        "frame_size_px": [int(camera.image_width_px), int(camera.image_height_px)],
        "aim_px": [round(value, 2) for value in aim],
        "floor_line_px": [[round(x, 2), round(y, 2)] for x, y in floor] if floor else None,
        "floor_line_use": "places_a_near_aim_on_its_screen" if floor else "not_marked",
        "ball_lfu_m": [round(ball[0], 4), round(ball[1], 4)],
        "method": solved.method,
        "method_reason": (
            solved.fallback_reason
            if solved.fallback_reason is not None
            else ("floor_line_marked" if floor else "no_floor_line")
        ),
        "yaw_deg": round(solved.yaw_deg, 4),
        "direction_yaw_deg": round(solved.direction_yaw_deg, 4),
        "floor_point": (
            {
                "target_lfu_m": [round(value, 4) for value in solved.floor_point_lfu_m],
                "distance_from_ball_m": round(
                    math.hypot(
                        solved.floor_point_lfu_m[0] - ball[0], solved.floor_point_lfu_m[1] - ball[1]
                    ),
                    4,
                ),
                "aim_height_m": round(float(solved.aim_height_m or 0.0), 4),
            }
            if solved.floor_point_lfu_m is not None
            else None
        ),
        "ball_offset_from_aim_ray_m": round(offset_m, 4),
        # what a direction can be off by if the target is really this far from the ball
        "parallax_deg_at_m": {
            f"{distance:g}": round(math.degrees(math.atan2(abs(offset_m), distance)), 3)
            for distance in PARALLAX_REFERENCE_DISTANCES_M
        }
        if solved.method == "direction"
        else None,
        "kiosk_offset_deg": round(-solved.yaw_deg, 4),
        "kiosk_offset_convention": KIOSK_OFFSET_CONVENTION,
        "applies_to": {
            "camera_yaw_deg": round(solved.yaw_deg, 4),
            "iwr_yaw_deg": round(iwr_yaw, 4),
            "camera_to_iwr_yaw_deg": CAMERA_TO_IWR_YAW_DEG,
            "camera_to_iwr_basis": CAMERA_TO_IWR_BASIS,
        },
        "uncertainty": uncertainty,
        # the one +- the page shows: every part, the touch and the parallax at the
        # target's distance; None when a part has no bound (13 Oct)
        "total_uncertainty_deg": shown,
        "parallax_deg": round(parallax, 4) if parallax is not None else None,
        "parallax_distance_m": target_distance_m,
        "parallax_basis": target_distance_basis,
        "camera": {
            "source": camera.source,
            "focal_px": float(camera.focal_size_px),
            "pitch_deg": ground_patch.projection_parameters(camera)["pitch_deg"],
            "tilt": dict(camera_tilt) if camera_tilt is not None else None,
        },
        "warnings": warnings,
        "policy": target_line_policy(),
    }


def _point_moved(before: Any, after: Any) -> bool:
    try:
        return (
            math.hypot(float(after[0]) - float(before[0]), float(after[1]) - float(before[1]))
            > AIM_MOVE_TOLERANCE_PX
        )
    except (TypeError, ValueError, IndexError):
        return True


def target_line_moved(previous: Mapping | None, current: Mapping | None) -> bool:
    """Whether a target line is somewhere else: its aim, its floor line or its ball.

    Pixels compare within ``AIM_MOVE_TOLERANCE_PX``; the ball (the patch centre)
    within the patch's own tolerance.
    """
    if not isinstance(previous, Mapping) or not isinstance(current, Mapping):
        return True
    if _point_moved(previous.get("aim_px"), current.get("aim_px")):
        return True
    before, after = previous.get("floor_line_px"), current.get("floor_line_px")
    if (before is None) != (after is None):
        return True
    if before is not None and any(_point_moved(a, b) for a, b in zip(before, after)):
        return True
    try:
        moved_m = math.hypot(
            float(current["ball_lfu_m"][0]) - float(previous["ball_lfu_m"][0]),
            float(current["ball_lfu_m"][1]) - float(previous["ball_lfu_m"][1]),
        )
    except (KeyError, TypeError, ValueError, IndexError):
        return True
    return moved_m > ground_patch.PATCH_MOVE_TOLERANCE_M


def parallax_deg(record: Mapping, target_distance_m: float | None) -> float | None:
    """The angle a direction-method yaw can be off for a target this far from the ball."""
    if record.get("method") != "direction":
        return 0.0
    if target_distance_m is None or float(target_distance_m) <= 0.0:
        return None
    arm = abs(float(record.get("ball_offset_from_aim_ray_m") or 0.0))
    return math.degrees(math.atan2(arm, float(target_distance_m)))


def handoff(
    record: Mapping | None,
    *,
    target_distance_m: float | None,
    distance_basis: str | None,
) -> tuple[list[str], dict[str, Any]]:
    """The kiosk's two yaw flags (one value), and the record of what they hand over.

    ``target_distance_m`` is how far the target is taken to be for a direction's
    parallax: the net's range from the empty static capture, or the swing
    server's default (``distance_basis`` says which).
    """
    if not isinstance(record, Mapping):
        return [], {
            "schema": SCHEMA,
            "status": "not_set",
            "source": None,
            "reason": "no aim point confirmed for this setup and patch",
            "yaw_deg": None,
            "kiosk_offset_deg": None,
            "total_uncertainty_deg": None,
            "flags": [],
            "sign_convention": SIGN_CONVENTION,
        }
    if record.get("status") == SKIPPED:
        return ["--target-line-status", SKIPPED], {
            "schema": SCHEMA,
            "status": SKIPPED,
            "source": None,
            "reason": SKIPPED_REASON,
            "yaw_deg": None,
            "kiosk_offset_deg": None,
            "total_uncertainty_deg": None,
            "flags": [],
            "sign_convention": SIGN_CONVENTION,
            "skipped_at_utc": record.get("skipped_at_utc"),
        }
    offset = float(record["kiosk_offset_deg"])
    value = f"{offset:.6g}"
    args = [CAMERA_FLAG, value, IWR_FLAG, value]
    uncertainty = dict(record.get("uncertainty") or {})
    parallax = parallax_deg(record, target_distance_m)
    bounded = uncertainty.get("total_deg") is not None
    total = float(uncertainty["total_deg"]) if bounded else None
    if parallax is not None and total is not None:
        total = math.hypot(total, parallax)
    uncertainty.update(
        {
            "parallax_deg": round(parallax, 4) if parallax is not None else None,
            "parallax_distance_m": target_distance_m,
            "parallax_basis": distance_basis,
            "total_deg": round(total, 4) if total is not None else None,
        }
    )
    # the kiosk's Live tab shows the line and its one +- (GET /api/target-line)
    args += ["--target-line-yaw-deg", f"{float(record['yaw_deg']):.6g}"]
    if total is not None:
        args += ["--target-line-uncertainty-deg", f"{total:.4g}"]
    args += ["--target-line-source", str(record.get("source", SOURCE))]
    return args, {
        "schema": SCHEMA,
        "status": "set",
        "source": record.get("source", SOURCE),
        "yaw_deg": record["yaw_deg"],
        "kiosk_offset_deg": offset,
        "kiosk_offset_convention": KIOSK_OFFSET_CONVENTION,
        "sign_convention": SIGN_CONVENTION,
        "flags": [CAMERA_FLAG, IWR_FLAG],
        "aim_px": record.get("aim_px"),
        "floor_line_px": record.get("floor_line_px"),
        "frame_size_px": record.get("frame_size_px"),
        "method": record.get("method"),
        "method_reason": record.get("method_reason"),
        "ball_lfu_m": record.get("ball_lfu_m"),
        "applies_to": record.get("applies_to"),
        "uncertainty": uncertainty,
        "total_uncertainty_deg": round(total, 4) if total is not None else None,
        "confirmed_at_utc": record.get("confirmed_at_utc"),
        "warnings": list(record.get("warnings") or []),
    }


SKIPPED = "skipped"
SKIPPED_REASON = (
    "the tester skipped the aim point: horizontal angles are from the unit's boresight, "
    "not a target line"
)


def skipped_record() -> dict[str, Any]:
    """The aim step skipped (13 Oct): swings measure from the unit's boresight, labelled."""
    from datetime import datetime, timezone  # noqa: PLC0415

    return {
        "schema": SCHEMA,
        "status": SKIPPED,
        "source": None,
        "reason": SKIPPED_REASON,
        "skipped_at_utc": datetime.now(timezone.utc).isoformat(),
        "sign_convention": SIGN_CONVENTION,
    }


def compare_with_alignment_stick(
    record: Mapping,
    *,
    stick_yaw_deg: float,
    stick_uncertainty_deg: float,
    source: str = "alignment_stick",
) -> dict[str, Any]:
    """The aim point's yaw against an independent alignment-stick yaw (Part B's check).

    The stick's yaw takes the same sign convention. Nothing is replaced: the
    comparison is recorded, and a disagreement beyond ``STICK_AGREEMENT_SIGMA``
    combined uncertainties says the aim point, the stick or the unit moved.
    """
    aim_yaw = float(record["yaw_deg"])
    aim_uncertainty = float((record.get("uncertainty") or {}).get("total_deg") or 0.0)
    # an unbounded aim never agrees by its own width
    combined = math.hypot(aim_uncertainty, float(stick_uncertainty_deg))
    difference = aim_yaw - float(stick_yaw_deg)
    return {
        "source": source,
        "sign_convention": SIGN_CONVENTION,
        "aim_point_yaw_deg": aim_yaw,
        "aim_point_uncertainty_deg": aim_uncertainty,
        "stick_yaw_deg": float(stick_yaw_deg),
        "stick_uncertainty_deg": float(stick_uncertainty_deg),
        "difference_deg": difference,
        "combined_uncertainty_deg": combined,
        "agrees": abs(difference) <= STICK_AGREEMENT_SIGMA * max(combined, 1e-9),
        "agreement_sigma": STICK_AGREEMENT_SIGMA,
        "aim_px": record.get("aim_px"),
        "confirmed_at_utc": record.get("confirmed_at_utc"),
    }

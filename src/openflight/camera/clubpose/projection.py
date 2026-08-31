"""Frozen Phase 1b club-state solver, promoted to the Phase 3 fusion package."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import lru_cache

import cv2
import numpy as np

# Measured rig geometry (tape chain, 2026-08-26). Two DIFFERENT heights meet
# here and they were previously the same constant:
#
#   * the OV9281 lens sits 203.2 mm above the FLOOR (the kiosk log's
#     `mount_height_m`), and
#   * the world origin is the BALL CENTRE, which the tape puts 40 mm above the
#     floor -- so the camera's world z is 163.2 mm, not 203.2 mm.
#
# `camera_center_world` takes the height ABOVE THE BALL for that reason. Using
# the floor height there put the camera 40 mm high and, because the centre is
# forced onto the 1581 mm range sphere, ~4 mm too near in x as well.
CAMERA_LENS_HEIGHT_MM = 203.2
BALL_CENTRE_HEIGHT_MM = 40.0
CAMERA_HEIGHT_ABOVE_BALL_MM = CAMERA_LENS_HEIGHT_MM - BALL_CENTRE_HEIGHT_MM
CAMERA_BALL_RANGE_MM = 1581.0

# The lens is offset laterally from the ball line: -60.325 mm by tape (-55.7 mm
# solved from the teed ball's observed column). Declared here with the rest of
# the chain; `camera_center_world` consumes it.
CAMERA_LATERAL_OFFSET_MM = -60.325

# Kept because callers and docstrings name it. It is the FLOOR-referenced lens
# height and is NOT a world coordinate; anything placing the camera wants
# `CAMERA_HEIGHT_ABOVE_BALL_MM`.
CAMERA_HEIGHT_MM = CAMERA_LENS_HEIGHT_MM

# Where the camera POINTS. Solved per shot from the teed ball's own row over
# the 21 shots of session 20260825_181734: median -0.21 deg, sd 0.12 deg. The
# mount is level; the documented default is rounded to -0.22 deg, which is
# inside a tenth of the per-shot scatter.
CAMERA_PITCH_DEG = -0.22
# UNRESOLVED. The session's net lines image with a 3.18 deg tilt. That is
# consistent with a camera roll and it is NOT evidence of one -- nothing has
# established that the nets are plumb, and the teed ball is a point target that
# carries no roll information at all. Left at zero until something measures it.
CAMERA_ROLL_DEG = 0.0

# Superseded Phase-1b values, kept for provenance and for the radar.
# `NOMINAL_RANGE_MM` is the radar's own measured slant tee range and still
# anchors `RADAR_CENTER_WORLD`; it is NOT the camera-to-ball range.
# `PHASE1B_CAMERA_HEIGHT_MM` is the lens height the frozen Phase-1b solver
# assumed before the mount was taped, and nothing derives geometry from it.
NOMINAL_RANGE_MM = 1_575.0
PHASE1B_CAMERA_HEIGHT_MM = 209.55

RADAR_STATIC_BIAS_MM = 66.0069821
BALL_RADIUS_MM = 42.67 / 2.0
FRAME_TO_IMPACT_S = 1.0e-3
FRAME_PERIOD_S = 2.137e-3
MAX_EXTRAPOLATION_S = 2.5e-3
CENTROID_NOISE_PX = 0.5
MOMENT_EDGE_NOISE_PX = 0.5
RANGE_NOISE_MM = 3.0
FIT_RESIDUAL_LIMIT_PX = 8.0
AMBIGUITY_RATIO_MIN = 1.10
MODEL_VERSION = "phase1b-v1-frozen"
RADAR_HEIGHT_MM = 152.4

TARGET_WORLD = np.zeros(3)
WORLD_RIGHT = np.array([0.0, 1.0, 0.0])
WORLD_UP = np.array([0.0, 0.0, 1.0])
FACE_NORMAL = np.array([1.0, 0.0, 0.0])


def taped_camera_ball_range_mm(
    radar_range_mm: float = NOMINAL_RANGE_MM,
    radar_height_mm: float = RADAR_HEIGHT_MM,
    ball_height_mm: float = BALL_CENTRE_HEIGHT_MM,
    lens_height_mm: float = CAMERA_LENS_HEIGHT_MM,
    lateral_mm: float = CAMERA_LATERAL_OFFSET_MM,
) -> float:
    """Camera-to-ball slant range from the tape chain, not from a comment.

    The radar's own measured slant tee range fixes the DOWNRANGE distance to
    the ball once its height above the ball centre is taken out; the camera
    then sits at that same downrange distance, offset laterally and raised to
    the lens height. Running it out gives 1580.6 mm, which is where
    ``CAMERA_BALL_RANGE_MM`` came from.
    """
    radar_rise = float(radar_height_mm) - float(ball_height_mm)
    squared = float(radar_range_mm) ** 2 - radar_rise**2
    if squared <= 0.0:
        raise ValueError("radar slant range must exceed its own height above the ball")
    downrange = math.sqrt(squared)
    rise = float(lens_height_mm) - float(ball_height_mm)
    return math.sqrt(downrange**2 + float(lateral_mm) ** 2 + rise**2)


def camera_center_world(
    height_above_ball_mm: float = CAMERA_HEIGHT_ABOVE_BALL_MM,
    range_mm: float = CAMERA_BALL_RANGE_MM,
    lateral_mm: float = 0.0,
) -> np.ndarray:
    """Camera centre for a lens ``height_above_ball_mm`` up and ``range_mm`` away.

    The height is measured from the BALL CENTRE -- the world origin -- not from
    the floor. The camera sits behind the ball on the -x side; the slant range,
    the height and the lateral offset fix the remaining coordinate exactly.
    """
    height = float(height_above_ball_mm)
    slant = float(range_mm)
    lateral = float(lateral_mm)
    if not all(math.isfinite(value) for value in (height, slant, lateral)):
        raise ValueError("camera height, range and lateral offset must be finite")
    remaining = slant**2 - height**2 - lateral**2
    if height < 0.0 or slant <= 0.0 or remaining <= 0.0:
        raise ValueError(
            f"camera height {height} mm and lateral offset {lateral} mm must sit "
            f"inside range {slant} mm, above the ball centre"
        )
    return np.array([-math.sqrt(remaining), lateral, height])


@lru_cache(maxsize=32)
def _rotation_world_to_camera(pitch_deg: float, roll_deg: float) -> np.ndarray:
    """World-to-camera rotation for a boresight at ``pitch_deg`` / ``roll_deg``.

    Rows are the camera's right, down and forward axes in world coordinates.
    At zero pitch and roll the camera is LEVEL: it looks straight downrange
    along world +x, with world +y on the image right and world +z up. Positive
    pitch raises the boresight; positive roll turns the camera clockwise about
    the boresight, so a point on the image right swings DOWN.

    This used to aim the boresight at the world origin, which made pitch a
    consequence of the mount height rather than a property of the mount. At
    163.2 mm over 1571 mm that is 5.9 deg of invented down-tilt, and the teed
    ball -- whose world position is taped -- landed 46.8 px off its own pixel.

    The result is cached and read-only: it is rebuilt for every projected point
    otherwise, and callers must not be able to corrupt a shared basis.
    """
    if not all(math.isfinite(value) for value in (pitch_deg, roll_deg)):
        raise ValueError("camera pitch and roll must be finite")
    pitch = math.radians(float(pitch_deg))
    roll = math.radians(float(roll_deg))
    forward = np.array([math.cos(pitch), 0.0, math.sin(pitch)])
    down = np.array([math.sin(pitch), 0.0, -math.cos(pitch)])
    right = np.array(WORLD_RIGHT, dtype=float)
    if roll:
        # Rodrigues about the boresight. Rotating BOTH image axes by the same
        # rotation preserves right x down = -forward, so the frame stays the
        # left-handed imaging frame `TestWorldFrameHandedness` pins.
        cosine, sine = math.cos(roll), math.sin(roll)
        cross = np.array(
            [
                [0.0, -forward[2], forward[1]],
                [forward[2], 0.0, -forward[0]],
                [-forward[1], forward[0], 0.0],
            ]
        )
        rotation_about_boresight = np.eye(3) + sine * cross + (1.0 - cosine) * (cross @ cross)
        right = rotation_about_boresight @ right
        down = rotation_about_boresight @ down
    rotation = np.stack([right, down, forward])
    rotation.flags.writeable = False
    return rotation


def camera_pitch_from_ball_row(
    ball_row_px: float, camera: "CameraPreset", ball_world_mm: np.ndarray = TARGET_WORLD
) -> float:
    """Solve the boresight pitch that puts the teed ball on ``ball_row_px``.

    Closed form, and exact: the ball's world position is taped and its row is
    observed, so the only unknown in

        (row - cy) / fy = (sin p * dx - cos p * dz) / (cos p * dx + sin p * dz)

    is ``p``. The camera's LATERAL offset never enters -- it moves the ball's
    column, not its row -- so the two calibrations separate and neither has to
    be guessed before the other.

    Over the 21 shots of session 20260825_181734 this gives a median of
    -0.21 deg with a standard deviation of 0.12 deg, which is a level mount.
    See `CAMERA_PITCH_DEG`.
    """
    delta = np.asarray(ball_world_mm, dtype=float).reshape(3) - camera.center_world
    downrange, rise = float(delta[0]), float(delta[2])
    k = (float(ball_row_px) - camera.cy) / camera.fy
    return math.degrees(math.atan2(k * downrange + rise, downrange - k * rise))


CAMERA_CENTER_WORLD = camera_center_world()
DEFAULT_CENTER_WORLD_MM = (
    float(CAMERA_CENTER_WORLD[0]),
    float(CAMERA_CENTER_WORLD[1]),
    float(CAMERA_CENTER_WORLD[2]),
)
# The Phase-1b centre, superseded by the tape chain above. Kept so a frozen
# result can be reproduced, not because anything should build geometry on it.
# Phase 1b measured its lens height from the floor and used it as a world z, so
# it is reproduced exactly that way.
PHASE1B_CAMERA_CENTER_WORLD = camera_center_world(PHASE1B_CAMERA_HEIGHT_MM, NOMINAL_RANGE_MM)
RADAR_CENTER_WORLD = np.array(
    [
        -math.sqrt(NOMINAL_RANGE_MM**2 - RADAR_HEIGHT_MM**2),
        0.0,
        RADAR_HEIGHT_MM,
    ]
)


@dataclass(frozen=True)
class CameraPreset:
    """One explicit camera/crop configuration from approved spec section 5."""

    name: str
    width: int
    height: int
    fx: float
    fy: float
    cx: float
    cy: float
    plate_scale_px_per_mm: float
    sensor_crop: tuple[int, int, int, int]
    sampling_increment: tuple[int, int]
    isp_offset: tuple[int, int]
    orientation: str
    gate_b1_passed: bool
    physical_status: str
    # Where this camera is. Part of the camera model rather than a module
    # constant, so a preset and the geometry it is projected through cannot
    # disagree. Stored as a tuple to keep the dataclass hashable and frozen.
    center_world_mm: tuple[float, float, float] = field(default=DEFAULT_CENTER_WORLD_MM)
    # And where it POINTS, for the same reason. Zero is a LEVEL camera looking
    # straight downrange; it is not "aimed at the ball". `measured_camera()`
    # carries the solved mount pitch.
    pitch_deg: float = 0.0
    roll_deg: float = 0.0

    @property
    def horizontal_fov_deg(self) -> float:
        return math.degrees(2.0 * math.atan(self.width / (2.0 * self.fx)))

    @property
    def center_world(self) -> np.ndarray:
        """This camera's optical centre in world millimetres."""
        return np.asarray(self.center_world_mm, dtype=float)

    @property
    def rotation_world_to_camera(self) -> np.ndarray:
        """Read-only world-to-camera rotation implied by this camera's mount."""
        return _rotation_world_to_camera(float(self.pitch_deg), float(self.roll_deg))


@dataclass(frozen=True)
class ClubTemplate:
    """Named analytic rear-view silhouette and speed distribution."""

    name: str
    radius_u_mm: float
    radius_v_mm: float
    speed_mean_mm_s: float
    speed_sd_mm_s: float
    impact_u_limit_mm: float
    impact_v_limit_mm: float
    velocity_direction: tuple[float, float, float]


@dataclass(frozen=True)
class SilhouetteObservation:
    centroid_uv: np.ndarray
    covariance_px2: np.ndarray


@dataclass(frozen=True)
class ClubState:
    ok: bool
    reason: str | None
    frame_center_world: np.ndarray | None
    roll_rad: float | None
    fit_residual_px: float | None
    calibrated_range_mm: float | None
    predicted_covariance_px2: np.ndarray | None


def camera_presets() -> dict[str, CameraPreset]:
    """Return independent intrinsics; no fixed-FOV scaling is permitted.

    These are HYPOTHETICAL configurations only, and each says so in its own
    ``physical_status``. For the camera actually in the kiosk use
    ``openflight.camera.clubpose.fit.measured_camera()``.

    The retired ``A0`` entry used to live here claiming
    ``existing_320x200_plus_10us_strobe`` -- that it described the shipped
    hardware -- with fx = 1033 px and a 0.656 px/mm plate scale. The shipped
    camera measures fx = 466.7 px and 0.295 px/mm, so A0 was wrong by 2.2x in
    the quantity that turns pixels into millimetres. Nothing called it, which
    is the only reason it never produced a wrong number.
    """
    return {
        "A1": CameraPreset(
            name="A1",
            width=320,
            height=200,
            fx=2063.0,
            fy=2063.0,
            cx=160.0,
            cy=100.0,
            plate_scale_px_per_mm=1.31,
            sensor_crop=(480, 150, 320, 200),
            sampling_increment=(1, 1),
            isp_offset=(0, 0),
            orientation="landscape_crop_metadata_sensitivity",
            gate_b1_passed=False,
            physical_status="plate_scale_sensitivity_only",
        ),
        "B": CameraPreset(
            name="B",
            width=1280,
            height=200,
            fx=2095.0,
            fy=2095.0,
            cx=640.0,
            cy=100.0,
            plate_scale_px_per_mm=1.33,
            sensor_crop=(0, 300, 1280, 200),
            sampling_increment=(1, 1),
            isp_offset=(0, 0),
            orientation="portrait_experimental",
            gate_b1_passed=False,
            physical_status="experimental_gate_b1_not_run",
        ),
    }


def _project(points_world: np.ndarray, camera: CameraPreset) -> tuple[np.ndarray, np.ndarray]:
    points = np.asarray(points_world, dtype=float).reshape(-1, 3)
    cam = (points - camera.center_world) @ camera.rotation_world_to_camera.T
    in_front = cam[:, 2] > 1e-9
    safe_z = np.where(in_front, cam[:, 2], 1.0)
    uv = np.column_stack(
        [
            camera.fx * cam[:, 0] / safe_z + camera.cx,
            camera.fy * cam[:, 1] / safe_z + camera.cy,
        ]
    )
    return uv, in_front


def _ray_world(uv: np.ndarray, camera: CameraPreset) -> np.ndarray:
    xy = np.array([(uv[0] - camera.cx) / camera.fx, (uv[1] - camera.cy) / camera.fy, 1.0])
    ray = xy @ camera.rotation_world_to_camera
    return ray / np.linalg.norm(ray)


def _backproject_range(
    uv: np.ndarray,
    range_mm: float,
    camera: CameraPreset,
    range_origin_world: np.ndarray | None = None,
) -> np.ndarray:
    """Intersect a camera ray with a range sphere around the supplied sensor.

    ``range_origin_world`` defaults to the camera's own centre, so a camera-side
    range needs no second geometry argument.
    """
    ray = _ray_world(uv, camera)
    center = camera.center_world
    if range_origin_world is None:
        range_origin_world = center
    offset = center - np.asarray(range_origin_world, dtype=float)
    projection = float(offset @ ray)
    discriminant = projection**2 - float(offset @ offset) + float(range_mm) ** 2
    if discriminant < 0.0:
        return np.full(3, np.nan)
    distance = -projection + math.sqrt(discriminant)
    return center + ray * distance


def _range_mm(
    point_world: np.ndarray, range_origin_world: np.ndarray = CAMERA_CENTER_WORLD
) -> float:
    return float(np.linalg.norm(np.asarray(point_world) - range_origin_world))


def _face_axes(roll_rad: float) -> tuple[np.ndarray, np.ndarray]:
    c = math.cos(float(roll_rad))
    s = math.sin(float(roll_rad))
    return c * WORLD_RIGHT + s * WORLD_UP, -s * WORLD_RIGHT + c * WORLD_UP


def _velocity(template: ClubTemplate, speed_mm_s: float, reverse: bool = False) -> np.ndarray:
    direction = np.asarray(template.velocity_direction, dtype=float)
    direction /= np.linalg.norm(direction)
    return direction * float(speed_mm_s) * (-1.0 if reverse else 1.0)


def _projected_velocity(
    center_world: np.ndarray, velocity_world: np.ndarray, camera: CameraPreset
) -> np.ndarray:
    dt = 1.0e-5
    uv_pair, front = _project(
        np.stack([center_world - velocity_world * dt / 2, center_world + velocity_world * dt / 2]),
        camera,
    )
    if not bool(np.all(front)):
        return np.zeros(2)
    return (uv_pair[1] - uv_pair[0]) / dt


def _projection_jacobian(center_world: np.ndarray, camera: CameraPreset) -> np.ndarray:
    """Pixel derivative for one millimetre along world-right/world-up."""
    epsilon = 1.0e-3
    points = np.stack(
        [
            center_world - WORLD_RIGHT * epsilon,
            center_world + WORLD_RIGHT * epsilon,
            center_world - WORLD_UP * epsilon,
            center_world + WORLD_UP * epsilon,
        ]
    )
    uv, front = _project(points, camera)
    if not bool(np.all(front)):
        return np.full((2, 2), np.nan)
    return np.column_stack([(uv[1] - uv[0]) / (2.0 * epsilon), (uv[3] - uv[2]) / (2.0 * epsilon)])


def _silhouette_moments(
    center_world: np.ndarray,
    roll_rad: float,
    velocity_world: np.ndarray,
    exposure_us: float,
    camera: CameraPreset,
    template: ClubTemplate,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    center_uv, front = _project(center_world[None, :], camera)
    if not bool(front[0]):
        nan = np.full(2, np.nan)
        return nan, np.full((2, 2), np.nan), nan, nan, nan
    center_uv = center_uv[0]
    axis_u, axis_v = _face_axes(roll_rad)
    jacobian = _projection_jacobian(center_world, camera)
    if not bool(np.all(np.isfinite(jacobian))):
        nan = np.full(2, np.nan)
        return nan, np.full((2, 2), np.nan), nan, nan, nan
    body_u = np.array([float(axis_u @ WORLD_RIGHT), float(axis_u @ WORLD_UP)])
    body_v = np.array([float(axis_v @ WORLD_RIGHT), float(axis_v @ WORLD_UP)])
    vector_u = jacobian @ body_u * template.radius_u_mm
    vector_v = jacobian @ body_v * template.radius_v_mm
    blur_vector = _projected_velocity(center_world, velocity_world, camera) * (
        float(exposure_us) * 1e-6
    )
    covariance = (
        np.outer(vector_u, vector_u) / 4.0
        + np.outer(vector_v, vector_v) / 4.0
        + np.outer(blur_vector, blur_vector) / 12.0
    )
    extents = np.sqrt(vector_u**2 + vector_v**2) + np.abs(blur_vector) / 2.0
    return center_uv, covariance, extents, vector_u, vector_v


def _visible(center_uv: np.ndarray, extents: np.ndarray, camera: CameraPreset) -> bool:
    if not bool(np.all(np.isfinite(center_uv))) or not bool(np.all(np.isfinite(extents))):
        return False
    return bool(
        center_uv[0] - extents[0] >= 0.0
        and center_uv[0] + extents[0] < camera.width
        and center_uv[1] - extents[1] >= 0.0
        and center_uv[1] + extents[1] < camera.height
    )


def _ball_geometry(
    ball_center_world: np.ndarray, camera: CameraPreset
) -> tuple[np.ndarray, np.ndarray]:
    center_uv, front = _project(ball_center_world[None, :], camera)
    if not bool(front[0]):
        return np.full(2, np.nan), np.full(2, np.nan)
    center_uv = center_uv[0]
    endpoints, endpoint_front = _project(
        np.stack(
            [
                ball_center_world + WORLD_RIGHT * BALL_RADIUS_MM,
                ball_center_world + WORLD_UP * BALL_RADIUS_MM,
            ]
        ),
        camera,
    )
    if not bool(np.all(endpoint_front)):
        return np.full(2, np.nan), np.full(2, np.nan)
    extents = np.abs(endpoints - center_uv).max(axis=0)
    return center_uv, extents


def _silhouette_polygon(
    center_uv: np.ndarray, vector_u: np.ndarray, vector_v: np.ndarray, blur_vector: np.ndarray
) -> np.ndarray:
    theta = np.linspace(0.0, 2.0 * np.pi, 24, endpoint=False)
    ellipse = (
        center_uv[None, :]
        + np.cos(theta)[:, None] * vector_u[None, :]
        + np.sin(theta)[:, None] * vector_v[None, :]
    )
    points = np.vstack([ellipse - blur_vector / 2.0, ellipse + blur_vector / 2.0])
    return cv2.convexHull(points.astype(np.float32)).reshape(-1, 2)


def _polygon_iou(a: np.ndarray, b: np.ndarray) -> float:
    area_a = abs(float(cv2.contourArea(a)))
    area_b = abs(float(cv2.contourArea(b)))
    if area_a <= 0.0 or area_b <= 0.0:
        return 0.0
    intersection, _ = cv2.intersectConvexConvex(a.astype(np.float32), b.astype(np.float32))
    union = area_a + area_b - float(intersection)
    return float(intersection / union) if union > 0.0 else 0.0


def _normalize_roll(angle: float) -> float:
    return (float(angle) + math.pi / 2.0) % math.pi - math.pi / 2.0

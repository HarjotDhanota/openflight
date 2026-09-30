"""The ground patch: a 2 ft x 2 ft square of ground where the ball will be (P8-1, D14).

The tester places the patch on the live 1280x800 view; everything the setup does
takes its window from where it is put. The patch is stored as ground coordinates
in the rig frame and projected through the camera model (the rig file, the LIS3DH
and the unit's camera tilt, P8-5) into the picture, where it is drawn in true
perspective. At the lens's 95 mm it is wide and shallow on screen.

The rig frame is the camera models' lateral, forward, up frame with its origin on
the hitting surface directly below the lens: lateral is positive to the right in
the camera's view, forward runs along the camera's heading, up is height above the
surface the ball rests on. The radar's phase centre sits at ``radar_origin_lfu``
in the same frame (a little below and behind the lens).

Until the camera tilt is calibrated (P8-5), the image rows of anything the
camera sees are uncertain by a few degrees: at 95 mm, 1 deg moves a ball 1.25 m
out by about 0.25 m. So the outline the camera searches is padded vertically by
that uncertainty (never sideways), and every distance window derived from the
patch is taken over that padded outline, so the ball is inside it whether the
tester placed the patch by eye over the ball or by its displayed distance.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np

GOLF_BALL_DIAMETER_M = 0.04267
BALL_CENTER_HEIGHT_M = GOLF_BALL_DIAMETER_M / 2.0
PATCH_SCHEMA = "openflight.tester_ground_patch.v1"
# 2 ft x 2 ft (D14)
PATCH_SIZE_M = 0.61
# The patch centre's distance from the radar, along the ground. The sensors support
# at least 0.8-2.5 m; the camera sees a ball 3 m out at about 13 px, which it can
# still fit, and the setup capture is widened for a far patch (P8-3).
PATCH_CENTRE_DISTANCE_M = (0.8, 3.0)
PATCH_DEFAULT_DISTANCE_M = 1.25
# The camera's vertical (pitch plus principal-point row) is uncertain by about 3 deg
# until P8-5 calibrates it: harjot-indoor-test-1 put the floor 2-4 deg below where
# the level, nominal-centre model puts it. The pad covers that with a margin.
UNCALIBRATED_TILT_PAD_DEG = 4.0
# Once calibrated, what is left: the LIS3DH's settling, a mat under the ball but
# not the unit, and the calibration's own uncertainty.
CALIBRATED_TILT_PAD_DEG = 1.0
# The distance windows (the radar's range window, the camera's size window) are
# taken over the ball-centre outline padded by this much: the whole search pad
# before calibration, and the calibration's own few tenths of a degree after it.
# At 95 mm every degree is a quarter of a metre at 1.25 m, so a calibrated window
# padded by the search's full degree would reach past 2.8 m.
UNCALIBRATED_WINDOW_PAD_DEG = UNCALIBRATED_TILT_PAD_DEG
CALIBRATED_WINDOW_PAD_DEG = 0.5
# The LIS3DH roll is recorded but not applied to the camera (camera_roll, C8), so
# the rows at the outline's ends are padded for it; without a reading, this much.
UNKNOWN_ROLL_PAD_DEG = 3.0
MIN_ROLL_PAD_DEG = 1.0
# A ball's apparent size may differ from the nominal focal length's prediction by
# this fraction (focal length 8 %, the lit-ball fit about 20 %).
SIZE_TOLERANCE = 0.25
# Radar slant-range window margin beyond the patch's near and far edges.
RADAR_WINDOW_MARGIN_M = 0.10
# A ray in the padded outline that never reaches the surface (at or above the
# horizon) is taken to reach this far; no sensor looks further for a ball at address.
FAR_CAP_M = 4.0
# A re-confirmed patch whose centre moved less than this has not moved.
PATCH_MOVE_TOLERANCE_M = 0.02
_EDGE_SAMPLES = 16


def ground_patch_policy() -> dict[str, Any]:
    """Every constant of the patch, for the setup's evidence and estimator identity."""
    return {
        "schema": PATCH_SCHEMA,
        "size_m": PATCH_SIZE_M,
        "centre_distance_m": list(PATCH_CENTRE_DISTANCE_M),
        "default_distance_m": PATCH_DEFAULT_DISTANCE_M,
        "frame": "rig_lfu_origin_on_the_surface_below_the_lens",
        "distance": "radar_slant_range_to_a_ball_centre_at_the_patch_centre",
        "uncalibrated_tilt_pad_deg": UNCALIBRATED_TILT_PAD_DEG,
        "calibrated_tilt_pad_deg": CALIBRATED_TILT_PAD_DEG,
        "uncalibrated_window_pad_deg": UNCALIBRATED_WINDOW_PAD_DEG,
        "calibrated_window_pad_deg": CALIBRATED_WINDOW_PAD_DEG,
        "unknown_roll_pad_deg": UNKNOWN_ROLL_PAD_DEG,
        "min_roll_pad_deg": MIN_ROLL_PAD_DEG,
        "pad": "rows_only_columns_exact",
        "size_tolerance": SIZE_TOLERANCE,
        "radar_window_margin_m": RADAR_WINDOW_MARGIN_M,
        "far_cap_m": FAR_CAP_M,
        "windows": "taken_over_the_padded_ball_centre_outline",
    }


@dataclass(frozen=True)
class GroundPatch:
    """A square of ground, centred ``(lateral, forward)`` in the rig frame."""

    centre_lateral_m: float
    centre_forward_m: float
    size_m: float = PATCH_SIZE_M

    def __post_init__(self) -> None:
        for name in ("centre_lateral_m", "centre_forward_m", "size_m"):
            value = getattr(self, name)
            if isinstance(value, bool) or not math.isfinite(float(value)):
                raise ValueError(f"{name} must be finite")
            object.__setattr__(self, name, float(value))
        if self.size_m <= 0.0:
            raise ValueError("the patch size must be positive")
        if self.centre_forward_m - self.size_m / 2.0 <= 0.0:
            raise ValueError("the patch must lie in front of the lens")

    def corners(self, height_m: float = 0.0) -> np.ndarray:
        """Near-left, near-right, far-right, far-left, at ``height_m``."""
        half = self.size_m / 2.0
        x, y = self.centre_lateral_m, self.centre_forward_m
        return np.asarray(
            [
                (x - half, y - half, height_m),
                (x + half, y - half, height_m),
                (x + half, y + half, height_m),
                (x - half, y + half, height_m),
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "centre_lfu_m": [self.centre_lateral_m, self.centre_forward_m],
            "size_m": self.size_m,
            "frame": "rig_lfu_origin_on_the_surface_below_the_lens",
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "GroundPatch":
        centre = payload["centre_lfu_m"]
        return cls(float(centre[0]), float(centre[1]), float(payload.get("size_m", PATCH_SIZE_M)))


def _origins(camera) -> tuple[np.ndarray, np.ndarray]:
    return (
        np.asarray(camera.camera_origin_lfu, dtype=float),
        np.asarray(camera.radar_origin_lfu, dtype=float),
    )


def patch_distance(
    camera, patch: GroundPatch, ball_center_height_m: float = BALL_CENTER_HEIGHT_M
) -> dict[str, float]:
    """What the tester is shown: how far the patch centre is from the radar, and how far aside.

    ``distance_m`` is the radar's slant range to a ball centre resting at the patch
    centre (what a tape from the radar to the ball measures); ``ground_distance_m``
    is along the surface; ``side_offset_m`` is from the radar's axis, + right.
    """
    _camera, radar = _origins(camera)
    ball = np.asarray([patch.centre_lateral_m, patch.centre_forward_m, ball_center_height_m])
    return {
        "distance_m": float(np.linalg.norm(ball - radar)),
        "ground_distance_m": float(
            math.hypot(patch.centre_lateral_m - radar[0], patch.centre_forward_m - radar[1])
        ),
        "side_offset_m": float(patch.centre_lateral_m - radar[0]),
    }


def patch_at(
    camera,
    ground_distance_m: float = PATCH_DEFAULT_DISTANCE_M,
    side_offset_m: float = 0.0,
    size_m: float = PATCH_SIZE_M,
) -> GroundPatch:
    """The patch whose centre lies ``ground_distance_m`` from the radar, ``side_offset_m`` aside."""
    _camera, radar = _origins(camera)
    low, high = PATCH_CENTRE_DISTANCE_M
    distance = min(max(float(ground_distance_m), low), high)
    side = max(-distance * 0.9, min(distance * 0.9, float(side_offset_m)))
    forward = math.sqrt(distance**2 - side**2)
    return GroundPatch(radar[0] + side, radar[1] + forward, size_m)


def clamp_patch(camera, patch: GroundPatch) -> GroundPatch:
    """The same patch, with its centre kept inside the supported distances."""
    facts = patch_distance(camera, patch)
    distance = facts["ground_distance_m"]
    low, high = PATCH_CENTRE_DISTANCE_M
    if low <= distance <= high and patch.centre_forward_m > 0.0:
        return patch
    _camera, radar = _origins(camera)
    heading = math.atan2(
        patch.centre_lateral_m - radar[0], max(patch.centre_forward_m - radar[1], 1e-6)
    )
    distance = min(max(distance, low), high)
    return GroundPatch(
        radar[0] + distance * math.sin(heading),
        radar[1] + distance * math.cos(heading),
        patch.size_m,
    )


def patch_from_centre_pixel(
    camera, pixel_xy: Sequence[float], size_m: float = PATCH_SIZE_M
) -> GroundPatch:
    """The patch whose centre is the ground point under ``pixel_xy``, kept in range.

    A pixel at or above the horizon has no ground under it; the patch then goes
    to the farthest supported distance along that pixel's heading.
    """
    origin, _radar = _origins(camera)
    ray = np.asarray(camera.ray_model.rays(np.asarray(pixel_xy, dtype=float)), dtype=float)
    if ray.shape != (3,) or not np.all(np.isfinite(ray)):
        raise ValueError("the camera model returned an invalid ray")
    if ray[2] < -1e-9:
        distance = -origin[2] / ray[2]
        point = origin + distance * ray
        patch = GroundPatch(point[0], max(point[1], size_m / 2.0 + 1e-3), size_m)
    else:
        heading = math.atan2(ray[0], max(ray[1], 1e-6))
        far = PATCH_CENTRE_DISTANCE_M[1]
        patch = GroundPatch(far * math.sin(heading), far * math.cos(heading), size_m)
    return clamp_patch(camera, patch)


def _pixels(camera, points: np.ndarray) -> np.ndarray:
    from openflight.camera.reference_ball_range import project_to_pixel  # noqa: PLC0415

    return np.asarray([project_to_pixel(camera, point) for point in points], dtype=float)


def _roll_pad_deg(roll_deg: float | None) -> float:
    if roll_deg is None or isinstance(roll_deg, bool) or not math.isfinite(float(roll_deg)):
        return UNKNOWN_ROLL_PAD_DEG
    return max(abs(float(roll_deg)), MIN_ROLL_PAD_DEG)


def point_in_polygon(x: float, y: float, polygon: Sequence[Sequence[float]]) -> bool:
    """Whether (x, y) lies inside a simple polygon (even-odd rule)."""
    inside = False
    points = [(float(px), float(py)) for px, py in polygon]
    count = len(points)
    for index in range(count):
        x0, y0 = points[index]
        x1, y1 = points[(index + 1) % count]
        if (y0 > y) != (y1 > y):
            crossing = x0 + (y - y0) * (x1 - x0) / (y1 - y0)
            if x < crossing:
                inside = not inside
    return inside


def points_in_polygon(xs: Any, ys: Any, polygon: Sequence[Sequence[float]]) -> np.ndarray:
    """Vectorised ``point_in_polygon``."""
    xs = np.asarray(xs, dtype=float)
    ys = np.asarray(ys, dtype=float)
    inside = np.zeros(xs.shape, dtype=bool)
    points = [(float(px), float(py)) for px, py in polygon]
    count = len(points)
    for index in range(count):
        x0, y0 = points[index]
        x1, y1 = points[(index + 1) % count]
        straddles = (y0 > ys) != (y1 > ys)
        if y1 == y0:
            continue
        crossing = x0 + (ys - y0) * (x1 - x0) / (y1 - y0)
        inside ^= straddles & (xs < crossing)
    return inside


def _bounds(points: np.ndarray, width: int, height: int) -> list[int]:
    x0 = int(math.floor(float(np.min(points[:, 0]))))
    y0 = int(math.floor(float(np.min(points[:, 1]))))
    x1 = int(math.ceil(float(np.max(points[:, 0])))) + 1
    y1 = int(math.ceil(float(np.max(points[:, 1])))) + 1
    x0, y0 = min(max(x0, 0), width - 1), min(max(y0, 0), height - 1)
    x1, y1 = max(min(x1, width), x0 + 1), max(min(y1, height), y0 + 1)
    return [x0, y0, x1, y1]


@dataclass(frozen=True)
class PatchProjection:  # pylint: disable=too-many-instance-attributes
    """The patch in one camera mode's pixels, and every window taken from it."""

    patch: GroundPatch
    image_size_px: tuple[int, int]
    outline_px: tuple[tuple[float, float], ...]
    ball_outline_px: tuple[tuple[float, float], ...]
    search_outline_px: tuple[tuple[float, float], ...]
    box_px: tuple[int, int, int, int]
    search_box_px: tuple[int, int, int, int]
    lens_distance_m: tuple[float, float]
    radar_slant_m: tuple[float, float]
    radar_window_m: tuple[float, float]
    diameter_px: tuple[float, float]
    tilt_pad_deg: float
    window_pad_deg: float
    roll_pad_deg: float
    pad_px: float
    nominal: Mapping[str, float]

    def to_dict(self) -> dict[str, Any]:
        rounded = lambda points: [[round(x, 1), round(y, 1)] for x, y in points]  # noqa: E731
        return {
            "patch": self.patch.to_dict(),
            "frame_size_px": list(self.image_size_px),
            "outline_px": rounded(self.outline_px),
            "ball_outline_px": rounded(self.ball_outline_px),
            "search_outline_px": rounded(self.search_outline_px),
            "box_px": list(self.box_px),
            "search_box_px": list(self.search_box_px),
            "windows": {
                "lens_distance_m": [round(value, 4) for value in self.lens_distance_m],
                "radar_slant_m": [round(value, 4) for value in self.radar_slant_m],
                "radar_window_m": [round(value, 4) for value in self.radar_window_m],
                "diameter_px": [round(value, 2) for value in self.diameter_px],
            },
            "tilt_pad_deg": self.tilt_pad_deg,
            "window_pad_deg": self.window_pad_deg,
            "roll_pad_deg": self.roll_pad_deg,
            "pad_px": round(self.pad_px, 2),
            "nominal": {key: round(value, 4) for key, value in self.nominal.items()},
        }


def tilt_pads_deg(calibrated: bool) -> tuple[float, float]:
    """The search outline's row pad and the distance windows' pad, in degrees."""
    if calibrated:
        return CALIBRATED_TILT_PAD_DEG, CALIBRATED_WINDOW_PAD_DEG
    return UNCALIBRATED_TILT_PAD_DEG, UNCALIBRATED_WINDOW_PAD_DEG


def _ball_plane_distances(
    camera, pixels: np.ndarray, ball_center_height_m: float
) -> tuple[np.ndarray, np.ndarray]:
    """Lens distance and radar slant range to the ball-centre plane along each pixel's ray."""
    origin, radar = _origins(camera)
    rays = np.asarray(camera.ray_model.rays(pixels), dtype=float).reshape(-1, 3)
    drop = ball_center_height_m - origin[2]
    with np.errstate(divide="ignore", invalid="ignore"):
        distance = np.where(rays[:, 2] < -1e-9, drop / rays[:, 2], np.inf)
    distance = np.minimum(distance, FAR_CAP_M)
    points = origin + distance[:, None] * rays
    slant = np.minimum(np.linalg.norm(points - radar, axis=1), FAR_CAP_M)
    return distance, slant


def _sample_polygon(polygon: np.ndarray) -> np.ndarray:
    samples = []
    for index in range(len(polygon)):
        start, end = polygon[index], polygon[(index + 1) % len(polygon)]
        for step in range(_EDGE_SAMPLES):
            samples.append(start + (end - start) * step / _EDGE_SAMPLES)
    return np.asarray(samples)


def _padded(outline: np.ndarray, rows: float, roll_pad_deg: float, centre_x: float) -> np.ndarray:
    """The outline with its near edge lowered and its far edge raised; columns unchanged."""
    padded = outline.copy()
    for index in range(4):
        # near-left, near-right go down; far-right, far-left go up
        sign = 1.0 if index < 2 else -1.0
        roll_rows = abs(padded[index, 0] - centre_x) * math.tan(math.radians(roll_pad_deg))
        padded[index, 1] += sign * (rows + roll_rows)
    return padded


def project_patch(  # pylint: disable=too-many-locals
    camera,
    patch: GroundPatch,
    *,
    tilt_pad_deg: float,
    window_pad_deg: float,
    roll_deg: float | None,
    ball_center_height_m: float = BALL_CENTER_HEIGHT_M,
) -> PatchProjection:
    """Project the patch, pad its ball-centre outline's rows, and derive its windows.

    ``outline_px`` is the patch on the ground, for drawing; ``ball_outline_px`` is
    where the centres of balls resting on it appear; ``search_outline_px`` is that
    outline with its far edge raised and its near edge lowered by the camera tilt's
    uncertainty (``tilt_pad_deg``) and the unapplied roll, its columns exact.
    ``box_px`` bounds both outlines and is the hitting zone the light and the
    ladder judge (it replaces the P7-4 box); ``search_box_px`` bounds the search.
    The distance windows come from the ball-centre outline padded by
    ``window_pad_deg`` (and the roll), sampled along its edges.
    """
    width, height = int(camera.image_width_px), int(camera.image_height_px)
    ground = _pixels(camera, patch.corners(0.0))
    ball = _pixels(camera, patch.corners(ball_center_height_m))
    focal = float(camera.focal_size_px)
    pad_px = focal * math.tan(math.radians(float(tilt_pad_deg)))
    window_px = focal * math.tan(math.radians(float(window_pad_deg)))
    roll_pad = _roll_pad_deg(roll_deg)
    centre_x = width / 2.0
    search = _padded(ball, pad_px, roll_pad, centre_x)
    windowed = _padded(ball, window_px, roll_pad, centre_x)
    lens, slant = _ball_plane_distances(camera, _sample_polygon(windowed), ball_center_height_m)
    nearest_lens, farthest_lens = float(np.min(lens)), float(np.max(lens))
    nearest, farthest = float(np.min(slant)), float(np.max(slant))
    smallest = focal * GOLF_BALL_DIAMETER_M / farthest_lens / (1.0 + SIZE_TOLERANCE)
    largest = focal * GOLF_BALL_DIAMETER_M / max(nearest_lens, 0.05) * (1.0 + SIZE_TOLERANCE)
    _nominal_lens, nominal_slant = _ball_plane_distances(camera, ball, ball_center_height_m)
    facts = patch_distance(camera, patch, ball_center_height_m)
    return PatchProjection(
        patch=patch,
        image_size_px=(width, height),
        outline_px=tuple((float(x), float(y)) for x, y in ground),
        ball_outline_px=tuple((float(x), float(y)) for x, y in ball),
        search_outline_px=tuple((float(x), float(y)) for x, y in search),
        box_px=tuple(_bounds(np.vstack([ground, search]), width, height)),  # type: ignore[arg-type]
        search_box_px=tuple(_bounds(search, width, height)),  # type: ignore[arg-type]
        lens_distance_m=(nearest_lens, farthest_lens),
        radar_slant_m=(nearest, farthest),
        radar_window_m=(
            max(0.1, nearest - RADAR_WINDOW_MARGIN_M),
            farthest + RADAR_WINDOW_MARGIN_M,
        ),
        diameter_px=(smallest, largest),
        tilt_pad_deg=float(tilt_pad_deg),
        window_pad_deg=float(window_pad_deg),
        roll_pad_deg=roll_pad,
        pad_px=pad_px,
        nominal={
            **facts,
            "near_slant_m": float(np.min(nominal_slant)),
            "far_slant_m": float(np.max(nominal_slant)),
        },
    )


def scale_outline(points: Sequence[Sequence[float]], factor: float) -> list[list[float]]:
    """An outline in a mode ``factor`` times the size (640x400 is 1280x800 halved)."""
    return [[float(x) * factor, float(y) * factor] for x, y in points]


def projection_parameters(camera) -> dict[str, Any]:
    """What the page needs to draw and drag the patch itself, in true perspective.

    The nominal pinhole model the setup uses: focal length, image centre, pitch and
    roll correction, and the lens and radar origins. A calibrated model is drawn
    with its pinhole part only (``approximate``); the server's outline is exact.
    """
    model = camera.ray_model
    pitch_rad = getattr(model, "pitch_rad", None)
    return {
        "focal_px": float(camera.focal_size_px),
        "image_size_px": [int(camera.image_width_px), int(camera.image_height_px)],
        "principal_point_px": [camera.image_width_px / 2.0, camera.image_height_px / 2.0],
        "pitch_deg": math.degrees(pitch_rad) if pitch_rad is not None else None,
        "roll_correction_deg": float(getattr(model, "roll_correction_deg", 0.0)),
        "horizontal_pixel_sign": float(getattr(model, "horizontal_pixel_sign", 1.0)),
        "camera_origin_lfu_m": [float(value) for value in camera.camera_origin_lfu],
        "radar_origin_lfu_m": [float(value) for value in camera.radar_origin_lfu],
        "ball_center_height_m": BALL_CENTER_HEIGHT_M,
        "size_m": PATCH_SIZE_M,
        "centre_distance_m": list(PATCH_CENTRE_DISTANCE_M),
        "approximate": pitch_rad is None,
    }

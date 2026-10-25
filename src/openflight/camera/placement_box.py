"""The fixed placement box drawn over the live view, computed from the rig file and lens.

It covers ground positions across ``PLACEMENT_WINDOW_M`` (forward of the lens along its
boresight) and ``half_width_m`` either side, with a margin for the tilt and unapplied roll.
**The box never yields a distance:** the camera gives the ball's direction and the IWR
the distance, searched along that ray (``ray_window``). A ball outside is labelled, never
blocked (``distance_note``).
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from openflight.camera import ground_patch

CONFIG_SCHEMA = "openflight.placement_box_config.v1"
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "placement_box.json"
RECORD_SCHEMA = "openflight.tester_placement_box.v2"
MODE = "fixed"
SOURCE = "fixed_box"

BALL_STATES = ("waiting", "found", "no_ball", "ambiguous", "outside_box")
BALL_MESSAGES = {
    "waiting": "Looking for the ball in the box.",
    "found": "Ball found in the box.",
    "no_ball": "No ball in the box. Put the ball in the box.",
    "ambiguous": "More than one ball-like thing is in the box. Keep only the ball in the box.",
    "outside_box": "A ball is outside the box. Move it into the box.",
}


@dataclass(frozen=True)
class PlacementBoxConfig:  # pylint: disable=too-many-instance-attributes
    """The fixed box's constants, from ``config/placement_box.json``."""

    placement_window_m: tuple[float, float]
    half_width_m: float
    uncalibrated_tilt_margin_deg: float
    calibrated_tilt_margin_deg: float
    uncalibrated_window_pad_deg: float
    calibrated_window_pad_deg: float
    net_margin_m: float
    size_far_margin_m: float
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": CONFIG_SCHEMA,
            "placement_window_m": list(self.placement_window_m),
            "half_width_m": self.half_width_m,
            "uncalibrated_tilt_margin_deg": self.uncalibrated_tilt_margin_deg,
            "calibrated_tilt_margin_deg": self.calibrated_tilt_margin_deg,
            "uncalibrated_window_pad_deg": self.uncalibrated_window_pad_deg,
            "calibrated_window_pad_deg": self.calibrated_window_pad_deg,
            "net_margin_m": self.net_margin_m,
            "size_far_margin_m": self.size_far_margin_m,
            "sha256": self.sha256,
        }


def _positive(payload: Mapping[str, Any], name: str) -> float:
    value = payload.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"placement box config needs a number {name}")
    value = float(value)
    if not math.isfinite(value) or value < 0.0:
        raise ValueError(f"placement box config {name} must be finite and not negative")
    return value


def load_config(path: Path | None = None) -> PlacementBoxConfig:
    """The fixed box's constants; a malformed file is refused, never defaulted."""
    source = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    raw = source.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, Mapping) or payload.get("schema") != CONFIG_SCHEMA:
        raise ValueError(f"{source} is not an {CONFIG_SCHEMA} file")
    window = payload.get("placement_window_m")
    if (
        not isinstance(window, Sequence)
        or len(window) != 2
        or not all(isinstance(value, (int, float)) for value in window)
        or not 0.0 < float(window[0]) < float(window[1])
    ):
        raise ValueError(f"{source} needs placement_window_m [near, far] with near < far")
    return PlacementBoxConfig(
        placement_window_m=(float(window[0]), float(window[1])),
        half_width_m=_positive(payload, "half_width_m"),
        uncalibrated_tilt_margin_deg=_positive(payload, "uncalibrated_tilt_margin_deg"),
        calibrated_tilt_margin_deg=_positive(payload, "calibrated_tilt_margin_deg"),
        uncalibrated_window_pad_deg=_positive(payload, "uncalibrated_window_pad_deg"),
        calibrated_window_pad_deg=_positive(payload, "calibrated_window_pad_deg"),
        net_margin_m=_positive(payload, "net_margin_m"),
        size_far_margin_m=_positive(payload, "size_far_margin_m"),
        sha256=hashlib.sha256(raw).hexdigest(),
    )


# config, never a page input
PLACEMENT_WINDOW_M = load_config().placement_window_m


def pads_deg(config: PlacementBoxConfig, calibrated: bool) -> tuple[float, float]:
    """The drawn box's tilt margin and the distance windows' pad, in degrees."""
    if calibrated:
        return config.calibrated_tilt_margin_deg, config.calibrated_window_pad_deg
    return config.uncalibrated_tilt_margin_deg, config.uncalibrated_window_pad_deg


def fixed_box(camera: Any, config: PlacementBoxConfig | None = None) -> ground_patch.GroundPatch:
    """The box on the ground: the window ahead of the lens, ``half_width_m`` either side."""
    config = config or load_config()
    near, far = config.placement_window_m
    lens = np.asarray(camera.camera_origin_lfu, dtype=float)
    return ground_patch.GroundPatch(
        centre_lateral_m=float(lens[0]),
        centre_forward_m=float(lens[1]) + (near + far) / 2.0,
        size_m=2.0 * config.half_width_m,
        depth_m=far - near,
    )


def box_id(config: PlacementBoxConfig, lens_id: str | None) -> str:
    """The box's identity: its constants and the lens it is drawn through."""
    encoded = json.dumps(
        {"config_sha256": config.sha256, "lens_id": lens_id}, sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


class _PitchedRays:  # pylint: disable=too-few-public-methods
    """A camera's rays turned ``degrees`` further up about the lateral axis."""

    def __init__(self, model: Any, degrees: float):
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
        return np.asarray(self._model.rays(pixels_px), dtype=float) @ self._turn.T


def pitched(camera: Any, degrees: float) -> Any:
    """The same camera pitched ``degrees`` further up."""
    from dataclasses import replace  # noqa: PLC0415

    return replace(camera, ray_model=_PitchedRays(camera.ray_model, degrees))


def _padded_outline(
    camera: Any, box: ground_patch.GroundPatch, pad_deg: float, roll_pad_deg: float
) -> np.ndarray:
    """The on-screen rectangle that holds every ball in the box at a pitch within +-pad.

    Corners are ordered near-left, near-right, far-right, far-left, as the ground outline.
    """
    # the ball centres, and the ground under them, so the drawn box holds both
    corners = np.vstack([box.corners(ground_patch.BALL_CENTER_HEIGHT_M), box.corners(0.0)])
    points = np.vstack(
        [
            ground_patch._pixels(pitched(camera, sign * pad_deg), corners)  # noqa: SLF001
            for sign in (-1.0, 0.0, 1.0)
        ]
    )
    centre_x = camera.image_width_px / 2.0
    lift = np.abs(points[:, 0] - centre_x) * math.tan(math.radians(roll_pad_deg))
    x0, x1 = float(np.min(points[:, 0])), float(np.max(points[:, 0]))
    y0 = float(np.min(points[:, 1] - lift))
    y1 = float(np.max(points[:, 1] + lift))
    return np.asarray([(x0, y1), (x1, y1), (x1, y0), (x0, y0)], dtype=float)


def project_fixed_box(
    camera: Any,
    *,
    calibrated: bool,
    roll_deg: float | None,
    config: PlacementBoxConfig | None = None,
) -> ground_patch.PatchProjection:
    """The fixed box in one camera mode's pixels, and the windows taken from it.

    ``search_outline_px`` (the contract's ``box_outline_px``) holds ball centres at any
    pitch within the tilt margin; ``ball_outline_px`` holds them at the model's pitch.
    """
    config = config or load_config()
    box = fixed_box(camera, config)
    margin, window_pad = pads_deg(config, calibrated)
    width, height = int(camera.image_width_px), int(camera.image_height_px)
    roll_pad = ground_patch.roll_pad_deg(camera, roll_deg)
    ground = ground_patch._pixels(camera, box.corners(0.0))  # noqa: SLF001
    ball = ground_patch._pixels(  # noqa: SLF001
        camera, box.corners(ground_patch.BALL_CENTER_HEIGHT_M)
    )
    search = _padded_outline(camera, box, margin, roll_pad)
    windowed = _padded_outline(camera, box, window_pad, roll_pad)
    lens, slant = ground_patch._ball_plane_distances(  # noqa: SLF001
        camera,
        ground_patch._sample_polygon(windowed),  # noqa: SLF001
        ground_patch.BALL_CENTER_HEIGHT_M,
    )
    focal = float(camera.focal_size_px)
    nearest_lens, farthest_lens = float(np.min(lens)), float(np.max(lens))
    nearest, farthest = float(np.min(slant)), float(np.max(slant))
    tolerance = ground_patch.SIZE_TOLERANCE
    # size does not move with pitch: the smallest ball stands just past the window,
    # not where a padded row meets the horizon
    size_far = min(farthest_lens, config.placement_window_m[1] + config.size_far_margin_m)
    _nominal_lens, nominal_slant = ground_patch._ball_plane_distances(  # noqa: SLF001
        camera, ball, ground_patch.BALL_CENTER_HEIGHT_M
    )
    facts = ground_patch.patch_distance(camera, box)
    return ground_patch.PatchProjection(
        patch=box,
        image_size_px=(width, height),
        outline_px=tuple((float(x), float(y)) for x, y in ground),
        ball_outline_px=tuple((float(x), float(y)) for x, y in ball),
        search_outline_px=tuple((float(x), float(y)) for x, y in search),
        box_px=tuple(ground_patch._bounds(np.vstack([ground, search]), width, height)),  # type: ignore[arg-type]  # noqa: SLF001
        search_box_px=tuple(ground_patch._bounds(search, width, height)),  # type: ignore[arg-type]  # noqa: SLF001
        lens_distance_m=(nearest_lens, farthest_lens),
        radar_slant_m=(nearest, farthest),
        radar_window_m=(
            max(0.1, nearest - ground_patch.RADAR_WINDOW_MARGIN_M),
            farthest + ground_patch.RADAR_WINDOW_MARGIN_M,
        ),
        diameter_px=(
            focal * ground_patch.GOLF_BALL_DIAMETER_M / size_far / (1.0 + tolerance),
            focal * ground_patch.GOLF_BALL_DIAMETER_M / max(nearest_lens, 0.05) * (1.0 + tolerance),
        ),
        tilt_pad_deg=float(margin),
        window_pad_deg=float(window_pad),
        roll_pad_deg=roll_pad,
        pad_px=focal * math.tan(math.radians(margin)),
        nominal={
            **facts,
            "near_slant_m": float(np.min(nominal_slant)),
            "far_slant_m": float(np.max(nominal_slant)),
        },
    )


def forward_from_radar_range(camera: Any, range_m: float, side_offset_m: float = 0.0) -> float:
    """How far forward of the lens a ball resting at this radar slant range lies."""
    radar = np.asarray(camera.radar_origin_lfu, dtype=float)
    lens = np.asarray(camera.camera_origin_lfu, dtype=float)
    drop = ground_patch.BALL_CENTER_HEIGHT_M - float(radar[2])
    ground = math.sqrt(max(float(range_m) ** 2 - drop**2 - float(side_offset_m) ** 2, 0.0))
    return float(radar[1]) + ground - float(lens[1])


def distance_note(
    camera: Any,
    range_m: float | None,
    *,
    side_offset_m: float | None = None,
    config: PlacementBoxConfig | None = None,
) -> str:
    """Where a measured ball sits against the placement window: a label, never a block."""
    config = config or load_config()
    near, far = config.placement_window_m
    if range_m is None:
        return "distance not measured yet"
    forward = forward_from_radar_range(camera, float(range_m), float(side_offset_m or 0.0))
    if forward < near:
        return f"closer than {near:g} m: numbers labelled, not blocked"
    if forward > far:
        return f"farther than {far:g} m: numbers labelled, not blocked"
    return f"inside the {near:g}–{far:g} m window"


def _plane_range(camera: Any, pixel: Sequence[float]) -> float | None:
    """Radar slant range to where this pixel's ray meets the ball-centre plane, or None."""
    lens = np.asarray(camera.camera_origin_lfu, dtype=float)
    ray = np.asarray(camera.ray_model.rays(np.asarray(pixel, dtype=float)), dtype=float)
    drop = ground_patch.BALL_CENTER_HEIGHT_M - float(lens[2])
    if ray[2] >= -1e-9:
        return None
    point = lens + (drop / float(ray[2])) * ray
    if point[1] <= lens[1]:
        return None
    return float(np.linalg.norm(point - np.asarray(camera.radar_origin_lfu, dtype=float)))


def ray_window(
    camera: Any,
    pixel: Sequence[float],
    *,
    pad_deg: float,
    far_cap_m: float,
    near_cap_m: float = 0.1,
) -> dict[str, Any]:
    """The radar slant ranges a ball seen at this pixel can lie at (row angle +-``pad_deg``).

    The far side is capped (the net, or the box's reach); ``nominal_m`` is the ray's
    distance at the model's pitch.
    """
    nominal = _plane_range(camera, pixel)
    low = _plane_range(pitched(camera, -abs(pad_deg)), pixel)
    high = _plane_range(pitched(camera, abs(pad_deg)), pixel)
    values = [value for value in (nominal, low, high) if value is not None]
    lo = min(values) if values else near_cap_m
    hi = far_cap_m if high is None or nominal is None else max(values)
    lo, hi = max(near_cap_m, lo), min(far_cap_m, hi)
    return {
        "window_m": [round(lo, 4), round(max(hi, lo), 4)],
        "nominal_m": round(nominal, 4) if nominal is not None else None,
        "pad_deg": float(pad_deg),
        "far_cap_m": float(far_cap_m),
        "reaches_the_cap": bool(high is None or (nominal is not None and hi >= far_cap_m)),
    }


def classify_ball(
    in_box: Mapping[str, Any] | None,
    wide: Mapping[str, Any] | None,
    *,
    outline_px: Sequence[Sequence[float]],
    settled: bool,
) -> dict[str, Any]:
    """The ball's state from the in-box search and, when it found nothing, a wider one.

    A miss while brightness is still being set is ``waiting``, never ``no_ball``.
    """
    if not isinstance(in_box, Mapping):
        return {"state": "waiting", "selected": None}
    status = in_box.get("status")
    selected = in_box.get("selected")
    if status == "selected" and isinstance(selected, Mapping):
        return {"state": "found", "selected": dict(selected)}
    if status == "ambiguous":
        return {"state": "ambiguous", "selected": None}
    other = (wide or {}).get("selected") if isinstance(wide, Mapping) else None
    if (
        isinstance(wide, Mapping)
        and wide.get("status") == "selected"
        and isinstance(other, Mapping)
        and not ground_patch.point_in_polygon(
            float(other["x_px"]), float(other["y_px"]), outline_px
        )
    ):
        return {"state": "outside_box", "selected": dict(other)}
    if not settled:
        return {"state": "waiting", "selected": None}
    return {"state": "no_ball", "selected": None}

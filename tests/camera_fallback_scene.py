"""A synthetic rear-camera clip of a struck ball, rendered through a known pinhole model.

The resting ball and the flying ball are the lit sphere ``ball_model`` fits (the same
picture model the setup measures balls with), drawn at the size the pinhole gives
for their distance, on a ground with a gentle gradient and deterministic noise.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from openflight.camera import ball_model
from openflight.camera.ball_launch_fallback import PinholeCamera, launch_direction
from openflight.constants import BALL_DIAMETER_M as CONSTANT_BALL_DIAMETER_M

BALL_DIAMETER_M = CONSTANT_BALL_DIAMETER_M
FRAME_INTERVAL_NS = 8_333_333
MPH_PER_MS = 2.2369362920544


@dataclass
class Scene:
    """A shot's truth and its rendered clip."""

    camera: PinholeCamera
    ball_lfu: np.ndarray
    vertical_deg: float
    horizontal_deg: float  # from the unit boresight
    speed_mph: float
    contact_ns: int
    ops_lfu: np.ndarray
    frames: np.ndarray = field(repr=False)
    sensor_timestamp_ns: np.ndarray = field(repr=False)
    exposure_us: float
    first_post_index: int

    def position(self, t_s: float) -> np.ndarray:
        """The ball's centre ``t_s`` after contact (gravity included)."""
        if t_s <= 0.0:
            return self.ball_lfu.copy()
        velocity = (
            self.speed_mph / MPH_PER_MS * launch_direction(self.vertical_deg, self.horizontal_deg)
        )
        point = self.ball_lfu + velocity * t_s
        point[2] -= 0.5 * 9.80665 * t_s * t_s
        return point

    def ops_radial_mph(self, t_s: float = 0.010) -> float:
        """The OPS's line-of-sight speed ``t_s`` after contact."""
        velocity = (
            self.speed_mph / MPH_PER_MS * launch_direction(self.vertical_deg, self.horizontal_deg)
        )
        sight = self.position(t_s) - self.ops_lfu
        return float(velocity @ (sight / np.linalg.norm(sight)) * MPH_PER_MS)

    def rest_px(self) -> tuple[float, float, float]:
        x, y = self.camera.project(self.ball_lfu[None, :])[0]
        distance = float(np.linalg.norm(self.ball_lfu - np.asarray(self.camera.origin_lfu)))
        return float(x), float(y), self.camera.focal_px * BALL_DIAMETER_M / distance


def _draw_ball(image: np.ndarray, x: float, y: float, diameter_px: float, ground: np.ndarray):
    radius = diameter_px / 2.0
    half = int(radius + 6)
    x0, x1 = max(0, int(x) - half), min(image.shape[1], int(x) + half + 1)
    y0, y1 = max(0, int(y) - half), min(image.shape[0], int(y) + half + 1)
    if x0 >= x1 or y0 >= y1:
        return
    yy, xx = np.mgrid[y0:y1, x0:x1].astype(float)
    params = np.array(
        [x, y, radius, math.radians(-90.0), math.radians(40.0), 70.0, 170.0, 0.0, 0.0, 0.0]
    )
    dx, dy = xx - x, yy - y
    cover = np.clip(radius + 0.5 - np.sqrt(dx * dx + dy * dy), 0.0, 1.0)
    sphere = ball_model._render(params, yy, xx)  # pylint: disable=protected-access
    patch = image[y0:y1, x0:x1]
    image[y0:y1, x0:x1] = cover * sphere + (1.0 - cover) * patch


def render_scene(  # pylint: disable=too-many-arguments,too-many-locals
    *,
    vertical_deg: float = 16.0,
    horizontal_deg: float = 2.0,
    speed_mph: float = 90.0,
    distance_m: float = 1.9,
    ball_side_m: float = 0.03,
    focal_px: float = 2000.0,
    pitch_deg: float = -0.5,
    roll_correction_deg: float = 0.0,
    camera_height_m: float = 0.095,
    ball_height_m: float = 0.02135,
    width: int = 1280,
    height: int = 800,
    pre_frames: int = 18,
    post_frames: int = 6,
    contact_before_trigger_ms: float = 2.0,
    exposure_us: float = 150.0,
    seed: int = 7,
) -> Scene:
    """Render a clip; frame ``pre_frames`` is the first frame exposed after the trigger."""
    camera = PinholeCamera(
        focal_px=focal_px,
        width=width,
        height=height,
        pitch_deg=pitch_deg,
        roll_correction_deg=roll_correction_deg,
        origin_lfu=(0.0, 0.0, camera_height_m),
    )
    drop = ball_height_m - camera_height_m
    forward = math.sqrt(distance_m**2 - drop**2 - ball_side_m**2)
    ball = np.array([ball_side_m, forward, ball_height_m])
    count = pre_frames + post_frames
    start_ns = 1_000_000_000
    stamps = start_ns + FRAME_INTERVAL_NS * np.arange(count, dtype=np.int64)
    # the trigger lands inside the last pre-trigger frame's interval
    trigger_ns = int(stamps[pre_frames - 1]) + FRAME_INTERVAL_NS // 2
    contact_ns = trigger_ns - int(contact_before_trigger_ms * 1e6)
    ops = np.array([-0.085, -0.020, camera_height_m - 0.047])
    scene = Scene(
        camera=camera,
        ball_lfu=ball,
        vertical_deg=vertical_deg,
        horizontal_deg=horizontal_deg,
        speed_mph=speed_mph,
        contact_ns=contact_ns,
        ops_lfu=ops,
        frames=np.zeros((count, height, width), dtype=np.uint8),
        sensor_timestamp_ns=stamps,
        exposure_us=exposure_us,
        first_post_index=pre_frames,
    )
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:height, 0:width].astype(float)
    ground = 38.0 + 10.0 * yy / height + 4.0 * np.sin(xx / 37.0)
    for index, stamp in enumerate(stamps):
        picture = ground.copy()
        mid_ns = int(stamp) + int(exposure_us * 500.0)
        t_s = (mid_ns - contact_ns) / 1e9
        point = scene.position(t_s)
        x, y = camera.project(point[None, :])[0]
        diameter = (
            focal_px
            * BALL_DIAMETER_M
            / float(np.linalg.norm(point - np.asarray(camera.origin_lfu)))
        )
        _draw_ball(picture, float(x), float(y), diameter, ground)
        picture += rng.normal(0.0, 1.5, picture.shape)
        scene.frames[index] = np.clip(np.round(picture), 0, 255).astype(np.uint8)
    return scene

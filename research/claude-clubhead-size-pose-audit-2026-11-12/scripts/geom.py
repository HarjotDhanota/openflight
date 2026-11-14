"""My own projection of the application's generic model (independent of its code; checked against its outline, E1),
plus exact scan-line extremes of the model's silhouette. Analysis coordinates = the prepared ROI's native pixels."""
import json
import math
from pathlib import Path

import numpy as np

APP = Path("F:/Coding/OpenFlight/openflight-integration")
BALL_RADIUS_MM = 42.7 / 2.0
_ref = json.loads((APP / "src/openflight/camera/club_outline_reference.json").read_text())["categories"]["iron"]
OUTLINE = np.asarray(_ref["head_outline_uv_mm"], dtype=float)
STATIC_LIE = float(_ref["static_lie_deg"])


def axes(face, loft, lie):
    f, lo, li = (math.radians(v) for v in (face, loft, lie))
    yaw = np.array([[math.cos(f), 0, math.sin(f)], [0, 1, 0], [-math.sin(f), 0, math.cos(f)]])
    toe0 = yaw @ np.array([1.0, 0.0, 0.0])
    normal = yaw @ np.array([0.0, -math.sin(lo), math.cos(lo)])
    up0 = np.cross(toe0, normal)
    return toe0 * math.cos(li) + up0 * math.sin(li), up0 * math.cos(li) - toe0 * math.sin(li), normal


def pitch_matrix(pitch_deg):
    """Level frame -> camera frame for a camera pitched DOWN by pitch_deg (y down, z forward)."""
    p = math.radians(pitch_deg)
    return np.array([[1, 0, 0], [0, math.cos(p), -math.sin(p)], [0, math.sin(p), math.cos(p)]])


class Scene:
    def __init__(self, focal, centre, ball_px, range_mm, pitch_deg=0.0):
        self.focal, self.centre = float(focal), np.asarray(centre, dtype=float)
        self.rot = pitch_matrix(pitch_deg)
        ray = np.array([(ball_px[0] - self.centre[0]) / self.focal, (ball_px[1] - self.centre[1]) / self.focal, 1.0])
        self.ball = self.rot.T @ (ray * range_mm)  # level frame

    def project(self, points):
        cam = np.atleast_2d(points) @ self.rot.T
        return self.centre + self.focal * cam[:, :2] / cam[:, 2:3]


def pose_origin(scene, ax, toe_mm, up_mm, normal_mm):
    toe, up, normal = ax
    return scene.ball - BALL_RADIUS_MM * normal - toe_mm * toe - up_mm * up + normal_mm * normal


def model_segments(scene, ax, origin, sole_width, bounce_deg, size=1.0, outline=OUTLINE):
    """(outline pixels, segment soup (M,2,2) of the outline and of the sole strip's quads) at one pose."""
    toe, up, normal = ax
    uv = outline * size
    lead = origin + uv[:, :1] * toe + uv[:, 1:2] * up
    back = -np.array([normal[0], 0.0, normal[2]])
    back /= max(math.hypot(back[0], back[2]), 1e-12)
    b = math.radians(bounce_deg)
    offset = sole_width * size * (math.cos(b) * back + math.sin(b) * np.array([0.0, 1.0, 0.0]))
    lead_px, trail_px = scene.project(lead), scene.project(lead + offset)
    nxt = np.roll(np.arange(len(uv)), -1)
    soup = [np.stack([lead_px, lead_px[nxt]], axis=1), np.stack([trail_px, trail_px[nxt]], axis=1), np.stack([lead_px, trail_px], axis=1)]
    return lead_px, np.concatenate(soup)


def shaft_line(scene, ax, origin, loft, foot_uv, size=1.0, length=150.0):
    toe, up, normal = ax
    lo, li = math.radians(loft), math.radians(STATIC_LIE)
    vertical = math.cos(lo) * up + math.sin(lo) * normal
    direction = -math.cos(li) * toe + math.sin(li) * vertical
    base = origin + size * foot_uv[0] * toe + size * foot_uv[1] * up
    return scene.project(np.stack([base, base + length * direction]))


def _cross(soup, value, axis):
    """Coordinates on the other axis where segments cross the line axis=value."""
    a, b = soup[:, 0, axis], soup[:, 1, axis]
    hit = ((a <= value) & (b > value)) | ((b <= value) & (a > value))
    t = (value - a[hit]) / (b[hit] - a[hit])
    other = 1 - axis
    return soup[hit, 0, other] + t * (soup[hit, 1, other] - soup[hit, 0, other])


def rightmost(soup, y):
    c = _cross(soup, y, 1)
    return float(c.max()) if len(c) else math.nan


def lowest(soup, x):
    c = _cross(soup, x, 0)
    return float(c.max()) if len(c) else math.nan


def highest(soup, x):
    c = _cross(soup, x, 0)
    return float(c.min()) if len(c) else math.nan

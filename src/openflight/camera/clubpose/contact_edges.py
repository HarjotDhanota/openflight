"""Specific clubhead edge measurements in the two contact-bracketing frames."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from openflight.camera.club_motion import ReferenceBall

_WINDOW_LEFT_PX = 32.0
_WINDOW_RIGHT_PX = 32.0
_WINDOW_TOP_PX = 14.0
_WINDOW_BOTTOM_PX = 3.0
_BALL_EXCLUSION_PAD_PX = 1.5
_TOPLINE_RESIDUAL_PX = 1.5
_SHAFT_INLIER_DISTANCE_PX = 2.0
_TOPLINE_ABSOLUTE_DN = 205.0
_TOPLINE_ABOVE_MAT_DN = 50.0
_SHAFT_ABSOLUTE_DN = 210.0
_SHAFT_ABOVE_MAT_DN = 55.0


@dataclass(frozen=True)
class ToplineEdge:
    """Robust bright-ridge line in image coordinates."""

    slope: float
    row_at_ball: float

    def row_at(self, x: float | np.ndarray, ball_x: float) -> float | np.ndarray:
        """Evaluate the line at an image column."""
        return self.row_at_ball + self.slope * (x - ball_x)


@dataclass(frozen=True)
class ToeEdge:
    """Dark-head to mat crossing below the topline."""

    x: float
    row_spread_px: float


@dataclass(frozen=True)
class HeelEdge:
    """Intersection of the shaft centreline and topline."""

    x: float
    shaft_angle_deg: float


@dataclass(frozen=True)
class ContactEdges:
    """All requested contact-frame measurements and fail-closed reasons."""

    status: str
    reason: str
    topline_slope: float | None
    topline_row_at_ball: float | None
    heel_x: float | None
    toe_x: float | None
    width_px: float | None
    toe_row_spread_px: float | None
    shaft_angle_deg: float | None
    topline_reason: str
    heel_reason: str
    toe_reason: str


def _window_bounds(frame: np.ndarray, ball: ReferenceBall) -> tuple[int, int, int, int] | None:
    if frame.ndim != 2 or not np.issubdtype(frame.dtype, np.number):
        return None
    height, width = frame.shape
    x0 = max(0, int(math.floor(ball.x - _WINDOW_LEFT_PX)))
    x1 = min(width - 1, int(math.ceil(ball.x + _WINDOW_RIGHT_PX)))
    y0 = max(0, int(math.floor(ball.y - _WINDOW_TOP_PX)))
    y1 = min(height - 1, int(math.ceil(ball.y + _WINDOW_BOTTOM_PX)))
    if x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1, y1


def _theil_sen_line(xs: np.ndarray, ys: np.ndarray) -> tuple[float, float]:
    dx = xs[np.newaxis, :] - xs[:, np.newaxis]
    dy = ys[np.newaxis, :] - ys[:, np.newaxis]
    upper = np.triu(np.ones(dx.shape, dtype=bool), k=1) & (dx != 0)
    slope = float(np.median(dy[upper] / dx[upper]))
    intercept = float(np.median(ys - slope * xs))
    return slope, intercept


def detect_topline(
    frame: np.ndarray, ball: ReferenceBall, mat_level: float
) -> tuple[ToplineEdge | None, str]:
    """Find the bright topline ridge and robustly fit its image-plane line."""
    bounds = _window_bounds(frame, ball)
    if bounds is None or not math.isfinite(mat_level):
        return None, "topline_invalid_window"
    x0, y0, x1, y1 = bounds
    bright_threshold = max(_TOPLINE_ABSOLUTE_DN, mat_level + _TOPLINE_ABOVE_MAT_DN)
    dark_threshold = mat_level - 35.0
    ball_radius = ball.diameter_px / 2.0
    point_xs: list[float] = []
    point_ys: list[float] = []

    toe_side_start = max(x0, int(math.ceil(ball.x + ball_radius + _BALL_EXCLUSION_PAD_PX)))
    for x in range(toe_side_start, x1 + 1):
        candidate_y = None
        for y in range(y0, min(y1, frame.shape[0] - 3) + 1):
            if frame[y, x] >= bright_threshold and frame[y + 2, x] <= dark_threshold:
                candidate_y = y
                break
        if candidate_y is None:
            continue
        run_end = candidate_y
        while run_end + 1 <= y1 and frame[run_end + 1, x] >= bright_threshold:
            run_end += 1
        rows = np.arange(candidate_y, run_end + 1, dtype=float)
        weights = frame[candidate_y : run_end + 1, x].astype(float)
        point_xs.append(float(x))
        point_ys.append(float(np.average(rows, weights=weights)))

    if len(point_xs) < 6:
        return None, "topline_insufficient_points"
    xs = np.asarray(point_xs)
    ys = np.asarray(point_ys)
    slope, intercept = _theil_sen_line(xs, ys)
    inliers = np.abs(ys - (slope * xs + intercept)) < _TOPLINE_RESIDUAL_PX
    if int(inliers.sum()) < 4 or float(np.ptp(xs[inliers])) < 8.0:
        return None, "topline_insufficient_inliers"
    slope, intercept = np.polyfit(xs[inliers], ys[inliers], 1)
    return ToplineEdge(float(slope), float(slope * ball.x + intercept)), "ok"


@dataclass(frozen=True)
class _ToeScan:
    frame: np.ndarray
    ball: ReferenceBall
    topline: ToplineEdge
    dark_threshold: float
    start_x: int
    end_x: int

    def crossing(self, offset: int) -> float | None:
        """Return one below-topline dark-to-mat crossing."""
        x = self.start_x
        last_dark_x: int | None = None
        while x <= self.end_x:
            y = int(np.rint(self.topline.row_at(float(x), self.ball.x) + offset))
            if not 0 <= y < self.frame.shape[0]:
                return None
            value = float(self.frame[y, x])
            if value <= self.dark_threshold:
                last_dark_x = x
                x += 1
                continue
            next_x = x + 1
            if next_x <= self.end_x:
                next_y = int(np.rint(self.topline.row_at(float(next_x), self.ball.x) + offset))
                if (
                    0 <= next_y < self.frame.shape[0]
                    and self.frame[next_y, next_x] <= self.dark_threshold
                ):
                    last_dark_x = next_x
                    x += 2
                    continue
            if last_dark_x is None:
                return None
            last_y = int(np.rint(self.topline.row_at(float(last_dark_x), self.ball.x) + offset))
            dark_value = float(self.frame[last_y, last_dark_x])
            if value <= dark_value:
                return None
            fraction = (self.dark_threshold - dark_value) / (value - dark_value)
            return float(last_dark_x + fraction * (x - last_dark_x))
        return None


def detect_toe(
    frame: np.ndarray,
    ball: ReferenceBall,
    mat_level: float,
    topline: ToplineEdge,
) -> tuple[ToeEdge | None, str]:
    """Find the toe from the dark-head to mat step below the topline."""
    bounds = _window_bounds(frame, ball)
    if bounds is None or not math.isfinite(mat_level):
        return None, "toe_invalid_window"
    _x0, _y0, x1, _y1 = bounds
    start_x = int(math.ceil(ball.x + ball.diameter_px / 2.0 + 3.0))
    if start_x >= x1:
        return None, "toe_start_outside_window"
    scan = _ToeScan(frame, ball, topline, mat_level - 35.0, start_x, x1)
    crossings = [
        crossing for offset in range(2, 7) if (crossing := scan.crossing(offset)) is not None
    ]
    if len(crossings) < 3:
        return None, "toe_insufficient_crossings"
    values = np.asarray(crossings)
    return ToeEdge(float(np.median(values)), float(np.ptp(values))), "ok"


def _principal_line(points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    centre = points.mean(axis=0)
    _values, vectors = np.linalg.eigh(np.cov(points, rowvar=False))
    direction = vectors[:, -1]
    return centre, direction


# pylint: disable-next=too-many-return-statements
def detect_heel(
    frame: np.ndarray,
    ball: ReferenceBall,
    mat_level: float,
    topline: ToplineEdge,
) -> tuple[HeelEdge | None, str]:
    """Find the heel where the trimmed bright-shaft PCA line meets the topline."""
    if frame.ndim != 2 or not math.isfinite(mat_level):
        return None, "shaft_invalid_window"
    height, width = frame.shape
    x0 = max(0, int(math.floor(ball.x - 70.0)))
    x1 = min(width - 1, int(math.ceil(ball.x - 2.0)))
    y0 = max(0, int(math.floor(ball.y - 60.0)))
    y1 = min(height - 1, int(math.ceil(ball.y - 4.0)))
    if x1 <= x0 or y1 <= y0:
        return None, "shaft_invalid_window"
    yy, xx = np.mgrid[y0 : y1 + 1, x0 : x1 + 1]
    pixels = frame[y0 : y1 + 1, x0 : x1 + 1]
    threshold = max(_SHAFT_ABSOLUTE_DN, mat_level + _SHAFT_ABOVE_MAT_DN)
    above_topline = yy < topline.row_at(xx.astype(float), ball.x) - 1.5
    outside_ball = (xx - ball.x) ** 2 + (yy - ball.y) ** 2 > (
        ball.diameter_px / 2.0 + _BALL_EXCLUSION_PAD_PX
    ) ** 2
    selected = (pixels >= threshold) & above_topline & outside_ball
    points = np.column_stack((xx[selected], yy[selected])).astype(float)
    if len(points) < 20:
        return None, "shaft_insufficient_pixels"

    for _round in range(3):
        centre, direction = _principal_line(points)
        normal = np.asarray((-direction[1], direction[0]))
        inliers = np.abs((points - centre) @ normal) < _SHAFT_INLIER_DISTANCE_PX
        points = points[inliers]
        if len(points) < 20:
            return None, "shaft_insufficient_inliers"
    centre, direction = _principal_line(points)
    denominator = direction[1] - topline.slope * direction[0]
    if abs(denominator) < 1e-3:
        return None, "shaft_parallel_to_topline"
    topline_intercept = topline.row_at_ball - topline.slope * ball.x
    distance = (topline.slope * centre[0] + topline_intercept - centre[1]) / denominator
    heel_x = float(centre[0] + distance * direction[0])
    bounds = _window_bounds(frame, ball)
    assert bounds is not None
    if not bounds[0] <= heel_x <= ball.x:
        return None, "heel_outside_topline"
    shaft_angle = math.degrees(math.atan2(abs(direction[1]), abs(direction[0])))
    return HeelEdge(heel_x, shaft_angle), "ok"


def find_contact_edges(frame: np.ndarray, ball: ReferenceBall, mat_level: float) -> ContactEdges:
    """Compose the three fail-closed contact-edge measurements."""
    topline, topline_reason = detect_topline(frame, ball, mat_level)
    if topline is None:
        return ContactEdges(
            status="failed",
            reason=topline_reason,
            topline_slope=None,
            topline_row_at_ball=None,
            heel_x=None,
            toe_x=None,
            width_px=None,
            toe_row_spread_px=None,
            shaft_angle_deg=None,
            topline_reason=topline_reason,
            heel_reason="topline_unavailable",
            toe_reason="topline_unavailable",
        )
    heel, heel_reason = detect_heel(frame, ball, mat_level, topline)
    toe, toe_reason = detect_toe(frame, ball, mat_level, topline)
    failures = [reason for reason in (heel_reason, toe_reason) if reason != "ok"]
    return ContactEdges(
        status="ok" if not failures else "failed",
        reason="ok" if not failures else ";".join(failures),
        topline_slope=topline.slope,
        topline_row_at_ball=topline.row_at_ball,
        heel_x=None if heel is None else heel.x,
        toe_x=None if toe is None else toe.x,
        width_px=None if heel is None or toe is None else toe.x - heel.x,
        toe_row_spread_px=None if toe is None else toe.row_spread_px,
        shaft_angle_deg=None if heel is None else heel.shaft_angle_deg,
        topline_reason=topline_reason,
        heel_reason=heel_reason,
        toe_reason=toe_reason,
    )

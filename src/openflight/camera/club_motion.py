"""Offline OV9281 club-motion measurements around impact.

The tracker intentionally reports image-plane motion only. Converting these
measurements to club path or attack angle requires camera-pose calibration and
an independent downrange velocity measurement.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np
from scipy import ndimage

from openflight.camera.ball_model import fit_lit_ball

BALL_DIAMETER_MM = 42.67


@dataclass(frozen=True)
class ReferenceBall:
    """Stationary ball location and apparent size before the swing."""

    x: float
    y: float
    diameter_px: float
    area_px: int


@dataclass(frozen=True)
class ImagePoint:
    """Tracked image coordinate for one camera frame."""

    frame_index: int
    x: float
    y: float


@dataclass(frozen=True)
class ImagePlaneMotion:
    """Terminal transverse motion measured in the camera image plane."""

    horizontal_px_s: float
    vertical_px_s: float
    horizontal_m_s: float
    vertical_m_s: float
    mm_per_px: float
    interval_ms: float


@dataclass(frozen=True)
class ShaftTrack:
    """Bright-shaft endpoint track leading into impact."""

    points: tuple[ImagePoint, ...]
    confidence: float
    reason: str


# A room-lit ball is brighter than what surrounds it but rarely saturates: a
# ceiling light leaves a small highlight on top and a face near the mat's own
# level. It is found as the disk that stands out most from the ring around it,
# within the hitting zone, and only when it clearly beats every other disk.
DISK_MIN_CONTRAST_DN = 20.0
DISK_MIN_NOISE_MULTIPLE = 6.0
DISK_MIN_MARGIN = 1.25
DISK_ZONE = (0.15, 0.40, 0.85, 0.92)  # x0, y0, x1, y1 as fractions of the frame
DISK_SEARCH_WIDTH_PX = 320  # larger frames are searched binned to this width


def _box_mean(image: np.ndarray, side: float) -> np.ndarray:
    return ndimage.uniform_filter(image, size=max(1, int(round(side))), mode="nearest")


def _disk_contrast(image: np.ndarray, radius: float) -> np.ndarray:
    """Inscribed square of a disk of this radius against the square ring just outside it."""
    inner = _box_mean(image, 1.4 * radius)
    mid_side, out_side = 2.6 * radius, 4.0 * radius
    mid, out = _box_mean(image, mid_side), _box_mean(image, out_side)
    ring = (out * out_side**2 - mid * mid_side**2) / (out_side**2 - mid_side**2)
    return inner - ring


def _bin(image: np.ndarray, factor: int) -> np.ndarray:
    height, width = (image.shape[0] // factor) * factor, (image.shape[1] // factor) * factor
    blocks = image[:height, :width].reshape(height // factor, factor, width // factor, factor)
    return blocks.mean(axis=(1, 3))


def _brighter_all_round(
    image: np.ndarray, row: int, col: int, radius: float, margin: float
) -> bool:
    """Whether the middle outshines the ring around it in at least six of eight directions."""
    yy, xx = np.indices(image.shape)
    distance = np.hypot(xx - col, yy - row)
    middle = image[distance <= 0.7 * radius]
    if not middle.size:
        return False
    level = float(middle.mean()) - margin
    sector = ((np.arctan2(yy - row, xx - col) + np.pi) / (2 * np.pi) * 8).astype(int) % 8
    ring = (distance >= 1.6 * radius) & (distance <= 2.3 * radius)
    darker = checked = 0
    for index in range(8):
        values = image[ring & (sector == index)]
        if values.size:
            checked += 1
            darker += int(values.mean() <= level)
    return checked >= 4 and darker >= 0.75 * checked


def _lit_ball_near_size(
    image: np.ndarray,
    coarse: np.ndarray,
    window: tuple[int, int, int, int],
    factor: int,
    expected_radius: float,
    noise: float,
) -> ReferenceBall | None:
    """The lit ball among the round candidates of the size the distance predicts.

    A door panel over the dark gap beneath it, or a bright patch of carpet, can
    stand out as much as the ball; none of them looks like a lit sphere, so the
    one the model explains best is the ball.
    """
    x0, y0, x1, y1 = window
    coarse_r = expected_radius / factor
    # only noise is ruled out here: a dim ball can sit below the contrast a
    # size-free search needs, and the lit-sphere fit is the real test
    floor = max(8.0, 3.0 * noise / factor)
    score = _disk_contrast(coarse, coarse_r)[y0:y1, x0:x1]
    peaks = (score == ndimage.maximum_filter(score, size=max(3, int(coarse_r)))) & (score >= floor)
    rows, cols = np.nonzero(peaks)
    kept: list[tuple[int, int]] = []
    for i in np.argsort(score[rows, cols])[::-1]:
        row, col = int(rows[i]), int(cols[i])
        if not all(math.hypot(col - c, row - r) > coarse_r for r, c in kept):
            continue
        # an edge, like a door's foot over the gap beneath it, scores along its
        # whole length; a ball is brighter than its surroundings all round
        if _brighter_all_round(coarse, row + y0, col + x0, coarse_r, 0.5 * floor):
            kept.append((row, col))
        if len(kept) == 3:
            break
    fits = []
    for row, col in kept:
        fit = fit_lit_ball(
            image,
            (col + x0 + 0.5) * factor - 0.5,
            (row + y0 + 0.5) * factor - 0.5,
            expected_radius,
            noise_dn=max(1.5, noise),
            expected_radius=expected_radius,
        )
        if fit is not None:
            fits.append(fit)
    if not fits:
        return None
    best = max(fits, key=lambda fit: fit.quality)
    return ReferenceBall(
        x=best.x,
        y=best.y,
        diameter_px=best.diameter_px,
        area_px=int(round(math.pi * best.radius_px**2)),
    )


def _contrast_ball(
    background: np.ndarray,
    frames: np.ndarray,
    roi: tuple[int, int, int, int] | None,
    expected_radius: float | None = None,
) -> ReferenceBall | None:
    """The disk that stands out most from its surroundings, if it clearly does."""
    height, width = background.shape
    if roi is None:
        fx0, fy0, fx1, fy1 = DISK_ZONE
        roi = (int(width * fx0), int(height * fy0), int(width * fx1), int(height * fy1))
    factor = max(1, round(width / DISK_SEARCH_WIDTH_PX))
    image = background.astype(np.float32)
    coarse = _bin(image, factor)
    x0, y0, x1, y1 = (value // factor for value in roi)
    noise = float(np.median(np.std(frames[: min(20, len(frames))], axis=0))) / factor
    floor = max(DISK_MIN_CONTRAST_DN, DISK_MIN_NOISE_MULTIPLE * noise)
    if expected_radius is not None:
        lit = _lit_ball_near_size(
            image, coarse, (x0, y0, x1, y1), factor, expected_radius, noise * factor
        )
        if lit is not None:
            return lit

    # 1. where: the best disk over plausible ball sizes, in the binned frame
    radii = np.geomspace(2.5, max(3.0, 0.04 * coarse.shape[1]), num=12)
    maps = [_disk_contrast(coarse, float(radius))[y0:y1, x0:x1] for radius in radii]
    peaks = [np.unravel_index(int(np.argmax(score)), score.shape) for score in maps]
    best = max(range(len(radii)), key=lambda i: maps[i][peaks[i]])
    contrast = float(maps[best][peaks[best]])
    if contrast < floor:
        return None
    row, col = peaks[best]
    # the largest scale still scoring near the best keeps the inner square
    # inside the ball, so it sits at the ball's own radius
    radius = max(
        float(r)
        for r, score in zip(radii, maps)
        if r >= radii[best] and score[row, col] >= 0.9 * contrast
    )
    yy, xx = np.indices(maps[0].shape)
    elsewhere = np.hypot(xx - col, yy - row) > 3.0 * radius
    if elsewhere.any():
        rival = max(float(score[elsewhere].max()) for score in maps)
        if rival > 0 and contrast / rival < DISK_MIN_MARGIN:
            return None

    # 2. how big and exactly where: the lit part places it on the binned frame;
    # the whole ball is then measured at full resolution
    cx, cy, r = _fit_disk(coarse, float(col + x0), float(row + y0), radius)
    return _measure_ball(image, (cx + 0.5) * factor - 0.5, (cy + 0.5) * factor - 0.5, r * factor)


def _disk_score(image: np.ndarray, cx: float, cy: float, r: float) -> float:
    """A disk's mean against the thin ring just outside its rim."""
    reach = int(np.ceil(1.4 * r)) + 2
    top, left = max(0, int(cy) - reach), max(0, int(cx) - reach)
    patch = image[top : int(cy) + reach + 1, left : int(cx) + reach + 1]
    yy, xx = np.indices(patch.shape, dtype=np.float32)
    distance = np.hypot(xx - (cx - left), yy - (cy - top))
    disk = np.clip(r + 0.5 - distance, 0.0, 1.0)
    ring = np.clip(1.4 * r + 0.5 - distance, 0.0, 1.0) - disk
    return float((patch * disk).sum() / disk.sum() - (patch * ring).sum() / ring.sum())


def _fit_disk(image: np.ndarray, x: float, y: float, radius: float) -> tuple[float, float, float]:
    """Every radius against every nearby centre: the disk that stands out most.

    Searched jointly because fitting centre and size in turns can settle on a
    highlight's small disk. On a ball lit from one side this is the lit part,
    so it places the measurement rather than making it.
    """
    step = max(0.5, 0.1 * radius)
    span = max(2.0, 0.6 * radius)
    offsets = np.arange(-span, span + step / 2, step)
    radii = np.geomspace(max(2.0, 0.5 * radius), max(4.0, 4.0 * radius), 30)
    _, x, y, radius = max(
        (_disk_score(image, x + dx, y + dy, r), x + dx, y + dy, float(r))
        for r in radii
        for dx in offsets
        for dy in offsets
    )
    return x, y, radius


BALL_EDGE_FRACTION = 0.25  # of the ball's contrast over the ground around it
RIM_RAY_STEP_DEG = 4.0
RIM_HALF_ARC_DEG = 80.0


def _measure_ball(image: np.ndarray, x: float, y: float, radius: float) -> ReferenceBall | None:
    """The whole ball, from the rim on its lit side.

    A ball lit from one side has a sharp rim facing the light and a shaded
    side that fades to the ground's own level, often beside its cast shadow.
    The bright patch finds the lit part and which way the light comes from; a
    circle through the lit half of the rim then gives the whole ball.
    """
    height, width = image.shape
    reach = int(np.ceil(3.0 * radius)) + 2
    top, left = max(0, int(y) - reach), max(0, int(x) - reach)
    patch = image[top : min(height, int(y) + reach + 1), left : min(width, int(x) + reach + 1)]
    yy, xx = np.indices(patch.shape, dtype=np.float32)
    distance = np.hypot(xx - (x - left), yy - (y - top))
    ground = float(np.median(patch[(distance >= 1.8 * radius) & (distance <= 2.8 * radius)]))
    ball = float(np.percentile(patch[distance <= radius], 90))
    if ball <= ground:
        return None
    mask = ndimage.binary_fill_holes(patch > ground + BALL_EDGE_FRACTION * (ball - ground))
    labels, _count = ndimage.label(mask)
    inside = labels[distance <= radius]
    inside = inside[inside > 0]
    if not inside.size:
        return None
    blob = labels == np.bincount(inside).argmax()
    ys, xs = np.nonzero(blob)
    lit_r = math.sqrt(len(xs) / math.pi)
    if not 0.6 * radius <= lit_r <= 1.75 * radius:
        return None  # it ran into the ground, or found only a speck
    bx, by = float(xs.mean()), float(ys.mean())
    weight = patch[ys, xs] - ground
    toward_light = np.array(
        [
            float((weight * xs).sum() / weight.sum()) - bx,
            float((weight * ys).sum() / weight.sum()) - by,
        ]
    )
    fitted = _fit_lit_rim(patch, bx, by, lit_r, toward_light, ball - ground)
    cx, cy, r = fitted if fitted is not None else (bx, by, lit_r)
    return ReferenceBall(
        x=cx + left, y=cy + top, diameter_px=2.0 * r, area_px=int(round(math.pi * r * r))
    )


def _fit_lit_rim(
    patch: np.ndarray,
    x: float,
    y: float,
    radius: float,
    toward_light: np.ndarray,
    contrast: float,
) -> tuple[float, float, float] | None:
    """A circle through the rim's sharp, lit side.

    Rays leave the lit patch's centre across the half facing the light (all
    the way round when the light is square on); on each, the rim is the
    outermost strong fall in brightness, so a logo or the highlight's own edge
    inside the ball is passed over.
    """
    smooth = ndimage.gaussian_filter(patch, 0.7)
    lean = float(np.hypot(*toward_light))
    if lean >= 0.08 * radius:
        facing = math.atan2(float(toward_light[1]), float(toward_light[0]))
        half = math.radians(RIM_HALF_ARC_DEG)
    else:
        facing, half = 0.0, math.pi
    angles = facing + np.arange(-half, half + 1e-9, math.radians(RIM_RAY_STEP_DEG))
    steps = np.arange(0.5 * radius, 2.0 * radius, 0.25)
    xs = x + steps[None, :] * np.cos(angles)[:, None]
    ys = y + steps[None, :] * np.sin(angles)[:, None]
    values = ndimage.map_coordinates(smooth, [ys.ravel(), xs.ravel()], order=1, mode="nearest")
    falls = -np.gradient(values.reshape(xs.shape), 0.25, axis=1)
    points = []
    for ray, fall in enumerate(falls):
        strongest = float(fall.max())
        if strongest < 0.1 * contrast:
            continue  # no rim on this ray: occluded, or it runs along the shadow
        peaks = [
            i
            for i in range(1, len(fall) - 1)
            if fall[i] >= 0.6 * strongest and fall[i] >= fall[i - 1] and fall[i] >= fall[i + 1]
        ]
        i = max(peaks) if peaks else int(np.argmax(fall))
        left_fall, here, right_fall = fall[i - 1], fall[i], fall[min(i + 1, len(fall) - 1)]
        curvature = left_fall - 2.0 * here + right_fall
        offset = 0.5 * (left_fall - right_fall) / curvature if curvature < 0 else 0.0
        s = steps[i] + 0.25 * offset
        points.append((x + s * math.cos(angles[ray]), y + s * math.sin(angles[ray])))
    if len(points) < 12:
        return None
    pts = np.asarray(points)
    for _round in range(2):
        # algebraic circle fit, then again without the rays it cannot explain
        a = np.column_stack([pts[:, 0], pts[:, 1], np.ones(len(pts))])
        b = -(pts[:, 0] ** 2 + pts[:, 1] ** 2)
        d, e, f = np.linalg.lstsq(a, b, rcond=None)[0]
        cx, cy = -d / 2.0, -e / 2.0
        r = math.sqrt(max(cx * cx + cy * cy - f, 0.0))
        residual = np.abs(np.hypot(pts[:, 0] - cx, pts[:, 1] - cy) - r)
        keep = residual <= max(1.0, 2.5 * float(np.median(residual)))
        if keep.all() or keep.sum() < 12:
            break
        pts = pts[keep]
    if not 0.8 * radius <= r <= 1.8 * radius:
        return None
    return float(cx), float(cy), float(r)


def detect_reference_ball(
    frames: np.ndarray,
    *,
    roi: tuple[int, int, int, int] | None = None,
    brightness_threshold: int = 210,
    expected_diameter_px: float | None = None,
) -> ReferenceBall:
    """Find the stationary ball in bright- or dark-on-ground lighting.

    ``expected_diameter_px`` is the size the ball must have at the distance the
    radar or the tape gives, through the lens's focal length. With it, the ball
    is the lit sphere of that size, placed by its lit rim and shading, so its
    centre holds when grass or carpet hides its underside. Without it, the size
    is read from the pixels, which a room-lit ball leaves loose by a tenth.
    """
    if frames.ndim != 3 or frames.shape[0] < 3:
        raise ValueError("frames must have shape (n, height, width) with n >= 3")

    background = np.median(frames[: min(20, frames.shape[0])], axis=0)
    height, width = background.shape
    x0, y0, x1, y1 = roi or (0, 0, width, height)
    if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
        raise ValueError("ball ROI is outside the image")

    image_center = np.asarray((width / 2, height / 2))

    def components(  # pylint: disable=too-many-arguments
        mask: np.ndarray,
        *,
        min_area: int,
        aspect_limits: tuple[float, float],
        min_fill: float,
        diameter_from_extent: bool = False,
        contrast: np.ndarray | None = None,
    ) -> list[tuple[float, ReferenceBall]]:
        labels, _count = ndimage.label(mask)
        found: list[tuple[float, ReferenceBall]] = []
        for label, box in enumerate(ndimage.find_objects(labels), start=1):
            if box is None:
                continue
            ys, xs = np.nonzero(labels[box] == label)
            ys, xs = ys + box[0].start, xs + box[1].start
            area = len(xs)
            if not min_area <= area <= 600:
                continue
            component_width = int(np.ptp(xs)) + 1
            component_height = int(np.ptp(ys)) + 1
            aspect = component_width / component_height
            fill = area / (component_width * component_height)
            if not (aspect_limits[0] <= aspect <= aspect_limits[1] and fill >= min_fill):
                continue
            center = np.asarray((float(xs.mean()), float(ys.mean())))
            diameter = (
                float(max(component_width, component_height))
                if diameter_from_extent
                else math.sqrt(4 * area / math.pi)
            )
            strength = float(np.mean(contrast[ys, xs])) if contrast is not None else 0.0
            score = (
                float(np.linalg.norm(center - image_center))
                + abs(math.log(aspect)) * 4.0
                - strength * 0.05
            )
            found.append(
                (
                    score,
                    ReferenceBall(
                        x=float(center[0]),
                        y=float(center[1]),
                        diameter_px=diameter,
                        area_px=area,
                    ),
                )
            )
        return found

    # Prefer a clean white-ball component in the compact high-speed crop. The
    # dark-first order remains useful for spotlight-washed 640x400 indoor
    # scenes, but at 320x200 it can select dark foliage instead of the ball.
    bright_mask = np.zeros_like(background, dtype=bool)
    bright_mask[y0:y1, x0:x1] = background[y0:y1, x0:x1] >= brightness_threshold
    bright_candidates = components(
        bright_mask,
        min_area=20,
        aspect_limits=(0.55, 1.8),
        min_fill=0.45,
    )
    compact_capture = background.shape[0] <= 200 and background.shape[1] <= 320
    if compact_capture and bright_candidates:
        # Compact outdoor crops contain many saturated, round highlights in
        # the net and foliage. A regulation ball belongs in the central-lower
        # hitting zone and has a stable apparent diameter at tee distance.
        plausible = [
            item
            for item in bright_candidates
            if 9.0 <= item[1].diameter_px <= 30.0
            and width * 0.2 <= item[1].x <= width * 0.8
            and height * 0.45 <= item[1].y <= height * 0.9
        ]
        if plausible:
            found = min(plausible, key=lambda item: item[0])[1]
            if expected_diameter_px is None:
                return found
            fit = fit_lit_ball(
                background.astype(np.float32),
                found.x,
                found.y,
                expected_diameter_px / 2.0,
                expected_radius=expected_diameter_px / 2.0,
            )
            if fit is None:
                return found
            return ReferenceBall(
                x=fit.x,
                y=fit.y,
                diameter_px=fit.diameter_px,
                area_px=int(round(math.pi * fit.radius_px**2)),
            )

    lit = _contrast_ball(
        background,
        frames,
        roi,
        expected_diameter_px / 2.0 if expected_diameter_px is not None else None,
    )
    if lit is not None:
        return lit

    # A spotlight can wash the white face of the ball into the turf while its
    # lower silhouette remains dark. Local contrast is more stable than an
    # absolute dark threshold across indoor and outdoor exposure settings.
    dark_x0 = x0 if roi is not None else max(x0, int(width * 0.25))
    dark_x1 = x1 if roi is not None else min(x1, int(width * 0.75))
    dark_y0 = y0 if roi is not None else max(y0, int(height * 0.30))
    dark_y1 = y1 if roi is not None else min(y1, int(height * 0.70))
    blur_sigma = max(4.0, min(height, width) * 0.02)
    dark_contrast = ndimage.gaussian_filter(background, sigma=blur_sigma) - background
    dark_roi = dark_contrast[dark_y0:dark_y1, dark_x0:dark_x1]
    dark_threshold = max(20.0, float(np.percentile(dark_roi, 99.0)))
    dark_mask = np.zeros_like(background, dtype=bool)
    dark_mask[dark_y0:dark_y1, dark_x0:dark_x1] = dark_roi >= dark_threshold
    dark_candidates = components(
        dark_mask,
        min_area=8,
        aspect_limits=(0.25, 5.0),
        min_fill=0.25,
        diameter_from_extent=True,
        contrast=dark_contrast,
    )
    if dark_candidates:
        seed = min(dark_candidates, key=lambda item: item[0])[1]
        radius = max(8, int(math.ceil(seed.diameter_px * 1.5)))
        patch_x0 = max(dark_x0, int(round(seed.x)) - radius)
        patch_x1 = min(dark_x1, int(round(seed.x)) + radius + 1)
        patch_y0 = max(dark_y0, int(round(seed.y)) - radius)
        patch_y1 = min(dark_y1, int(round(seed.y)) + radius + 1)
        low_mask = dark_contrast[patch_y0:patch_y1, patch_x0:patch_x1] >= max(
            12.0, dark_threshold * 0.3
        )
        labels, _count = ndimage.label(low_mask)
        mask_ys, mask_xs = np.where(low_mask)
        if len(mask_xs):
            nearest = np.argmin(
                (mask_xs + patch_x0 - seed.x) ** 2 + (mask_ys + patch_y0 - seed.y) ** 2
            )
            selected_label = labels[mask_ys[nearest], mask_xs[nearest]]
            component_ys, component_xs = np.where(labels == selected_label)
            component_xs = component_xs + patch_x0
            component_ys = component_ys + patch_y0
            component_width = int(np.ptp(component_xs)) + 1
            component_height = int(np.ptp(component_ys)) + 1
            area = len(component_xs)
            if 8 <= area <= 600:
                return ReferenceBall(
                    x=float(component_xs.mean()),
                    y=float(component_ys.mean()),
                    diameter_px=float(max(component_width, component_height)),
                    area_px=area,
                )
        return seed

    if bright_candidates:
        return min(bright_candidates, key=lambda item: item[0])[1]
    raise ValueError("no stable reference ball found")


def _shaft_candidates(
    frame: np.ndarray,
    background: np.ndarray,
    ball: ReferenceBall,
    *,
    bright_threshold: int,
    difference_threshold: int,
) -> list[tuple[float, float, float]]:
    """Return Hough shaft endpoints as (score, x, y)."""
    try:
        import cv2
    except ImportError as exc:  # pragma: no cover - depends on optional analysis extra
        raise RuntimeError(
            "camera club tracking needs the analysis extra: uv run --extra analysis ..."
        ) from exc

    background_u8 = background.astype(np.uint8)
    difference = cv2.absdiff(frame, background_u8)
    mask = ((frame > bright_threshold) & (difference > difference_threshold)).astype(np.uint8)
    mask *= 255
    height, width = frame.shape
    mask[: max(10, height // 20)] = 0
    mask[min(height, int(height * 0.7)) :] = 0
    mask[:, : max(10, int(width * 0.15))] = 0
    mask[:, min(width, int(width * 0.85)) :] = 0

    lines = cv2.HoughLinesP(
        mask,
        1,
        np.pi / 360,
        threshold=25,
        minLineLength=25,
        maxLineGap=12,
    )
    if lines is None:
        return []

    candidates: list[tuple[float, float, float]] = []
    for x1, y1, x2, y2 in np.asarray(lines).reshape(-1, 4):
        dx = float(x2 - x1)
        dy = float(y2 - y1)
        length = math.hypot(dx, dy)
        verticality = abs(dy) / (abs(dx) + 1.0)
        if verticality < 0.7:
            continue
        distance1 = math.hypot(x1 - ball.x, y1 - ball.y)
        distance2 = math.hypot(x2 - ball.x, y2 - ball.y)
        endpoint_x, endpoint_y, distance = (
            (float(x1), float(y1), distance1)
            if distance1 < distance2
            else (float(x2), float(y2), distance2)
        )
        if distance > max(frame.shape) * 0.35:
            continue
        candidates.append((distance - length * 0.08, endpoint_x, endpoint_y))
    return candidates


def track_bright_shaft_endpoint(
    frames: np.ndarray,
    *,
    trigger_frame_index: int,
    ball: ReferenceBall,
    frames_before_trigger: int = 4,
    bright_threshold: int = 145,
    difference_threshold: int = 25,
) -> ShaftTrack:
    """Track the lower endpoint of the bright shaft before the trigger frame."""
    if frames.ndim != 3:
        raise ValueError("frames must have shape (n, height, width)")
    first = trigger_frame_index - frames_before_trigger
    if first < 0 or trigger_frame_index > len(frames):
        raise ValueError("trigger frame does not leave the requested pre-impact window")

    background = np.median(frames[: min(20, len(frames))], axis=0)
    points: list[ImagePoint] = []
    for frame_index in range(first, trigger_frame_index):
        candidates = _shaft_candidates(
            frames[frame_index],
            background,
            ball,
            bright_threshold=bright_threshold,
            difference_threshold=difference_threshold,
        )
        if not candidates:
            continue
        _, x, y = min(candidates, key=lambda candidate: candidate[0])
        points.append(ImagePoint(frame_index=frame_index, x=x, y=y))

    if len(points) < 2:
        return ShaftTrack(tuple(points), 0.0, "insufficient_shaft_endpoints")

    distances = np.asarray([math.hypot(point.x - ball.x, point.y - ball.y) for point in points])
    decreasing_fraction = float(np.mean(np.diff(distances) < 0)) if len(points) > 1 else 0.0
    coverage = len(points) / frames_before_trigger
    confidence = min(1.0, 0.65 * coverage + 0.35 * decreasing_fraction)
    reason = "ok" if len(points) == frames_before_trigger else "partial_preimpact_track"
    return ShaftTrack(tuple(points), confidence, reason)


def image_plane_motion(
    points: Sequence[ImagePoint],
    timestamps_ns: np.ndarray,
    *,
    ball_diameter_px: float,
) -> ImagePlaneMotion:
    """Calculate terminal image-plane motion from the final adjacent points."""
    if len(points) < 2:
        raise ValueError("at least two tracked points are required")
    first, second = points[-2:]
    if second.frame_index != first.frame_index + 1:
        raise ValueError("terminal tracked points must be consecutive")
    if ball_diameter_px <= 0:
        raise ValueError("ball diameter must be positive")

    delta_s = (int(timestamps_ns[second.frame_index]) - int(timestamps_ns[first.frame_index])) / 1e9
    if delta_s <= 0:
        raise ValueError("frame timestamps must increase")
    horizontal_px_s = (second.x - first.x) / delta_s
    # Image y grows downward; physical vertical velocity uses the opposite sign.
    vertical_px_s = -(second.y - first.y) / delta_s
    mm_per_px = BALL_DIAMETER_MM / ball_diameter_px
    return ImagePlaneMotion(
        horizontal_px_s=horizontal_px_s,
        vertical_px_s=vertical_px_s,
        horizontal_m_s=horizontal_px_s * mm_per_px / 1000.0,
        vertical_m_s=vertical_px_s * mm_per_px / 1000.0,
        mm_per_px=mm_per_px,
        interval_ms=delta_s * 1000.0,
    )

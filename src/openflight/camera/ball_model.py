"""A lit golf ball's image fitted to the pixels: centre, size and light direction.

The whole shaded disk is fitted, not just its edge: where the lit side fades and where the
terminator falls both depend on the ball's size, even where the rim has no edge to the ground.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np
from scipy import ndimage, optimize

EDGE_BLUR_PX = 0.7  # optics and focus soften the rim by about this much
BATCHED_JACOBIAN = True
# The gates are in the picture's own noise, not DN: contrast moves with the light.
MIN_DIFFUSE_NOISE_MULTIPLE = 5.0  # the lit top stands this far over the shade
MAX_MISFIT_FRACTION = 0.35  # of the diffuse brightness left unexplained, at most
# A ball's lit rim steps down to the ground; a shadow's straight edge on a wall does not.
MIN_RIM_STEP_FRACTION = 0.2  # of the diffuse brightness
MIN_RIM_STEP_NOISE_MULTIPLE = 3.0
# The lit rim's edge lies on the circle the distance gives: within 0.02-0.09 of the radius on
# real balls at 0.6-2 m, against 0.09-0.6 for look-alikes (door stop, light streak, clipped white).
MAX_RIM_OFFSET_FRACTION = 0.12
# A ball's lit rim curves with the circle; a straight lit edge (an empty mat's back edge, a
# kerb) runs on past it. Along rays 30-48 deg either side of the light, a line tangent at
# the top lies 1/cos - 1 (0.15-0.5) of the radius further out than at the top; a ball's
# rim, about 0. Harjot-outdoor-test-1 (dusk): fits on the empty mat's back edge 0.22-0.25,
# and the balls -0.06-0.05, however well sized; the mat's edge outscored the ball.
MAX_RIM_FLANK_BULGE_FRACTION = 0.15
RIM_TOP_HALF_ARC_DEG = 18.0
RIM_FLANK_ARC_DEG = (30.0, 48.0)
# Lights are above the ball: in image axes (y down) the light comes from the upper half.
LIGHT_AZIMUTH_DEG = (170.0, 370.0)


@dataclass(frozen=True)
class LitBall:
    """A fitted ball and how well the model explains the pixels around it."""

    x: float
    y: float
    radius_px: float
    light_azimuth_deg: float
    light_tilt_deg: float
    diffuse_dn: float
    shade_dn: float
    ground_dn: float
    misfit_dn: float
    rim_step_dn: float = 0.0

    @property
    def diameter_px(self) -> float:
        return 2.0 * self.radius_px

    @property
    def quality(self) -> float:
        """Diffuse brightness over what the model could not explain."""
        return self.diffuse_dn / max(self.misfit_dn, 1e-6)


def _render(params: np.ndarray, yy: np.ndarray, xx: np.ndarray) -> np.ndarray:
    cx, cy, radius, azimuth, tilt, shade, diffuse, ground, ground_x, ground_y = params
    dx, dy = xx - cx, yy - cy
    rho2 = (dx * dx + dy * dy) / (radius * radius)
    nz = np.sqrt(np.clip(1.0 - rho2, 0.0, None))
    light = (math.sin(tilt) * math.cos(azimuth), math.sin(tilt) * math.sin(azimuth), math.cos(tilt))
    lit = np.clip(dx / radius * light[0] + dy / radius * light[1] + nz * light[2], 0.0, None)
    ball = shade + diffuse * lit
    cover = np.clip(radius + 0.5 - np.sqrt(dx * dx + dy * dy), 0.0, 1.0)
    floor = ground + ground_x * (xx - xx.mean()) + ground_y * (yy - yy.mean())
    picture = ndimage.gaussian_filter(cover * ball + (1.0 - cover) * floor, EDGE_BLUR_PX)
    return np.clip(picture, 0.0, 255.0)


def _render_batch(params: np.ndarray, yy: np.ndarray, xx: np.ndarray) -> np.ndarray:
    """``_render`` for many parameter sets at once, as one (n, H, W) array; cheaper on a Pi."""
    p = np.asarray(params, dtype=np.float64)[:, :, None, None]
    cx, cy, radius, azimuth, tilt, shade, diffuse, ground, ground_x, ground_y = (
        p[:, index] for index in range(10)
    )
    dx, dy = xx - cx, yy - cy
    rho2 = (dx * dx + dy * dy) / (radius * radius)
    nz = np.sqrt(np.clip(1.0 - rho2, 0.0, None))
    light_x = np.sin(tilt) * np.cos(azimuth)
    light_y = np.sin(tilt) * np.sin(azimuth)
    light_z = np.cos(tilt)
    lit = np.clip(dx / radius * light_x + dy / radius * light_y + nz * light_z, 0.0, None)
    ball = shade + diffuse * lit
    cover = np.clip(radius + 0.5 - np.sqrt(dx * dx + dy * dy), 0.0, 1.0)
    floor = ground + ground_x * (xx - xx.mean()) + ground_y * (yy - yy.mean())
    picture = ndimage.gaussian_filter(
        cover * ball + (1.0 - cover) * floor, (0.0, EDGE_BLUR_PX, EDGE_BLUR_PX)
    )
    return np.clip(picture, 0.0, 255.0)


def _batched_jacobian(yy, xx, patch, use, noise_dn, lower, upper):
    """Forward differences with scipy's '2-point' steps, all variants drawn at once."""
    lower, upper = np.asarray(lower, dtype=float), np.asarray(upper, dtype=float)
    epsilon = np.sqrt(np.finfo(np.float64).eps)

    def jacobian(params: np.ndarray) -> np.ndarray:
        sign = np.where(params >= 0.0, 1.0, -1.0)
        step = epsilon * sign * np.maximum(1.0, np.abs(params))
        outside = (params + step > upper) | (params + step < lower)
        step = np.where(outside, -step, step)
        step = (params + step) - params
        variants = np.vstack([params, params + np.diag(step)])
        pictures = _render_batch(variants, yy, xx)
        base = pictures[0][use]
        changed = pictures[1:][:, use]
        return ((changed - base) / step[:, None]).T / noise_dn

    return jacobian


def _bin2(image: np.ndarray) -> np.ndarray:
    height, width = (image.shape[0] // 2) * 2, (image.shape[1] // 2) * 2
    return (
        image[:height, :width]
        .astype(np.float64)
        .reshape(height // 2, 2, width // 2, 2)
        .mean(axis=(1, 3))
    )


def fit_lit_ball(
    image: np.ndarray,
    x: float,
    y: float,
    radius: float,
    *,
    noise_dn: float = 2.5,
    expected_radius: float | None = None,
    size_tolerance: float = 0.01,
    size_starts: Sequence[float] | None = None,
    extra_light_starts: bool = True,
) -> LitBall | None:
    """Fit a lit sphere near (x, y); ``expected_radius`` holds its size to ``size_tolerance``.

    ``size_starts`` are the free size's starting fractions of ``radius``; ``extra_light_starts``
    False starts the light only from its measured lean, for a centre already placed.
    """
    binning = 2 if max(radius, expected_radius or 0.0) > 12.0 else 1
    if binning > 1:
        fit = fit_lit_ball(
            _bin2(image),
            (x + 0.5) / 2 - 0.5,
            (y + 0.5) / 2 - 0.5,
            radius / 2,
            noise_dn=noise_dn / 2,
            expected_radius=expected_radius / 2 if expected_radius else None,
            size_tolerance=size_tolerance,
            size_starts=size_starts,
            extra_light_starts=extra_light_starts,
        )
        if fit is None:
            return None
        return LitBall(
            x=(fit.x + 0.5) * 2 - 0.5,
            y=(fit.y + 0.5) * 2 - 0.5,
            radius_px=fit.radius_px * 2,
            light_azimuth_deg=fit.light_azimuth_deg,
            light_tilt_deg=fit.light_tilt_deg,
            diffuse_dn=fit.diffuse_dn,
            shade_dn=fit.shade_dn,
            ground_dn=fit.ground_dn,
            misfit_dn=fit.misfit_dn,
            rim_step_dn=fit.rim_step_dn,
        )
    height, width = image.shape
    guess = expected_radius or radius
    reach = int(math.ceil(2.4 * max(radius, guess))) + 3
    top, left = max(0, int(y) - reach), max(0, int(x) - reach)
    bottom, right = min(height, int(y) + reach + 1), min(width, int(x) + reach + 1)
    patch = image[top:bottom, left:right].astype(np.float64)
    if patch.shape[0] < 8 or patch.shape[1] < 8:
        return None
    yy, xx = np.indices(patch.shape, dtype=np.float64)
    px, py = x - left, y - top
    distance = np.hypot(xx - px, yy - py)
    ring = patch[(distance >= 1.6 * guess) & (distance <= 2.3 * guess)]
    ground = float(np.median(ring)) if ring.size else float(np.median(patch))
    inside = patch[distance <= 0.8 * guess]
    if not inside.size:
        return None
    bright, dark = np.percentile(inside, (95, 10))
    weight = np.clip(patch - ground, 0.0, None) * (distance <= guess)
    total = float(weight.sum())
    lean_x = float((weight * (xx - px)).sum()) if total > 0 else 0.0
    lean_y = float((weight * (yy - py)).sum()) if total > 0 else -1.0
    # a lean downwards is a dark gap or shadow above the ball, not light from below
    azimuth = math.atan2(min(lean_y, -1e-6), lean_x) % (2 * math.pi)
    if math.degrees(azimuth) < LIGHT_AZIMUTH_DEG[0]:
        azimuth += 2 * math.pi
    # the rim away from the light fades into the cast shadow: size comes from the lit rim
    away = (np.cos(azimuth) * (xx - px) + np.sin(azimuth) * (yy - py)) < 0
    use = ~(away & (distance > 0.85 * radius)) if total > 0 else np.ones_like(away)
    low_r, high_r = (
        ((1.0 - size_tolerance) * expected_radius, (1.0 + size_tolerance) * expected_radius)
        if expected_radius
        else (0.5 * radius, 2.0 * radius)
    )

    def residuals(params: np.ndarray) -> np.ndarray:
        return (_render(params, yy, xx) - patch)[use] / noise_dn

    best = None
    sizes = (1.0,) if expected_radius else tuple(size_starts or (0.85, 1.0, 1.2))
    # an off-centre seed can turn the lean the wrong way: also start upper left and right
    lights = [azimuth] + [
        math.radians(a)
        for a in (225.0, 315.0)
        if extra_light_starts and abs(math.degrees(azimuth) - a) > 30.0
    ]
    for start_r, start_az in (
        (float(np.clip(s * guess, low_r * 1.01, high_r * 0.99)), az) for s in sizes for az in lights
    ):
        initial = np.array(
            [
                px,
                py,
                start_r,
                start_az,
                math.radians(60.0),
                dark,
                max(bright - dark, 1.0),
                ground,
                0.0,
                0.0,
            ]
        )
        az_low, az_high = (math.radians(a) for a in LIGHT_AZIMUTH_DEG)
        lower = [px - guess, py - guess, low_r, az_low, 0.0, 0.0, 0.0, 0.0, -5.0, -5.0]
        upper = [
            px + guess,
            py + guess,
            high_r,
            az_high,
            math.radians(120.0),
            255.0,
            400.0,
            255.0,
            5.0,
            5.0,
        ]
        try:
            result = optimize.least_squares(
                residuals,
                initial,
                jac=(
                    _batched_jacobian(yy, xx, patch, use, noise_dn, lower, upper)
                    if BATCHED_JACOBIAN
                    else "2-point"
                ),
                bounds=(lower, upper),
                # grass, carpet and the cast shadow hide parts of the ball: Cauchy lets them go
                loss="cauchy",
                f_scale=3.0,
                max_nfev=120,
            )
        except (ValueError, np.linalg.LinAlgError):
            continue
        if best is None or result.cost < best.cost:
            best = result
    if best is None:
        return None
    cx, cy, r, azimuth, tilt, shade, diffuse, ground, _gx, _gy = best.x
    misfit = np.abs(_render(best.x, yy, xx) - patch)
    misfit_dn = float(np.median(misfit[use & (np.hypot(xx - cx, yy - cy) <= 1.3 * r)]) * 1.4826)
    # no model beats the pixels' own noise: clipped white fits any bright sphere otherwise
    misfit_dn = max(misfit_dn, noise_dn)
    if diffuse < MIN_DIFFUSE_NOISE_MULTIPLE * noise_dn or misfit_dn > MAX_MISFIT_FRACTION * diffuse:
        return None
    step = _lit_rim_step(patch, cx, cy, r, azimuth)
    if step < max(MIN_RIM_STEP_NOISE_MULTIPLE * noise_dn, MIN_RIM_STEP_FRACTION * diffuse):
        return None
    turns, offsets = _lit_rim_offsets(patch, cx, cy, r, azimuth)
    if (
        float(np.median(np.abs(offsets))) > MAX_RIM_OFFSET_FRACTION
        or _rim_flank_bulge(turns, offsets) > MAX_RIM_FLANK_BULGE_FRACTION
    ):
        return None
    return LitBall(
        x=float(cx + left),
        y=float(cy + top),
        radius_px=float(r),
        light_azimuth_deg=math.degrees(azimuth) % 360.0,
        light_tilt_deg=math.degrees(tilt),
        diffuse_dn=float(diffuse),
        shade_dn=float(shade),
        ground_dn=float(ground),
        misfit_dn=misfit_dn,
        rim_step_dn=step,
    )


def _lit_rim_step(patch: np.ndarray, cx: float, cy: float, r: float, azimuth: float) -> float:
    """How much brighter the ball is just inside its lit rim than the ground just outside."""
    angles = azimuth + np.radians(np.arange(-60.0, 61.0, 6.0))

    def band(low: float, high: float) -> float:
        radii = np.linspace(low * r, high * r, 5)
        xs = cx + radii[None, :] * np.cos(angles)[:, None]
        ys = cy + radii[None, :] * np.sin(angles)[:, None]
        return float(
            ndimage.map_coordinates(patch, [ys.ravel(), xs.ravel()], order=1, mode="nearest").mean()
        )

    return band(0.7, 0.92) - band(1.15, 1.45)


def _lit_rim_offsets(
    patch: np.ndarray, cx: float, cy: float, r: float, azimuth: float
) -> tuple[np.ndarray, np.ndarray]:
    """Where the lit rim's edge lies on rays either side of the light: each ray's turn
    from the light in degrees, and its edge's distance past the circle over the radius."""
    smooth = ndimage.gaussian_filter(patch, EDGE_BLUR_PX)
    turns = np.arange(-60.0, 61.0, 6.0)
    angles = azimuth + np.radians(turns)
    radii = np.arange(0.4 * r, 1.6 * r, 0.25)
    xs = cx + radii[None, :] * np.cos(angles)[:, None]
    ys = cy + radii[None, :] * np.sin(angles)[:, None]
    values = ndimage.map_coordinates(smooth, [ys.ravel(), xs.ravel()], order=1, mode="nearest")
    falls = -np.gradient(values.reshape(xs.shape), 0.25, axis=1)
    edges = radii[np.argmax(falls, axis=1)]
    return turns, (edges - r) / r


def _rim_flank_bulge(turns: np.ndarray, offsets: np.ndarray) -> float:
    """How much further out the lit rim's edge lies on the flanks than at the top."""
    away = np.abs(turns)
    low, high = RIM_FLANK_ARC_DEG
    flanks = offsets[(away >= low) & (away <= high)]
    top = offsets[away <= RIM_TOP_HALF_ARC_DEG]
    return float(np.median(flanks) - np.median(top))

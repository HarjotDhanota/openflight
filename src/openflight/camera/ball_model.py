"""A lit golf ball's image fitted to the pixels: centre, size and light direction.

A resting ball is a sphere of known size under the room's light. Its image is a
disk whose brightness follows that light: bright towards it, fading to the
terminator, a dark self-shadowed side, all set against the ground. Fitting the
whole picture uses the shaded side even where its rim has no edge against the
ground or the ball's own cast shadow, because where the lit side fades, and
where the terminator falls, both depend on the ball's size.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy import ndimage, optimize

EDGE_BLUR_PX = 0.7  # optics and focus soften the rim by about this much
# The gates are in the picture's own noise, not in DN: a dim room or a low gain
# leaves a real ball's lit top only 11 DN over its shade, and its contrast
# rises and falls with the light while the noise sets what can be seen.
MIN_DIFFUSE_NOISE_MULTIPLE = 5.0  # the lit top stands this far over the shade
MAX_MISFIT_FRACTION = 0.35  # of the diffuse brightness left unexplained, at most
# A ball's lit rim steps down to the ground beyond it. A half-lit sphere can
# otherwise mimic a shadow's straight edge on a wall, where no such step exists.
MIN_RIM_STEP_FRACTION = 0.2  # of the diffuse brightness
MIN_RIM_STEP_NOISE_MULTIPLE = 3.0
# The lit rim's edge lies on the circle of the size the distance gives: within
# 0.02 to 0.09 of the radius on real balls from 0.6 to 2 m, against 0.09 to
# 0.6 for a door stop, a streak of light on a storage bin, a door's corner and
# clipped white.
MAX_RIM_OFFSET_FRACTION = 0.12
# Room lights and the sun are above the ball: in image axes (y down) the light
# comes from the upper half, horizontal-left through overhead to horizontal-right.
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
) -> LitBall | None:
    """Fit a lit sphere near (x, y); ``expected_radius`` holds its size.

    On a real ball in room light the size is not in the pixels: the shaded
    side meets its own shadow with no edge, and a glossy, dimpled ball with a
    logo is not the matte sphere the model draws, so a free size drifts by a
    fifth either way. The size the ball must have at the distance the radar or
    the tape gives, through the lens's focal length, is held to within
    ``size_tolerance``; the pixels then place the centre and the light.
    """
    # a big ball is fitted on a patch binned 2x2: a 38 px ball is still 19 px,
    # and the fit is four times cheaper
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
    # away from the light, the rim fades into the ball's own cast shadow: those
    # pixels would let a model without a shadow grow the ball into it, so the
    # size is left to the lit rim and the shading inside
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
    sizes = (1.0,) if expected_radius else (0.85, 1.0, 1.2)
    # the lean can point the wrong way when the seed sits off the ball's centre,
    # so the fit also starts from the upper left and the upper right
    lights = [azimuth] + [
        math.radians(a) for a in (225.0, 315.0) if abs(math.degrees(azimuth) - a) > 30.0
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
                bounds=(lower, upper),
                # grass, carpet pile and the cast shadow hide parts of the
                # ball: Cauchy lets those pixels go rather than drag the fit
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
    # no model explains pixels better than their own noise: clipped white
    # otherwise fits any bright sphere with nothing left over
    misfit_dn = max(misfit_dn, noise_dn)
    if diffuse < MIN_DIFFUSE_NOISE_MULTIPLE * noise_dn or misfit_dn > MAX_MISFIT_FRACTION * diffuse:
        return None
    step = _lit_rim_step(patch, cx, cy, r, azimuth)
    if step < max(MIN_RIM_STEP_NOISE_MULTIPLE * noise_dn, MIN_RIM_STEP_FRACTION * diffuse):
        return None
    if _lit_rim_offset(patch, cx, cy, r, azimuth) > MAX_RIM_OFFSET_FRACTION:
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


def _lit_rim_offset(patch: np.ndarray, cx: float, cy: float, r: float, azimuth: float) -> float:
    """How far the lit rim's edge lies from the fitted circle, as a fraction of its radius.

    On each ray across the lit side the edge is where the brightness falls
    fastest; a ray with no rim on it finds its fall anywhere, far from the circle.
    """
    smooth = ndimage.gaussian_filter(patch, EDGE_BLUR_PX)
    angles = azimuth + np.radians(np.arange(-60.0, 61.0, 6.0))
    radii = np.arange(0.4 * r, 1.6 * r, 0.25)
    xs = cx + radii[None, :] * np.cos(angles)[:, None]
    ys = cy + radii[None, :] * np.sin(angles)[:, None]
    values = ndimage.map_coordinates(smooth, [ys.ravel(), xs.ravel()], order=1, mode="nearest")
    falls = -np.gradient(values.reshape(xs.shape), 0.25, axis=1)
    edges = radii[np.argmax(falls, axis=1)]
    return float(np.median(np.abs(edges - r)) / r)

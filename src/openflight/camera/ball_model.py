"""A lit golf ball's image fitted to the pixels: centre, size and light direction.

The whole shaded disk is fitted, not just its edge: that finds the ball and the light, and
tells a ball from look-alikes. A free size, and the centre with it, are then measured on the
ball's outline (``measure_rim``): under an open sky the light wraps round the ball, which
one point light cannot draw, and the fitted sphere grows down into the shaded underside and
the shadow beneath it (Harjot-outdoor-test-1 setup: 58.98 px for a ball about 43 px across).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
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
# A free size is measured on the outline all round but the bottom, where the underside rests
# in its own cast shadow: rays every few degrees, up to this far either side of straight
# down left out. On each ray the outline steps the way the sphere says: down where the rim
# is lit and brighter than the ground, up where it is in shade and darker (a sunlit ball's
# far side, Outdoors-test-7); any other ray has no say.
RIM_SIZE_SHADOW_HALF_ARC_DEG = 45.0
RIM_SIZE_RAY_STEP_DEG = 4.0
RIM_SIZE_REACH = (0.5, 1.4)  # of the sphere's radius, out from its centre
RIM_SIZE_GROUND_FROM = 1.2  # the ground beyond the rim, as far out as the reach
# A rim lit, and brighter or darker than the ground, by this fraction of the diffuse
# brightness: less, near the terminator, and the fading light inside it is taken for the edge.
RIM_SIZE_MIN_STEP_FRACTION = 0.2
# The steps sought, per pixel along the ray, as a fraction of the diffuse brightness: the
# sides step far less than the lit top (Harjot-outdoor-test-1 at dusk: under a tenth).
RIM_SIZE_MIN_EDGE_FRACTION = 0.04
# A ray agrees with a circle when one of its steps lies this close to it.
RIM_SIZE_TOLERANCE_PX = 1.0
RIM_SIZE_TOLERANCE_FRACTION = 0.03  # of the radius, for a near ball
RIM_SIZE_TRIALS = 200
RIM_SIZE_MIN_RAYS = 16  # of the 68
# The outline lies within these fractions of the sphere's radius, its centre within the
# lower fraction's remainder of the sphere's. Harjot-outdoor-test-1: outlines 0.72-0.93 of
# a sphere grown into the shade, 1.19-1.23 of one stopped small on its seed's bound.
RIM_SIZE_RADIUS_FRACTION = (0.6, 1.4)
# A sphere its outline agrees with this closely stands: near the terminator the outline
# lies up to a pixel inside, a tenth of a 12 px ball's size (a sunlit ball 2-4 % small),
# where a sphere grown into the shade is 7-39 % too large.
RIM_SIZE_AGREES_FRACTION = 0.05


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
    """Scipy's residual differences and column layout, all variants drawn at once."""
    lower, upper = np.asarray(lower, dtype=float), np.asarray(upper, dtype=float)
    epsilon = np.sqrt(np.finfo(np.float64).eps)

    def jacobian(params: np.ndarray) -> np.ndarray:
        sign = np.where(params >= 0.0, 1.0, -1.0)
        step = epsilon * sign * np.maximum(1.0, np.abs(params))
        outside = (params + step > upper) | (params + step < lower)
        step = np.where(outside, -step, step)
        step = (params + step) - params
        variants = np.vstack([params, params + np.diag(step)])
        pictures = (_render_batch(variants, yy, xx) - patch) / noise_dn
        base = pictures[0][use]
        changed = pictures[1:][:, use]
        return np.asfortranarray(((changed - base) / step[:, None]).T)

    return jacobian


def _analytic_jacobian(yy, xx, patch, use, noise_dn, lower, upper):
    """Differentiate the same renderer; retain numerical differences at its square-root rim."""
    fallback = _batched_jacobian(yy, xx, patch, use, noise_dn, lower, upper)
    xx0, yy0 = xx - xx.mean(), yy - yy.mean()

    def jacobian(params):  # pylint: disable=too-many-locals
        cx, cy, radius, azimuth, tilt, shade, diffuse, ground, ground_x, ground_y = params
        dx, dy = xx - cx, yy - cy
        rho2 = (dx * dx + dy * dy) / (radius * radius)
        if np.any(np.abs(1.0 - rho2) < 1e-6):
            return fallback(params)
        nz = np.sqrt(np.clip(1.0 - rho2, 0.0, None))
        inv_nz = np.zeros_like(nz)
        np.divide(1.0, nz, out=inv_nz, where=nz > 0.0)
        sin_az, cos_az = np.sin(azimuth), np.cos(azimuth)
        sin_tilt, cos_tilt = np.sin(tilt), np.cos(tilt)
        light_x, light_y, light_z = sin_tilt * cos_az, sin_tilt * sin_az, cos_tilt
        nx, ny = dx / radius, dy / radius
        irradiance = nx * light_x + ny * light_y + nz * light_z
        lit = np.maximum(irradiance, 0.0)
        ball = shade + diffuse * lit
        floor = ground + ground_x * xx0 + ground_y * yy0
        distance = np.hypot(dx, dy)
        cover = np.clip(radius + 0.5 - distance, 0.0, 1.0)
        edge = (cover > 0.0) & (cover < 1.0)
        inv_distance = np.zeros_like(distance)
        np.divide(1.0, distance, out=inv_distance, where=distance > 0.0)
        weight = cover * diffuse * (irradiance > 0.0)
        derivatives = np.stack(
            [
                edge * dx * inv_distance * (ball - floor)
                + weight * (-light_x / radius + light_z * dx / radius**2 * inv_nz),
                edge * dy * inv_distance * (ball - floor)
                + weight * (-light_y / radius + light_z * dy / radius**2 * inv_nz),
                edge * (ball - floor)
                + weight
                * (
                    -nx * light_x / radius
                    - ny * light_y / radius
                    + light_z * rho2 / radius * inv_nz
                ),
                weight * (-nx * light_y + ny * light_x),
                weight * (nx * cos_tilt * cos_az + ny * cos_tilt * sin_az - nz * sin_tilt),
                cover,
                cover * lit,
                1.0 - cover,
                (1.0 - cover) * xx0,
                (1.0 - cover) * yy0,
            ]
        )
        derivatives = ndimage.gaussian_filter(derivatives, (0.0, EDGE_BLUR_PX, EDGE_BLUR_PX))
        rendered = ndimage.gaussian_filter(cover * ball + (1.0 - cover) * floor, EDGE_BLUR_PX)
        derivatives[:, (rendered <= 0.0) | (rendered >= 255.0)] = 0.0
        return np.asfortranarray(derivatives[:, use].T / noise_dn)

    return jacobian


def _bin2(image: np.ndarray) -> np.ndarray:
    height, width = (image.shape[0] // 2) * 2, (image.shape[1] // 2) * 2
    return (
        image[:height, :width]
        .astype(np.float64)
        .reshape(height // 2, 2, width // 2, 2)
        .mean(axis=(1, 3))
    )


def propose_ball_center(  # pylint: disable=too-many-locals
    image: np.ndarray, x: float, y: float, radius: float
) -> tuple[float, float] | None:
    """A whole-disk starting center, never a qualified center or size measurement.

    Fit linear shading over both ball halves and row structure sampled at the
    patch margins. Only the geometric center initializes a subsequent lit fit.
    """
    factor = max(1, math.ceil(radius / 10.0))
    if factor > 1:
        height, width = (image.shape[0] // factor) * factor, (image.shape[1] // factor) * factor
        image = (
            image[:height, :width]
            .reshape(height // factor, factor, width // factor, factor)
            .mean(axis=(1, 3))
        )
    x, y, radius = (x + 0.5) / factor - 0.5, (y + 0.5) / factor - 0.5, radius / factor
    reach = math.ceil(3.0 * radius) + 3
    top, left = max(0, int(y) - reach), max(0, int(x) - reach)
    patch = image[top : int(y) + reach + 1, left : int(x) + reach + 1].astype(float)
    if min(patch.shape) < 8 or np.ptp(patch) == 0.0:
        return None
    yy, xx = np.indices(patch.shape, dtype=float)
    margin = max(3, round(0.1 * patch.shape[1]))
    rows = ndimage.gaussian_filter1d(
        np.median(np.hstack([patch[:, :margin], patch[:, -margin:]]), axis=1), 0.7
    )
    rows = rows[:, None] - np.median(rows)
    xx, yy = xx + left - x, yy + top - y
    target = patch.ravel()

    def residual(params):
        dx, dy = xx - params[0], yy - params[1]
        r = params[2]
        nz = np.sqrt(np.clip(1.0 - (dx * dx + dy * dy) / (r * r), 0.0, None))
        cover = np.clip(r + 0.5 - np.hypot(dx, dy), 0.0, 1.0)
        basis = np.stack(
            [
                cover,
                cover * dx / r,
                cover * dy / r,
                cover * nz,
                1.0 - cover,
                (1.0 - cover) * xx,
                (1.0 - cover) * yy,
                (1.0 - cover) * np.broadcast_to(rows, xx.shape),
            ],
            axis=-1,
        )
        basis = ndimage.gaussian_filter(basis, (0.7, 0.7, 0.0)).reshape(-1, 8)
        coefficients = np.linalg.lstsq(basis, target, rcond=None)[0]
        return basis @ coefficients - target

    try:
        result = optimize.least_squares(
            residual,
            [0.0, 0.0, radius],
            bounds=([-radius, -radius, 0.5 * radius], [radius, radius, 2.0 * radius]),
            max_nfev=100,
        )
    except (ValueError, np.linalg.LinAlgError):
        return None
    dx, dy, _radius = result.x
    if not result.success or math.hypot(dx, dy) > radius:
        return None
    return float((x + dx + 0.5) * factor - 0.5), float((y + dy + 0.5) * factor - 0.5)


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
    check_other_starts: bool = False,
) -> LitBall | None:
    """Fit a lit sphere near (x, y); ``expected_radius`` holds its size to ``size_tolerance``.

    ``size_starts`` are the free size's starting fractions of ``radius``; ``extra_light_starts``
    False starts the light only from its measured lean, for a centre already placed.
    A free size, and its centre, are then measured on the ball's outline (``measure_rim``).
    ``check_other_starts`` checks already-computed starts if the cheapest fails the gates.
    """
    options = dict(
        noise_dn=noise_dn,
        expected_radius=expected_radius,
        size_tolerance=size_tolerance,
        size_starts=size_starts,
        extra_light_starts=extra_light_starts,
        check_other_starts=check_other_starts,
    )
    fit = _fit_sphere(image, x, y, radius, **options)
    if fit is not None and BATCHED_JACOBIAN and expected_radius is None and not check_other_starts:
        fit = _fit_sphere(image, x, y, radius, **options, reference_jacobian=True)
    if fit is None or expected_radius:
        return fit
    return measure_rim(image, fit, seed_center=(x, y))


def measure_rim(
    image: np.ndarray, fit: LitBall, *, seed_center: tuple[float, float] | None = None
) -> LitBall:
    """``fit`` with its centre and size from the circle its outline follows, at full resolution.

    The outline is sought all round but the bottom, never in the shaded underside or the
    shadow under it. If shading pulls the fit off the outline, retry around the original
    seed with the same circle gates. Without a measured circle the sphere stands.
    """
    radius = fit.radius_px
    reach = int(math.ceil((RIM_SIZE_REACH[1] + 0.2) * radius)) + 3
    height, width = image.shape
    centers = [(fit.x, fit.y)]
    if seed_center is not None and seed_center != centers[0]:
        centers.append(seed_center)
    for index, (x, y) in enumerate(centers):
        top, left = max(0, int(y) - reach), max(0, int(x) - reach)
        bottom, right = min(height, int(y) + reach + 1), min(width, int(x) + reach + 1)
        edges, ray_count = _rim_edges(
            image[top:bottom, left:right].astype(np.float64), fit, x - left, y - top
        )
        circle = _outline_circle(edges, ray_count, x - left, y - top, radius)
        if circle is None:
            continue
        if index == 0 and abs(circle[2] / radius - 1.0) <= RIM_SIZE_AGREES_FRACTION:
            return fit
        return replace(fit, x=circle[0] + left, y=circle[1] + top, radius_px=circle[2])
    return fit


def _rim_edges(patch: np.ndarray, fit: LitBall, x: float, y: float) -> tuple[np.ndarray, int]:
    """Every step out of the ball on the rays about (x, y): rows of (ray, x, y, step), and
    how many rays there are. A step is a fall where the sphere's rim is brighter than the
    ground and a rise where it is darker, in DN per pixel."""
    radius = fit.radius_px
    half = math.radians(180.0 - RIM_SIZE_SHADOW_HALF_ARC_DEG)
    angles = -math.pi / 2.0 + np.arange(-half, half + 1e-9, math.radians(RIM_SIZE_RAY_STEP_DEG))
    tilt, azimuth = math.radians(fit.light_tilt_deg), math.radians(fit.light_azimuth_deg)
    # the sphere just inside its rim (0.9 of the radius out) against the ground
    lit = np.clip(
        0.9 * math.sin(tilt) * np.cos(angles - azimuth) + 0.44 * math.cos(tilt), 0.0, None
    )
    smooth = ndimage.gaussian_filter(patch, EDGE_BLUR_PX)
    steps = np.arange(RIM_SIZE_REACH[0] * radius, RIM_SIZE_REACH[1] * radius, 0.25)
    xs = x + steps[None, :] * np.cos(angles)[:, None]
    ys = y + steps[None, :] * np.sin(angles)[:, None]
    values = ndimage.map_coordinates(
        smooth, [ys.ravel(), xs.ravel()], order=1, mode="nearest"
    ).reshape(xs.shape)
    # the ground each ray runs out onto, past the rim of a sphere grown into the shade
    beyond = np.median(values[:, steps >= RIM_SIZE_GROUND_FROM * radius], axis=1)
    least = RIM_SIZE_MIN_STEP_FRACTION * fit.diffuse_dn
    lit_rim = np.minimum(fit.shade_dn + fit.diffuse_dn * lit, 255.0) - beyond > least
    # where the rim is in shade a fall may be the terminator: only a rise down to the
    # shade (a sunlit ball's far side) says where the outline is
    lit_well = fit.diffuse_dn * lit >= least
    shaded_rim = ~lit_well & (fit.shade_dn < beyond - least)
    sign = np.where(lit_rim & lit_well, 1.0, np.where(shaded_rim, -1.0, 0.0))
    out = -np.gradient(values, 0.25, axis=1) * sign[:, None]
    before, here, after = out[:, :-2], out[:, 1:-1], out[:, 2:]
    floor = RIM_SIZE_MIN_EDGE_FRACTION * fit.diffuse_dn
    rays, index = np.nonzero((here >= floor) & (here >= before) & (here > after))
    left, peak, right = before[rays, index], here[rays, index], after[rays, index]
    curvature = left - 2.0 * peak + right
    with np.errstate(divide="ignore", invalid="ignore"):
        offset = np.where(curvature < 0.0, 0.5 * (left - right) / curvature, 0.0)
    distance = steps[index + 1] + 0.25 * offset
    turn = angles[rays]
    edges = np.column_stack([rays, x + distance * np.cos(turn), y + distance * np.sin(turn), peak])
    return edges.astype(np.float64), len(angles)


def _circle_through(points: np.ndarray) -> tuple[float, float, float]:
    """The algebraic least-squares circle through the points."""
    a = np.column_stack([points[:, 0], points[:, 1], np.ones(len(points))])
    b = -(points[:, 0] ** 2 + points[:, 1] ** 2)
    d, e, f = np.linalg.lstsq(a, b, rcond=None)[0]
    cx, cy = -d / 2.0, -e / 2.0
    return float(cx), float(cy), math.sqrt(max(cx * cx + cy * cy - f, 0.0))


def _rays_agreeing(edges: np.ndarray, ray_count: int, circle, tolerance: float) -> np.ndarray:
    """Each ray's strongest step on the circle, as an index into ``edges``; -1 for none."""
    cx, cy, r = circle
    near = np.abs(np.hypot(edges[:, 1] - cx, edges[:, 2] - cy) - r) <= tolerance
    chosen = np.full(ray_count, -1)
    strength = np.zeros(ray_count)
    for index in np.flatnonzero(near):
        ray = int(edges[index, 0])
        if edges[index, 3] > strength[ray]:
            strength[ray], chosen[ray] = edges[index, 3], index
    return chosen


def _outline_circle(
    edges: np.ndarray, ray_count: int, x: float, y: float, radius: float
) -> tuple[float, float, float] | None:
    """The circle of about ``radius`` near (x, y) the most rays have a step on; None when
    too few do.

    A ray can step more than once (a logo, the lit cap's own edge, a bright patch of net or
    a mat edge behind the ball): circles through steps on three rays, one from each third of
    the rays, are tried, and the one most rays agree with is refitted through them.
    """
    if len(edges) < 3:
        return None
    rays = edges[:, 0].astype(int)
    thirds = [
        np.flatnonzero((rays * 3 >= part * ray_count) & (rays * 3 < (part + 1) * ray_count))
        for part in range(3)
    ]
    if any(part.size == 0 for part in thirds):
        return None
    tolerance = max(RIM_SIZE_TOLERANCE_PX, RIM_SIZE_TOLERANCE_FRACTION * radius)
    low, high = RIM_SIZE_RADIUS_FRACTION

    def plausible(circle) -> bool:
        cx, cy, r = circle
        return (
            low * radius <= r <= high * radius
            and math.hypot(cx - x, cy - y) <= (1.0 - low) * radius
        )

    rng = np.random.default_rng(0)  # the same picture always gives the same circle
    best, best_score = None, -1.0
    for _trial in range(RIM_SIZE_TRIALS):
        picked = [int(rng.choice(part)) for part in thirds]
        try:
            circle = _circle_through(edges[picked, 1:3])
        except np.linalg.LinAlgError:
            continue
        if not plausible(circle):
            continue
        agreeing = _rays_agreeing(edges, ray_count, circle, tolerance)
        agreeing = agreeing[agreeing >= 0]
        # the most rays; between as many, the stronger steps
        score = len(agreeing) + 1e-3 * float(np.sqrt(edges[agreeing, 3]).sum())
        if score > best_score:
            best, best_score = circle, score
    if best is None:
        return None
    for _round in range(3):
        agreeing = _rays_agreeing(edges, ray_count, best, tolerance)
        agreeing = agreeing[agreeing >= 0]
        if len(agreeing) < RIM_SIZE_MIN_RAYS:
            return None
        best = _circle_through(edges[agreeing, 1:3])
    return best if plausible(best) else None


def _fit_sphere(  # pylint: disable=too-many-arguments,too-many-locals,too-many-return-statements
    image: np.ndarray,
    x: float,
    y: float,
    radius: float,
    *,
    noise_dn: float,
    expected_radius: float | None,
    size_tolerance: float,
    size_starts: Sequence[float] | None,
    extra_light_starts: bool,
    check_other_starts: bool = False,
    reference_jacobian: bool = False,
) -> LitBall | None:
    """The lit sphere's own fit: centre, size and light, and the look-alike gates."""
    binning = 2 if max(radius, expected_radius or 0.0) > 12.0 else 1
    if binning > 1:
        fit = _fit_sphere(
            _bin2(image),
            (x + 0.5) / 2 - 0.5,
            (y + 0.5) / 2 - 0.5,
            radius / 2,
            noise_dn=noise_dn / 2,
            expected_radius=expected_radius / 2 if expected_radius else None,
            size_tolerance=size_tolerance,
            size_starts=size_starts,
            extra_light_starts=extra_light_starts,
            check_other_starts=check_other_starts,
            reference_jacobian=reference_jacobian,
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
    solutions = []
    jacobian_factory = (
        _batched_jacobian
        if check_other_starts or reference_jacobian or expected_radius is not None
        else _analytic_jacobian
    )
    screen = BATCHED_JACOBIAN and jacobian_factory is _analytic_jacobian and size_starts is None
    sizes = (1.0,) if expected_radius or screen else tuple(size_starts or (0.85, 1.0, 1.2))
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
                    jacobian_factory(yy, xx, patch, use, noise_dn, lower, upper)
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
        if check_other_starts:
            solutions.append(result)
        if best is None or result.cost < best.cost:
            best = result
    if best is None:
        return None

    def qualified(solution):
        cx, cy, r, azimuth, tilt, shade, diffuse, ground, _gx, _gy = solution.x
        misfit = np.abs(_render(solution.x, yy, xx) - patch)
        misfit_dn = float(np.median(misfit[use & (np.hypot(xx - cx, yy - cy) <= 1.3 * r)]) * 1.4826)
        # no model beats the pixels' own noise: clipped white fits any bright sphere otherwise
        misfit_dn = max(misfit_dn, noise_dn)
        if (
            diffuse < MIN_DIFFUSE_NOISE_MULTIPLE * noise_dn
            or misfit_dn > MAX_MISFIT_FRACTION * diffuse
        ):
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

    for solution in sorted(solutions if check_other_starts else [best], key=lambda item: item.cost):
        fit = qualified(solution)
        if fit is not None:
            return fit
    return None


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

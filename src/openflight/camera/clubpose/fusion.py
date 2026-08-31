"""Radar range and radar+camera clubhead velocity, and what they constrain.

Two things the silhouette fit used to spend free parameters on are already
measured by the sensors, and this module supplies both.

RANGE. Every earlier fit rendered the clubhead at a constant 1581 mm, the
camera-to-ball distance. The radar shows the club traversing roughly 529 mm
through that value across the fitted frames, and constant range is rejected at
p ~ 0.04 on mask area alone. `ranges_from_radar` walks the range back from the
taped ball position at impact using the radar's own range rate, so per-frame
depth costs NO free parameter.

VELOCITY, and through it the ROTATION AXIS. For a rigid body ``v = omega x r``,
so the angular velocity is PERPENDICULAR to the linear velocity -- and the
linear velocity is measurable without the silhouette::

    dP/dt = (dr/dt) * ray  +  r * d(ray)/dt
            \\__radar__/       \\___camera___/

The radar owns the radial term through its range rate; the camera owns the two
transverse terms through the clubhead centroid's motion across frames. Neither
instrument can supply the other's part: the IWR6843 has a ~277 mm cross-range
cell at this distance, and the camera has no depth at all. That division of
labour leaves the rotation axis with ONE degree of freedom -- its phase around
v -- instead of two.

The velocity half is the part that VALIDATES. Fused ``|v|`` matches the OPS243's
independent club speed to a mean ratio of 0.97-1.00, and the OPS243 takes no
part in the computation, so it is a genuine cross-sensor check. The rotation
half does not validate: fixing the axis perpendicular to v is a physical
constraint, not a measurement of which perpendicular it is, and no orientation
figure here has been scored against truth.

Ported from the fork's `test_fused_refit.py` experiment, with everything that
depended on session paths, masks or plotting removed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from openflight.camera.clubpose.motion import rotation_from_omega_deg_s
from openflight.camera.clubpose.projection import CAMERA_BALL_RANGE_MM

__all__ = [
    "RadarRanges",
    "axis_basis",
    "clubhead_velocity_world",
    "omega_from_phase",
    "ranges_from_radar",
    "ranges_from_track",
    "rotation_from_omega_deg_s",
]

MM_PER_M = 1000.0


@dataclass(frozen=True)
class RadarRanges:
    """Per-frame camera-to-clubhead range, AND the model that produced it.

    A straight-line range walk is a model, not a measurement: it is the range
    RATE walked out under an assumption that the rate is constant. On a
    clubhead that assumption is wrong by tens of millimetres over one capture
    (`tests/test_iwr6843_club_quadratic_range`). `fit_sequence` calls what it
    is handed a measurement and pins depth to it, so which model it got has to
    travel with the numbers rather than be inferred from the call site.
    """

    ranges_mm: np.ndarray
    model: str
    range_rate_ms: float
    range_accel_ms2: float


def ranges_from_radar(
    elapsed_s,
    impact_elapsed_s: float,
    range_rate_ms: float,
    ball_range_mm: float = CAMERA_BALL_RANGE_MM,
    *,
    range_accel_ms2: float = 0.0,
) -> RadarRanges:
    """Per-frame camera-to-clubhead range, from the radar's own range rate.

    The clubhead is at the ball at impact, and the ball's range is taped, so
    that instant is a measured anchor rather than a fitted one. Every other
    frame is the anchor walked back along the radar's range rate.

    Args:
        elapsed_s: Frame times, in seconds, on any common origin.
        impact_elapsed_s: Time of impact on that same origin.
        range_rate_ms: Radar range rate in metres per second
            (`iwr_club_path_range_rate_ms`), positive when the range is
            OPENING. The camera sits behind the ball, so a club swinging
            downrange into the ball is receding: it is nearer the camera than
            the ball before impact, and reaches the ball's range at contact.
            With a non-zero ``range_accel_ms2`` this is the rate AT IMPACT.
        ball_range_mm: Camera-to-ball range at impact. Defaults to the measured
            1581 mm tape chain.
        range_accel_ms2: Radial acceleration, in m/s^2, if one was measured --
            `iwr6843.tracking.BallTrack.quad_bins`, via `ranges_from_track`.
            Zero, the default, is the straight-line walk, and the result says
            so in ``model``.

    Returns:
        A `RadarRanges` whose ``ranges_mm`` holds one range in millimetres per
        entry of ``elapsed_s``. The entry at the impact anchor is exactly
        ``ball_range_mm``.

    Raises:
        ValueError: If any input is not finite.
    """
    times = np.asarray(elapsed_s, dtype=float)
    impact = float(impact_elapsed_s)
    rate = float(range_rate_ms)
    accel = float(range_accel_ms2)
    anchor = float(ball_range_mm)
    if not np.all(np.isfinite(times)):
        raise ValueError("elapsed_s must be finite")
    if not all(math.isfinite(value) for value in (impact, rate, accel, anchor)):
        raise ValueError("impact time, range rate, acceleration and ball range must be finite")
    if anchor <= 0.0:
        raise ValueError("ball range must be positive")
    delta = times - impact
    walk = rate * delta + 0.5 * accel * delta**2
    return RadarRanges(
        ranges_mm=anchor + MM_PER_M * walk,
        model="linear" if accel == 0.0 else "quadratic",
        range_rate_ms=rate,
        range_accel_ms2=accel,
    )


def ranges_from_track(
    track,
    elapsed_s,
    impact_elapsed_s: float,
    range_res_m: float,
    ball_range_mm: float = CAMERA_BALL_RANGE_MM,
) -> RadarRanges:
    """`ranges_from_radar` fed from a fitted radar track, quadratic if it has one.

    ``track`` is duck-typed on `iwr6843.tracking.BallTrack`: it needs
    ``speed_ms`` and ``quad_bins``. When the quadratic refit survived its
    sanity bound the rate AT IMPACT and the radial acceleration are read off
    it, and the result says ``model == "quadratic"``; otherwise the track
    average is walked out straight and it says ``"linear"``. Either way the
    range at the impact anchor is exactly ``ball_range_mm``.
    """
    impact = float(impact_elapsed_s)
    quad = getattr(track, "quad_bins", None)
    if quad is None:
        return ranges_from_radar(elapsed_s, impact, float(track.speed_ms), ball_range_mm)
    q2, q1, _q0 = (float(value) for value in quad)
    res = float(range_res_m)
    return ranges_from_radar(
        elapsed_s,
        impact,
        (2.0 * q2 * impact + q1) * res,
        ball_range_mm,
        range_accel_ms2=2.0 * q2 * res,
    )


def clubhead_velocity_world(rays, ranges_mm, elapsed_s, range_rate_ms: float) -> np.ndarray:
    """Clubhead velocity in world mm/s, fusing the radar's range rate with the camera's rays.

    ``dP/dt = (dr/dt) * ray + r * d(ray)/dt``. The direction derivative is taken
    by least squares per component rather than by finite difference, so a single
    noisy centroid cannot swing the recovered direction. The result is evaluated
    at the middle sample, which is where a straight-line fit is most accurate.

    Args:
        rays: ``[N,3]`` unit rays from the camera centre through the clubhead
            centroid, one per frame, ordered in time.
        ranges_mm: ``[N]`` camera-to-clubhead ranges, e.g. from
            `ranges_from_radar`.
        elapsed_s: ``[N]`` frame times in seconds.
        range_rate_ms: Radar range rate in metres per second.

    Returns:
        A three-vector in world millimetres per second.

    Raises:
        ValueError: If fewer than two frames are supplied, if the shapes
            disagree, if any value is not finite, or if the frames do not span
            any time.
    """
    directions = np.asarray(rays, dtype=float)
    ranges = np.asarray(ranges_mm, dtype=float).reshape(-1)
    times = np.asarray(elapsed_s, dtype=float).reshape(-1)
    rate = float(range_rate_ms)
    if directions.ndim != 2 or directions.shape[1] != 3:
        raise ValueError("rays must have shape [N,3]")
    if len(times) < 2:
        raise ValueError("clubhead velocity needs at least two frames")
    if not len(directions) == len(ranges) == len(times):
        raise ValueError("rays, ranges and times must describe the same frames")
    if not (
        np.all(np.isfinite(directions))
        and np.all(np.isfinite(ranges))
        and np.all(np.isfinite(times))
        and math.isfinite(rate)
    ):
        raise ValueError("rays, ranges, times and range rate must be finite")
    if float(np.ptp(times)) <= 0.0:
        raise ValueError("frames must span a non-zero time")
    slope = np.array([np.polyfit(times, directions[:, axis], 1)[0] for axis in range(3)])
    mid_ray = directions[len(directions) // 2]
    return rate * MM_PER_M * mid_ray + float(np.mean(ranges)) * slope


def axis_basis(velocity_world) -> tuple[np.ndarray, np.ndarray]:
    """Two orthonormal vectors spanning the plane perpendicular to a velocity.

    For a rigid body ``v = omega x r``, so the angular velocity lies in this
    plane. Together with a magnitude from ``omega = v / r`` that reduces the
    rotation axis to a single phase angle -- see `omega_from_phase`.

    Args:
        velocity_world: A three-vector; only its direction is used.

    Returns:
        ``(e1, e2)``, orthonormal and both perpendicular to the velocity, with
        ``e1 x e2`` along it.

    Raises:
        ValueError: If the velocity is not a finite, non-degenerate three-vector.
    """
    velocity = np.asarray(velocity_world, dtype=float).reshape(-1)
    if velocity.shape != (3,) or not np.all(np.isfinite(velocity)):
        raise ValueError("velocity_world must be a finite three-vector")
    norm = float(np.linalg.norm(velocity))
    if norm < 1e-9:
        raise ValueError("velocity_world is degenerate: no perpendicular plane is defined")
    unit = velocity / norm
    # Any seed not parallel to v works; switch seeds near the pole so the cross
    # product never collapses.
    seed = np.array([0.0, 0.0, 1.0]) if abs(unit[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
    first = np.cross(unit, seed)
    first /= np.linalg.norm(first)
    return first, np.cross(unit, first)


def omega_from_phase(basis, phase_deg: float, magnitude_deg_s: float) -> np.ndarray:
    """Angular velocity of a fixed magnitude, at one phase around the velocity.

    Args:
        basis: The ``(e1, e2)`` pair from `axis_basis`.
        phase_deg: Where in the perpendicular plane the axis points. This is the
            one rotation parameter the sensors do NOT determine.
        magnitude_deg_s: Angular speed in degrees per second, normally
            ``degrees(v / r)`` from the radar's club speed and the swing radius.

    Returns:
        A three-vector in degrees per second, perpendicular to the velocity.

    Raises:
        ValueError: If the basis or the scalars are not finite.
    """
    first, second = (np.asarray(vector, dtype=float).reshape(-1) for vector in basis)
    if first.shape != (3,) or second.shape != (3,):
        raise ValueError("basis must be a pair of three-vectors")
    phase = float(phase_deg)
    magnitude = float(magnitude_deg_s)
    if not (math.isfinite(phase) and math.isfinite(magnitude)):
        raise ValueError("phase and magnitude must be finite")
    radians = math.radians(phase)
    return magnitude * (math.cos(radians) * first + math.sin(radians) * second)

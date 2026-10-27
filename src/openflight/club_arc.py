"""Club delivery from a circular arc fitted near impact (docs/how-it-works/club-arc.md).

Part E, Tier 2 (MASTER-PLAN D38, D39). Near impact the club head's path is
fitted as a **circular arc in a plane**, from the shot's own head positions:
camera rays intersected with the IWR club range sphere, each on the camera's
clock, plus the camera's contact time (``camera/club_delivery.py``). Every
output is read off that arc at the impact time.

Model
-----
The arc is a circle of radius ``R`` in a plane tilted ``theta`` from the
horizon. The plane's base line (its horizontal line) has bearing ``psi`` from
the target line. ``e1`` is the base line's unit vector, pointing downrange;
``e2`` is the in-plane unit vector perpendicular to it, rising toward the
golfer (the circle's centre side): ``e2 = cos(theta) h + sin(theta) z`` with
``h`` the horizontal unit vector toward the golfer, to the target-LEFT for a
right-handed golfer (the default) and to the target-RIGHT for an explicit
left-handed one. With ``L`` the arc's lowest point, the head at time ``t`` is

    P(t) = L + R sin(phi) e1 + R (1 - cos(phi)) e2,   phi = omega (t - t_L)

so the head moves at a constant angular speed ``omega`` through the low point
at ``t_L``. Eight parameters: theta, psi, R, L (3), omega, t_L. Before the low
point (phi < 0) the head moves down and away from the golfer, so a right-hander
hitting down on a square plane swings in-to-out: path ~ psi - atan(tan(phi)
cos(theta)), which is TrackMan's familiar "hitting down moves the path right".

Why a circle near impact: high-speed (2,000 fps) measurements fit the head's
path near impact with a circle to about 1 mm RMS (Krasna et al. 2019, Table 3),
and the head's motion from mid-downswing to mid-follow-through is planar to
0.3 cm RMS (Kwon et al. 2012, Table III).

Labels (D39)
------------
- **calculated**: the shot's own points fix the arc (``CONDITIONING`` below).
  The fit uses no prior at all.
- **model geometry**: fewer than 3 positions, or a fit the points do not fix.
  The radius and plane angle are then constrained by a **published per-club
  prior** (``prior_for_club``) in a maximum a posteriori fit; every other
  parameter still comes from this shot. The prior's source is recorded.
- **not measured**: no or one head position, no impact time, positions far from
  impact, or no prior for an unknown club when the points alone do not fix the
  arc. Nothing is filled in.

A model-geometry fit also reports, per output, the prior's share of its variance
(``prior_share``): in the linearized fit the output's variance splits into a part
from the positions and a part from the two prior rows, and the share is the second
over the sum (1: the prior alone sets it; 0: the positions alone do). It also
reports how far each output moves per degree of the prior's plane and per 0.1 m of
its radius (``prior_sensitivity``): with two or three points the share of an output
can be small while it still moves with the assumed plane (a bias the range omits).

Positions carry their own label (25 Oct 2026). The camera head track's hosel points
(``camera/head_track.py``) are experimental: their depths are walked back from the
OPS club speed, and two or three of them cannot tilt the plane. On them every output
is experimental, except one whose ``prior_share`` is at least ``PRIOR_SET_SHARE``:
that one is the published prior's value and is labelled model geometry, with the
share in its note. The head track's own path and attack stay the shot's (the arc
reads the same points); the arc fills one only when the track withheld it.

The arc is never "measured": a measured tangent needs 4-6 frames from the new
lenses (Part E, Tier 2, "method later").

Published priors (D38: published sources first, refitted to TM4 in E8)
---------------------------------------------------------------------
- **Swing plane:** Kwon, Como, Singhal, Lee & Han (2012), "Assessment of
  planarity of the golf swing based on the functional swing plane of the
  clubhead and motion planes of the body points", *Sports Biomechanics*
  11(2):127-148, doi:10.1080/14763141.2012.660799, Table III: the clubhead's
  functional swing plane slope (mid-downswing to mid-follow-through, 14 skilled
  golfers, 250 Hz) is 47.2 +- 2.3 deg (driver), 55.8 +- 1.5 (5-iron) and
  59.1 +- 1.9 (pitching wedge). TrackMan's own guidance agrees for the driver
  ("typically between 45-50 deg"; support article "Parameters | Swing Plane").
- **Radius:** Krasna, Coh, Prebil & Mackala (2019), "Comparative analysis of
  golf clubhead motion at impact", *Facta Universitatis: Physical Education and
  Sport* 17(3):437-452, doi:10.22190/FUPES191205039K, Table 3: the radius of a
  circle fitted to the clubhead path near impact (3 scratch golfers, 2,000 fps).
  Mean +- SD over the trials: driver 1.077 +- 0.044 m (n = 8), 6-iron
  1.002 +- 0.014 m (n = 5), pitching wedge 0.993 +- 0.039 m (n = 5).
  Note the near-impact radius is about 1.0 m, well under arm-plus-club length:
  the hub is not fixed (Nesbit & McGinnis 2009, *J Sports Sci Med* 8:235-246).
- Other clubs are interpolated linearly in nominal loft
  (``clubs.physics``) between those anchors and clamped outside them (no
  extrapolation). That interpolation is our choice, not a published value.

Definitions (TrackMan's public pages, D37)
------------------------------------------
- Club path: "The in-to-out or out-to-in movement of the club head's geometric
  center at the time of maximum compression"; + right of the target line
  regardless of dexterity (https://www.trackman.com/blog/40-trackman-parameters,
  https://www.trackman.com/blog/club-path). Here: the azimuth of the arc's
  tangent at impact.
- Attack angle: "The up or down movement of the club head's geometric center at
  the time of maximum compression"; + up (same pages,
  https://www.trackman.com/blog/attack-angle). Here: the tangent's elevation.
- Swing plane: "The vertical angle between the plane created by the club head's
  geometric center movement and the horizon" (40-trackman-parameters). Here:
  theta.
- Swing direction: "The angle between the base of the plane created by the club
  head's geometric center movement and the target line" (40-trackman-parameters;
  https://www.trackman.com/blog/what-is-swing-direction: negative is "over the
  top" for a right-hander). Here: psi, + right of the target line.
- Low point: "Distance from the club head's geometric center to the lowest point
  on the swing arc at the time of maximum compression" (40-trackman-parameters);
  a negative attack angle puts it after impact
  (https://www.trackman.com/blog/low-point). TrackMan's support pages
  "Parameters | Low Point Height" and "| Low Point Side" (Tee to Green) give
  the vertical and the perpendicular-to-target-line horizontal distances from
  the head's position at impact to "where the swing would bottom out". Here:
  ``L - P(t_impact)`` in the target-line frame: distance along the target line
  (+ after the ball), height (+ above the head at impact), side (+ right).
  The reported low point distance adds a per-club offset for the 7-iron, driver
  and pitching wedge (``LOW_POINT_OFFSETS_MM``): the reported distance is the arc's
  geometry plus a constant per club, fitted by the maintainer to a TM4 normalized
  export, 6 May 2026 (``session_logs/OpenFlight-Test.Normalized.csv``; 9, 8 and 7 shots, 1.5 mm
  RMS after the offset). Other clubs have no fitted offset yet and report the arc's
  geometry alone, with a note.

Frame (D36): positions arrive in the unit-boresight frame; they are turned by
the one horizontal offset (``horizontal_frame``) before the fit, so every
horizontal output is in ``frame_for(offset)``: ``target_line`` with an offset,
``unit_boresight`` without one. Vertical outputs have no horizontal frame.

Limits: the head point is the median of the tracked head features in each
frame, not TrackMan's geometric centre (W9); constant angular speed over the
last few frames; pre-impact frames only.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Mapping, Optional, Sequence

import numpy as np
from scipy.optimize import least_squares

from openflight.camera.club_delivery import HeadPosition
from openflight.camera.head_track import SOURCE as HEAD_TRACK_SOURCE
from openflight.clubs import ClubType
from openflight.clubs.physics import get_club_physics
from openflight.horizontal_frame import frame_for
from openflight.shot_metrics import (
    CALCULATED,
    ESTIMATED,
    EXPERIMENTAL,
    MEASURED,
    MODEL_GEOMETRY,
    NOT_MEASURED,
)

LABEL_RANK = {
    MEASURED: 5,
    CALCULATED: 4,
    MODEL_GEOMETRY: 3,
    ESTIMATED: 2,
    EXPERIMENTAL: 1,
    NOT_MEASURED: 0,
}
# An output whose variance is at least this share the prior's is the prior's value.
PRIOR_SET_SHARE = 0.9

RIGHT_HANDED = "RH"
LEFT_HANDED = "LH"

KWON_2012 = (
    "Kwon, Como, Singhal, Lee & Han 2012, Sports Biomechanics 11(2):127-148, "
    "doi:10.1080/14763141.2012.660799, Table III (functional swing plane slope)"
)
KRASNA_2019 = (
    "Krasna, Coh, Prebil & Mackala 2019, Facta Universitatis PE&S 17(3):437-452, "
    "doi:10.22190/FUPES191205039K, Table 3 (fitted near-impact circle radius)"
)
# (club, mean, SD) deg from KWON_2012; the SD is the between-golfer spread
PLANE_ANCHORS_DEG = (
    (ClubType.DRIVER, 47.2, 2.3),
    (ClubType.IRON_5, 55.8, 1.5),
    (ClubType.PW, 59.1, 1.9),
)
# (club, mean, SD) m from KRASNA_2019's rC, over each club's trials
RADIUS_ANCHORS_M = (
    (ClubType.DRIVER, 1.077, 0.044),
    (ClubType.IRON_6, 1.002, 0.014),
    (ClubType.PW, 0.993, 0.039),
)

# Assumed 3-D head-position noise floor: ~1 px across the head at 2 m with the stock lens
# in 640x400 (~4 mm), rounded up.
POSITION_NOISE_FLOOR_M = 0.005
# "calculated" needs every 1-sigma, data-only uncertainty within these, the fit's
# RMS residual within RESIDUAL_MAX_NOISE noise floors, at least 3 positions, and
# impact no more than IMPACT_EXTRAPOLATION_MAX_FRAMES frame intervals past the last.
CONDITIONING = {
    "club_path_deg": 0.5,
    "attack_angle_deg": 0.5,
    "swing_plane_deg": 2.0,
    "swing_direction_deg": 2.0,
    "low_point_distance_mm": 25.0,
    "low_point_height_mm": 25.0,
    "low_point_side_mm": 25.0,
    "radius_m": 0.10,
}
RESIDUAL_MAX_NOISE = 3.0
IMPACT_EXTRAPOLATION_MAX_FRAMES = 2.0
MIN_CALCULATED_POSITIONS = 3
# Positions farther than this from impact cannot carry an arc to it at all.
MAX_IMPACT_GAP_S = 0.010
# A range is the value +- this many standard deviations (about 95 %).
RANGE_SIGMAS = 2.0

_OUTPUTS = (
    "club_path_deg",
    "attack_angle_deg",
    "swing_plane_deg",
    "swing_direction_deg",
    "low_point_distance_mm",
    "low_point_height_mm",
    "low_point_side_mm",
)
_N_PARAMS = 8
_SINGULAR_RATIO_MIN = 1e-10
# The arc: P(t) = L + R sin(phi) e1 + R (1 - cos(phi)) e2, phi = omega (t - t_L); e1 the
# plane's horizontal base line (bearing psi from the target line, downrange), and
# e2 = cos(theta) h + sin(theta) z with h horizontal toward the golfer (target-left for RH).
# Parameter bounds: theta, psi, R, L (3), omega, t_L
_LOWER = np.array(
    [math.radians(5.0), math.radians(-80.0), 0.3, -np.inf, -np.inf, -np.inf, 1.0, -np.inf]
)
_UPPER = np.array(
    [math.radians(85.0), math.radians(80.0), 3.0, np.inf, np.inf, np.inf, 300.0, np.inf]
)
_DEFAULT_PLANE_DEG = 55.0
_DEFAULT_RADIUS_M = 1.0


@dataclass(frozen=True)
class ArcPrior:
    """A published per-club radius and swing-plane prior."""

    club: str
    plane_deg: float
    plane_sd_deg: float
    radius_m: float
    radius_sd_m: float
    source: str
    interpolated: bool


@dataclass(frozen=True)
class ClubArc:
    """The arc's outputs at impact, with their label, range and the fit's facts."""

    status: str
    reason: Optional[str] = None
    club_path_deg: Optional[float] = None
    attack_angle_deg: Optional[float] = None
    swing_plane_deg: Optional[float] = None
    swing_direction_deg: Optional[float] = None
    low_point_distance_mm: Optional[float] = None
    low_point_height_mm: Optional[float] = None
    low_point_side_mm: Optional[float] = None
    # value +- RANGE_SIGMAS standard deviations, per output
    ranges: dict = field(default_factory=dict)
    frame: Optional[str] = None
    handedness: str = RIGHT_HANDED
    n_positions: int = 0
    residual_rms_mm: Optional[float] = None
    radius_m: Optional[float] = None
    head_speed_mph: Optional[float] = None
    # the prior used (model geometry only), with its source
    prior: Optional[dict] = None
    conditioning: dict = field(default_factory=dict)
    # the low point from the resting ball (lateral/up/forward given), when known
    low_point_from_ball_mm: Optional[dict] = None
    # model geometry only: each output's share of its variance from the prior, 0-1
    prior_share: dict = field(default_factory=dict)
    # model geometry only: each output's move per degree of the prior's plane and per
    # 0.1 m of its radius
    prior_sensitivity: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["ranges"] = {name: list(bounds) for name, bounds in self.ranges.items()}
        return data


def _loft(club: ClubType) -> float:
    return float(get_club_physics(club).nominal_loft_deg)


def _interpolate(anchors, loft: float) -> tuple[float, float]:
    lofts = [_loft(club) for club, _mean, _sd in anchors]
    means = [mean for _club, mean, _sd in anchors]
    sds = [sd for _club, _mean, sd in anchors]
    return float(np.interp(loft, lofts, means)), float(np.interp(loft, lofts, sds))


def prior_for_club(club: Any) -> Optional[ArcPrior]:
    """The published radius and swing-plane prior for ``club``; None when unknown."""
    if not isinstance(club, ClubType) or club == ClubType.UNKNOWN:
        return None
    loft = _loft(club)
    plane, plane_sd = _interpolate(PLANE_ANCHORS_DEG, loft)
    radius, radius_sd = _interpolate(RADIUS_ANCHORS_M, loft)
    anchors = {anchor for anchor, _mean, _sd in (*PLANE_ANCHORS_DEG, *RADIUS_ANCHORS_M)}
    return ArcPrior(
        club=club.value,
        plane_deg=plane,
        plane_sd_deg=plane_sd,
        radius_m=radius,
        radius_sd_m=radius_sd,
        source=f"plane: {KWON_2012}; radius: {KRASNA_2019}; "
        "other clubs interpolated in nominal loft between the anchors, clamped",
        interpolated=club not in anchors,
    )


def _side(handedness: str) -> float:
    """+1 when the golfer stands target-left of the ball (right-handed)."""
    return -1.0 if handedness == LEFT_HANDED else 1.0


def _basis(theta: float, psi: float, side: float) -> tuple[np.ndarray, np.ndarray]:
    e1 = np.array([math.cos(psi), math.sin(psi), 0.0])
    toward_golfer = side * np.array([math.sin(psi), -math.cos(psi), 0.0])
    e2 = math.cos(theta) * toward_golfer + np.array([0.0, 0.0, math.sin(theta)])
    return e1, e2


def _positions_at(params: np.ndarray, times: np.ndarray, side: float) -> np.ndarray:
    theta, psi, radius = params[0], params[1], params[2]
    low = params[3:6]
    omega, t_low = params[6], params[7]
    e1, e2 = _basis(theta, psi, side)
    phi = omega * (np.asarray(times, dtype=float) - t_low)
    return (
        low[None, :]
        + radius * np.sin(phi)[:, None] * e1[None, :]
        + radius * (1.0 - np.cos(phi))[:, None] * e2[None, :]
    )


def _outputs(params: np.ndarray, impact_t_s: float, side: float) -> np.ndarray:
    """The seven outputs at impact, in the fit's (reported) frame.

    The low point is L - P(t_impact): forward (+ after the ball), up, and side (+ right).
    """
    theta, psi = params[0], params[1]
    omega, t_low = params[6], params[7]
    e1, e2 = _basis(theta, psi, side)
    phi = omega * (impact_t_s - t_low)
    direction = math.cos(phi) * e1 + math.sin(phi) * e2
    at_impact = _positions_at(params, np.array([impact_t_s]), side)[0]
    low = params[3:6] - at_impact
    return np.array(
        [
            math.degrees(math.atan2(direction[1], direction[0])),
            math.degrees(math.atan2(direction[2], math.hypot(direction[0], direction[1]))),
            math.degrees(theta),
            math.degrees(psi),
            low[0] * 1000.0,
            low[2] * 1000.0,
            low[1] * 1000.0,
        ]
    )


def _numeric_jacobian(function, params: np.ndarray) -> np.ndarray:
    base = np.asarray(function(params), dtype=float)
    jacobian = np.empty((base.size, params.size))
    for index in range(params.size):
        step = 1e-6 * max(1.0, abs(params[index]))
        upper = params.copy()
        lower = params.copy()
        upper[index] += step
        lower[index] -= step
        jacobian[:, index] = (
            np.asarray(function(upper), dtype=float) - np.asarray(function(lower), dtype=float)
        ) / (2.0 * step)
    return jacobian


def _covariance(jacobian: np.ndarray) -> Optional[np.ndarray]:
    """(J^T J)^-1, or None when the parameters are not all determined."""
    if jacobian.shape[0] < jacobian.shape[1]:
        return None
    _u, singular, vt = np.linalg.svd(jacobian, full_matrices=False)
    if singular[-1] <= _SINGULAR_RATIO_MIN * singular[0]:
        return None
    return (vt.T / singular**2) @ vt


def _initial_params(
    xyz: np.ndarray, times: np.ndarray, side: float, plane_deg: float, radius_m: float
) -> np.ndarray:
    """A starting arc through the first and last positions at the given plane and radius."""
    chord = xyz[-1] - xyz[0]
    elapsed = float(times[-1] - times[0])
    horizontal = math.hypot(chord[0], chord[1])
    path = math.atan2(chord[1], chord[0])
    attack = math.atan2(chord[2], horizontal)
    theta = math.radians(plane_deg)
    omega = max(float(np.linalg.norm(chord)) / elapsed / radius_m, 1.0)
    phi_mid = math.atan(math.tan(attack) / math.sin(theta))
    psi = path + side * math.atan(math.tan(phi_mid) * math.cos(theta))
    t_mid = 0.5 * float(times[0] + times[-1])
    e1, e2 = _basis(theta, psi, side)
    mid = 0.5 * (xyz[0] + xyz[-1])
    low = mid - radius_m * math.sin(phi_mid) * e1 - radius_m * (1.0 - math.cos(phi_mid)) * e2
    params = np.array([theta, psi, radius_m, *low, omega, t_mid - phi_mid / omega])
    return np.clip(params, _LOWER + 1e-9, _UPPER - 1e-9)


def _fit(xyz, times, side, start, prior: Optional[ArcPrior], noise_m: float):
    """Least squares on the positions (in units of the noise), plus the prior's two rows."""

    def residuals(params):
        rows = ((_positions_at(params, times, side) - xyz) / noise_m).ravel()
        if prior is None:
            return rows
        return np.concatenate(
            [
                rows,
                [
                    (params[0] - math.radians(prior.plane_deg)) / math.radians(prior.plane_sd_deg),
                    (params[2] - prior.radius_m) / prior.radius_sd_m,
                ],
            ]
        )

    result = least_squares(residuals, start, bounds=(_LOWER, _UPPER), x_scale="jac", max_nfev=2000)
    return result.x, residuals


def _turn(xyz_fwd_lat_up: np.ndarray, offset_deg: float) -> np.ndarray:
    """Rotate (forward, lateral, up) points about the vertical by the horizontal offset."""
    angle = math.radians(offset_deg or 0.0)
    forward, lateral = xyz_fwd_lat_up[:, 0], xyz_fwd_lat_up[:, 1]
    turned = xyz_fwd_lat_up.copy()
    turned[:, 0] = forward * math.cos(angle) - lateral * math.sin(angle)
    turned[:, 1] = forward * math.sin(angle) + lateral * math.cos(angle)
    return turned


def _not_measured(reason: str, *, handedness: str, frame: str, n_positions: int = 0) -> ClubArc:
    return ClubArc(
        status=NOT_MEASURED,
        reason=reason,
        frame=frame,
        handedness=handedness,
        n_positions=n_positions,
    )


def _sigmas(params, impact_t_s, side, covariance) -> dict:
    jacobian = _numeric_jacobian(lambda p: _outputs(p, impact_t_s, side), params)
    output_cov = jacobian @ covariance @ jacobian.T
    sigmas = dict(zip(_OUTPUTS, np.sqrt(np.clip(np.diag(output_cov), 0.0, None))))
    sigmas["radius_m"] = float(math.sqrt(max(covariance[2, 2], 0.0)))
    return {name: float(value) for name, value in sigmas.items()}


def _prior_influence(params, impact_t_s, side, jacobian, covariance) -> tuple[dict, dict]:
    """How much the prior sets each output: its variance share and its sensitivity.

    With C = (J^T J)^-1 over the data rows J_d and the prior rows J_p, an output with
    gradient g has variance g^T C g = |J_d C g|^2 + |J_p C g|^2; the share is the second
    term over the sum. The sensitivity is how far the output moves when the prior's
    plane moves by one degree, or its radius by 0.1 m (Gauss-Newton: a prior row
    (p_k - mu_k) / sigma_k moved by mu_k moves the fit by C e_k / sigma_k^2): the share
    can be small while the output still leans on the assumed plane.
    """
    if covariance is None:
        return {}, {}
    plane_row, radius_row = jacobian[-2], jacobian[-1]
    gradients = _numeric_jacobian(lambda p: _outputs(p, impact_t_s, side), params)
    radius = np.zeros(params.size)
    radius[2] = 1.0
    shares, sensitivity = {}, {}
    for name, gradient in (*zip(_OUTPUTS, gradients), ("radius_m", radius)):
        spread = covariance @ gradient
        total = float(gradient @ spread)
        if total <= 0.0:
            continue
        prior_part = float((plane_row @ spread) ** 2 + (radius_row @ spread) ** 2)
        shares[name] = round(min(max(prior_part / total, 0.0), 1.0), 4)
        sensitivity[name] = {
            "per_plane_deg": round(
                float(plane_row @ spread) * float(plane_row[0]) * math.pi / 180.0, 4
            ),
            "per_radius_0_1_m": round(float(radius_row @ spread) * float(radius_row[2]) * 0.1, 4),
        }
    return shares, sensitivity


def normalize_handedness(handedness: Optional[str]) -> str:
    """Right-handed is the default; left-handed only when said explicitly."""
    return LEFT_HANDED if str(handedness or "").strip().upper() == LEFT_HANDED else RIGHT_HANDED


def fit_club_arc(
    positions: Sequence[HeadPosition],
    *,
    impact_t_s: Optional[float],
    club: Any,
    handedness: Optional[str] = RIGHT_HANDED,
    horizontal_offset_deg: Optional[float] = None,
    position_noise_m: float = POSITION_NOISE_FLOOR_M,
    ball_position: Optional[HeadPosition] = None,
) -> ClubArc:
    """Fit the shot's head positions with a circular arc and read it at impact.

    ``impact_t_s`` is on the positions' clock; ``ball_position`` (``t_s`` ignored) adds the
    low point's offset from the resting ball. Calculated when the shot's own points fix the
    arc (CONDITIONING); otherwise model geometry, with the per-club prior on R and theta.
    """
    handed = normalize_handedness(handedness)
    frame = frame_for(horizontal_offset_deg)
    side = _side(handed)
    if impact_t_s is None or not math.isfinite(float(impact_t_s)):
        return _not_measured("no impact time on the camera clock", handedness=handed, frame=frame)
    impact_t_s = float(impact_t_s)
    usable = sorted(
        (
            p
            for p in positions
            if all(math.isfinite(float(v)) for v in (p.t_s, p.lateral_m, p.up_m, p.forward_m))
            and p.t_s < impact_t_s
        ),
        key=lambda p: p.t_s,
    )
    if not positions:
        return _not_measured("no club-head positions", handedness=handed, frame=frame)
    if len(usable) < 2:
        return _not_measured(
            f"{len(usable)} pre-impact head position(s): an arc needs at least 2",
            handedness=handed,
            frame=frame,
            n_positions=len(usable),
        )
    # times from impact: a large camera clock never reaches the optimizer
    times = np.array([p.t_s for p in usable]) - impact_t_s
    impact_t_s = 0.0
    if np.any(np.diff(times) <= 0.0):
        return _not_measured(
            "head positions share a time", handedness=handed, frame=frame, n_positions=len(usable)
        )
    gap_s = impact_t_s - float(times[-1])
    if gap_s > MAX_IMPACT_GAP_S:
        return _not_measured(
            f"the last head position is {gap_s * 1000:.1f} ms before impact "
            f"(more than {MAX_IMPACT_GAP_S * 1000:.0f} ms)",
            handedness=handed,
            frame=frame,
            n_positions=len(usable),
        )
    xyz = _turn(
        np.array([[p.forward_m, p.lateral_m, p.up_m] for p in usable]), horizontal_offset_deg
    )
    prior = prior_for_club(club)
    n_positions = len(usable)
    start = _initial_params(
        xyz,
        times,
        side,
        prior.plane_deg if prior else _DEFAULT_PLANE_DEG,
        prior.radius_m if prior else _DEFAULT_RADIUS_M,
    )
    floor = float(position_noise_m)
    reasons: list[str] = []

    # 1. The shot alone (>= 3 positions): calculated when its own points fix the arc.
    data_sigmas = None
    if n_positions >= MIN_CALCULATED_POSITIONS:
        map_start = start
        if prior is not None:
            map_start, _ = _fit(xyz, times, side, start, prior, floor)
        params, residuals = _fit(xyz, times, side, map_start, None, floor)
        error = (_positions_at(params, times, side) - xyz).ravel()
        dof = error.size - _N_PARAMS
        sigma = max(floor, math.sqrt(float(error @ error) / dof) if dof > 0 else 0.0)
        covariance = _covariance(_numeric_jacobian(residuals, params))
        rms_mm = float(np.sqrt(np.mean(error**2))) * 1000.0
        if covariance is None:
            reasons.append("the shot's own points do not determine the arc")
        else:
            covariance = covariance * (sigma / floor) ** 2
            data_sigmas = _sigmas(params, impact_t_s, side, covariance)
            for name, limit in CONDITIONING.items():
                if data_sigmas[name] > limit:
                    reasons.append(
                        f"{name} uncertainty {data_sigmas[name]:.2f} exceeds {limit:g} (1 sigma)"
                    )
        if rms_mm > RESIDUAL_MAX_NOISE * floor * 1000.0:
            reasons.append(f"fit residual {rms_mm:.1f} mm exceeds the noise budget")
        interval = float(np.median(np.diff(times)))
        if gap_s > IMPACT_EXTRAPOLATION_MAX_FRAMES * interval:
            reasons.append(
                f"impact is {gap_s * 1000:.1f} ms past the last head position "
                f"(more than {IMPACT_EXTRAPOLATION_MAX_FRAMES:g} frame intervals)"
            )
        if not reasons:
            return _result(
                params,
                impact_t_s,
                side,
                status=CALCULATED,
                reason=None,
                covariance=covariance,
                frame=frame,
                handedness=handed,
                n_positions=n_positions,
                rms_mm=rms_mm,
                prior=None,
                conditioning={
                    "position_noise_floor_m": floor,
                    "sigma_used_m": sigma,
                    "data_only_sigma": data_sigmas,
                    "reasons": [],
                },
                ball=ball_position,
                offset_deg=horizontal_offset_deg,
            )
    else:
        reasons.append(
            f"{n_positions} head positions: fewer than {MIN_CALCULATED_POSITIONS} cannot fix "
            "the arc alone"
        )

    # 2. Model geometry: the published prior sets the radius and plane angle.
    if prior is None:
        return _not_measured(
            "no published prior for an unknown club, and "
            + "; ".join(reasons or ["the shot's own points do not fix the arc"]),
            handedness=handed,
            frame=frame,
            n_positions=n_positions,
        )
    reasons.append(f"the published {prior.club} prior sets the radius and swing plane")
    params, residuals = _fit(xyz, times, side, start, prior, floor)
    error = (_positions_at(params, times, side) - xyz).ravel()
    rms_mm = float(np.sqrt(np.mean(error**2))) * 1000.0
    jacobian = _numeric_jacobian(residuals, params)
    covariance = _covariance(jacobian)
    return _result(
        params,
        impact_t_s,
        side,
        status=MODEL_GEOMETRY,
        reason="; ".join(reasons),
        covariance=covariance,
        frame=frame,
        handedness=handed,
        n_positions=n_positions,
        rms_mm=rms_mm,
        prior=asdict(prior),
        conditioning={
            "position_noise_floor_m": floor,
            "sigma_used_m": floor,
            "data_only_sigma": data_sigmas,
            "reasons": reasons,
        },
        ball=ball_position,
        offset_deg=horizontal_offset_deg,
        prior_influence=_prior_influence(params, impact_t_s, side, jacobian, covariance),
    )


def _result(
    params,
    impact_t_s,
    side,
    *,
    status,
    reason,
    covariance,
    frame,
    handedness,
    n_positions,
    rms_mm,
    prior,
    conditioning,
    ball,
    offset_deg,
    prior_influence=None,
) -> ClubArc:
    values = _outputs(params, impact_t_s, side)
    ranges = {}
    if covariance is not None:
        sigmas = _sigmas(params, impact_t_s, side, covariance)
        ranges = {
            name: (
                round(float(value) - RANGE_SIGMAS * sigmas[name], 2),
                round(float(value) + RANGE_SIGMAS * sigmas[name], 2),
            )
            for name, value in zip(_OUTPUTS, values)
        }
    from_ball = None
    if ball is not None:
        ball_xyz = _turn(np.array([[ball.forward_m, ball.lateral_m, ball.up_m]]), offset_deg)[0]
        low = (params[3:6] - ball_xyz) * 1000.0
        from_ball = {
            "distance_mm": round(float(low[0]), 1),
            "height_mm": round(float(low[2]), 1),
            "side_mm": round(float(low[1]), 1),
        }
    rounded = {name: round(float(value), 2) for name, value in zip(_OUTPUTS, values)}
    return ClubArc(
        status=status,
        reason=reason,
        ranges=ranges,
        frame=frame,
        handedness=handedness,
        n_positions=n_positions,
        residual_rms_mm=round(rms_mm, 2),
        radius_m=round(float(params[2]), 4),
        head_speed_mph=round(float(params[2] * params[6]) * 2.23694, 1),
        prior=prior,
        conditioning=conditioning,
        low_point_from_ball_mm=from_ball,
        prior_share=dict((prior_influence or ({}, {}))[0]),
        prior_sensitivity=dict((prior_influence or ({}, {}))[1]),
        **rounded,
    )


SELECTION_RULE = (
    "club path and attack angle: the arc's value when its label ranks at least as high as "
    "the chained camera/IWR value's (measured > calculated > model geometry > estimated > "
    "experimental > not measured), otherwise the chained value; a chained value counts as "
    "measured when it exists and is not withheld, rejected or an error. The camera head "
    "track's value is experimental and is kept unless the arc's label ranks above "
    "experimental. Both are recorded under cross_check."
)
CHAINED_IWR_SOURCE = "camera_iwr_chained"
CHAINED_OPS_SOURCE = "camera_ops_chained"
ARC_FIT_SOURCE = "club_arc:shot_fit"
NO_CAMERA_DELIVERY = "no camera club delivery"
# Added to the arc's low-point distance per club; fitted to a TM4 normalized export.
LOW_POINT_OFFSETS_MM = {ClubType.IRON_7: 2.3, ClubType.DRIVER: -28.3, ClubType.PW: 5.5}
NOTE_NO_LOW_POINT_OFFSET = "arc geometry only: no per-club offset fitted for this club"
HEAD_TRACK_POSITIONS = "the camera head track's hosel positions"
HEAD_TRACK_POSITIONS_NOTE = (
    f"rests on {HEAD_TRACK_POSITIONS} (experimental): their depths are walked back from "
    "the OPS club speed"
)
ARC_PRIOR_SOURCE = "club_arc:published_prior"
_HORIZONTAL = {"club_path_deg", "swing_direction_deg", "low_point_distance_mm", "low_point_side_mm"}


def _as_mapping(delivery: Any) -> Optional[Mapping]:
    if delivery is None:
        return None
    if is_dataclass(delivery):
        return asdict(delivery)
    return delivery if isinstance(delivery, Mapping) else None


def _chained_value(delivery: Mapping, name: str, tier_name: str):
    """The chained value of ``name`` when it counts as measured, else None."""
    value = delivery.get(name)
    status = str(delivery.get("status") or "")
    if (
        value is None
        or not math.isfinite(float(value))
        or status.startswith("rejected")
        or status == "error"
        or delivery.get(tier_name) == "withheld"
    ):
        return None
    return float(value)


def _arc_source(arc: ClubArc) -> Optional[str]:
    if arc.status == CALCULATED:
        return ARC_FIT_SOURCE
    if arc.status == MODEL_GEOMETRY:
        return ARC_PRIOR_SOURCE
    return None


def _arc_metric(arc: ClubArc, name: str) -> dict:
    value = getattr(arc, name)
    return {
        "value": value,
        "status": arc.status if value is not None else NOT_MEASURED,
        "source": _arc_source(arc) if value is not None else None,
        "frame": arc.frame if name in _HORIZONTAL and value is not None else None,
        "range": list(arc.ranges[name]) if name in arc.ranges else None,
        "reason": arc.reason,
    }


def _note(entry: dict, text: str) -> dict:
    note = entry.get("note")
    return {**entry, "note": f"{note}; {text}" if note else text}


def _positions_label(data: Optional[Mapping]) -> tuple[Optional[str], float]:
    """The label the delivery's positions carry, and their per-point noise (metres).

    The head track's points are experimental, with their own pixel sigma; other camera
    positions keep the arc's own label (None) at the noise floor.
    """
    if not data or data.get("status") != HEAD_TRACK_SOURCE:
        return None, POSITION_NOISE_FLOOR_M
    sigma = (data.get("head_track") or {}).get("position_sigma_m")
    try:
        noise = max(POSITION_NOISE_FLOOR_M, float(sigma))
    except (TypeError, ValueError):
        noise = POSITION_NOISE_FLOOR_M
    return EXPERIMENTAL, noise


def _inherit_positions_label(entry: dict, name: str, arc: ClubArc, label: str) -> dict:
    """An arc output on the head track's positions: their label, or the prior's where it
    sets the value (``PRIOR_SET_SHARE``)."""
    if entry["value"] is None:
        return entry
    share = arc.prior_share.get(name)
    if arc.status == MODEL_GEOMETRY and share is not None and share >= PRIOR_SET_SHARE:
        return _note(
            {**entry, "status": MODEL_GEOMETRY},
            f"the published prior sets it: {share:.0%} of its variance is the prior's, "
            f"{HEAD_TRACK_POSITIONS} barely move it",
        )
    if LABEL_RANK[label] < LABEL_RANK[entry["status"]]:
        entry = {**entry, "status": label}
    entry = _note(entry, HEAD_TRACK_POSITIONS_NOTE)
    per_plane = (arc.prior_sensitivity.get(name) or {}).get("per_plane_deg")
    if arc.status == MODEL_GEOMETRY and per_plane is not None:
        unit = "mm" if name.endswith("_mm") else "deg"
        entry = _note(
            entry,
            f"and on the published prior's swing plane: it moves {per_plane:+.2f} {unit} "
            "per degree of the assumed plane",
        )
    return entry


def _with_low_point_offset(entry: dict, club: Any) -> dict:
    """The low point distance with the club's fitted offset, the arc's geometry kept."""
    entry = {**entry, "geometry_value": entry["value"], "offset_mm": None}
    if entry["value"] is None:
        return entry
    offset = LOW_POINT_OFFSETS_MM.get(club)
    if offset is None:
        return _note(entry, NOTE_NO_LOW_POINT_OFFSET)
    shifted = entry["range"]
    return _note(
        {
            **entry,
            "value": round(entry["value"] + offset, 2),
            "range": [round(bound + offset, 2) for bound in shifted] if shifted else None,
            "offset_mm": offset,
        },
        f"arc geometry {offset:+.1f} mm per-club offset (fitted by the maintainer)",
    )


def club_delivery_payload(delivery: Any, *, club: Any, handedness: Optional[str]) -> dict:
    """The shot's club delivery: every arc metric with status, source, frame, range, reason.

    ``delivery`` is the camera's ``ChainedDelivery`` or its persisted dict (``head_positions``,
    ``contact_t_s``, ``horizontal_offset_deg``); None when the camera stage did not run.
    """
    handed = normalize_handedness(handedness)
    data = _as_mapping(delivery)
    positions_label, noise_m = _positions_label(data)
    # None: no target line set (boresight frame); 0.0 is a target line (frame_for)
    recorded = (data or {}).get("horizontal_offset_deg")
    offset = float(recorded) if recorded is not None else None
    if data is None:
        arc = _not_measured(NO_CAMERA_DELIVERY, handedness=handed, frame=frame_for(offset))
    else:
        positions = [HeadPosition(**dict(item)) for item in data.get("head_positions") or ()]
        if not positions:
            arc = _not_measured(
                f"no club-head positions (club delivery: {data.get('status')})",
                handedness=handed,
                frame=frame_for(offset),
            )
        else:
            ball = data.get("ball_position")
            arc = fit_club_arc(
                positions,
                impact_t_s=data.get("contact_t_s"),
                club=club,
                handedness=handed,
                horizontal_offset_deg=offset,
                position_noise_m=noise_m,
                ball_position=HeadPosition(t_s=0.0, **dict(ball)) if ball else None,
            )
    metrics = {name: _arc_metric(arc, name) for name in _OUTPUTS}
    if positions_label is not None:
        metrics = {
            name: _inherit_positions_label(entry, name, arc, positions_label)
            for name, entry in metrics.items()
        }
    metrics["low_point_distance_mm"] = _with_low_point_offset(
        metrics["low_point_distance_mm"], club
    )
    chained = data or {}
    head_track = chained.get("status") == HEAD_TRACK_SOURCE
    if head_track:
        chained_source = HEAD_TRACK_SOURCE
    elif chained.get("status") == "camera_ops_fallback":
        chained_source = CHAINED_OPS_SOURCE
    else:
        chained_source = CHAINED_IWR_SOURCE
    for name, tier_name in (
        ("club_path_deg", "path_confidence_tier"),
        ("attack_angle_deg", "attack_confidence_tier"),
    ):
        measured = _chained_value(chained, name, tier_name)
        arc_rank = LABEL_RANK[metrics[name]["status"]]
        outranks = (
            arc_rank <= LABEL_RANK[EXPERIMENTAL] if head_track else arc_rank < LABEL_RANK[MEASURED]
        )
        if measured is not None and outranks:
            metrics[name] = {
                "value": measured,
                "status": EXPERIMENTAL if head_track else MEASURED,
                "source": chained_source,
                "frame": chained.get("club_path_frame") if name == "club_path_deg" else None,
                "range": None,
                "reason": None,
            }
        elif metrics[name]["value"] is None and chained:
            flag = (chained.get("head_track") or {}).get(name.replace("_deg", "_flag"))
            metrics[name]["reason"] = (
                f"{metrics[name]['reason']}; chained club delivery: {chained.get('status')}"
                + (f" ({flag})" if flag else "")
            )

    def difference(name):
        arc_value = getattr(arc, name)
        chained_value = chained.get(name)
        if arc_value is None or chained_value is None:
            return None
        return round(float(arc_value) - float(chained_value), 2)

    return {
        "schema": "openflight.club_delivery",
        "version": 1,
        "handedness": handed,
        "metrics": metrics,
        "selection_rule": SELECTION_RULE,
        "cross_check": {
            "arc": {
                "status": arc.status,
                "club_path_deg": arc.club_path_deg,
                "attack_angle_deg": arc.attack_angle_deg,
            },
            "chained": {
                "status": chained.get("status"),
                "club_path_deg": chained.get("club_path_deg"),
                "attack_angle_deg": chained.get("attack_angle_deg"),
                "club_path_frame": chained.get("club_path_frame"),
                "path_confidence_tier": chained.get("path_confidence_tier"),
                "attack_confidence_tier": chained.get("attack_confidence_tier"),
            },
            "arc_minus_chained_deg": {
                "club_path_deg": difference("club_path_deg"),
                "attack_angle_deg": difference("attack_angle_deg"),
            },
        },
        "arc": arc.to_dict(),
        "head_track": _head_track_state(chained),
    }


_HEAD_TRACK_STATE_KEYS = (
    "status",
    "turn",
    "positions",
    "position_sigma_m",
    "contact_ns",
    "contact_sigma_ms",
    "club_path_sigma_deg",
    "attack_angle_sigma_deg",
    "club_path_flag",
    "attack_angle_flag",
    "speed_mph",
)


def _head_track_state(delivery: Mapping) -> Optional[dict]:
    """The camera head track's state on the camera clock (camera-centred), or None."""
    record = delivery.get("head_track")
    if not isinstance(record, Mapping):
        return None
    return {key: record.get(key) for key in _HEAD_TRACK_STATE_KEYS}


def selected_path_and_attack(payload: Optional[Mapping]) -> dict:
    """The selected club path and attack angle entries (for the collision model)."""
    metrics = (payload or {}).get("metrics") or {}
    return {
        "club_path_deg": metrics.get("club_path_deg"),
        "attack_angle_deg": metrics.get("attack_angle_deg"),
    }

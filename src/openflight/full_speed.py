"""Full ball speed by measured projection, full club speed and full smash.

The OPS243 reads RADIAL speed: full = radial / (LOS . dir), dir the ball's unit velocity from
the shot's launch angles, LOS the OPS-to-ball unit vector at the reading's time. Frame: x along
the unit's boresight, y target-right, z up, metres, origin on the surface below the IWR.
"""

from __future__ import annotations

import math
from typing import Any, Callable, Optional

import numpy as np

from .flight_sim import (
    ACTIVE_AERO_MODEL,
    AIR_DENSITY_STD,
    BALL_AREA_M2,
    BALL_MASS_KG,
    CD_POLY,
    GRAVITY,
)
from .horizontal_frame import TARGET_LINE, UNIT_BORESIGHT, to_boresight_deg
from .metric_definitions import smash_factor
from .numeric import finite as _finite, finite_float
from .ops_speed_track import (  # noqa: F401  (ops_speed_track is re-exported)
    MAX_REFERRAL_MS,
    MPH_TO_MPS,
    SOUND_TRIGGER_IMPACT_TOLERANCE_MS,
    ops_speed_track,
)
from .rig_geometry import SPEED_OF_SOUND_M_S, camera_rdf_offset_to_target_lfu
from .shot_metrics import CALCULATED, NOT_MEASURED

METHOD = "measured_projection_v1"
BALL_SOURCE = "measured projection: OPS radial + measured launch + rig geometry"
CLUB_SOURCE = "measured projection: OPS radial + measured club direction + rig geometry"
SMASH_SOURCE = "metric_definitions.smash_factor (D37): full ball speed / full club speed"

# At 30 deg a 1 deg launch error already costs 1.0 % of speed (40 deg: 1.5 %).
MIN_PROJECTION = math.cos(math.radians(30.0))
# OPS defaults for a track that does not say: 128-sample windows at 30 ksps,
# zero-padded to 4096, so the output grid is 0.1018 mph and half a bin 0.051.
DEFAULT_WINDOW_MS = 128.0 / 30.0
DEFAULT_RADIAL_RESOLUTION_MPH = 0.5 * (30000.0 / 4096.0) * (0.01243 / 2.0) / MPH_TO_MPS

# Stated input tolerances for the range; none of the inputs is validated yet.
TOLERANCES = {
    "vertical_launch_deg": 1.0,
    "horizontal_launch_deg": 1.0,
    "tee_range_m": 0.05,
    "tee_side_offset_m": 0.05,
    "ops_position_mm": 3.0,
    "air_density_fraction": 0.10,
}

# Drag: the flight simulator's Cd(Sp) = CD_POLY. Spin is not needed: Sp 0.05 (low-spin
# driver) to 0.50 (wedge) gives Cd 0.186-0.424; the centre is used, the band goes in the range.
DRAG_SOURCE = (
    f"{ACTIVE_AERO_MODEL.version} (Ferguson, McNally & McPhee 2022 form, ISEA 14, "
    "doi:10.5703/1288284317493, refitted to TM4) Cd(Sp) over "
    "Sp 0.05-0.50, ISA sea-level air density, USGA-limit ball mass and diameter, plus "
    "gravity along the path"
)
DRAG_SPIN_PARAMETER_BAND = (0.05, 0.50)


def _cd(spin_parameter: float) -> float:
    return CD_POLY[0] + CD_POLY[1] * spin_parameter + CD_POLY[2] * spin_parameter**2


CD_BAND = tuple(_cd(sp) for sp in DRAG_SPIN_PARAMETER_BAND)
CD_CENTRAL = 0.5 * (CD_BAND[0] + CD_BAND[1])

UNMODELLED = (
    "the OPS spectral-peak extraction bias (not validated against a reference)",
    "a ground-reflected (two-ray) OPS path near the tee",
    "gravity's bend of the path over the referral (under 5 mm in 30 ms)",
    "the unit's tilt applied to the rig offsets (millimetres)",
)

MEASURED_VERTICAL_SOURCES = frozenset({"radar", "camera"})
MEASURED_HORIZONTAL_SOURCES = frozenset(
    {"radar", "camera_assisted_experimental", "camera_only_experimental", "camera_legacy_fallback"}
)


def drag_deceleration_mps2(
    speed_mps: float,
    vertical_deg: float,
    *,
    cd: float = CD_CENTRAL,
    air_density: float = AIR_DENSITY_STD,
) -> float:
    """Deceleration along the path: aerodynamic drag plus gravity's share."""
    drag = 0.5 * air_density * cd * BALL_AREA_M2 / BALL_MASS_KG * speed_mps**2
    return drag + GRAVITY * math.sin(math.radians(vertical_deg))


def launch_direction(vertical_deg: float, horizontal_boresight_deg: float) -> np.ndarray:
    """Unit velocity (forward, right, up) at this elevation and boresight azimuth."""
    v = math.radians(vertical_deg)
    h = math.radians(horizontal_boresight_deg)
    return np.array([math.cos(v) * math.cos(h), math.cos(v) * math.sin(h), math.sin(v)])


def _projection(ops_m: np.ndarray, position_m: np.ndarray, direction: np.ndarray) -> float:
    los = position_m - ops_m
    norm = float(np.linalg.norm(los))
    if norm <= 0.0:
        return math.nan
    return float(np.dot(los / norm, direction))


def _solve_ball(
    radial_mph: float,
    t_s: float,
    ops_m: np.ndarray,
    rest_m: np.ndarray,
    direction: np.ndarray,
    decel: Callable[[float], float],
) -> Optional[tuple[float, float, float]]:
    """(speed at separation, speed at the reading, projection), all m/s, or None.

    Iterates: the ball's position at the reading depends on its speed.
    """
    radial = radial_mph * MPH_TO_MPS
    v0 = radial
    for _ in range(100):
        travelled = v0 * t_s - 0.5 * decel(v0) * t_s * t_s
        projection = _projection(ops_m, rest_m + direction * travelled, direction)
        if not math.isfinite(projection) or projection <= 0.0:
            return None
        at_reading = radial / projection
        updated = at_reading + decel(0.5 * (v0 + at_reading)) * t_s
        if abs(updated - v0) < 1e-10:
            v0 = updated
            break
        v0 = updated
    return v0, at_reading, projection


def _vector(value: Any) -> Optional[np.ndarray]:
    if value is None:
        return None
    try:
        array = np.asarray(value, dtype=float)
    except (TypeError, ValueError):
        return None
    if array.shape != (3,) or not np.all(np.isfinite(array)):
        return None
    return array


def select_ball_reading(
    track: dict, impact_ms: Optional[float] = None
) -> list[tuple[float, float]]:
    """Candidate ball readings in time order: (window start ms, radial mph).

    A window qualifies from impact to ``MAX_REFERRAL_MS`` after it, with speed between
    ``MIN_PROJECTION`` of the canonical radial (less 1 mph; the club never reaches it) and
    3 mph above it (the processor's ball-match tolerance). The fastest peak is the ball.
    """
    impact = track.get("impact_ms") if impact_ms is None else impact_ms
    canonical = track.get("canonical_radial_mph")
    if not _finite(impact) or not _finite(canonical):
        return []
    low = MIN_PROJECTION * canonical - 1.0
    high = canonical + 3.0
    best: dict[float, float] = {}
    for start, speed in track.get("outbound") or []:
        if not (_finite(start) and _finite(speed)):
            continue
        if not impact <= start <= impact + MAX_REFERRAL_MS or not low <= speed <= high:
            continue
        if speed > best.get(start, -math.inf):
            best[start] = speed
    return sorted(best.items())


def _not_measured(reason: str, **extra: Any) -> dict:
    result = {
        "status": NOT_MEASURED,
        "value_mph": None,
        "range_mph": None,
        "source": BALL_SOURCE,
        "method": METHOD,
        "reason": reason,
        "canonical_speed_contract": "ops_radial_unchanged",
    }
    result.update(extra)
    return result


def _ball_geometry(
    rig: Any,
    tee_range_m: Any,
    radar_height_m: Any,
    ball_height_m: Any,
    tee_side_offset_m: Any,
    ops_shift_mm: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> tuple[Optional[dict], Optional[str]]:
    """(OPS, resting ball, mic) in the module frame, or a refusal reason."""
    if rig is None:
        return None, "no rig file: the OPS position is not measured"
    ops_offset = getattr(rig, "ops_offset_mm", None)
    origin = getattr(rig, "iwr_origin_mm", None)
    if _vector(ops_offset) is None:
        return None, "the rig file has no OPS offset: the OPS position is not measured"
    if _vector(origin) is None:
        return None, "the rig file cannot place the IWR origin the tee range starts from"
    if not _finite(tee_range_m) or tee_range_m <= 0:
        return None, "no IWR tee range: the ball's distance is not measured"
    if not _finite(radar_height_m) or not _finite(ball_height_m):
        return None, "the radar or ball height is not set"
    if not _finite(tee_side_offset_m):
        return None, (
            "the tee side offset (D36) is not measured by the setup pair; the ball is "
            "not assumed dead ahead"
        )
    vertical = float(ball_height_m) - float(radar_height_m)
    squared = float(tee_range_m) ** 2 - vertical**2 - float(tee_side_offset_m) ** 2
    if squared <= 0.0:
        return None, "the tee range cannot reach the ball at its height and side offset"
    shifted = tuple(float(a) + float(b) for a, b in zip(ops_offset, ops_shift_mm))
    ops_l, ops_f, ops_u = camera_rdf_offset_to_target_lfu(shifted)
    org_l, org_f, org_u = camera_rdf_offset_to_target_lfu(origin)
    geometry = {
        "ops_m": np.array([ops_f - org_f, ops_l - org_l, float(radar_height_m) + ops_u - org_u]),
        "ball_rest_m": np.array(
            [math.sqrt(squared), float(tee_side_offset_m), float(ball_height_m)]
        ),
        "mic_m": None,
    }
    mic = _vector(getattr(rig, "mic_offset_mm", None))
    if mic is not None:
        mic_l, mic_f, mic_u = camera_rdf_offset_to_target_lfu(mic)
        geometry["mic_m"] = np.array(
            [mic_f - org_f, mic_l - org_l, float(radar_height_m) + mic_u - org_u]
        )
    return geometry, None


def _horizontal_boresight(
    horizontal_deg: Any,
    source: Any,
    status: Any,
    frame: Any,
    offset_deg: Any,
) -> tuple[Optional[float], Optional[str]]:
    if not _finite(horizontal_deg):
        return None, "horizontal launch is not measured"
    if source not in MEASURED_HORIZONTAL_SOURCES:
        return None, f"horizontal launch source {source!r} is not a measured horizontal launch"
    if source == "radar" and status == "azimuth_uncalibrated":
        return None, (
            "the IWR horizontal launch has no horizontal phase reference (azimuth_uncalibrated)"
        )
    if frame == UNIT_BORESIGHT:
        return float(horizontal_deg), None
    if frame == TARGET_LINE:
        if not _finite(offset_deg):
            return None, "a target-line horizontal launch needs the horizontal offset (D34)"
        return float(to_boresight_deg(float(horizontal_deg), float(offset_deg))), None
    return None, f"horizontal launch frame {frame!r} is not one known frame"


def evaluate_full_ball_speed(
    *,
    ops_track: Optional[dict],
    vertical_deg: Any,
    vertical_source: Any,
    horizontal_deg: Any,
    horizontal_source: Any,
    horizontal_status: Any,
    horizontal_frame: Any,
    horizontal_offset_deg: Any,
    rig: Any,
    tee_range_m: Any,
    radar_height_m: Any,
    ball_height_m: Any,
    tee_side_offset_m: Any,
    tee_range_uncertainty_m: Any = None,
    tee_range_source: Any = None,
) -> dict:
    """Full ball speed at separation from one OPS reading, or not measured with the reason.

    ``tee_range_m`` is the shot's own range; ``tee_range_uncertainty_m`` widens its
    tolerance when larger than the stated 5 cm.
    """
    inputs = {
        "vertical_deg": finite_float(vertical_deg),
        "vertical_source": vertical_source,
        "horizontal_deg": finite_float(horizontal_deg),
        "horizontal_source": horizontal_source,
        "horizontal_frame": horizontal_frame,
        "horizontal_offset_deg": finite_float(horizontal_offset_deg),
        "tee_range_m": finite_float(tee_range_m),
        "tee_range_uncertainty_m": finite_float(tee_range_uncertainty_m),
        "tee_range_source": tee_range_source,
        "tee_side_offset_m": finite_float(tee_side_offset_m),
        "radar_height_m": finite_float(radar_height_m),
        "ball_height_m": finite_float(ball_height_m),
    }
    if not _finite(vertical_deg) or abs(float(vertical_deg)) >= 90.0:
        return _not_measured("vertical launch is not measured", inputs=inputs)
    if vertical_source not in MEASURED_VERTICAL_SOURCES:
        return _not_measured(
            f"vertical launch source {vertical_source!r} is not a measured vertical launch",
            inputs=inputs,
        )
    horizontal, reason = _horizontal_boresight(
        horizontal_deg,
        horizontal_source,
        horizontal_status,
        horizontal_frame,
        horizontal_offset_deg,
    )
    if reason:
        return _not_measured(reason, inputs=inputs)
    inputs["horizontal_boresight_deg"] = horizontal
    geometry, reason = _ball_geometry(
        rig, tee_range_m, radar_height_m, ball_height_m, tee_side_offset_m
    )
    if reason:
        return _not_measured(reason, inputs=inputs)
    if not isinstance(ops_track, dict):
        return _not_measured("no OPS speed track for this shot", inputs=inputs)
    impact_ms = ops_track.get("impact_ms")
    if not _finite(impact_ms):
        return _not_measured("no measured impact time on the OPS clock", inputs=inputs)
    impact_ms = float(impact_ms)
    impact_source = ops_track.get("impact_source")
    walk_back_ms = 0.0
    if impact_source == "sound_trigger":
        if geometry["mic_m"] is None:
            return _not_measured(
                "a sound-trigger impact needs the rig's mic offset for the acoustic walk-back",
                inputs=inputs,
            )
        distance = float(np.linalg.norm(geometry["ball_rest_m"] - geometry["mic_m"]))
        walk_back_ms = 1000.0 * distance / SPEED_OF_SOUND_M_S
    elif impact_source != "ops_transition":
        return _not_measured(
            f"impact source {impact_source!r} is not a measured impact", inputs=inputs
        )
    impact_ms -= walk_back_ms
    window_ms = float(ops_track.get("window_ms") or DEFAULT_WINDOW_MS)
    candidates = select_ball_reading(ops_track, impact_ms)
    if not candidates:
        return _not_measured(
            f"no OPS ball reading in a whole FFT window within {MAX_REFERRAL_MS:g} ms after impact",
            inputs=inputs,
        )

    vertical = float(vertical_deg)
    direction = launch_direction(vertical, horizontal)

    def central_drag(speed_mps: float) -> float:
        return drag_deceleration_mps2(speed_mps, vertical)

    best_projection = -math.inf
    chosen = None
    for start, radial in candidates:
        t_s = (start + 0.5 * window_ms - impact_ms) / 1000.0
        solved = _solve_ball(
            radial, t_s, geometry["ops_m"], geometry["ball_rest_m"], direction, central_drag
        )
        projection = solved[2] if solved else -math.inf
        best_projection = max(best_projection, projection)
        if solved and projection >= MIN_PROJECTION:
            chosen = (start, radial, t_s, solved)
            break
    if chosen is None:
        shown = best_projection if math.isfinite(best_projection) else float("nan")
        return _not_measured(
            f"ill-conditioned: the OPS line of sight shares at most {shown:.3f} of the ball's "
            f"direction (LOS . dir), below {MIN_PROJECTION:.3f} (cos 30 deg), at every reading "
            f"within {MAX_REFERRAL_MS:g} ms of impact",
            inputs=inputs,
            projection=shown if math.isfinite(shown) else None,
            min_projection=MIN_PROJECTION,
        )
    start, radial, t_s, (v0, at_reading, projection) = chosen
    value = v0 / MPH_TO_MPS

    def solve(
        *,
        radial_mph: float = radial,
        t: float = t_s,
        vertical_deg_: float = vertical,
        horizontal_deg_: float = horizontal,
        tee_range: float = float(tee_range_m),
        side: float = float(tee_side_offset_m),
        ops_shift: tuple = (0.0, 0.0, 0.0),
        decel: Optional[Callable[[float], float]] = None,
    ) -> float:
        geo, _ = _ball_geometry(rig, tee_range, radar_height_m, ball_height_m, side, ops_shift)
        if geo is None:
            return math.nan
        d = launch_direction(vertical_deg_, horizontal_deg_)
        drag = decel or (lambda v: drag_deceleration_mps2(v, vertical_deg_, cd=CD_CENTRAL))
        result = _solve_ball(radial_mph, max(t, 0.0), geo["ops_m"], geo["ball_rest_m"], d, drag)
        return result[0] / MPH_TO_MPS if result else math.nan

    def spread(*values: float) -> float:
        finite = [abs(v - value) for v in values if math.isfinite(v)]
        return max(finite) if finite and len(finite) == len(values) else math.inf

    tol = dict(TOLERANCES)
    if _finite(tee_range_uncertainty_m):
        tol["tee_range_m"] = max(tol["tee_range_m"], float(tee_range_uncertainty_m))
    resolution = float(ops_track.get("radial_resolution_mph") or DEFAULT_RADIAL_RESOLUTION_MPH)
    impact_tol_s = float(ops_track.get("impact_uncertainty_ms") or 0.0) / 1000.0
    # the FFT peak is about the window's mean radial; the value takes it at the centre
    mean_projection = float(
        np.mean(
            [
                _projection(
                    geometry["ops_m"],
                    geometry["ball_rest_m"] + direction * v0 * (t_s + dt / 1000.0),
                    direction,
                )
                for dt in np.linspace(-0.5 * window_ms, 0.5 * window_ms, 9)
            ]
        )
    )

    def ops_axis(axis: int, sign: float) -> tuple:
        return tuple(sign * tol["ops_position_mm"] * (axis == k) for k in range(3))

    ops_axes = [
        spread(solve(ops_shift=ops_axis(axis, 1.0)), solve(ops_shift=ops_axis(axis, -1.0)))
        for axis in range(3)
    ]
    rho = AIR_DENSITY_STD
    rho_tol = tol["air_density_fraction"]
    drag_low = solve(
        decel=lambda v: drag_deceleration_mps2(
            v, vertical, cd=CD_BAND[0], air_density=rho * (1 - rho_tol)
        )
    )
    drag_high = solve(
        decel=lambda v: drag_deceleration_mps2(
            v, vertical, cd=CD_BAND[1], air_density=rho * (1 + rho_tol)
        )
    )
    budget = {
        "radial_resolution": spread(
            solve(radial_mph=radial + resolution), solve(radial_mph=radial - resolution)
        ),
        "fft_window": abs(projection / mean_projection - 1.0) * value,
        "vertical_launch": spread(
            solve(vertical_deg_=vertical + tol["vertical_launch_deg"]),
            solve(vertical_deg_=vertical - tol["vertical_launch_deg"]),
        ),
        "horizontal_launch": spread(
            solve(horizontal_deg_=horizontal + tol["horizontal_launch_deg"]),
            solve(horizontal_deg_=horizontal - tol["horizontal_launch_deg"]),
        ),
        "tee_range": spread(
            solve(tee_range=float(tee_range_m) + tol["tee_range_m"]),
            solve(tee_range=float(tee_range_m) - tol["tee_range_m"]),
        ),
        "tee_side_offset": spread(
            solve(side=float(tee_side_offset_m) + tol["tee_side_offset_m"]),
            solve(side=float(tee_side_offset_m) - tol["tee_side_offset_m"]),
        ),
        "impact_time": spread(solve(t=t_s + impact_tol_s), solve(t=t_s - impact_tol_s)),
        "ops_position": math.sqrt(sum(term**2 for term in ops_axes)),
        "drag": 0.5 * abs(drag_high - drag_low)
        if math.isfinite(drag_high - drag_low)
        else math.inf,
    }
    half_width = math.sqrt(sum(term**2 for term in budget.values()))
    bounded = math.isfinite(half_width)
    return {
        "status": CALCULATED,
        "value_mph": value,
        "range_mph": [value - half_width, value + half_width] if bounded else None,
        "range_basis": "root-sum-square of one-at-a-time changes for the stated tolerances"
        if bounded
        else "unbounded: an input moved by its tolerance leaves the geometry",
        # an unbounded term (its perturbed geometry has no solution) reads None
        "uncertainty_mph": {
            name: term if math.isfinite(term) else None for name, term in budget.items()
        },
        "tolerances": dict(
            tol, impact_time_ms=impact_tol_s * 1000.0, radial_resolution_mph=resolution
        ),
        "tee_range_source": tee_range_source,
        "source": BALL_SOURCE,
        "method": METHOD,
        "reason": None,
        "referred_to": "separation",
        "projection": projection,
        "min_projection": MIN_PROJECTION,
        "speed_at_reading_mph": at_reading / MPH_TO_MPS,
        "drag_referral_mph": value - at_reading / MPH_TO_MPS,
        "drag_source": DRAG_SOURCE,
        "reading": {
            "window_start_ms": start,
            "window_centre_ms": start + 0.5 * window_ms,
            "window_ms": window_ms,
            "radial_mph": radial,
            "time_after_impact_ms": t_s * 1000.0,
            "impact_ms": impact_ms,
            "impact_source": impact_source,
            "impact_walk_back_ms": walk_back_ms,
            "clock": ops_track.get("clock", "ops_capture"),
            "choice": "first whole FFT window after impact on the ball track with LOS . dir "
            "at or above the threshold",
        },
        "inputs": inputs,
        "geometry_m": {
            "frame": "x forward along the unit boresight, y target-right, z up; origin on the "
            "hitting surface below the IWR range origin",
            "ops": geometry["ops_m"].tolist(),
            "ball_at_rest": geometry["ball_rest_m"].tolist(),
            "direction_unit": direction.tolist(),
        },
        "unmodelled": list(UNMODELLED),
        "canonical_speed_contract": "ops_radial_unchanged",
    }


def evaluate_full_club_speed(
    *,
    radial_mph: Any,
    head_position_m: Any,
    direction: Any,
    ops_position_m: Any,
    direction_source: Any,
    reason: Optional[str] = None,
) -> dict:
    """Full club speed from ONE radial reading, the head's position and direction at its time.

    Every input must be at the reading's own time: the head moves on a curve.
    """
    base = {
        "status": NOT_MEASURED,
        "value_mph": None,
        "range_mph": None,
        "source": CLUB_SOURCE,
        "method": METHOD,
        "direction_source": direction_source,
        "min_projection": MIN_PROJECTION,
    }
    if reason:
        return {**base, "reason": reason}
    if not _finite(radial_mph) or radial_mph <= 0:
        return {**base, "reason": "no single OPS club reading"}
    head = _vector(head_position_m)
    ops = _vector(ops_position_m)
    d = _vector(direction)
    if head is None or ops is None:
        return {**base, "reason": "the head or OPS position at the reading is not measured"}
    if d is None or not direction_source or float(np.linalg.norm(d)) <= 0:
        return {**base, "reason": "the club's direction at the reading is not measured"}
    d = d / float(np.linalg.norm(d))
    projection = _projection(ops, head, d)
    if not math.isfinite(projection) or projection < MIN_PROJECTION:
        return {
            **base,
            "projection": projection if math.isfinite(projection) else None,
            "reason": (
                f"ill-conditioned: LOS . dir = {projection:.3f}, below {MIN_PROJECTION:.3f} "
                "(cos 30 deg)"
            ),
        }
    return {
        **base,
        "status": CALCULATED,
        "value_mph": float(radial_mph) / projection,
        "projection": projection,
        "reason": None,
    }


def _is_full(speed: Any) -> bool:
    return (
        isinstance(speed, dict)
        and speed.get("status") == CALCULATED
        and _finite(speed.get("value_mph"))
    )


def evaluate_full_smash(ball_full: Any, club_full: Any) -> dict:
    """Smash factor from two full speeds; not measured otherwise (never full over radial)."""
    base = {"status": NOT_MEASURED, "value": None, "source": SMASH_SOURCE}
    if not _is_full(ball_full):
        return {**base, "reason": "the ball speed is not a full speed (D21)"}
    if not _is_full(club_full):
        return {
            **base,
            "reason": "the club speed is not a full speed: it is the OPS radial reading (D21)",
        }
    value = smash_factor(ball_full["value_mph"], club_full["value_mph"])
    if value is None or not math.isfinite(value):
        return {**base, "reason": "no ratio is defined for these speeds"}
    return {**base, "status": CALCULATED, "value": value, "reason": None}

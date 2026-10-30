"""Full ball speed by measured projection, full club speed and full smash.

The OPS243 measures RADIAL speed: the share of the ball's velocity along the
line from the OPS to the ball. This module turns one radial reading into the
ball's full speed with the shot's own measured geometry, and nothing fitted:

    full speed = radial speed / (LOS . dir)

- ``dir`` is the ball's unit velocity from this shot's measured launch angles:
  vertical from the IWR LCMF (or the camera), horizontal from the camera or the
  IWR HLCMF, turned from the target-line frame into the unit's frame by the one
  horizontal offset (``horizontal_frame``).
- ``LOS`` is the unit vector from the OPS to the ball at the reading's own time.
  The OPS sits where the rig file puts it (``ops_offset_mm``); the ball starts
  where the IWR's tee range and the setup's side offset (D36) put it, and has
  moved along ``dir`` by the time of the reading.
- The reading is ONE FFT window of the OPS speed timeline: the first whole
  window after the measured impact whose projection is well conditioned, so it
  is as close to separation as the geometry allows. The value is referred back
  to separation with the flight model's drag of a golf ball (the form of
  Ferguson, McNally & McPhee 2022, refitted to TM4 as flight-fit-v1) and gravity
  along the path; over a few milliseconds that adds about 0.03-0.04 mph per
  millisecond.

Refusals (status ``not_measured``, value None, with the reason):

- the projection is below ``MIN_PROJECTION`` = cos 30 deg = 0.866 at every
  candidate reading. The error grows as ``tan(angle) x angle error``: at 30 deg a
  1 deg launch error (the D32 95th-percentile launch goal) already costs 1.0 %
  of speed, about 1-1.6 mph at 100-160 mph, the D32 95th-percentile budget for
  full ball speed; past 30 deg it grows quickly (40 deg: 1.5 %);
- a launch angle is missing, a table estimate, of a source no label covers, or in an
  unknown or mixed frame;
- a geometry source is missing: the rig file or its OPS offset, the tee range,
  the radar or ball height, or the setup's tee side offset (no ball is assumed
  dead ahead: that would be a filled-in value);
- no OPS ball reading lies inside ``MAX_REFERRAL_MS`` after a measured impact.

Every value carries a range: the root-sum-square of one-at-a-time changes for
stated input tolerances (``TOLERANCES``), each named in ``uncertainty_mph``.

The canonical radial ball speed is never changed (``docs/camera/speed-contract.md``),
and the legacy peak-radial factor (``speed_correction.radial_speed_factor``) is
not used here: it stays recorded only as a comparator.

Frame: x forward along the unit's boresight, y target-right, z up, metres, with
the origin on the hitting surface directly below the IWR's range origin.
"""

from __future__ import annotations

import math
from typing import Any, Callable, Optional

import numpy as np

from .acoustic_timing import sound_travel_time_ns
from .flight_sim import (
    ACTIVE_AERO_MODEL,
    AIR_DENSITY_STD,
    BALL_AREA_M2,
    BALL_MASS_KG,
    CD_POLY,
    GRAVITY,
)
from .horizontal_frame import (
    TARGET_LINE,
    UNIT_BORESIGHT,
    implausible_launch_reason,
    to_boresight_deg,
)
from .metric_definitions import smash_factor
from .numeric import finite as _finite, finite_float
from .ops_speed_track import (  # noqa: F401  (ops_speed_track is re-exported)
    MAX_REFERRAL_MS,
    MPH_TO_MPS,
    SOUND_TRIGGER_IMPACT_TOLERANCE_MS,
    ops_speed_track,
)
from .rig_geometry import SPEED_OF_SOUND_M_S, camera_rdf_offset_to_target_lfu
from .source_chain import weakest_label
from .source_labels import (
    EXPERIMENTAL_LAUNCH_SOURCES,
    LABEL_EXPERIMENTAL,
    LABEL_MEASURED,
    LABEL_MODEL_GEOMETRY,
    WEAK_HORIZONTAL_SOURCES,
    launch_label,
    launch_label_note,
)

CALCULATED = "calculated"
ESTIMATED = "estimated"
EXPERIMENTAL = LABEL_EXPERIMENTAL
NOT_MEASURED = "not_measured"
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
MEASURED_HORIZONTAL_SOURCES = frozenset({"radar"})
# Exactly the launch sources the label policy labels (source_labels.launch_label): the
# full speed is projected on each and takes its weakest input's label (fix 5, 25 Oct
# 2026): calculated on measured inputs, estimated on an estimated one (an uncalibrated
# azimuth, a configured tilt, a calibrated-lens camera fallback), experimental on an
# experimental one (a failed gate, a nominal-lens camera value). Only a calculated one
# is flown by a simulator (D31).
VERTICAL_SOURCES = MEASURED_VERTICAL_SOURCES | frozenset(EXPERIMENTAL_LAUNCH_SOURCES)
HORIZONTAL_SOURCES = (
    MEASURED_HORIZONTAL_SOURCES
    | frozenset(WEAK_HORIZONTAL_SOURCES)
    | frozenset(EXPERIMENTAL_LAUNCH_SOURCES)
)
# The side offset from this shot's resting ball (its camera bearing at its range), when
# the setup measured none: a stand-in, labelled.
CAMERA_SIDE_OFFSET_SOURCE = "camera_resting_ball"
# A range that is a stand-in, not the shot's own IWR launch point (13 Oct, 25 Oct).
WEAK_RANGE_SOURCES = {
    "camera_prior": "the tee range is the camera prior's (coarse)",
    "camera_ball_size": "the tee range is the resting ball's own size (experimental)",
}
# Each stand-in range's label (the resting ball's size follows the chain's own label:
# estimated through a calibrated lens, Q6), and the IWR's own launch point when it is
# weaker than measured.
_RANGE_LABELS = {"camera_prior": ESTIMATED, "camera_ball_size": EXPERIMENTAL}
_IWR_RANGE_NOTES = {
    "iwr_partial": "the tee range is from a partial IWR dump (estimated)",
    "estimated": "the tee range is one two IWR tracks compete for (estimated)",
}


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
    if source not in HORIZONTAL_SOURCES:
        return None, f"horizontal launch source {source!r} is not a labelled horizontal launch"
    implausible = implausible_launch_reason(horizontal_deg)
    if implausible is not None:
        return None, f"horizontal launch {implausible}"
    # an estimated or experimental horizontal is an input: the value takes its label
    del status
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
    tee_side_offset_source: Any = None,
    tee_range_label: Any = None,
    vertical_label: Optional[str] = None,
    vertical_note: Optional[str] = None,
    horizontal_label: Optional[str] = None,
    horizontal_note: Optional[str] = None,
) -> dict:
    """Full ball speed at separation from one OPS reading, or not measured with the reason.

    ``tee_range_m`` is the shot's own range (13 Oct): its IWR launch point, or the
    camera prior labelled; ``tee_range_uncertainty_m`` widens the range's tolerance
    when it is larger than the stated 5 cm, and ``tee_range_source`` is recorded.

    The value takes its weakest input's label: ``vertical_label`` and
    ``horizontal_label`` (with their notes) are the kiosk's own
    (``source_labels.shot_launch_label``), else the sources' alone
    (``source_labels.launch_label``); ``tee_range_label`` is the range chain's.
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
    if vertical_source not in VERTICAL_SOURCES:
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
        walk_back_ms = (
            sound_travel_time_ns(
                geometry["ball_rest_m"], geometry["mic_m"], speed_m_s=SPEED_OF_SOUND_M_S
            )
            / 1e6
        )
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
    labelled = _weak_inputs(
        (
            (
                "vertical",
                vertical_source,
                vertical_label or launch_label(vertical_source),
                vertical_note,
            ),
            (
                "horizontal",
                horizontal_source,
                horizontal_label or launch_label(horizontal_source, horizontal_status),
                horizontal_note or launch_label_note(horizontal_source, horizontal_status),
            ),
        ),
        tee_range_source=tee_range_source,
        tee_range_label=tee_range_label,
        tee_side_offset_source=tee_side_offset_source,
    )
    weak = [text for _label, text in labelled]
    labels = {label for label, _text in labelled}
    status = EXPERIMENTAL if EXPERIMENTAL in labels else ESTIMATED if labelled else CALCULATED
    return {
        "status": status,
        "weak_inputs": weak,
        "note": "; ".join(weak) or None,
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


def _weak_inputs(
    launches, *, tee_range_source, tee_range_label, tee_side_offset_source
) -> list[tuple[str, str]]:
    """(label, why) of every input weaker than measured: a launch angle under its label,
    a stand-in or weaker IWR range, the camera's side offset."""
    weak = []
    for name, source, label, note in launches:
        if label in (None, LABEL_MEASURED):
            continue
        if source in EXPERIMENTAL_LAUNCH_SOURCES:
            why = f"{name} launch from {EXPERIMENTAL_LAUNCH_SOURCES[source]} ({label})"
            why = f"{why}: {note}" if note else why
        else:
            why = f"{name} launch {label} ({note or source})"
        weak.append((EXPERIMENTAL if label == LABEL_EXPERIMENTAL else ESTIMATED, why))
    if tee_range_source in WEAK_RANGE_SOURCES:
        label = _RANGE_LABELS[tee_range_source]
        why = WEAK_RANGE_SOURCES[tee_range_source]
        if tee_range_source == "camera_ball_size" and tee_range_label == ESTIMATED:
            label = ESTIMATED
            why = (
                "the tee range is the resting ball's own size through a calibrated lens (estimated)"
            )
        weak.append((label, why))
    elif tee_range_label in _IWR_RANGE_NOTES:
        weak.append((ESTIMATED, _IWR_RANGE_NOTES[tee_range_label]))
    if tee_side_offset_source == CAMERA_SIDE_OFFSET_SOURCE:
        weak.append(
            (EXPERIMENTAL, "the side offset is the resting ball's camera bearing (experimental)")
        )
    return weak


TOTAL_ROUTE = "ops_total_projection"
TOTAL_SOURCE = (
    "OPS total-speed projection: the canonical OPS radial over its peak line-of-sight share "
    "(speed_correction.radial_speed_factor) + vertical launch + rig geometry"
)
TOTAL_WINDOW_MS = 70.0
# Historical scatter allowance reported in the source-chain audit, not a current-rig grade.
TOTAL_MODEL_SCATTER_MPH = 0.5
TOTAL_VALIDATION = (
    "Historical audit reports 0.5 mph scatter on 12 Oct 2026 TrackMan pairs at 1.83 m; "
    "not independently reproduced here; no current-rig accuracy grade"
)
TOTAL_ASSUMPTIONS = (
    "the ball flies along the unit's boresight: the horizontal launch is not an input "
    "(a few degrees change the share by well under 1 %)",
    "the OPS reading is the peak radial speed within its first 70 ms (mode extraction)",
    "the drag over that window is the historical 0.027 mph per ms",
)
_FEET_PER_M = 1.0 / 0.3048
ASSUMED_SIDE_OFFSET_NOTE = (
    "the ball's side offset is assumed dead ahead of the IWR origin (the setup measured none)"
)


def _total_value(radial_mph: float, vertical_deg: float, geometry: dict) -> float:
    """Radial over the model's peak share; its 0.5 safety clamp is not a solution."""
    from .speed_correction import radial_speed_factor  # noqa: PLC0415

    forward, lateral, above = (
        float(value) for value in geometry["ball_rest_m"] - geometry["ops_m"]
    )
    if forward <= 0.0:
        return math.nan
    factor = radial_speed_factor(
        vertical_deg,
        radial_mph,
        forward * _FEET_PER_M,
        above * _FEET_PER_M,
        TOTAL_WINDOW_MS,
        ball_lateral_ft=lateral * _FEET_PER_M,
    )
    return radial_mph / factor if factor > 0.5 else math.nan


def evaluate_total_speed_projection(
    *,
    ball_radial_mph: Any,
    vertical_deg: Any,
    vertical_source: Any,
    rig: Any,
    tee_range_m: Any,
    radar_height_m: Any,
    ball_height_m: Any,
    tee_side_offset_m: Any,
    tee_range_uncertainty_m: Any = None,
    tee_range_source: Any = None,
    tee_side_offset_source: Any = None,
    tee_range_label: Any = None,
    vertical_label: Optional[str] = None,
    vertical_note: Optional[str] = None,
) -> dict:
    """Full ball speed from the canonical OPS radial and the peak line-of-sight share.

    The second full-ball-speed route (``shot_models``), used when the launch-based
    projection refuses (a missing horizontal launch, no measured side
    offset, no OPS reading in the referral window, an ill-conditioned line of sight). It
    needs no horizontal launch and no OPS timeline: ``radial_speed_factor`` predicts the
    peak share of the ball's speed the OPS sees over its first 70 ms from the vertical
    launch and where the ball sits from the OPS (``_ball_geometry``), and the canonical
    radial is divided by it. Labels follow the launch-based route: calculated on a
    measured vertical launch, the shot's own range and the setup's side offset; estimated,
    naming each, on nominal geometry; experimental on an experimental input.
    """
    side_offset = finite_float(tee_side_offset_m)
    side_assumed = side_offset is None
    inputs = {
        "ball_radial_mph": finite_float(ball_radial_mph),
        "vertical_deg": finite_float(vertical_deg),
        "vertical_source": vertical_source,
        "tee_range_m": finite_float(tee_range_m),
        "tee_range_uncertainty_m": finite_float(tee_range_uncertainty_m),
        "tee_range_source": tee_range_source,
        "tee_side_offset_m": 0.0 if side_assumed else side_offset,
        "tee_side_offset_source": "assumed" if side_assumed else tee_side_offset_source,
        "radar_height_m": finite_float(radar_height_m),
        "ball_height_m": finite_float(ball_height_m),
    }

    def refused(reason: str) -> dict:
        return _not_measured(
            reason, inputs=inputs, source=TOTAL_SOURCE, method=TOTAL_ROUTE, route=TOTAL_ROUTE
        )

    radial = inputs["ball_radial_mph"]
    if radial is None or radial <= 0.0:
        return refused("no OPS radial ball speed")
    if not _finite(vertical_deg) or abs(float(vertical_deg)) >= 90.0:
        return refused("vertical launch is not measured")
    if vertical_source not in VERTICAL_SOURCES:
        return refused(
            f"vertical launch source {vertical_source!r} is not a measured vertical launch"
        )
    vertical = float(vertical_deg)
    side = inputs["tee_side_offset_m"]
    geometry, reason = _ball_geometry(rig, tee_range_m, radar_height_m, ball_height_m, side)
    if reason:
        return refused(reason)
    value = _total_value(radial, vertical, geometry)
    if not math.isfinite(value):
        return refused("peak radial projection is outside the model's supported geometry")

    tol = dict(TOLERANCES)
    if _finite(tee_range_uncertainty_m):
        tol["tee_range_m"] = max(tol["tee_range_m"], float(tee_range_uncertainty_m))

    def solve(*, vertical_=vertical, tee_range=float(tee_range_m), side_=side, ops_shift=None):
        geo, _ = _ball_geometry(
            rig, tee_range, radar_height_m, ball_height_m, side_, ops_shift or (0.0, 0.0, 0.0)
        )
        return _total_value(radial, vertical_, geo) if geo is not None else math.nan

    def spread(*values: float) -> float:
        finite = [abs(v - value) for v in values if math.isfinite(v)]
        return max(finite) if finite and len(finite) == len(values) else math.inf

    def ops_axis(axis: int, sign: float) -> tuple:
        return tuple(sign * tol["ops_position_mm"] * (axis == k) for k in range(3))

    budget = {
        "vertical_launch": spread(
            solve(vertical_=vertical + tol["vertical_launch_deg"]),
            solve(vertical_=vertical - tol["vertical_launch_deg"]),
        ),
        "tee_range": spread(
            solve(tee_range=float(tee_range_m) + tol["tee_range_m"]),
            solve(tee_range=float(tee_range_m) - tol["tee_range_m"]),
        ),
        "tee_side_offset": spread(
            solve(side_=side + tol["tee_side_offset_m"]),
            solve(side_=side - tol["tee_side_offset_m"]),
        ),
        "ops_position": math.sqrt(
            sum(
                spread(solve(ops_shift=ops_axis(axis, 1.0)), solve(ops_shift=ops_axis(axis, -1.0)))
                ** 2
                for axis in range(3)
            )
        ),
        "radial_resolution": DEFAULT_RADIAL_RESOLUTION_MPH * value / radial,
        "model_scatter": TOTAL_MODEL_SCATTER_MPH,
    }
    half_width = math.sqrt(sum(term**2 for term in budget.values()))
    bounded = math.isfinite(half_width)
    weak = _weak_inputs(
        [
            (
                "vertical",
                vertical_source,
                vertical_label or launch_label(vertical_source),
                vertical_note,
            )
        ],
        tee_range_source=tee_range_source,
        tee_range_label=tee_range_label,
        tee_side_offset_source=tee_side_offset_source,
    )
    if side_assumed:
        weak.append((ESTIMATED, ASSUMED_SIDE_OFFSET_NOTE))
    notes = [note for _, note in weak]
    return {
        "status": weakest_label(CALCULATED, *(label for label, _ in weak)),
        "weak_inputs": notes,
        "note": "; ".join(notes) or None,
        "value_mph": value,
        "range_mph": [value - half_width, value + half_width] if bounded else None,
        "range_basis": "root-sum-square of one-at-a-time changes for the stated tolerances, "
        "plus the model's scatter against TrackMan"
        if bounded
        else "unbounded: an input moved by its tolerance leaves the geometry",
        "uncertainty_mph": {
            name: term if math.isfinite(term) else None for name, term in budget.items()
        },
        "tolerances": tol,
        "radial_mph": radial,
        "radial_share": radial / value,
        "window_ms": TOTAL_WINDOW_MS,
        "tee_range_source": tee_range_source,
        "source": TOTAL_SOURCE,
        "method": TOTAL_ROUTE,
        "route": TOTAL_ROUTE,
        "reason": None,
        "referred_to": "launch",
        "validation": TOTAL_VALIDATION,
        "assumptions": list(TOTAL_ASSUMPTIONS),
        "inputs": inputs,
        "geometry_m": {
            "frame": "x forward along the unit boresight, y target-right, z up; origin on the "
            "hitting surface below the IWR range origin",
            "ops": geometry["ops_m"].tolist(),
            "ball_at_rest": geometry["ball_rest_m"].tolist(),
        },
        "canonical_speed_contract": "ops_radial_unchanged",
    }


# ---------------------------------------------------------------- club


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


CLUB_HEAD_TRACK_SOURCE = (
    "OPS club reading (one FFT window) + the camera head track's direction and position at "
    "its time + rig geometry"
)
CLUB_HEAD_TRACK_NOTE = (
    "rests on the camera head track's direction (experimental): hosel + OPS speed, not "
    "validated against TrackMan"
)
# The head track's constant-speed arc holds over its own frames, the last ~25 ms.
MAX_CLUB_READING_BEFORE_CONTACT_MS = 30.0
CLUB_UNMODELLED = (
    "the OPS sees the club's fastest scatterer (the toe, the leading edge), the head track "
    "follows the hosel: the toe moves a few per cent faster on the arc",
    "the speed at the reading, not at contact: the head still gains a little before impact",
    "the unit's tilt applied to the rig offsets (millimetres)",
)


def _camera_relative(offset_mm) -> Optional[np.ndarray]:
    """A rig offset (camera right, down, forward, mm) as (lateral, up, forward) metres."""
    vector = _vector(offset_mm)
    if vector is None:
        return None
    right, down, forward = vector / 1000.0
    return np.array([right, -down, forward])


def _turned(direction: np.ndarray, *, path_deg: float = 0.0, attack_deg: float = 0.0):
    """A (lateral, up, forward) unit direction with its path and attack angle moved."""
    lateral, up, forward = (float(v) for v in direction)
    path = math.atan2(lateral, forward) + math.radians(path_deg)
    attack = math.atan2(up, math.hypot(lateral, forward)) + math.radians(attack_deg)
    return np.array(
        [math.cos(attack) * math.sin(path), math.sin(attack), math.cos(attack) * math.cos(path)]
    )


def evaluate_full_club_speed_head_track(*, ops_track: Any, head_track: Any, rig: Any) -> dict:
    """Full club speed from the one OPS club reading and the head track's direction then.

    Geometry, all in the head track's frame ((lateral, up, forward) metres from the
    camera's optical centre, level, along the unit's boresight):

    - the reading is the club pick's FFT window (``ops_speed_track`` ``club_reading``);
      its centre's time before contact is its distance from the OPS impact time (walked
      back by the sound's flight to the mic for a sound trigger), and that same time
      before the head track's contact (the acoustic window's centre) puts it on the
      camera's clock: both clocks are anchored on contact;
    - the head's position and direction then are the head track's own turning arc
      (``head_track.head_state_at``);
    - the OPS sits at the rig's offset from the lens; ``LOS`` is the unit vector from it
      to the head, and full speed = radial / (LOS . dir) with the same cos 30 deg refusal
      as the ball (``evaluate_full_club_speed``).

    The value is experimental: the head track's direction is. The range is the root-sum-
    square of one-at-a-time changes: the radial bin, the reading's time (the OPS impact
    and the camera contact uncertainties), the FFT window (the projection over the window
    against its centre), the head track's path and attack sigmas, the head's depth (the
    stated tee-range tolerance) and the OPS position.
    """
    from .camera.head_track import head_state_at  # noqa: PLC0415

    base = {
        "status": NOT_MEASURED,
        "value_mph": None,
        "range_mph": None,
        "source": CLUB_HEAD_TRACK_SOURCE,
        "method": METHOD,
        "direction_source": None,
        "min_projection": MIN_PROJECTION,
    }

    def refused(reason: str, **extra) -> dict:
        return {**base, "reason": reason, **extra}

    record = head_track if isinstance(head_track, dict) else {}
    if record.get("status") != "ok" or not isinstance(record.get("turn"), dict):
        why = record.get("status") or "not run"
        timing = record.get("contact_timing")
        if isinstance(timing, dict) and timing.get("reason"):
            why = f"{why}: {timing['reason']}"
        return refused(f"no camera head track direction ({why})")
    flags = [record.get(name) for name in ("club_path_flag", "attack_angle_flag")]
    if any(flags):
        return refused(
            f"the camera head track flagged its direction: {next(f for f in flags if f)}"
        )
    if not isinstance(ops_track, dict):
        return refused("no OPS speed track for this shot")
    reading = ops_track.get("club_reading")
    if not isinstance(reading, dict) or not (
        _finite(reading.get("window_start_ms")) and _finite(reading.get("radial_mph"))
    ):
        return refused("no one OPS club reading with its window time (the club pick kept none)")
    radial = float(reading["radial_mph"])
    if radial <= 0.0:
        return refused("no one OPS club reading with its window time (not positive)")
    if rig is None:
        return refused("no rig file: the OPS position is not measured")
    ops = _camera_relative(getattr(rig, "ops_offset_mm", None))
    if ops is None:
        return refused("the rig file has no OPS offset: the OPS position is not measured")
    impact_ms = ops_track.get("impact_ms")
    if not _finite(impact_ms):
        return refused("no measured impact time on the OPS clock")
    impact_source = ops_track.get("impact_source")
    walk_back_ms = 0.0
    timing = record.get("contact_timing")
    if impact_source == "sound_trigger":
        if timing is not None and not isinstance(timing, dict):
            return refused("no bounded shared acoustic contact delay")
        if timing is not None and timing.get("status") != "legacy_unqualified":
            try:
                delay = np.asarray(timing.get("gate_to_contact_delay_ns"), dtype=float)
                valid = (
                    timing.get("status") == "model_interval"
                    and delay.shape == (2,)
                    and np.all(np.isfinite(delay))
                    and 0 <= delay[0] <= delay[1]
                )
            except (TypeError, ValueError):
                valid = False
            if not valid:
                return refused("no bounded shared acoustic contact delay")
            walk_back_ms = float(np.mean(delay)) / 1e6
        else:
            mic = _camera_relative(getattr(rig, "mic_offset_mm", None))
            ball = _vector(record["turn"].get("ball_m"))
            if mic is None or ball is None:
                return refused("a sound-trigger impact needs the rig's mic offset and the ball")
            walk_back_ms = sound_travel_time_ns(ball, mic, speed_m_s=SPEED_OF_SOUND_M_S) / 1e6
    elif impact_source != "ops_transition":
        return refused(f"impact source {impact_source!r} is not a measured impact")
    window_ms = float(reading.get("window_ms") or ops_track.get("window_ms") or DEFAULT_WINDOW_MS)
    centre_ms = float(reading["window_start_ms"]) + 0.5 * window_ms
    before_ms = float(impact_ms) - walk_back_ms - centre_ms
    if before_ms < 0.0:
        return refused(f"the club reading's centre is {-before_ms:.1f} ms after contact")
    if before_ms < 0.5 * window_ms:
        return refused("the OPS club reading's FFT window crosses contact")
    if before_ms > MAX_CLUB_READING_BEFORE_CONTACT_MS:
        return refused(
            f"the club reading is {before_ms:.1f} ms before contact, more than "
            f"{MAX_CLUB_READING_BEFORE_CONTACT_MS:g} ms: beyond the head track's arc"
        )
    contact_ns = float(record["turn"]["contact_ns"])

    def projection_at(
        before: float, *, path=0.0, attack=0.0, depth=0.0, ops_shift=(0.0, 0.0, 0.0)
    ) -> float:
        state = head_state_at(record, contact_ns - before * 1e6)
        if state is None:
            return math.nan
        position, direction = state
        direction = _turned(direction, path_deg=path, attack_deg=attack)
        position = position + np.array([0.0, 0.0, depth])
        return _projection(ops + np.asarray(ops_shift) / 1000.0, position, direction)

    state = head_state_at(record, contact_ns - before_ms * 1e6)
    if state is None:
        return refused("no camera head track position at the club reading")
    position, direction = state
    core = evaluate_full_club_speed(
        radial_mph=radial,
        head_position_m=position + 0.0,
        direction=direction,
        ops_position_m=ops,
        direction_source="camera_head_track",
    )
    reading_record = {
        "window_start_ms": float(reading["window_start_ms"]),
        "window_centre_ms": centre_ms,
        "window_ms": window_ms,
        "radial_mph": radial,
        "time_before_contact_ms": before_ms,
        "impact_ms": float(impact_ms),
        "impact_source": impact_source,
        "impact_walk_back_ms": walk_back_ms,
        "clock": ops_track.get("clock", "ops_capture"),
        "camera_contact_ns": contact_ns,
        "contact_timing": timing
        or {
            "status": (
                "ops_transition"
                if impact_source == "ops_transition"
                else "legacy_travel_only_unqualified"
            )
        },
        "physical_timing_qualified": False,
    }
    if core["status"] != CALCULATED:
        return refused(core["reason"], projection=core.get("projection"), reading=reading_record)
    projection = float(core["projection"])
    value = radial / projection

    def value_for(proj: float) -> float:
        return radial / proj if math.isfinite(proj) and proj > 0.0 else math.nan

    def spread(*values: float) -> float:
        finite = [abs(v - value) for v in values if math.isfinite(v)]
        return max(finite) if finite and len(finite) == len(values) else math.inf

    time_sigma_ms = math.hypot(
        float(ops_track.get("impact_uncertainty_ms") or 0.0),
        float(record.get("contact_sigma_ms") or 0.0),
    )
    path_sigma = float(record.get("club_path_sigma_deg") or 0.0)
    attack_sigma = float(record.get("attack_angle_sigma_deg") or 0.0)
    depth_m = TOLERANCES["tee_range_m"]
    ops_mm = TOLERANCES["ops_position_mm"]
    resolution = float(ops_track.get("radial_resolution_mph") or DEFAULT_RADIAL_RESOLUTION_MPH)
    window_mean = float(
        np.mean(
            [
                projection_at(before_ms + offset)
                for offset in np.linspace(-0.5 * window_ms, 0.5 * window_ms, 9)
            ]
        )
    )

    def ops_axis(axis: int, sign: float) -> tuple:
        return tuple(sign * ops_mm * (axis == k) for k in range(3))

    budget = {
        "radial_resolution": resolution / projection,
        "reading_time": spread(
            value_for(projection_at(max(before_ms - time_sigma_ms, 0.0))),
            value_for(projection_at(before_ms + time_sigma_ms)),
        ),
        "fft_window": abs(projection / window_mean - 1.0) * value,
        "club_path": spread(
            value_for(projection_at(before_ms, path=path_sigma)),
            value_for(projection_at(before_ms, path=-path_sigma)),
        ),
        "attack_angle": spread(
            value_for(projection_at(before_ms, attack=attack_sigma)),
            value_for(projection_at(before_ms, attack=-attack_sigma)),
        ),
        "head_depth": spread(
            value_for(projection_at(before_ms, depth=depth_m)),
            value_for(projection_at(before_ms, depth=-depth_m)),
        ),
        "ops_position": math.sqrt(
            sum(
                spread(
                    value_for(projection_at(before_ms, ops_shift=ops_axis(axis, 1.0))),
                    value_for(projection_at(before_ms, ops_shift=ops_axis(axis, -1.0))),
                )
                ** 2
                for axis in range(3)
            )
        ),
    }
    half_width = math.sqrt(sum(term**2 for term in budget.values()))
    bounded = math.isfinite(half_width)
    return {
        **base,
        "status": EXPERIMENTAL,
        "value_mph": value,
        "range_mph": [value - half_width, value + half_width] if bounded else None,
        "range_basis": "root-sum-square of one-at-a-time changes for the stated tolerances"
        if bounded
        else "unbounded: an input moved by its tolerance leaves the geometry",
        "uncertainty_mph": {
            name: term if math.isfinite(term) else None for name, term in budget.items()
        },
        "tolerances": {
            "reading_time_ms": time_sigma_ms,
            "club_path_deg": path_sigma,
            "attack_angle_deg": attack_sigma,
            "head_depth_m": depth_m,
            "ops_position_mm": ops_mm,
            "radial_resolution_mph": resolution,
        },
        "direction_source": "camera_head_track",
        "note": CLUB_HEAD_TRACK_NOTE,
        "reason": None,
        "projection": projection,
        "referred_to": "the reading's time",
        "reading": reading_record,
        "geometry_m": {
            "frame": "(lateral + target-right, up, forward along the unit boresight) from the "
            "camera's optical centre, level",
            "ops": ops.tolist(),
            "head_at_reading": [float(v) for v in position],
            "direction_at_reading": [float(v) for v in direction],
        },
        "unmodelled": list(CLUB_UNMODELLED),
    }


# ---------------------------------------------------------------- smash


_FULL_SPEED_LABELS = (CALCULATED, LABEL_MODEL_GEOMETRY, ESTIMATED, EXPERIMENTAL)


def _is_full(speed: Any) -> bool:
    return (
        isinstance(speed, dict)
        and speed.get("status") in _FULL_SPEED_LABELS
        and _finite(speed.get("value_mph"))
    )


def evaluate_full_smash(ball_full: Any, club_full: Any) -> dict:
    """D37 smash factor from two full speeds; not measured otherwise (D21).

    A full ball speed over a radial club speed is never computed: it would be a
    ratio of two different kinds of speed. The ratio takes the weaker speed's label
    (measured > calculated > model geometry > estimated > experimental).
    The ball must be referred to launch/separation, the clubhead to impact;
    an earlier reading-time club speed is not an impact-speed observation.
    """
    base = {"status": NOT_MEASURED, "value": None, "source": SMASH_SOURCE}
    if not _is_full(ball_full):
        return {**base, "reason": "the ball speed is not a full speed (D21)"}
    if not _is_full(club_full):
        return {
            **base,
            "reason": "the club speed is not a full speed: it is the OPS radial reading (D21)",
        }
    if ball_full.get("referred_to") not in ("launch", "separation"):
        return {**base, "reason": "the full ball speed is not referred to launch/separation"}
    if club_full.get("referred_to") != "impact":
        return {**base, "reason": "the full club speed is not referred to impact"}
    if club_full.get("reference_point") != "clubhead":
        return {**base, "reason": "the full impact speed has no bound clubhead reference point"}
    value = smash_factor(ball_full["value_mph"], club_full["value_mph"])
    if value is None or not math.isfinite(value):
        return {**base, "reason": "no ratio is defined for these speeds"}
    label = weakest_label(ball_full["status"], club_full["status"])
    weak = [
        f"{name} full speed is {speed['status']}"
        for name, speed in (("the ball", ball_full), ("the club", club_full))
        if speed["status"] != CALCULATED
    ]
    return {
        **base,
        "status": label,
        "value": value,
        "reason": None,
        "note": "; ".join(weak) or None,
    }

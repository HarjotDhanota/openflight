"""Camera and radar validate each other inside the box (P8-4, D14, D15; fixed box 13 Oct).

**The radar's ball** is a magnitude change and a coherent change that agree with
each other (within ``AGREEMENT_SIGMAS`` of their combined uncertainty, and never
closer than ``RADAR_METHODS_MIN_TOLERANCE_BINS`` bins). Neither method alone is the
ball: on harjot-indoor-test-1 the coherent difference took the door at 1.575 m and
the magnitude change sat 17 deg above the array; on harjot-indoor-test-2 the door
was the coherent difference's strongest peak and the ball its 4th. A candidate at
a dominant still reflector (range_evidence.dominant_still_reflectors) is never the
ball. When a capture has no per-channel means the coherent method cannot run; the
magnitude change then stands with the camera alone, and says so.

**The camera's ball** gives the direction (its ray) and a sanity check (its size,
about 21 %). Once the unit's camera tilt is calibrated its ray distance is the
camera's main distance. A radar pair must lie inside the window the camera's ray
spans across the box's depth (``ray_window_m``) and agree with the camera's
distance; the distance comes from the radar's coherent peak, the side offset from
the camera's ray at that distance.

Whatever exists is saved, labelled experimental (D15). **No ball**: a camera that
looked and found no ball finishes as "no ball: put the ball in the box", and never
falls back to a radar peak. Only a camera that could not look at all lets a radar
pair stand alone (``radar_only_unconfirmed``), and only inside the box's range
extent. The camera never gives the distance (D17).
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

import numpy as np

DECISION_SCHEMA = "openflight.tester_patch_ball.v2"
# every pairing decision a saved setup may carry: v1 before the fixed box
DECISION_SCHEMAS = frozenset({"openflight.tester_patch_ball.v1", DECISION_SCHEMA})
AGREEMENT_SIGMAS = 2.0
# the radar's range bin at the setup profiles' resolution (128-point FFT over 6 m)
RANGE_BIN_M = 0.046875
# the two radar methods' centroids differ by up to two bins on a real ball (a loss in
# the next bin pulls the magnitude's); within this many bins they agree
RADAR_METHODS_MIN_TOLERANCE_BINS = 2.5
NO_BALL_MESSAGE = "No ball: put the ball in the box."
CAMERA_STATUSES = ("found", "no_ball", "ambiguous", "unavailable")
AMBIGUOUS_MESSAGE = (
    "More than one ball-like thing is in the box. Keep only the ball in the box; the setup "
    "saves no distance."
)
CAMERA_UNAVAILABLE_MESSAGE = (
    "The camera could not look for the ball (the picture was too dark or too bright), and "
    "the radar alone does not confirm a ball inside the box: no distance is saved."
)
# where a saved range came from, as the swing server's --iwr6843-tee-range-source
SOURCES = {
    "static_iwr_coherent_magnitude": "patch_validated_static_iwr_coherent_magnitude",
    "static_iwr_magnitude": "patch_validated_static_iwr_magnitude",
    "static_iwr_coherent_magnitude_unconfirmed": "unqualified_static_iwr_coherent_magnitude",
    # setups saved before the fixed box
    "static_iwr_coherent": "patch_validated_static_iwr_coherent",
    "static_iwr_magnitude_unconfirmed": "unqualified_static_iwr_magnitude",
    "static_iwr_coherent_unconfirmed": "unqualified_static_iwr_coherent",
}


def pairing_policy() -> dict[str, Any]:
    return {
        "schema": DECISION_SCHEMA,
        "radar_ball": "magnitude_and_coherent_changes_that_agree",
        "radar_methods_tolerance": {
            "sigmas": AGREEMENT_SIGMAS,
            "minimum_bins": RADAR_METHODS_MIN_TOLERANCE_BINS,
            "bin_m": RANGE_BIN_M,
        },
        "radar_ball_range": "coherent_peak_with_half_the_methods_disagreement_in_quadrature",
        "never_the_ball": [
            "a_dominant_still_reflector",
            "a_coherent_change_off_the_ground",
            "a_radar_pair_outside_the_camera_ray_window",
        ],
        "coherent_unavailable": "magnitude_with_the_camera_labelled",
        "camera_distance": "ray_once_the_tilt_is_calibrated_else_size_as_a_sanity_check",
        "agreement": "absolute_residual_within_sigmas_of_combined_uncertainty",
        "agreement_sigmas": AGREEMENT_SIGMAS,
        "best_pair": "smallest_normalized_residual_then_radar_score",
        "distance": "radar",
        "side_offset": "camera_ray_at_the_radar_distance",
        "saves": {
            "validated": "radar_distance",
            "camera_only": "nothing_with_a_warning",
            "disagree": "nothing_with_a_warning_naming_both",
            "no_ball": "nothing_never_a_radar_fallback",
            "ambiguous": "nothing_keep_only_the_ball_in_the_box",
            "camera_unavailable": "nothing_unless_an_agreed_radar_pair_inside_the_box",
            "radar_only_unconfirmed": (
                "an_agreed_radar_pair_inside_the_box_range_only_when_the_camera_could_not_look"
            ),
        },
        "label": "experimental",
    }


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def camera_ball(selected: Any, camera: Any) -> dict | None:
    """The camera's ball for pairing: its pixel, size distance, side offset and ray."""
    if selected is None:
        return None
    get = (
        selected.get
        if isinstance(selected, Mapping)
        else lambda name, default=None: getattr(selected, name, default)
    )
    range_m = _finite(get("size_radar_range_m"))
    uncertainty = _finite(get("floor_range_uncertainty_m")) or _finite(
        get("size_range_uncertainty_m")
    )
    if range_m is None or uncertainty is None or uncertainty <= 0.0:
        return None
    radar = [float(value) for value in camera.radar_origin_lfu]
    point = get("size_point_lfu_m")
    side = float(point[0]) - radar[0] if point is not None else None
    ray = np.asarray(
        camera.ray_model.rays(np.asarray([float(get("x_px")), float(get("y_px"))])), dtype=float
    )
    return {
        "x_px": float(get("x_px")),
        "y_px": float(get("y_px")),
        "diameter_px": float(get("diameter_px")),
        "range_m": range_m,
        "uncertainty_m": uncertainty,
        "side_offset_m": side,
        "ray_lfu": [float(value) for value in ray],
        "camera_origin_lfu": [float(value) for value in camera.camera_origin_lfu],
        "radar_origin_lfu": radar,
    }


def side_offset_at(ball: Mapping, radar_range_m: float) -> float | None:
    """The ball's side offset from the radar's axis, on the camera's ray at the radar's distance."""
    ray = ball.get("ray_lfu")
    if ray is None:
        return ball.get("side_offset_m")
    ray = np.asarray(ray, dtype=float)
    camera = np.asarray(ball["camera_origin_lfu"], dtype=float)
    radar = np.asarray(ball["radar_origin_lfu"], dtype=float)
    offset = camera - radar
    along = float(np.dot(ray, offset))
    discriminant = along * along - (float(np.dot(offset, offset)) - radar_range_m**2)
    if discriminant < 0.0:
        return ball.get("side_offset_m")
    distance = -along + math.sqrt(discriminant)
    point = camera + distance * ray
    return float(point[0] - radar[0])


def _radar_facts(item: Mapping) -> dict:
    keys = (
        "method",
        "candidate",
        "range_m",
        "uncertainty_m",
        "score",
        "fractional_excess",
        "elevation_deg",
        "ground_level",
        "width_bins",
        "still_reflector",
        "warnings",
    )
    return {key: item.get(key) for key in keys}


def _usable(candidates: Sequence[Mapping]) -> tuple[list[dict], list[dict]]:
    """The candidates that may be the ball, and those that never are, with why."""
    usable, rejected = [], []
    for item in candidates:
        range_m = _finite(item.get("range_m"))
        uncertainty = _finite(item.get("uncertainty_m"))
        reason = None
        if range_m is None or uncertainty is None or uncertainty <= 0.0:
            reason = "no_range"
        elif item.get("still_reflector"):
            reason = "still_reflector"
        elif item.get("method") == "coherent" and item.get("ground_level") is False:
            reason = "coherent_off_the_ground"
        if reason is None:
            usable.append(dict(item))
        else:
            rejected.append({**_radar_facts(item), "reason": reason})
    return usable, rejected


def _methods_tolerance(magnitude: Mapping, coherent: Mapping) -> float:
    combined = math.hypot(float(magnitude["uncertainty_m"]), float(coherent["uncertainty_m"]))
    return max(AGREEMENT_SIGMAS * combined, RADAR_METHODS_MIN_TOLERANCE_BINS * RANGE_BIN_M)


def radar_pairs(candidates: Sequence[Mapping], *, coherent_available: bool) -> list[dict[str, Any]]:
    """Every radar ball: a magnitude and a coherent change that agree, best agreement first.

    Without the coherent method (no per-channel means) each magnitude change is
    one, labelled ``magnitude_only``.
    """
    magnitudes = [item for item in candidates if item.get("method") == "magnitude"]
    coherents = [item for item in candidates if item.get("method") == "coherent"]
    pairs = []
    if not coherent_available:
        for item in magnitudes:
            pairs.append(
                {
                    "source": "static_iwr_magnitude",
                    "range_m": float(item["range_m"]),
                    "uncertainty_m": float(item["uncertainty_m"]),
                    "score": float(item.get("score") or 0.0),
                    "magnitude": _radar_facts(item),
                    "coherent": None,
                    "methods_residual_m": None,
                }
            )
        return pairs
    for coherent in coherents:
        for magnitude in magnitudes:
            residual = abs(float(magnitude["range_m"]) - float(coherent["range_m"]))
            if residual > _methods_tolerance(magnitude, coherent):
                continue
            pairs.append(
                {
                    "source": "static_iwr_coherent_magnitude",
                    "range_m": float(coherent["range_m"]),
                    "uncertainty_m": math.hypot(float(coherent["uncertainty_m"]), residual / 2.0),
                    "score": float(coherent.get("score") or 0.0),
                    "magnitude": _radar_facts(magnitude),
                    "coherent": _radar_facts(coherent),
                    "methods_residual_m": residual,
                }
            )
    pairs.sort(key=lambda pair: (pair["methods_residual_m"] or 0.0, -pair["score"]))
    return pairs


def _camera_distance(camera: Mapping) -> tuple[float, float, str]:
    ray = _finite(camera.get("ray_range_m"))
    ray_uncertainty = _finite(camera.get("ray_uncertainty_m"))
    if ray is not None and ray_uncertainty is not None and ray_uncertainty > 0.0:
        return ray, ray_uncertainty, "ray_calibrated_tilt"
    return float(camera["range_m"]), float(camera["uncertainty_m"]), "size_sanity_check"


def _inside(range_m: float, window: Any) -> bool:
    if window is None:
        return True
    low, high = (float(value) for value in window)
    return low <= range_m <= high


def pair_patch_ball(  # pylint: disable=too-many-return-statements,too-many-locals
    camera: Mapping | None,
    radar_candidates: Sequence[Mapping],
    *,
    camera_status: str | None = None,
    coherent_available: bool | None = None,
    box_range_m: Sequence[float] | None = None,
) -> dict:
    """The ball in the box, from the camera's ball and the radar's candidates.

    ``camera_status`` says whether the camera looked: ``found`` (``camera`` is its
    ball), ``no_ball`` (it looked and found none: the default without a ball),
    ``ambiguous`` (more than one ball-like thing) or ``unavailable`` (it could not
    look: too dark or too bright). ``coherent_available`` says whether the
    coherent method ran (inferred from the candidates when not given);
    ``box_range_m`` is the box's radar range extent, for a radar-only save.
    """
    status_in = camera_status or ("found" if camera is not None else "no_ball")
    if status_in not in CAMERA_STATUSES:
        raise ValueError(f"unknown camera status: {status_in}")
    if coherent_available is None:
        coherent_available = any(item.get("method") == "coherent" for item in radar_candidates)
    usable, rejected = _usable(radar_candidates)
    window = camera.get("ray_window_m") if isinstance(camera, Mapping) else None
    if camera is not None and window is not None:
        inside = []
        for item in usable:
            if _inside(float(item["range_m"]), window):
                inside.append(item)
            else:
                rejected.append({**_radar_facts(item), "reason": "outside_the_camera_ray_window"})
        usable = inside
    pairs = radar_pairs(usable, coherent_available=bool(coherent_available))
    base = {
        "schema": DECISION_SCHEMA,
        "label": "experimental",
        "policy": pairing_policy(),
        "camera": dict(camera) if camera is not None else None,
        "camera_status": status_in,
        "coherent_available": bool(coherent_available),
        "radar_candidates": [_radar_facts(item) for item in radar_candidates],
        "rejected": rejected,
        "radar_pairs": pairs,
        "weighed": [],
        "pair": None,
        "camera_distance": None,
    }
    nothing = {
        "source": None,
        "range_m": None,
        "uncertainty_m": None,
        "side_offset_m": None,
        "radar_candidate": None,
    }
    if camera is not None and status_in == "found":
        cam_range, cam_uncertainty, basis = _camera_distance(camera)
        base["camera_distance"] = basis
        weighed = []
        for pair in pairs:
            residual = abs(pair["range_m"] - cam_range)
            combined = math.hypot(pair["uncertainty_m"], cam_uncertainty)
            weighed.append(
                {
                    "radar_source": pair["source"],
                    "radar_range_m": pair["range_m"],
                    "residual_m": residual,
                    "combined_uncertainty_m": combined,
                    "normalized_residual": residual / combined,
                    "agrees": residual <= AGREEMENT_SIGMAS * combined,
                    "score": pair["score"],
                    "camera_range_m": cam_range,
                    "camera_distance": basis,
                }
            )
        base["weighed"] = weighed
        agreeing = [
            (entry, pair) for entry, pair in zip(weighed, pairs, strict=True) if entry["agrees"]
        ]
        if agreeing:
            entry, pair = min(
                agreeing, key=lambda item: (item[0]["normalized_residual"], -item[1]["score"])
            )
            range_m = pair["range_m"]
            warning = (
                None
                if coherent_available
                else "The coherent radar method could not run on these captures; only the "
                "magnitude change and the camera agree on the ball."
            )
            return {
                **base,
                "status": "validated",
                "warning": warning,
                "source": pair["source"],
                "range_m": range_m,
                "uncertainty_m": pair["uncertainty_m"],
                "side_offset_m": side_offset_at(camera, range_m),
                "pair": {**entry, "radar": pair},
                "radar_candidate": pair["coherent"] or pair["magnitude"],
            }
        if not pairs:
            magnitudes = [item for item in usable if item.get("method") == "magnitude"]
            coherents = [item for item in usable if item.get("method") == "coherent"]
            if magnitudes or coherents:
                found = ", ".join(
                    f"{item['method']} {float(item['range_m']):.2f} m"
                    for item in [*magnitudes[:2], *coherents[:2]]
                )
                why = f"the radar's two methods did not agree on a ball ({found})"
            else:
                why = "the radar found no ball"
            return {
                **base,
                **nothing,
                "status": "camera_only",
                "warning": (
                    f"The camera sees the ball, but {why}. The camera never gives the "
                    "distance, so the setup saves no distance."
                ),
            }
        best = pairs[0]
        return {
            **base,
            **nothing,
            "status": "disagree",
            "warning": (
                f"The camera puts the ball at {cam_range:.2f} m and the radar reads "
                f"{best['range_m']:.2f} m: they disagree. Check for other objects in the box. "
                "The setup saves no distance."
            ),
            "radar_candidate": best["coherent"] or best["magnitude"],
        }
    if status_in == "unavailable":
        inside = [
            pair
            for pair in pairs
            if pair["source"] == "static_iwr_coherent_magnitude"
            and box_range_m is not None
            and _inside(pair["range_m"], box_range_m)
        ]
        if inside:
            best = max(inside, key=lambda pair: pair["score"])
            return {
                **base,
                "status": "radar_only_unconfirmed",
                "warning": (
                    f"The camera could not look for the ball; the radar's magnitude and "
                    f"coherent changes agree at {best['range_m']:.2f} m inside the box. It is "
                    "used, unconfirmed."
                ),
                "source": "static_iwr_coherent_magnitude_unconfirmed",
                "range_m": best["range_m"],
                "uncertainty_m": best["uncertainty_m"],
                "side_offset_m": None,
                "radar_candidate": best["coherent"],
            }
    if status_in == "unavailable":
        return {
            **base,
            **nothing,
            "status": "camera_unavailable",
            "warning": CAMERA_UNAVAILABLE_MESSAGE,
        }
    if status_in == "ambiguous":
        return {**base, **nothing, "status": "ambiguous", "warning": AMBIGUOUS_MESSAGE}
    return {**base, **nothing, "status": "no_ball", "warning": NO_BALL_MESSAGE}

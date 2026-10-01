"""Camera and radar validate each other inside the patch (P8-4, D14, D15).

After both finish, each radar candidate inside the patch's window is weighed
against the camera's ball: they agree when their distances are within
``AGREEMENT_SIGMAS`` of their combined uncertainty (the camera's from the ball's
apparent size, the radar's from its bin or cluster width and the range bias). The
ball is the best-agreeing pair. Its distance is the radar's and its side offset
the camera's, taken along the camera's ray at the radar's distance.

Whatever exists is saved, labelled experimental (D15: measure, label, don't
block): a pair is ``validated``; the camera's ball alone gives its own distance
with a warning; the radar alone, or two sensors that disagree, give a warning
that names both. With nothing in the patch the setup says so.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

import numpy as np

DECISION_SCHEMA = "openflight.tester_patch_ball.v1"
AGREEMENT_SIGMAS = 2.0
NO_BALL_MESSAGE = (
    "No ball found in the patch. The ball may be outside it: move the ball or the patch."
)
# where a saved range came from, as the swing server's --iwr6843-tee-range-source
SOURCES = {
    "static_iwr_magnitude": "patch_validated_static_iwr_magnitude",
    "static_iwr_coherent": "patch_validated_static_iwr_coherent",
    "camera_size_range": "unqualified_camera_size_range",
    "static_iwr_magnitude_unconfirmed": "unqualified_static_iwr_magnitude",
    "static_iwr_coherent_unconfirmed": "unqualified_static_iwr_coherent",
}


def pairing_policy() -> dict[str, Any]:
    return {
        "schema": DECISION_SCHEMA,
        "agreement": "absolute_residual_within_sigmas_of_combined_uncertainty",
        "agreement_sigmas": AGREEMENT_SIGMAS,
        "camera_uncertainty": "apparent_size_focal_length_and_fit",
        "radar_uncertainty": "bin_or_cluster_half_width_and_range_bias",
        "best_pair": "smallest_normalized_residual_then_radar_score",
        "distance": "radar",
        "side_offset": "camera_ray_at_the_radar_distance",
        "coherent_off_the_ground": "never_the_ball",
        "saves": {
            "validated": "radar_distance",
            "camera_only": "camera_distance_with_a_warning",
            "disagree": "camera_distance_with_a_warning_naming_both",
            "radar_only": "strongest_radar_peak_with_a_warning",
            "no_ball": "nothing",
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


def _pairable(candidates: Sequence[Mapping]) -> list[dict]:
    usable = []
    for item in candidates:
        if item.get("method") == "coherent" and item.get("ground_level") is False:
            continue
        range_m = _finite(item.get("range_m"))
        uncertainty = _finite(item.get("uncertainty_m"))
        if range_m is None or uncertainty is None or uncertainty <= 0.0:
            continue
        usable.append(dict(item))
    return usable


def _strongest(candidates: Sequence[Mapping]) -> dict:
    """The radar's own best without the camera: a coherent peak first, by score."""
    return max(
        candidates,
        key=lambda item: (
            item.get("method") == "coherent",
            bool(item.get("selector_accepted")),
            float(item.get("score") or 0.0),
        ),
    )


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
        "warnings",
    )
    return {key: item.get(key) for key in keys}


def pair_patch_ball(  # pylint: disable=too-many-return-statements
    camera: Mapping | None, radar_candidates: Sequence[Mapping]
) -> dict:
    """The ball in the patch, from the camera's ball and the radar's candidates."""
    radar = _pairable(radar_candidates)
    base = {
        "schema": DECISION_SCHEMA,
        "label": "experimental",
        "policy": pairing_policy(),
        "camera": dict(camera) if camera is not None else None,
        "radar_candidates": [_radar_facts(item) for item in radar_candidates],
        "weighed": [],
        "pair": None,
    }
    if camera is not None:
        weighed = []
        for item in radar:
            residual = abs(float(item["range_m"]) - float(camera["range_m"]))
            combined = math.hypot(float(item["uncertainty_m"]), float(camera["uncertainty_m"]))
            weighed.append(
                {
                    "radar_method": item["method"],
                    "radar_candidate": item.get("candidate"),
                    "radar_range_m": float(item["range_m"]),
                    "residual_m": residual,
                    "combined_uncertainty_m": combined,
                    "normalized_residual": residual / combined,
                    "agrees": residual <= AGREEMENT_SIGMAS * combined,
                    "score": item.get("score"),
                }
            )
        base["weighed"] = weighed
        agreeing = [
            (entry, item) for entry, item in zip(weighed, radar, strict=True) if entry["agrees"]
        ]
        if agreeing:
            entry, item = min(
                agreeing,
                key=lambda pair: (
                    pair[0]["normalized_residual"],
                    -float(pair[1].get("score") or 0),
                ),
            )
            range_m = float(item["range_m"])
            return {
                **base,
                "status": "validated",
                "warning": None,
                "source": f"static_iwr_{item['method']}",
                "range_m": range_m,
                "uncertainty_m": float(item["uncertainty_m"]),
                "side_offset_m": side_offset_at(camera, range_m),
                "pair": {**entry, "radar": _radar_facts(item)},
                "radar_candidate": _radar_facts(item),
            }
        if not radar:
            return {
                **base,
                "status": "camera_only",
                "warning": (
                    "The radar found no ball in the patch, so the camera's distance "
                    f"({camera['range_m']:.2f} m, about ±{camera['uncertainty_m']:.2f} m from "
                    "the ball's size) is used."
                ),
                "source": "camera_size_range",
                "range_m": float(camera["range_m"]),
                "uncertainty_m": float(camera["uncertainty_m"]),
                "side_offset_m": camera.get("side_offset_m"),
                "radar_candidate": None,
            }
        strongest = _strongest(radar)
        return {
            **base,
            "status": "disagree",
            "warning": (
                f"The camera puts the ball at {camera['range_m']:.2f} m and the radar at "
                f"{float(strongest['range_m']):.2f} m: they disagree. Check for other objects "
                "in the patch. The camera's distance is used."
            ),
            "source": "camera_size_range",
            "range_m": float(camera["range_m"]),
            "uncertainty_m": float(camera["uncertainty_m"]),
            "side_offset_m": camera.get("side_offset_m"),
            "radar_candidate": _radar_facts(strongest),
        }
    if radar:
        strongest = _strongest(radar)
        return {
            **base,
            "status": "radar_only",
            "warning": (
                f"The camera did not find the ball in the patch; the radar's "
                f"{float(strongest['range_m']):.2f} m is used, unconfirmed."
            ),
            "source": f"static_iwr_{strongest['method']}_unconfirmed",
            "range_m": float(strongest["range_m"]),
            "uncertainty_m": float(strongest["uncertainty_m"]),
            "side_offset_m": None,
            "radar_candidate": _radar_facts(strongest),
        }
    return {
        **base,
        "status": "no_ball",
        "warning": NO_BALL_MESSAGE,
        "source": None,
        "range_m": None,
        "uncertainty_m": None,
        "side_offset_m": None,
        "radar_candidate": None,
    }

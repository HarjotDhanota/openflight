"""Offline checkerboard intrinsic calibration with held-out validation."""

from __future__ import annotations

import math
import string
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

from ..rig_geometry import geometry_fingerprint

PROFILE_VERSION = 1
ARTIFACT_VERSION = 1
MIN_FIT_VIEWS = 10
MIN_VALIDATION_VIEWS = 3
_IDENTITY_FIELDS = ("camera_id", "lens_id", "focus_id")
_MAPPING_FIELDS = (
    "native_sensor_crop",
    "scaler_crop",
    "driver_vertical_offset_px",
    "sensor_output_mapping",
    "saved_image_mapping",
)


def _reject_unknown(mapping: Mapping[str, Any], allowed: set[str], name: str) -> None:
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        raise ValueError(f"{name} has unknown fields: {', '.join(unknown)}")


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return int(value)


def _finite_positive(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, np.number)):
        raise ValueError(f"{name} must be a positive finite number")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{name} must be a positive finite number")
    return result


def _nonempty_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _sha256(value: Any, name: str) -> str:
    value = _nonempty_string(value, name).lower()
    if len(value) != 64 or any(character not in string.hexdigits for character in value):
        raise ValueError(f"{name} must be a 64-character hexadecimal SHA-256")
    return value


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def validate_mode_profile(profile: Mapping[str, Any]) -> dict[str, Any]:
    """Validate, normalize and fingerprint an explicitly declared capture mode."""
    profile = _mapping(profile, "mode_profile")
    _reject_unknown(
        profile,
        {
            "version",
            "camera_id",
            "lens_id",
            "focus_id",
            "sensor_output",
            "saved_image",
            "crop_readout_mapping",
            "sha256",
        },
        "mode_profile",
    )
    if profile.get("version") != PROFILE_VERSION:
        raise ValueError(f"mode_profile.version must be {PROFILE_VERSION}")
    normalized: dict[str, Any] = {"version": PROFILE_VERSION}
    for field in _IDENTITY_FIELDS:
        normalized[field] = _nonempty_string(profile.get(field), f"mode_profile.{field}")

    sensor = _mapping(profile.get("sensor_output"), "mode_profile.sensor_output")
    _reject_unknown(
        sensor, {"width", "height", "bit_depth", "raw_format", "mode_id"}, "sensor_output"
    )
    normalized["sensor_output"] = {
        "width": _positive_int(sensor.get("width"), "sensor_output.width"),
        "height": _positive_int(sensor.get("height"), "sensor_output.height"),
        "bit_depth": _positive_int(sensor.get("bit_depth"), "sensor_output.bit_depth"),
        "raw_format": _nonempty_string(sensor.get("raw_format"), "sensor_output.raw_format"),
        "mode_id": _nonempty_string(sensor.get("mode_id"), "sensor_output.mode_id"),
    }
    saved = _mapping(profile.get("saved_image"), "mode_profile.saved_image")
    _reject_unknown(saved, {"width", "height", "stream", "rotate_180", "mirror"}, "saved_image")
    rotate_180 = saved.get("rotate_180")
    mirror = saved.get("mirror")
    if not isinstance(rotate_180, bool) or not isinstance(mirror, bool):
        raise ValueError("saved_image.rotate_180 and saved_image.mirror must be booleans")
    normalized["saved_image"] = {
        "width": _positive_int(saved.get("width"), "saved_image.width"),
        "height": _positive_int(saved.get("height"), "saved_image.height"),
        "stream": _nonempty_string(saved.get("stream"), "saved_image.stream"),
        "rotate_180": rotate_180,
        "mirror": mirror,
    }
    mapping = _mapping(profile.get("crop_readout_mapping"), "mode_profile.crop_readout_mapping")
    _reject_unknown(mapping, set(_MAPPING_FIELDS), "crop_readout_mapping")
    missing = [field for field in _MAPPING_FIELDS if field not in mapping]
    if missing:
        raise ValueError(f"crop_readout_mapping missing explicit fields: {', '.join(missing)}")
    normalized["crop_readout_mapping"] = {field: mapping[field] for field in _MAPPING_FIELDS}
    try:
        normalized["sha256"] = geometry_fingerprint(normalized)
    except (TypeError, ValueError) as exc:
        raise ValueError("mode_profile must contain finite JSON values") from exc
    supplied_hash = profile.get("sha256")
    if supplied_hash is not None and supplied_hash != normalized["sha256"]:
        raise ValueError("mode_profile.sha256 does not match its parameters")
    return normalized


def _validate_board(board: Mapping[str, Any]) -> dict[str, Any]:
    board = _mapping(board, "board")
    _reject_unknown(
        board,
        {
            "inner_corners_columns",
            "inner_corners_rows",
            "square_size_mm",
            "square_size_uncertainty_mm",
            "board_id",
        },
        "board",
    )
    columns = _positive_int(board.get("inner_corners_columns"), "board.inner_corners_columns")
    rows = _positive_int(board.get("inner_corners_rows"), "board.inner_corners_rows")
    if columns < 3 or rows < 3:
        raise ValueError("board inner_columns and inner_rows must each be at least 3")
    result: dict[str, Any] = {
        "inner_corners_columns": columns,
        "inner_corners_rows": rows,
        "square_size_mm": _finite_positive(board.get("square_size_mm"), "board.square_size_mm"),
    }
    if "square_size_uncertainty_mm" in board and board["square_size_uncertainty_mm"] is not None:
        result["square_size_uncertainty_mm"] = _finite_positive(
            board["square_size_uncertainty_mm"], "board.square_size_uncertainty_mm"
        )
    if "board_id" in board and board["board_id"] is not None:
        result["board_id"] = _nonempty_string(board["board_id"], "board.board_id")
    return result


def _object_points(board: Mapping[str, Any]) -> np.ndarray:
    columns, rows = board["inner_corners_columns"], board["inner_corners_rows"]
    points = np.zeros((columns * rows, 3), np.float32)
    column_grid, row_grid = np.meshgrid(np.arange(columns), np.arange(rows))
    points[:, :2] = np.column_stack((column_grid.ravel(), row_grid.ravel()))
    points *= board["square_size_mm"]
    return points


def _percentile(values: Sequence[float], percentile: float) -> float:
    return float(np.percentile(np.asarray(values, dtype=float), percentile))


def _residual_summary(errors: np.ndarray) -> dict[str, Any]:
    errors = np.asarray(errors, dtype=float).reshape(-1, 2)
    radial = np.linalg.norm(errors, axis=1)
    return {
        "rms_px": float(np.sqrt(np.mean(np.square(radial)))),
        "median_px": _percentile(radial, 50),
        "p90_px": _percentile(radial, 90),
        "max_px": float(np.max(radial)),
        "signed_bias_x_px": float(np.mean(errors[:, 0])),
        "signed_bias_y_px": float(np.mean(errors[:, 1])),
    }


def _coverage(corners: np.ndarray, image_size: tuple[int, int]) -> dict[str, Any]:
    width, height = image_size
    minimum = np.min(corners, axis=0)
    maximum = np.max(corners, axis=0)
    return {
        "bounds_px": {
            "min_x": float(minimum[0]),
            "min_y": float(minimum[1]),
            "max_x": float(maximum[0]),
            "max_y": float(maximum[1]),
        },
        "bounds_fraction": {
            "min_x": float(minimum[0] / width),
            "min_y": float(minimum[1] / height),
            "max_x": float(maximum[0] / width),
            "max_y": float(maximum[1] / height),
        },
    }


def _validate_observations(
    observations: Sequence[Mapping[str, Any]],
    *,
    board: Mapping[str, Any],
    profile: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], tuple[int, int]]:
    if isinstance(observations, (str, bytes)) or not isinstance(observations, Sequence):
        raise ValueError("observations must be a sequence")
    expected_count = board["inner_corners_columns"] * board["inner_corners_rows"]
    expected_size = (profile["saved_image"]["width"], profile["saved_image"]["height"])
    seen_ids: set[str] = set()
    seen_content: set[str] = set()
    group_splits: dict[str, str] = {}
    normalized = []
    for index, raw in enumerate(observations):
        item = _mapping(raw, f"observations[{index}]")
        view_id = _nonempty_string(item.get("id"), f"observations[{index}].id")
        split = item.get("split")
        if split not in {"fit", "validation"}:
            raise ValueError(f"observation {view_id} split must be fit or validation")
        if view_id in seen_ids:
            raise ValueError(f"duplicate observation id: {view_id}")
        seen_ids.add(view_id)
        group_id = _nonempty_string(item.get("group_id"), f"observations[{index}].group_id")
        previous_split = group_splits.setdefault(group_id, split)
        if previous_split != split:
            raise ValueError(f"observation group {group_id} spans fit and validation splits")
        if item.get("profile_sha256") != profile["sha256"]:
            raise ValueError(f"observation {view_id} has a different mode profile")
        status = item.get("status", "detected")
        record: dict[str, Any] = {
            "id": view_id,
            "split": split,
            "group_id": group_id,
            "profile_sha256": profile["sha256"],
        }
        if status == "failed":
            image_hash = item.get("image_sha256")
            if image_hash is not None:
                image_hash = _sha256(image_hash, f"observation {view_id} image_sha256")
                if image_hash in seen_content:
                    raise ValueError(f"duplicate image pixel content: {image_hash}")
                seen_content.add(image_hash)
                record["image_sha256"] = image_hash
            if item.get("image_size") is not None:
                size = item["image_size"]
                if (
                    not isinstance(size, Sequence)
                    or isinstance(size, (str, bytes))
                    or len(size) != 2
                ):
                    raise ValueError(f"observation {view_id} image_size must be [width, height]")
                actual_size = (
                    _positive_int(size[0], "image width"),
                    _positive_int(size[1], "image height"),
                )
                if actual_size != expected_size:
                    raise ValueError(
                        f"observation {view_id} dimensions do not match the mode profile"
                    )
                record["image_size"] = list(actual_size)
            record.update(
                status="failed",
                reason=_nonempty_string(item.get("reason"), f"observation {view_id} reason"),
            )
            normalized.append(record)
            continue
        if status != "detected":
            raise ValueError(f"observation {view_id} has invalid status")
        image_hash = _sha256(item.get("image_sha256"), f"observation {view_id} image_sha256")
        if image_hash in seen_content:
            raise ValueError(f"duplicate image pixel content: {image_hash}")
        seen_content.add(image_hash)
        size = item.get("image_size")
        if not isinstance(size, Sequence) or isinstance(size, (str, bytes)) or len(size) != 2:
            raise ValueError(f"observation {view_id} image_size must be [width, height]")
        actual_size = (
            _positive_int(size[0], "image width"),
            _positive_int(size[1], "image height"),
        )
        if actual_size != expected_size:
            raise ValueError(f"observation {view_id} dimensions do not match the mode profile")
        record.update(image_sha256=image_hash, image_size=list(actual_size))
        corners = np.asarray(item.get("corners"), dtype=float)
        if corners.shape != (expected_count, 2) or not np.all(np.isfinite(corners)):
            raise ValueError(
                f"observation {view_id} corners must be finite {expected_count}x2 points"
            )
        if np.ptp(corners[:, 0]) <= 0 or np.ptp(corners[:, 1]) <= 0:
            raise ValueError(f"observation {view_id} corners are degenerate")
        singular_values = np.linalg.svd(corners - np.mean(corners, axis=0), compute_uv=False)
        if singular_values[1] <= singular_values[0] * 1e-6:
            raise ValueError(f"observation {view_id} corners are degenerate")
        if np.any(corners[:, 0] < 0) or np.any(corners[:, 0] >= actual_size[0]):
            raise ValueError(f"observation {view_id} corners fall outside image width")
        if np.any(corners[:, 1] < 0) or np.any(corners[:, 1] >= actual_size[1]):
            raise ValueError(f"observation {view_id} corners fall outside image height")
        record.update(status="detected", corners=corners.astype(np.float32))
        normalized.append(record)
    return normalized, expected_size


def calibrate_checkerboard(
    *,
    board: Mapping[str, Any],
    mode_profile: Mapping[str, Any],
    observations: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Fit intrinsics from fit views and score frozen intrinsics on validation views."""
    try:
        import cv2
    except ImportError as exc:  # pragma: no cover - depends on optional camera extra
        raise RuntimeError("OpenCV is required for optical calibration") from exc
    opencv_error = getattr(cv2, "error")

    checked_board = _validate_board(board)
    profile = validate_mode_profile(mode_profile)
    views, image_size = _validate_observations(observations, board=checked_board, profile=profile)
    fit_views = [view for view in views if view["split"] == "fit" and view["status"] == "detected"]
    validation_views = [
        view for view in views if view["split"] == "validation" and view["status"] == "detected"
    ]
    fit_groups = {view["group_id"] for view in fit_views}
    validation_groups = {view["group_id"] for view in validation_views}
    if len(fit_groups) < MIN_FIT_VIEWS:
        raise ValueError(f"at least {MIN_FIT_VIEWS} detected fit groups are required")
    if len(validation_groups) < MIN_VALIDATION_VIEWS:
        raise ValueError(f"at least {MIN_VALIDATION_VIEWS} detected validation groups are required")

    object_points = _object_points(checked_board)
    flags = (
        getattr(cv2, "CALIB_FIX_K4") | getattr(cv2, "CALIB_FIX_K5") | getattr(cv2, "CALIB_FIX_K6")
    )
    _, camera_matrix, distortion, rvecs, tvecs = getattr(cv2, "calibrateCamera")(
        [object_points] * len(fit_views),
        [view["corners"] for view in fit_views],
        image_size,
        None,
        None,
        flags=flags,
    )
    distortion = distortion.reshape(-1)[:5]
    if (
        camera_matrix.shape != (3, 3)
        or distortion.size != 5
        or not np.all(np.isfinite(camera_matrix))
        or not np.all(np.isfinite(distortion))
        or camera_matrix[0, 0] <= 0
        or camera_matrix[1, 1] <= 0
    ):
        raise ValueError("calibration produced invalid camera parameters")

    evaluated: dict[str, dict[str, Any]] = {}
    fit_normals = []
    fit_distances = []
    for view, rvec, tvec in zip(fit_views, rvecs, tvecs):
        if not np.all(np.isfinite(rvec)) or not np.all(np.isfinite(tvec)):
            raise ValueError("calibration produced a non-finite fit pose")
        projected, _ = getattr(cv2, "projectPoints")(
            object_points, rvec, tvec, camera_matrix, distortion
        )
        if not np.all(np.isfinite(projected)):
            raise ValueError("calibration produced non-finite fit projections")
        residual = projected.reshape(-1, 2) - view["corners"]
        rotation, _ = getattr(cv2, "Rodrigues")(rvec)
        fit_normals.append(rotation[:, 2])
        fit_distances.append(float(np.linalg.norm(tvec)))
        evaluated[view["id"]] = {
            **{key: value for key, value in view.items() if key != "corners"},
            "status": "evaluated",
            "residuals": _residual_summary(residual),
            "coverage": _coverage(view["corners"], image_size),
            "pose": {
                "rotation_vector": np.asarray(rvec).reshape(3).astype(float).tolist(),
                "translation_mm": np.asarray(tvec).reshape(3).astype(float).tolist(),
            },
        }

    validation_success_groups: set[str] = set()
    for view in validation_views:
        failure_reason = None
        try:
            success, rvec, tvec = getattr(cv2, "solvePnP")(
                object_points, view["corners"], camera_matrix, distortion
            )
            if not success:
                failure_reason = "pose solve failed with frozen intrinsics"
            elif not np.all(np.isfinite(rvec)) or not np.all(np.isfinite(tvec)):
                failure_reason = "pose solve produced non-finite values"
            else:
                projected, _ = getattr(cv2, "projectPoints")(
                    object_points, rvec, tvec, camera_matrix, distortion
                )
                if not np.all(np.isfinite(projected)):
                    failure_reason = "pose projection produced non-finite values"
        except opencv_error:  # pylint: disable=catching-non-exception
            failure_reason = "pose solve raised an OpenCV error"
        if failure_reason is not None:
            evaluated[view["id"]] = {
                **{key: value for key, value in view.items() if key != "corners"},
                "status": "failed",
                "reason": failure_reason,
                "coverage": _coverage(view["corners"], image_size),
            }
            continue
        residual = projected.reshape(-1, 2) - view["corners"]
        validation_success_groups.add(view["group_id"])
        evaluated[view["id"]] = {
            **{key: value for key, value in view.items() if key != "corners"},
            "status": "evaluated",
            "residuals": _residual_summary(residual),
            "coverage": _coverage(view["corners"], image_size),
            "pose": {
                "rotation_vector": np.asarray(rvec).reshape(3).astype(float).tolist(),
                "translation_mm": np.asarray(tvec).reshape(3).astype(float).tolist(),
            },
        }

    output_views = []
    for view in views:
        output_views.append(
            evaluated.get(
                view["id"], {key: value for key, value in view.items() if key != "corners"}
            )
        )
    fit_results = [
        view["residuals"]
        for view in output_views
        if view["split"] == "fit" and view["status"] == "evaluated"
    ]
    validation_results = [
        view["residuals"]
        for view in output_views
        if view["split"] == "validation" and view["status"] == "evaluated"
    ]
    warnings = []
    normal_angles = []
    reference = np.asarray(fit_normals[0])
    for normal in fit_normals[1:]:
        cosine = float(np.clip(np.dot(reference, normal), -1.0, 1.0))
        normal_angles.append(math.degrees(math.acos(cosine)))
    if normal_angles and max(normal_angles) < 10.0:
        warnings.append("fit board orientations span less than 10 degrees")
    if max(fit_distances) / min(fit_distances) < 1.15:
        warnings.append("fit board distances span less than a 1.15 ratio")

    def aggregate(
        results: list[dict[str, Any]],
        *,
        attempted: int,
        detected: int,
        attempted_groups: int,
        detected_groups: int,
        evaluated_groups: int,
    ) -> dict[str, Any]:
        values = [result["rms_px"] for result in results]
        biases_x = [result["signed_bias_x_px"] for result in results]
        biases_y = [result["signed_bias_y_px"] for result in results]
        return {
            "attempted_view_count": attempted,
            "detected_view_count": detected,
            "evaluated_view_count": len(results),
            "attempted_group_count": attempted_groups,
            "detected_group_count": detected_groups,
            "evaluated_group_count": evaluated_groups,
            "rms_px": float(np.sqrt(np.mean(np.square(values)))),
            "median_view_rms_px": _percentile(values, 50),
            "p90_view_rms_px": _percentile(values, 90),
            "max_px": float(max(result["max_px"] for result in results)),
            "signed_bias_x_px": float(np.mean(biases_x)),
            "signed_bias_y_px": float(np.mean(biases_y)),
        }

    artifact_status = (
        "candidate" if len(validation_success_groups) >= MIN_VALIDATION_VIEWS else "failed"
    )
    return {
        "version": ARTIFACT_VERSION,
        "status": artifact_status,
        "accuracy_qualified": False,
        "quality_gate": "not_defined",
        "mode_binding": {
            "status": "unverified",
            "reason": (
                "captures do not establish native crop, driver offset, or stable hardware binding"
            ),
        },
        "board": checked_board,
        "mode_profile": profile,
        "mode_profile_sha256": profile["sha256"],
        "opencv_version": getattr(cv2, "__version__"),
        "camera_matrix": camera_matrix.tolist(),
        "distortion_model": "opencv_brown_5",
        "distortion_convention": {
            "coefficient_order": ["k1", "k2", "p1", "p2", "k3"],
            "coordinates": "OpenCV normalized camera coordinates",
        },
        "distortion_coefficients": {
            name: float(value) for name, value in zip(("k1", "k2", "p1", "p2", "k3"), distortion)
        },
        "fit": aggregate(
            fit_results,
            attempted=sum(view["split"] == "fit" for view in views),
            detected=len(fit_views),
            attempted_groups=len({view["group_id"] for view in views if view["split"] == "fit"}),
            detected_groups=len(fit_groups),
            evaluated_groups=len(fit_groups),
        ),
        "validation": aggregate(
            validation_results,
            attempted=sum(view["split"] == "validation" for view in views),
            detected=len(validation_views),
            attempted_groups=len(
                {view["group_id"] for view in views if view["split"] == "validation"}
            ),
            detected_groups=len(validation_groups),
            evaluated_groups=len(
                {
                    view["group_id"]
                    for view in output_views
                    if view["split"] == "validation" and view["status"] == "evaluated"
                }
            ),
        )
        if validation_results
        else None,
        "views": output_views,
        "residual_interpretation": {
            "sign": "projected_minus_observed",
            "units": "saved_image_pixels",
            "per_view_percentiles": "corner radial reprojection errors",
            "aggregate_percentiles": "per-view RMS reprojection errors",
            "validation_pose": "fitted separately on each held-out checkerboard view",
            "limitation": "not an independent pose or metric-accuracy measurement",
        },
        "pose_diversity": {
            "max_normal_angle_deg_from_first": float(max(normal_angles, default=0.0)),
            "distance_ratio": float(max(fit_distances) / min(fit_distances)),
            "warnings": warnings,
        },
    }

"""Offline projection through a declared optical-calibration candidate."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ..rig_geometry import geometry_fingerprint
from .geometry import intersect_radar_range_sphere
from .optical_calibration import ARTIFACT_VERSION, validate_mode_profile

_BROWN_ORDER = ("k1", "k2", "p1", "p2", "k3")

# OpenCV's Python extension members are not visible to Pylint. The inspector is
# intentionally branchy because every contradicted capture field must remain explicit.
# pylint: disable=no-member,catching-non-exception,bad-exception-cause
# pylint: disable=too-many-branches,too-many-return-statements,too-many-arguments
# pylint: disable=too-many-positional-arguments


def _load_json(value: Mapping[str, Any] | str | Path) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    try:
        loaded = json.loads(Path(value).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("calibration artifact must be readable JSON") from exc
    if not isinstance(loaded, Mapping):
        raise ValueError("calibration artifact must be an object")
    return loaded


def _camera_matrix(value: Any) -> np.ndarray:
    matrix = np.asarray(value, dtype=float)
    if matrix.shape != (3, 3) or not np.all(np.isfinite(matrix)):
        raise ValueError("camera_matrix must be a finite 3x3 matrix")
    canonical = np.array(
        [[matrix[0, 0], 0.0, matrix[0, 2]], [0.0, matrix[1, 1], matrix[1, 2]], [0, 0, 1]],
        dtype=float,
    )
    if matrix[0, 0] <= 0 or matrix[1, 1] <= 0 or not np.array_equal(matrix, canonical):
        raise ValueError("camera_matrix must be canonical with positive focal lengths")
    return matrix


def _proper_rotation(value: Any) -> np.ndarray:
    raw = np.asarray(value)
    if raw.dtype.kind == "b":
        raise ValueError("optical_to_world_lfu must not contain booleans")
    rotation = np.asarray(value, dtype=float)
    if rotation.shape != (3, 3) or not np.all(np.isfinite(rotation)):
        raise ValueError("optical_to_world_lfu must be a finite 3x3 matrix")
    if not np.allclose(rotation.T @ rotation, np.eye(3), rtol=0.0, atol=1e-9):
        raise ValueError("optical_to_world_lfu must be orthonormal")
    if not math.isclose(float(np.linalg.det(rotation)), 1.0, rel_tol=0.0, abs_tol=1e-9):
        raise ValueError("optical_to_world_lfu must be a proper rotation")
    return rotation


@dataclass(frozen=True)
class CalibratedProjection:
    """Validated Brown-five intrinsics bound to one saved-image mode."""

    camera_matrix: np.ndarray
    distortion_coefficients: np.ndarray
    mode_profile: Mapping[str, Any]

    @property
    def image_size(self) -> tuple[int, int]:
        """Return calibrated saved-image width and height."""
        saved = self.mode_profile["saved_image"]
        return int(saved["width"]), int(saved["height"])

    def pixel_rays_lfu(self, pixels_px: Any, *, optical_to_world_lfu: Any) -> np.ndarray:
        """Return unit LFU rays for pixels stored in the calibrated saved-image orientation."""
        try:
            import cv2
        except ImportError as exc:  # pragma: no cover - optional camera dependency
            raise RuntimeError("OpenCV is required for calibrated projection") from exc

        pixels = np.asarray(pixels_px, dtype=float)
        scalar = pixels.shape == (2,)
        if scalar:
            pixels = pixels[None, :]
        if pixels.ndim != 2 or pixels.shape[1] != 2 or not np.all(np.isfinite(pixels)):
            raise ValueError("pixels must be finite x/y pairs")
        width, height = self.image_size
        if np.any(pixels[:, 0] < 0) or np.any(pixels[:, 0] >= width):
            raise ValueError("pixel x coordinate is outside the calibrated saved image")
        if np.any(pixels[:, 1] < 0) or np.any(pixels[:, 1] >= height):
            raise ValueError("pixel y coordinate is outside the calibrated saved image")

        criteria = (cv2.TERM_CRITERIA_COUNT | cv2.TERM_CRITERIA_EPS, 50, 1e-12)
        try:
            if hasattr(cv2, "undistortPointsIter"):
                normalized = cv2.undistortPointsIter(
                    pixels.reshape(-1, 1, 2),
                    self.camera_matrix,
                    self.distortion_coefficients,
                    None,
                    None,
                    criteria,
                ).reshape(-1, 2)
            else:
                normalized = cv2.undistortPoints(
                    pixels.reshape(-1, 1, 2),
                    self.camera_matrix,
                    self.distortion_coefficients,
                    criteria=criteria,
                ).reshape(-1, 2)
        except cv2.error as exc:
            raise ValueError("Brown distortion inversion failed") from exc
        if not np.all(np.isfinite(normalized)):
            raise ValueError("Brown distortion inversion produced non-finite coordinates")
        reprojection = _brown_forward(normalized, self.camera_matrix, self.distortion_coefficients)
        residual = np.linalg.norm(reprojection - pixels, axis=1)
        if np.any(~np.isfinite(residual)) or np.any(residual > 1e-6):
            raise ValueError("Brown distortion inversion did not converge")

        if np.any(_brown_jacobian_determinant(normalized, self.distortion_coefficients) <= 0):
            raise ValueError("Brown distortion is locally folded at the requested pixel")

        physical = normalized.copy()
        saved = self.mode_profile["saved_image"]
        if saved["mirror"]:
            physical[:, 0] *= -1
        if saved["rotate_180"]:
            physical *= -1
        rays_rdf = np.column_stack((physical, np.ones(len(physical))))
        rays_lfu = rays_rdf @ _proper_rotation(optical_to_world_lfu).T
        norms = np.linalg.norm(rays_lfu, axis=1, keepdims=True)
        if np.any(~np.isfinite(norms)) or np.any(norms <= 0):
            raise ValueError("calibrated camera ray is invalid")
        result = rays_lfu / norms
        return result[0] if scalar else result

    def reconstruct_at_radar_range(
        self,
        pixels_px: Any,
        radar_range_m: float,
        *,
        optical_to_world_lfu: Any,
        camera_origin_lfu: Any,
        radar_origin_lfu: Any,
    ) -> np.ndarray:
        """Intersect calibrated rays with a radar-centered range sphere."""
        return intersect_radar_range_sphere(
            self.pixel_rays_lfu(pixels_px, optical_to_world_lfu=optical_to_world_lfu),
            radar_range_m,
            camera_origin_lfu=np.asarray(camera_origin_lfu, dtype=float),
            radar_origin_lfu=np.asarray(radar_origin_lfu, dtype=float),
        )


def _brown_forward(points: np.ndarray, matrix: np.ndarray, coefficients: np.ndarray) -> np.ndarray:
    x_coord, y_coord = points[:, 0], points[:, 1]
    k1, k2, p1, p2, k3 = coefficients
    radius2 = x_coord * x_coord + y_coord * y_coord
    radial = 1.0 + k1 * radius2 + k2 * radius2**2 + k3 * radius2**3
    distorted_x = x_coord * radial + 2 * p1 * x_coord * y_coord + p2 * (radius2 + 2 * x_coord**2)
    distorted_y = y_coord * radial + p1 * (radius2 + 2 * y_coord**2) + 2 * p2 * x_coord * y_coord
    return np.column_stack(
        (matrix[0, 0] * distorted_x + matrix[0, 2], matrix[1, 1] * distorted_y + matrix[1, 2])
    )


def _brown_jacobian_determinant(points: np.ndarray, coefficients: np.ndarray) -> np.ndarray:
    """Return the local normalized-coordinate area scale of Brown distortion."""
    x_coord, y_coord = points[:, 0], points[:, 1]
    k1, k2, p1, p2, k3 = coefficients
    radius2 = x_coord**2 + y_coord**2
    radial_derivative = k1 + 2 * k2 * radius2 + 3 * k3 * radius2**2
    radial = 1 + k1 * radius2 + k2 * radius2**2 + k3 * radius2**3
    dx_dx = radial + 2 * x_coord**2 * radial_derivative + 2 * p1 * y_coord + 6 * p2 * x_coord
    dx_dy = 2 * x_coord * y_coord * radial_derivative + 2 * p1 * x_coord + 2 * p2 * y_coord
    dy_dx = 2 * x_coord * y_coord * radial_derivative + 2 * p1 * x_coord + 2 * p2 * y_coord
    dy_dy = radial + 2 * y_coord**2 * radial_derivative + 6 * p1 * y_coord + 2 * p2 * x_coord
    return dx_dx * dy_dy - dx_dy * dy_dx


def load_calibration_candidate(
    artifact: Mapping[str, Any] | str | Path, *, mode_profile: Mapping[str, Any]
) -> CalibratedProjection:
    """Load a candidate only when its explicit profile exactly matches the supplied profile."""
    artifact = _load_json(artifact)
    if "candidate" in artifact:
        if artifact.get("version") != 1 or not isinstance(artifact.get("candidate"), Mapping):
            raise ValueError("calibration candidate wrapper is invalid")
        candidate = artifact["candidate"]
        candidate_profile = candidate.get("mode_profile")
        if not isinstance(candidate_profile, Mapping):
            raise ValueError("wrapped calibration candidate lacks a mode profile")
        if (
            artifact.get("mode_profile_sha256")
            != validate_mode_profile(candidate_profile)["sha256"]
        ):
            raise ValueError("calibration candidate wrapper profile fingerprint mismatch")
        artifact = candidate
    if artifact.get("version") != ARTIFACT_VERSION:
        raise ValueError(f"calibration artifact version must be {ARTIFACT_VERSION}")
    if artifact.get("status") != "candidate":
        raise ValueError("calibration artifact status must be candidate")
    if artifact.get("distortion_model") != "opencv_brown_5":
        raise ValueError("calibration artifact must use opencv_brown_5")
    convention = artifact.get("distortion_convention")
    if not isinstance(convention, Mapping) or convention.get("coefficient_order") != list(
        _BROWN_ORDER
    ):
        raise ValueError("Brown coefficient order must be k1,k2,p1,p2,k3")
    stored_profile = validate_mode_profile(artifact.get("mode_profile"))
    supplied_profile = validate_mode_profile(mode_profile)
    if stored_profile != supplied_profile:
        raise ValueError("supplied mode_profile does not exactly match the calibration profile")
    if artifact.get("mode_profile_sha256") != stored_profile["sha256"]:
        raise ValueError("calibration mode profile fingerprint mismatch")
    values = artifact.get("distortion_coefficients")
    if not isinstance(values, Mapping) or set(values) != set(_BROWN_ORDER):
        raise ValueError("distortion_coefficients must contain exactly the Brown-five coefficients")
    distortion = np.asarray([values[name] for name in _BROWN_ORDER], dtype=float)
    if distortion.shape != (5,) or not np.all(np.isfinite(distortion)):
        raise ValueError("distortion coefficients must be finite")
    return CalibratedProjection(
        _camera_matrix(artifact.get("camera_matrix")), distortion, stored_profile
    )


@dataclass(frozen=True)
class CaptureCompatibility:
    """Conservative comparison of capture evidence with one calibration profile."""

    status: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class CalibratedCameraModel:
    """One optical candidate placed explicitly in the target LFU frame."""

    projection: CalibratedProjection
    optical_to_world_lfu: np.ndarray
    camera_origin_lfu: np.ndarray
    radar_origin_lfu: np.ndarray
    snapshot: Mapping[str, Any]

    def rays(self, pixels_px: Any) -> np.ndarray:
        return self.projection.pixel_rays_lfu(
            pixels_px, optical_to_world_lfu=self.optical_to_world_lfu
        )

    def reconstruct(self, pixels_px: Any, radar_range_m: float) -> np.ndarray:
        return self.projection.reconstruct_at_radar_range(
            pixels_px,
            radar_range_m,
            optical_to_world_lfu=self.optical_to_world_lfu,
            camera_origin_lfu=self.camera_origin_lfu,
            radar_origin_lfu=self.radar_origin_lfu,
        )


def _vector(value: Any, name: str) -> np.ndarray:
    vector = np.asarray(value, dtype=float)
    if vector.shape != (3,) or not np.all(np.isfinite(vector)):
        raise ValueError(f"{name} must be a finite LFU three-vector")
    return vector


def _axis_rotation(axis: str, degrees: float) -> np.ndarray:
    angle = math.radians(float(degrees))
    sine, cosine = math.sin(angle), math.cos(angle)
    if axis == "pitch":
        return np.array([[1, 0, 0], [0, cosine, -sine], [0, sine, cosine]], dtype=float)
    return np.array([[cosine, 0, sine], [0, 1, 0], [-sine, 0, cosine]], dtype=float)


def _inclination_rotation(pitch_deg: float, roll_deg: float) -> np.ndarray:
    if not -90.0 < pitch_deg < 90.0 or not -90.0 < roll_deg < 90.0:
        raise ValueError("pitch and roll inclinations must be upright between -90 and 90 degrees")
    gravity_x = math.sin(math.radians(roll_deg))
    gravity_y = math.sin(math.radians(pitch_deg))
    remaining = 1.0 - gravity_x * gravity_x - gravity_y * gravity_y
    if remaining <= 0.0:
        raise ValueError("pitch and roll inclination pair is physically impossible")
    gravity_z = math.sqrt(remaining)
    return _axis_rotation("pitch", math.degrees(math.asin(gravity_y))) @ _axis_rotation(
        "roll", math.degrees(math.atan2(-gravity_x, gravity_z))
    )


def build_calibrated_camera_model(
    artifact: Mapping[str, Any],
    placement: Mapping[str, Any],
    *,
    observed_pitch_deg: float | None = None,
    observed_roll_deg: float | None = None,
) -> CalibratedCameraModel:
    """Validate and compose mount, target alignment, origins, and observed pose."""
    if placement.get("schema") != "openflight.camera.placement" or placement.get("version") != 1:
        raise ValueError("camera placement must use openflight.camera.placement v1")
    if placement.get("world_frame") != "target_lfu":
        raise ValueError("camera placement world_frame must be target_lfu")
    if (
        not isinstance(placement.get("rig_geometry_sha256"), str)
        or not placement["rig_geometry_sha256"]
    ):
        raise ValueError("camera placement requires rig_geometry_sha256")
    provenance = placement.get("origin_provenance")
    if not isinstance(provenance, Mapping) or any(
        not isinstance(provenance.get(name), str) or not provenance[name]
        for name in ("camera_origin_lfu", "radar_origin_lfu", "enclosure_pivot_lfu")
    ):
        raise ValueError("camera placement requires explicit origin_provenance")
    tolerance = placement.get("rig_offset_consistency_tolerance_m")
    if (
        isinstance(tolerance, bool)
        or not isinstance(tolerance, (int, float))
        or not math.isfinite(float(tolerance))
        or tolerance < 0
    ):
        raise ValueError("camera placement requires a finite rig offset consistency tolerance")
    mode_profile = artifact.get("candidate", artifact).get("mode_profile")
    if not isinstance(mode_profile, Mapping):
        raise ValueError("optical calibration lacks a mode profile")
    projection = load_calibration_candidate(artifact, mode_profile=mode_profile)
    mount = _proper_rotation(placement.get("optical_to_enclosure_lfu"))
    alignment = _proper_rotation(placement.get("enclosure_to_target_lfu"))
    camera = _vector(placement.get("camera_origin_lfu"), "camera_origin_lfu")
    radar = _vector(placement.get("radar_origin_lfu"), "radar_origin_lfu")
    pivot = _vector(placement.get("enclosure_pivot_lfu"), "enclosure_pivot_lfu")
    reference = placement.get("reference_pose_deg")
    if not isinstance(reference, Mapping):
        raise ValueError("camera placement requires reference_pose_deg")
    try:
        if isinstance(reference.get("pitch"), bool) or isinstance(reference.get("roll"), bool):
            raise ValueError
        reference_pitch = float(reference.get("pitch"))
        reference_roll = float(reference.get("roll"))
    except (TypeError, ValueError) as error:
        raise ValueError("camera placement reference pose must be finite") from error
    if not np.all(np.isfinite((reference_pitch, reference_roll))):
        raise ValueError("camera placement reference pose must be finite")
    if observed_pitch_deg is None or observed_roll_deg is None:
        raise ValueError("calibrated camera fusion requires observed pitch and roll")
    if isinstance(observed_pitch_deg, bool) or isinstance(observed_roll_deg, bool):
        raise ValueError("observed camera placement pose must be finite")
    pitch = float(observed_pitch_deg)
    roll = float(observed_roll_deg)
    if not np.all(np.isfinite((pitch, roll))):
        raise ValueError("observed camera placement pose must be finite")
    reference_rotation = _inclination_rotation(reference_pitch, reference_roll)
    observed_rotation = _inclination_rotation(pitch, roll)
    delta = alignment @ reference_rotation.T @ observed_rotation @ alignment.T
    camera = pivot + delta @ (camera - pivot)
    radar = pivot + delta @ (radar - pivot)
    rotation = delta @ alignment @ mount
    frozen = {
        "schema": "openflight.camera.calibrated_model",
        "version": 1,
        "artifact": deepcopy(dict(artifact)),
        "placement": deepcopy(dict(placement)),
        "observed_pose_deg": {"pitch": pitch, "roll": roll},
        "accuracy_qualified": False,
    }
    frozen["sha256"] = geometry_fingerprint(frozen)
    return CalibratedCameraModel(projection, rotation, camera, radar, frozen)


def calibrated_camera_model_from_snapshot(snapshot: Mapping[str, Any]) -> CalibratedCameraModel:
    """Restore a frozen model without consulting current calibration files."""
    payload = dict(snapshot)
    fingerprint = payload.pop("sha256", None)
    if payload.get("schema") != "openflight.camera.calibrated_model" or payload.get("version") != 1:
        raise ValueError("unsupported calibrated camera model snapshot")
    if fingerprint != geometry_fingerprint(payload):
        raise ValueError("calibrated camera model fingerprint mismatch")
    pose = payload.get("observed_pose_deg") or {}
    return build_calibrated_camera_model(
        payload["artifact"],
        payload["placement"],
        observed_pitch_deg=pose.get("pitch"),
        observed_roll_deg=pose.get("roll"),
    )


def inspect_capture_compatibility(
    capture_mode: Mapping[str, Any],
    *,
    mode_profile: Mapping[str, Any],
    frame_count: int | None = None,
) -> CaptureCompatibility:
    """Reject contradictions while retaining unknown mode identity as unverified."""
    profile = validate_mode_profile(mode_profile)
    if not isinstance(capture_mode, Mapping):
        return CaptureCompatibility("unverified", ("capture_mode evidence is absent",))
    if "capture_mode" in capture_mode or "frame_count" in capture_mode:
        sidecar = capture_mode
        if frame_count is None and isinstance(sidecar.get("frame_count"), int):
            frame_count = sidecar["frame_count"]
        capture_mode = sidecar.get("capture_mode")
        if capture_mode is None:
            return CaptureCompatibility("unverified", ("capture_mode evidence is absent",))
        if not isinstance(capture_mode, Mapping):
            return CaptureCompatibility("incompatible", ("capture_mode evidence is malformed",))
    if capture_mode.get("version") != 1:
        return CaptureCompatibility("incompatible", ("unsupported capture_mode evidence",))
    contexts = capture_mode.get("contexts")
    frames = capture_mode.get("frames")
    if not isinstance(contexts, list) or not isinstance(frames, Mapping):
        return CaptureCompatibility("incompatible", ("malformed capture_mode evidence",))
    arrays = {
        name: frames.get(name)
        for name in (
            "context_index",
            "scaler_crop",
            "frame_duration_us",
            "saved_width",
            "saved_height",
        )
    }
    if any(not isinstance(value, list) for value in arrays.values()):
        return CaptureCompatibility("incompatible", ("missing per-frame capture evidence",))
    lengths = {len(value) for value in arrays.values()}
    if len(lengths) != 1 or (frame_count is not None and lengths != {frame_count}):
        return CaptureCompatibility("incompatible", ("per-frame capture evidence is misaligned",))

    reasons: list[str] = []
    incompatible: list[str] = []
    saved = profile["saved_image"]
    for index, (width, height) in enumerate(zip(arrays["saved_width"], arrays["saved_height"])):
        if (width, height) != (saved["width"], saved["height"]):
            incompatible.append(f"frame {index} saved dimensions differ from calibration")
    for index, context_index in enumerate(arrays["context_index"]):
        if context_index is None:
            reasons.append(f"frame {index} has no usable capture context")
        elif (
            not isinstance(context_index, int)
            or isinstance(context_index, bool)
            or not 0 <= context_index < len(contexts)
        ):
            incompatible.append(f"frame {index} capture context index is invalid")

    for index, context in enumerate(contexts):
        if not isinstance(context, Mapping) or not isinstance(context.get("startup"), Mapping):
            incompatible.append(f"capture context {index} is malformed")
            continue
        startup = context["startup"]
        encoded = json.dumps(startup, sort_keys=True, separators=(",", ":"))
        expected_id = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        if context.get("id") != expected_id:
            incompatible.append(f"capture context {index} fingerprint mismatch")
            continue
        settings = startup.get("settings")
        if not isinstance(settings, Mapping):
            reasons.append(f"capture context {index} lacks startup settings")
            continue
        comparisons = {
            "width": saved["width"],
            "height": saved["height"],
            "stream": saved["stream"],
            "rotate_180": saved["rotate_180"],
            "mirror_horizontal": saved["mirror"],
        }
        for field, expected in comparisons.items():
            if field not in settings:
                reasons.append(f"capture context {index} lacks {field}")
            elif settings[field] != expected:
                incompatible.append(f"capture context {index} {field} differs from calibration")
        _compare_resolved_sensor(startup, profile, index, reasons, incompatible)
        _compare_declared_mapping(
            startup, arrays["scaler_crop"], profile, index, reasons, incompatible
        )

    if incompatible:
        return CaptureCompatibility("incompatible", tuple(dict.fromkeys(incompatible + reasons)))
    reasons.extend(
        (
            "capture mode identity is not independently established",
            "calibration remains unqualified",
        )
    )
    return CaptureCompatibility("unverified", tuple(dict.fromkeys(reasons)))


def _compare_resolved_sensor(startup, profile, index, reasons, incompatible):
    resolved = startup.get("resolved_config")
    sensor = profile["sensor_output"]
    if not isinstance(resolved, Mapping):
        reasons.append(f"capture context {index} lacks resolved sensor configuration")
        return
    resolved_sensor = resolved.get("sensor")
    if not isinstance(resolved_sensor, Mapping):
        reasons.append(f"capture context {index} lacks resolved sensor output")
    else:
        size = resolved_sensor.get("output_size")
        if size in (None, []):
            reasons.append(f"capture context {index} lacks resolved sensor size")
        elif not isinstance(size, (list, tuple)) or list(size) != [
            sensor["width"],
            sensor["height"],
        ]:
            incompatible.append(
                f"capture context {index} resolved sensor size differs from calibration"
            )
        bit_depth = resolved_sensor.get("bit_depth")
        if bit_depth is None:
            reasons.append(f"capture context {index} lacks resolved bit depth")
        elif bit_depth != sensor["bit_depth"]:
            incompatible.append(f"capture context {index} bit depth differs from calibration")
    if not isinstance(resolved.get("raw"), Mapping):
        reasons.append(f"capture context {index} lacks resolved raw stream configuration")
        return
    raw = resolved["raw"]
    raw_format = raw.get("format")
    if raw_format is None:
        reasons.append(f"capture context {index} lacks resolved raw format")
    elif raw_format != sensor["raw_format"]:
        incompatible.append(f"capture context {index} raw format differs from calibration")


def _compare_declared_mapping(startup, crops, profile, index, reasons, incompatible):
    mapping = profile["crop_readout_mapping"]
    declared_crop = mapping["scaler_crop"]
    if declared_crop == "not_applicable_raw_stream":
        if profile["saved_image"]["stream"] != "raw" or any(crop is not None for crop in crops):
            incompatible.append("raw-stream scaler-crop declaration contradicts capture")
    elif declared_crop is None or isinstance(declared_crop, str):
        reasons.append("calibration scaler-crop mapping is unknown")
    else:
        settings = startup.get("settings", {})
        requested_crop = settings.get("scaler_crop") if isinstance(settings, Mapping) else None
        if requested_crop is not None and requested_crop != declared_crop:
            incompatible.append(f"capture context {index} scaler crop differs from calibration")
        elif requested_crop is None:
            reasons.append(f"capture context {index} lacks a requested scaler crop")
        if any(crop is not None and crop != declared_crop for crop in crops):
            incompatible.append("reported per-frame scaler crop differs from calibration")
    declared_offset = mapping["driver_vertical_offset_px"]
    driver_block = startup.get("driver")
    driver = driver_block.get("strip_y_offset") if isinstance(driver_block, Mapping) else None
    if not isinstance(driver, Mapping):
        driver = {}
    if (
        declared_offset is None
        or isinstance(declared_offset, str)
        or driver.get("value_px") is None
    ):
        reasons.append("driver parameter mapping is unknown")
    elif driver.get("value_px") != declared_offset:
        incompatible.append(
            f"capture context {index} startup driver parameter differs from calibration"
        )
    else:
        reasons.append("startup driver parameter does not prove an applied sensor offset")

"""Candidate optics for the impact fitter's isotropic pinhole image plane."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping

import cv2
import numpy as np

from openflight.rig_geometry import geometry_fingerprint

from .calibrated_projection import (
    CalibratedProjection,
    calibrated_camera_model_from_snapshot,
    inspect_capture_compatibility,
    load_calibration_candidate,
)
from .impact_location import ImpactCamera

# pylint: disable=no-member


def recorded_model(shot: Mapping, session_uuid: str, capture_sha256: str):
    """Read opted-in optics only from this shot's intact, capture-bound context."""
    if shot.get("calibrated_camera_status") == "rejected":
        raise ValueError("declared calibrated camera was rejected upstream")
    context = shot.get("camera_fusion_context") or {}
    if not isinstance(context, Mapping):
        raise ValueError("camera fusion context must be an object")
    geometry = context.get("geometry") or {}
    if not isinstance(geometry, Mapping) or not isinstance(geometry.get("parameters", {}), Mapping):
        raise ValueError("camera geometry must be an object")
    snapshot = geometry.get("parameters", {}).get("calibrated_model_snapshot")
    if snapshot is None:
        if shot.get("calibrated_camera_status") == "experimental_unqualified":
            raise ValueError("declared calibrated camera has no frozen model snapshot")
        return None
    payload = dict(context)
    fingerprint = payload.pop("sha256", None)
    number = shot.get("shot_number")
    if (
        payload.get("schema") != "openflight.camera.fusion_context"
        or payload.get("version") not in (1, 2)
        or payload.get("available") is not True
        or not isinstance(number, int)
        or isinstance(number, bool)
        or number <= 0
        or payload.get("shot_number") != number
        or not session_uuid
        or payload.get("session_uuid") != session_uuid
        or not capture_sha256
        or payload.get("capture_npz_sha256") != capture_sha256
    ):
        raise ValueError("calibrated context does not match this shot/session/archive")
    if fingerprint != geometry_fingerprint(payload):
        raise ValueError("calibrated context fingerprint mismatch")
    from .geometry_contract import EffectiveCameraGeometryInputs  # noqa: PLC0415

    EffectiveCameraGeometryInputs.from_recorded_session({"effective_camera_geometry": geometry})
    return calibrated_camera_model_from_snapshot(snapshot)


def check_mode(frames, metadata, artifact, *, research_override=False):
    """Check actual saved dimensions/orientation and the existing per-frame mode contract."""
    candidate = artifact.get("candidate", artifact)
    projection = load_calibration_candidate(artifact, mode_profile=candidate["mode_profile"])
    width, height = projection.image_size
    if frames.ndim != 3 or frames.shape[1:] != (height, width):
        raise ValueError("calibration saved dimensions differ from capture")
    settings = metadata.get("settings") or {}
    saved = projection.mode_profile["saved_image"]
    for field, profile_field in (
        ("mirror_horizontal", "mirror"),
        ("rotate_180", "rotate_180"),
        ("stream", "stream"),
        ("width", "width"),
        ("height", "height"),
    ):
        if field in settings and settings[field] != saved[profile_field]:
            raise ValueError(f"capture {field} differs from calibration")
    crop = projection.mode_profile["crop_readout_mapping"]["scaler_crop"]
    if isinstance(crop, list) and settings.get("scaler_crop") not in (None, crop):
        raise ValueError("capture scaler_crop differs from calibration")
    compatibility = inspect_capture_compatibility(
        metadata.get("capture_mode"), mode_profile=projection.mode_profile, frame_count=len(frames)
    )
    allowed = {
        "capture mode identity is not independently established",
        "calibration remains unqualified",
        "startup driver parameter does not prove an applied sensor offset",
    }
    if compatibility.status == "incompatible":
        raise ValueError("incompatible capture_mode: " + "; ".join(compatibility.reasons))
    if not research_override and any(reason not in allowed for reason in compatibility.reasons):
        raise ValueError("incomplete capture_mode: " + "; ".join(compatibility.reasons))
    return {"status": compatibility.status, "reasons": list(compatibility.reasons)}


@dataclass(frozen=True)
class RectifiedClip:
    """Frames and explicit saved/rectified pixel mappings; originals stay untouched."""

    frames: np.ndarray
    camera: ImpactCamera
    projection: CalibratedProjection
    decision: dict[str, Any]

    def points(self, saved_pixels):
        """Map saved distorted coordinates to the unmirrored optical pinhole view."""
        rays = self.projection.pixel_rays_lfu(saved_pixels, optical_to_world_lfu=np.eye(3))
        return rays[:, :2] / rays[:, 2:] * self.camera.focal_px + self.camera.principal_point_px

    def saved_points(self, rectified_pixels):
        """Map pinhole view coordinates back to saved distorted coordinates."""
        normalized = (
            np.asarray(rectified_pixels) - self.camera.principal_point_px
        ) / self.camera.focal_px
        saved = self.projection.mode_profile["saved_image"]
        normalized = normalized.copy()
        if saved["rotate_180"]:
            normalized *= -1
        if saved["mirror"]:
            normalized[:, 0] *= -1
        points = np.column_stack((normalized, np.ones(len(normalized))))
        projected, _ = cv2.projectPoints(
            points,
            np.zeros(3),
            np.zeros(3),
            self.projection.camera_matrix,
            self.projection.distortion_coefficients,
        )
        return projected.reshape(-1, 2)

    def binned_camera(self, factor):
        """Pixel centres after block averaging, including a possible truncated last block."""
        return replace(
            self.camera,
            focal_px=self.camera.focal_px / factor,
            principal_point_px=tuple(
                (np.asarray(self.camera.principal_point_px) + 0.5) / factor - 0.5
            ),
        )


def rectify(frames: np.ndarray, artifact: Mapping) -> RectifiedClip:
    """Resample a Brown candidate to equal fx/fy, without invented border pixels."""
    candidate = artifact.get("candidate", artifact)
    projection = load_calibration_candidate(artifact, mode_profile=candidate["mode_profile"])
    width, height = projection.image_size
    if frames.ndim != 3 or frames.shape[1:] != (height, width):
        raise ValueError("calibration saved dimensions differ from capture")
    matrix = projection.camera_matrix
    cx, cy = matrix[0, 2], matrix[1, 2]
    saved = projection.mode_profile["saved_image"]
    if saved["mirror"]:
        cx = width - 1 - cx
    if saved["rotate_180"]:
        cx, cy = width - 1 - cx, height - 1 - cy
    if not (0 < cx < width - 1 and 0 < cy < height - 1):
        raise ValueError("calibration principal point must lie inside the saved image")
    grid = np.indices((height, width), dtype=float)[::-1].reshape(2, -1).T
    base_focal = float(max(matrix[0, 0], matrix[1, 1]))
    for zoom in np.linspace(1, 2, 51):
        camera = ImpactCamera(
            base_focal * zoom, (float(cx), float(cy)), "calibrated_rectified_candidate"
        )
        result = RectifiedClip(frames, camera, projection, {})
        mapped = result.saved_points(grid)
        if np.isfinite(mapped).all() and (
            (mapped >= 0).all()
            and (mapped[:, 0] <= width - 1).all()
            and (mapped[:, 1] <= height - 1).all()
        ):
            break
    else:
        raise ValueError("candidate has no supported border-free pinhole view within 2x zoom")
    # The shared inverse rejects Brown folds and ambiguous/failed inverse rays.
    recovered = result.points(mapped)
    if not np.allclose(recovered, grid, atol=1e-5, rtol=0):
        raise ValueError("candidate rectification rays do not round-trip")
    maps = mapped.astype(np.float32).reshape(height, width, 2)
    rectified = np.stack(
        [cv2.remap(frame, maps[:, :, 0], maps[:, :, 1], cv2.INTER_LINEAR) for frame in frames]
    )
    decision = {
        "applied": True,
        "physical_qualified": False,
        "coordinate_frame": "rectified_optical_rdf",
        "artifact_sha256": geometry_fingerprint(artifact),
        "mode_profile_sha256": projection.mode_profile["sha256"],
        "focal_px": camera.focal_px,
        "principal_point_px": list(camera.principal_point_px),
        "zoom": float(zoom),
        "interpolation": "bilinear",
        "saved_image": dict(saved),
        "placement_applied": False,
        "note": "Candidate optics only; head/face geometry, timing and strike remain unqualified.",
    }
    return RectifiedClip(rectified, camera, projection, decision)

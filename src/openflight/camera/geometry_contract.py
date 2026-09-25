"""Immutable effective geometry inputs shared by live camera estimators and replay."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, replace
from typing import Any, Mapping

from ..rig_geometry import geometry_fingerprint
from .ball_flight import CameraBallGeometry
from .club_delivery import GOLF_BALL_DIAMETER_M, CameraDeliveryGeometry
from .geometry import forward_distance_from_slant_range

SCHEMA = "openflight.camera.effective_geometry"
VERSION = 1
ASSUMPTIONS = {
    "focal_length": "inferred_from_reference_ball",
    "pitch": "inferred_from_reference_ball",
    "principal_point": "image_center",
    "distortion_model": "none",
    "yaw": "not_calibrated",
    "camera_origin": "lens_front_vs_optical_center_unresolved",
    "world_frame": "lateral_forward_from_iwr_horizontal_origin_and_height_above_floor",
    "tee_lateral_position": "assumed_zero_directly_downrange",
    "crop_readout_mapping": "not_modeled",
    "snapshot_scope": "session_start_effective_scalar_inputs",
}


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number")
    try:
        result = float(value)
    except (OverflowError, TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a finite number") from error
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


def _integer(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a positive integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a positive integer") from error
    if result <= 0 or result != float(value):
        raise ValueError(f"{name} must be a positive integer")
    return result


@dataclass(frozen=True)
class EffectiveCameraGeometryInputs:
    """Immutable scalar geometry inputs currently consumed by both estimators."""

    camera_height_m: float
    radar_height_m: float
    tee_slant_range_m: float
    ball_height_m: float
    camera_lateral_offset_m: float
    camera_forward_offset_m: float
    image_width_px: int
    image_height_px: int
    horizontal_pixel_sign: float
    roll_correction_deg: float
    ball_horizontal_output_offset_deg: float
    ball_diameter_m: float
    calibrated_model_snapshot: Mapping[str, Any] | None = None
    calibrated_mode_evidence: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        numeric = (
            "camera_height_m",
            "radar_height_m",
            "tee_slant_range_m",
            "ball_height_m",
            "camera_lateral_offset_m",
            "camera_forward_offset_m",
            "horizontal_pixel_sign",
            "roll_correction_deg",
            "ball_horizontal_output_offset_deg",
            "ball_diameter_m",
        )
        for name in numeric:
            object.__setattr__(self, name, _number(getattr(self, name), name))
        object.__setattr__(self, "image_width_px", _integer(self.image_width_px, "image_width_px"))
        object.__setattr__(
            self, "image_height_px", _integer(self.image_height_px, "image_height_px")
        )
        if self.camera_height_m <= 0 or self.radar_height_m <= 0:
            raise ValueError("camera and radar heights must be positive")
        if self.ball_height_m < 0:
            raise ValueError("ball height must be non-negative")
        if self.ball_diameter_m <= 0:
            raise ValueError("ball diameter must be positive")
        if self.tee_slant_range_m <= 0:
            raise ValueError("tee slant range must be positive")
        vertical = self.ball_height_m - self.radar_height_m
        if self.tee_slant_range_m <= abs(vertical):
            raise ValueError("tee slant range cannot reach the recorded ball height")
        ball_forward_m = forward_distance_from_slant_range(self.tee_slant_range_m, vertical)
        if ball_forward_m <= self.camera_forward_offset_m:
            raise ValueError("the recorded ball is not in front of the camera")
        if self.horizontal_pixel_sign not in (-1.0, 1.0):
            raise ValueError("horizontal_pixel_sign must be -1 or 1")
        if self.calibrated_model_snapshot is not None:
            from .calibrated_projection import calibrated_camera_model_from_snapshot

            model = calibrated_camera_model_from_snapshot(self.calibrated_model_snapshot)
            evidence = self.calibrated_mode_evidence
            if not isinstance(evidence, Mapping):
                raise ValueError("calibrated geometry requires recorded mode evidence")
            candidate = model.snapshot["artifact"].get("candidate", model.snapshot["artifact"])
            if evidence.get("mode_profile_sha256") != candidate.get("mode_profile_sha256"):
                raise ValueError("calibrated mode evidence profile fingerprint mismatch")
            allowed = {
                "capture mode identity is not independently established",
                "calibration remains unqualified",
                "startup driver parameter does not prove an applied sensor offset",
            }
            reasons = evidence.get("reasons")
            if (
                evidence.get("status") != "unverified"
                or not isinstance(reasons, list)
                or any(reason not in allowed for reason in reasons)
            ):
                raise ValueError("calibrated mode evidence is incomplete or incompatible")

    def calibrated_model(self):
        """Restore the optional frozen calibrated projection and placement."""
        if self.calibrated_model_snapshot is None:
            return None
        from .calibrated_projection import calibrated_camera_model_from_snapshot

        return calibrated_camera_model_from_snapshot(self.calibrated_model_snapshot)

    def with_calibrated_model(self, snapshot: Mapping[str, Any], mode_evidence: Mapping[str, Any]):
        """Return the same scalar contract with an opt-in frozen calibrated model."""
        return replace(
            self,
            calibrated_model_snapshot=dict(snapshot),
            calibrated_mode_evidence=dict(mode_evidence),
        )

    @classmethod
    def from_live(cls, camera_config: Mapping[str, Any], calibration: Any):
        """Freeze inputs from applied camera settings and runtime IWR calibration."""
        return cls._from_sources(
            camera_config,
            {
                "tee_slant_range_m": calibration.tee_range_m,
                "radar_height_m": calibration.radar_height_m,
                "ball_height_m": calibration.tee_ball_height_m,
            },
        )

    @classmethod
    def _from_sources(cls, camera: Mapping[str, Any], iwr: Mapping[str, Any]):
        required_camera = ("mount_height_m", "width", "height")
        required_iwr = ("tee_slant_range_m", "radar_height_m", "ball_height_m")
        missing = [name for name in required_camera if camera.get(name) is None]
        missing += [name for name in required_iwr if iwr.get(name) is None]
        if missing:
            raise ValueError("missing effective camera geometry: " + ", ".join(missing))
        mirror = camera.get("mirror_horizontal", False)
        if not isinstance(mirror, bool):
            raise ValueError("mirror_horizontal must be a boolean")
        return cls(
            camera_height_m=camera["mount_height_m"],
            radar_height_m=iwr["radar_height_m"],
            tee_slant_range_m=iwr["tee_slant_range_m"],
            ball_height_m=iwr["ball_height_m"],
            camera_lateral_offset_m=camera.get("lateral_offset_m", 0.0),
            camera_forward_offset_m=camera.get("forward_offset_m", 0.0),
            image_width_px=camera["width"],
            image_height_px=camera["height"],
            horizontal_pixel_sign=-1.0 if mirror else 1.0,
            roll_correction_deg=camera.get("roll_correction_deg", 0.0),
            ball_horizontal_output_offset_deg=camera.get("horizontal_offset_deg", 0.0),
            ball_diameter_m=camera.get("ball_diameter_m", GOLF_BALL_DIAMETER_M),
        )

    @classmethod
    def from_recorded_session(cls, config: Mapping[str, Any]):
        """Load a snapshot, or reconstruct the legacy recorded input fields."""
        if "effective_camera_geometry" in config:
            block = config["effective_camera_geometry"]
            if not isinstance(block, Mapping):
                raise ValueError("effective camera geometry snapshot must be an object")
            if block.get("schema") != SCHEMA or block.get("version") != VERSION:
                raise ValueError("unsupported effective camera geometry snapshot schema")
            if block.get("available") is not True:
                raise ValueError(
                    "effective camera geometry is unavailable: "
                    + str(block.get("reason", "unspecified reason"))
                )
            parameters = block.get("parameters")
            if not isinstance(parameters, Mapping):
                raise ValueError("effective camera geometry parameters must be an object")
            if block.get("sha256") != geometry_fingerprint(parameters):
                raise ValueError("effective camera geometry snapshot fingerprint mismatch")
            if not isinstance(block.get("assumptions"), Mapping):
                raise ValueError("effective camera geometry assumptions must be an object")
            try:
                return cls(**dict(parameters))
            except TypeError as error:
                raise ValueError("invalid effective camera geometry parameters") from error

        camera = config.get("camera_capture")
        iwr = config.get("iwr6843")
        if not isinstance(camera, Mapping) or not isinstance(iwr, Mapping):
            raise ValueError("legacy session lacks recorded camera/IWR geometry")
        return cls._from_sources(camera, iwr)

    def snapshot(self) -> dict[str, Any]:
        """Return a detached, versioned and fingerprinted session record."""
        parameters = asdict(self)
        assumptions = (
            {
                "intrinsics": "recorded_unqualified_optical_calibration_candidate",
                "distortion": "recorded_opencv_brown_5_candidate",
                "pose": "explicit_target_frame_placement_plus_recorded_lis3dh_inclination",
                "yaw": "declared_target_alignment_not_observed_by_lis3dh",
                "origins": "explicit_with_loaded_rig_offset_consistency_check",
                "qualification": "unvalidated",
            }
            if self.calibrated_model_snapshot is not None
            else dict(ASSUMPTIONS)
        )
        return {
            "schema": SCHEMA,
            "version": VERSION,
            "available": True,
            "parameters": parameters,
            "assumptions": assumptions,
            "sha256": geometry_fingerprint(parameters),
        }

    def delivery_geometry(self) -> CameraDeliveryGeometry:
        return CameraDeliveryGeometry(
            camera_height_m=self.camera_height_m,
            radar_height_m=self.radar_height_m,
            tee_range_m=self.tee_slant_range_m,
            ball_height_m=self.ball_height_m,
            camera_lateral_offset_m=self.camera_lateral_offset_m,
            camera_forward_offset_m=self.camera_forward_offset_m,
            image_width_px=self.image_width_px,
            image_height_px=self.image_height_px,
            horizontal_pixel_sign=self.horizontal_pixel_sign,
            roll_correction_deg=self.roll_correction_deg,
            ball_diameter_m=self.ball_diameter_m,
            calibrated_model=self.calibrated_model(),
        )

    def ball_geometry(self) -> CameraBallGeometry:
        return CameraBallGeometry(
            camera_height_m=self.camera_height_m,
            radar_height_m=self.radar_height_m,
            tee_range_m=self.tee_slant_range_m,
            ball_height_m=self.ball_height_m,
            camera_lateral_offset_m=self.camera_lateral_offset_m,
            camera_forward_offset_m=self.camera_forward_offset_m,
            horizontal_offset_deg=self.ball_horizontal_output_offset_deg,
            horizontal_pixel_sign=self.horizontal_pixel_sign,
            roll_correction_deg=self.roll_correction_deg,
            image_width_px=self.image_width_px,
            image_height_px=self.image_height_px,
            ball_diameter_m=self.ball_diameter_m,
            calibrated_model=self.calibrated_model(),
        )

    def validate_archive_dimensions(self, width_px: Any, height_px: Any) -> None:
        width = _integer(width_px, "archive width")
        height = _integer(height_px, "archive height")
        if (width, height) != (self.image_width_px, self.image_height_px):
            raise ValueError(
                "camera archive dimensions "
                f"{width}x{height} do not match recorded geometry "
                f"{self.image_width_px}x{self.image_height_px}"
            )


def unavailable_snapshot(reason: str) -> dict[str, Any]:
    """Return an explicit session block when effective inputs do not exist."""
    return {"schema": SCHEMA, "version": VERSION, "available": False, "reason": reason}

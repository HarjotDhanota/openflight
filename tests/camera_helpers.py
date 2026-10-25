"""Shared camera fixtures: the v3 nominal camera and a calibration candidate with its placement."""

from openflight.camera.optical_calibration import validate_mode_profile
from openflight.camera.reference_ball_range import BallPlaneCamera

V3_FOCAL_1280 = 933.3334
BROWN = ("k1", "k2", "p1", "p2", "k3")


def v3_camera(
    width=1280,
    height=800,
    *,
    pitch_deg=0.0,
    roll_deg=0.0,
    focal_px=None,
    mirrored=False,
    camera_origin=(0.0, 0.0, 0.095),
    radar_origin=(0.0, -0.0014, 0.0588),
    angular_uncertainty_deg=1.0,
):
    """The v3 enclosure: lens 95 mm up, the IWR phase centre below and just behind it."""
    return BallPlaneCamera.nominal(
        focal_px=V3_FOCAL_1280 * width / 1280 if focal_px is None else focal_px,
        image_width_px=width,
        image_height_px=height,
        pitch_deg=pitch_deg,
        roll_correction_deg=roll_deg,
        mirror_horizontal=mirrored,
        camera_origin_lfu=camera_origin,
        radar_origin_lfu=radar_origin,
        angular_uncertainty_deg=angular_uncertainty_deg,
        focal_relative_uncertainty=0.08,
    )


def mode_profile(*, width=640, height=400, mirror=False, rotate=False):
    return {
        "version": 1,
        "camera_id": "camera-1",
        "lens_id": "lens-1",
        "focus_id": "fixed-1",
        "sensor_output": {
            "width": 1280,
            "height": 800,
            "bit_depth": 10,
            "raw_format": "SBGGR10_CSI2P",
            "mode_id": "mode-1",
        },
        "saved_image": {
            "width": width,
            "height": height,
            "stream": "raw",
            "rotate_180": rotate,
            "mirror": mirror,
        },
        "crop_readout_mapping": dict.fromkeys(
            (
                "native_sensor_crop",
                "scaler_crop",
                "driver_vertical_offset_px",
                "sensor_output_mapping",
                "saved_image_mapping",
            ),
            "unknown",
        ),
    }


def calibration_artifact(profile=None, matrix=None, coefficients=None):
    profile = validate_mode_profile(profile or mode_profile())
    return {
        "version": 1,
        "status": "candidate",
        "mode_profile": profile,
        "mode_profile_sha256": profile["sha256"],
        "camera_matrix": matrix or [[610.0, 0.0, 307.0], [0.0, 600.0, 191.0], [0, 0, 1]],
        "distortion_model": "opencv_brown_5",
        "distortion_convention": {
            "coefficient_order": list(BROWN),
            "coordinates": "OpenCV normalized camera coordinates",
        },
        "distortion_coefficients": coefficients
        or dict(zip(BROWN, (-0.12, 0.025, 0.001, -0.0008, -0.004))),
    }


def camera_placement(**changes):
    placement = {
        "schema": "openflight.camera.placement",
        "version": 1,
        "world_frame": "target_lfu",
        "rig_geometry_sha256": "rig-hash",
        # optical right/down/forward to enclosure right/forward/up
        "optical_to_enclosure_lfu": [[1, 0, 0], [0, 0, 1], [0, -1, 0]],
        "enclosure_to_target_lfu": [[0, -1, 0], [1, 0, 0], [0, 0, 1]],
        "camera_origin_lfu": [0.08, 0.04, 0.13],
        "radar_origin_lfu": [-0.02, 0.0, 0.05],
        "enclosure_pivot_lfu": [0.0, 0.0, 0.0],
        "reference_pose_deg": {"pitch": 7.0, "roll": -3.0},
        "origin_provenance": {
            "camera_origin_lfu": "measured optical center",
            "radar_origin_lfu": "measured antenna center",
            "enclosure_pivot_lfu": "declared front foot contact",
        },
        "rig_offset_consistency_tolerance_m": 0.002,
    }
    placement.update(changes)
    return placement

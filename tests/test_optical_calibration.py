"""The camera mode profile an optical calibration artifact is tied to."""

import pytest

from openflight.camera.optical_calibration import validate_mode_profile


def _profile():
    return {
        "version": 1,
        "camera_id": "camera-serial-1",
        "lens_id": "lens-1",
        "focus_id": "locked-ring-mark-1",
        "sensor_output": {
            "width": 1280,
            "height": 720,
            "bit_depth": 10,
            "raw_format": "SBGGR10",
            "mode_id": "libcamera-mode-1280x720-10bit",
        },
        "saved_image": {
            "width": 640,
            "height": 480,
            "stream": "main",
            "rotate_180": False,
            "mirror": False,
        },
        "crop_readout_mapping": {
            "native_sensor_crop": None,
            "scaler_crop": None,
            "driver_vertical_offset_px": None,
            "sensor_output_mapping": None,
            "saved_image_mapping": None,
        },
    }


def test_profile_is_canonical_and_requires_explicit_unknown_mapping():
    first = validate_mode_profile(_profile())
    reordered = dict(reversed(list(_profile().items())))
    assert validate_mode_profile(reordered)["sha256"] == first["sha256"]

    missing_mapping = _profile()
    del missing_mapping["crop_readout_mapping"]["scaler_crop"]
    with pytest.raises(ValueError, match="missing explicit fields"):
        validate_mode_profile(missing_mapping)

    unexpected = _profile()
    unexpected["derived_scale"] = 0.5
    with pytest.raises(ValueError, match="unknown fields"):
        validate_mode_profile(unexpected)

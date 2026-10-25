"""The camera mode profile an optical calibration artifact is tied to."""

import pytest

from openflight.camera.optical_calibration import validate_mode_profile
from tests.camera_helpers import mode_profile


def test_profile_is_canonical_and_requires_explicit_unknown_mapping():
    first = validate_mode_profile(mode_profile())
    reordered = dict(reversed(list(mode_profile().items())))
    assert validate_mode_profile(reordered)["sha256"] == first["sha256"]

    missing_mapping = mode_profile()
    del missing_mapping["crop_readout_mapping"]["scaler_crop"]
    with pytest.raises(ValueError, match="missing explicit fields"):
        validate_mode_profile(missing_mapping)

    unexpected = mode_profile()
    unexpected["derived_scale"] = 0.5
    with pytest.raises(ValueError, match="unknown fields"):
        validate_mode_profile(unexpected)

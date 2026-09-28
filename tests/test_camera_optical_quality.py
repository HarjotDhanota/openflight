"""Every camera-derived shot carries the optical evidence its metrics depend on."""

import hashlib
import json

import numpy as np
import pytest

from openflight.camera.optical_quality import (
    capture_optical_quality,
    controls_match,
    load_armed_exposure_profile,
    within_armed_profile,
)


def _metadata(*, purpose="capture", eligible=True, exposure_us=300, gain=8.0, profile=None):
    auto_exposure = {
        "analysis_eligible": eligible,
        "exposure_us": exposure_us,
        "gain": gain,
        "controls_purpose": purpose,
    }
    if profile is not None:
        auto_exposure["armed_profile"] = profile
    return {"auto_exposure": auto_exposure, "mean_brightness": 88.5, "p99_brightness": 201.0}


def _archive(exposure_us=300, gain=8.0, frames=6):
    return {
        "exposure_us": np.full(frames, exposure_us, dtype=np.int32),
        "analogue_gain": np.full(frames, gain, dtype=np.float32),
    }


def test_matching_capture_controls_are_usable():
    quality = capture_optical_quality(_metadata(), _archive(exposure_us=298))

    assert quality["status"] == "usable"
    assert quality["reason"] is None
    assert quality["controls_match"] is True
    assert quality["applied"] == {
        "exposure_us": 298.0,
        "gain": 8.0,
        "frames": 6,
        "mismatched_frames": 0,
    }
    assert quality["brightness"] == {"mean": 88.5, "p99": 201.0}


def test_a_capture_taken_during_a_still_photo_is_withheld():
    quality = capture_optical_quality(
        _metadata(purpose="still_photo", exposure_us=8000, gain=2.0),
        _archive(exposure_us=8000, gain=2.0),
    )

    assert quality["status"] == "withheld"
    assert quality["reason"] == "still_photo_controls_active"


def test_frames_not_captured_at_the_requested_controls_are_withheld():
    archive = _archive()
    archive["exposure_us"][:2] = 8000

    quality = capture_optical_quality(_metadata(), archive)

    assert quality["status"] == "withheld"
    assert quality["reason"] == "applied_controls_mismatch"
    assert quality["applied"]["mismatched_frames"] == 2


def test_a_capture_the_lighting_check_rejected_is_withheld():
    quality = capture_optical_quality(_metadata(eligible=False), _archive())

    assert quality["status"] == "withheld"
    assert quality["reason"] == "lighting_not_eligible"


def test_missing_per_frame_controls_are_reported_as_unverified():
    quality = capture_optical_quality(_metadata(), {})

    assert quality["controls_match"] is None
    assert quality["status"] == "usable"
    assert quality["applied"] is None


def test_the_armed_profile_identity_is_carried_through():
    profile = {
        "sha256": "a" * 64,
        "qualified": True,
        "exposure_ceiling_us": 400,
        "gain_ceiling": 12.0,
    }

    quality = capture_optical_quality(_metadata(profile=profile), _archive())

    assert quality["armed_profile"] == profile


@pytest.mark.parametrize(
    ("applied", "expected"),
    [((298, 8.0), True), ((310, 8.0), False), ((300, 8.2), False), ((None, 8.0), False)],
)
def test_controls_match_uses_the_shared_tolerance(applied, expected):
    assert controls_match(300, 8.0, *applied) is expected


def _profile_file(tmp_path, **overrides):
    payload = {
        "schema": "openflight.camera.armed_exposure_profile.v1",
        "qualified": True,
        "exposure_ceiling_us": 400,
        "gain_ceiling": 12.0,
        **overrides,
    }
    path = tmp_path / "armed.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_a_qualified_armed_profile_loads_with_its_identity(tmp_path):
    path = _profile_file(tmp_path)

    profile = load_armed_exposure_profile(path)

    assert profile["exposure_ceiling_us"] == 400
    assert profile["qualified"] is True
    assert profile["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"qualified": False}, "not qualified"),
        ({"schema": "other"}, "schema"),
        ({"exposure_ceiling_us": 0}, "exposure_ceiling_us"),
        ({"gain_ceiling": -1.0}, "gain_ceiling"),
    ],
)
def test_an_unqualified_or_malformed_armed_profile_is_refused(tmp_path, overrides, message):
    with pytest.raises(ValueError, match=message):
        load_armed_exposure_profile(_profile_file(tmp_path, **overrides))


def test_a_capture_outside_the_armed_profile_is_withheld():
    profile = {
        "sha256": "a" * 64,
        "qualified": True,
        "exposure_ceiling_us": 400,
        "gain_ceiling": 12.0,
    }

    quality = capture_optical_quality(
        _metadata(profile=profile, exposure_us=1250), _archive(exposure_us=1250)
    )

    assert quality["status"] == "withheld"
    assert quality["reason"] == "outside_armed_profile"


def test_no_armed_profile_imposes_no_ceiling():
    assert within_armed_profile(None, 8000, 16.0) is True

"""The tester's view of the unit's camera-tilt file: where it lives, its status, a reset."""

from pathlib import Path

import pytest

from openflight.camera import camera_tilt, tilt_calibration_admin
from tests.test_camera_tilt import PAIR, _calibrate


def test_one_pair_leaves_the_status_pending(tmp_path):
    path = tmp_path / "camera-vertical-offset.json"
    camera_tilt.record_pair(path, PAIR, facts={"epoch_id": "one"})

    assert tilt_calibration_admin.calibration_status(path)["status"] == "pending"


def test_a_calibration_can_be_reset_and_starts_again(tmp_path):
    path = tmp_path / "camera-vertical-offset.json"
    _calibrate(path, 2.1, 2.3)

    reset = tilt_calibration_admin.reset_calibration(path, reason="tester asked")

    assert reset["action"] == "reset"
    assert reset["previous_offset_deg"] == pytest.approx(2.2)
    assert camera_tilt.load_calibration(path) is None
    assert tilt_calibration_admin.calibration_status(path)["status"] == "uncalibrated"
    assert camera_tilt.record_pair(path, PAIR, facts={"epoch_id": "new"})["action"] == "pending"
    # nothing to reset is not an error
    assert (
        tilt_calibration_admin.reset_calibration(tmp_path / "none.json")["action"]
        == "nothing_to_reset"
    )


def test_the_calibration_lives_with_the_unit_not_in_the_rig_file():
    default = tilt_calibration_admin.default_calibration_path()

    assert default == Path.home() / ".config" / "openflight" / "camera-vertical-offset.json"
    assert ".config" in default.parts and "enclosure" not in default.name

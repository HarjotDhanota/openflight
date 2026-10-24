"""The unit's camera tilt calibration is applied on swings, not only at setup.

A swing's pitch is its own LIS3DH pitch plus the unit's offset and its focal length the
rig's; the resting ball's inference is a recorded cross-check. Uncalibrated swings infer.
"""

from __future__ import annotations

import dataclasses
import math
from pathlib import Path

import numpy as np
import pytest

from openflight import server as server_module
from openflight.camera import ball_flight, camera_tilt, fusion_processing as fp
from openflight.camera.ball_pixels import mode_focal_px
from openflight.camera.club_motion import ReferenceBall
from openflight.camera.geometry import (
    SETUP_FOCAL_CHECK_TOLERANCE_REL,
    SETUP_PITCH_CHECK_TOLERANCE_DEG,
    resting_ball_camera_model,
)
from openflight.rig_geometry import RigGeometry

RIG_PATH = Path(__file__).resolve().parents[1] / "config" / "enclosure_v3_rig_geometry.json"
CALIBRATION = {"offset_deg": 2.0, "uncertainty_deg": 0.3, "calibrated_at_utc": "2026-10-01"}


def _inclinometer(calibrated_pitch_deg=1.0):
    return {
        "applied": True,
        "status": "stable",
        "stable": True,
        "calibrated_pitch_deg": calibrated_pitch_deg,
        "x_g": 0.0,
        "y_g": 0.0,
        "z_g": 1.0,
    }


def test_a_calibrated_unit_swings_with_the_lis3dh_pitch_plus_its_offset():
    rig = RigGeometry.from_json(RIG_PATH)
    expected = rig.expected_inclinometer_orientation().pitch_deg or 0.0

    pose = camera_tilt.swing_camera_pose(
        rig=rig, image_width_px=1280, inclinometer=_inclinometer(1.0), calibration=CALIBRATION
    )

    assert pose["status"] == "calibrated"
    assert pose["pitch_deg"] == pytest.approx(rig.boresight_pitch_deg + (1.0 - expected) + 2.0)
    assert pose["focal_px"] == pytest.approx(mode_focal_px(1280, rig))
    assert pose["offset_deg"] == 2.0


@pytest.mark.parametrize(
    ("calibration", "inclinometer", "reason"),
    [
        (None, _inclinometer(), "no unit camera tilt calibration"),
        (CALIBRATION, None, "no stable LIS3DH reading"),
        (CALIBRATION, {"applied": False, "status": "stale"}, "no stable LIS3DH reading"),
    ],
)
def test_without_calibration_or_a_reading_the_swing_stays_uncalibrated(
    calibration, inclinometer, reason
):
    pose = camera_tilt.swing_camera_pose(
        rig=RigGeometry.from_json(RIG_PATH),
        image_width_px=1280,
        inclinometer=inclinometer,
        calibration=calibration,
    )

    assert pose["status"] == "uncalibrated"
    assert pose["pitch_deg"] is None and pose["focal_px"] is None
    assert reason in pose["reason"]


def _reference(**changes):
    values = {
        "ball_x_px": 320.0,
        "ball_y_px": 200.0,
        "ball_diameter_px": 480.0 * 0.0427 / 1.5,
        "ball_diameter_m": 0.0427,
        "image_width_px": 640,
        "image_height_px": 400,
        "horizontal_pixel_sign": 1.0,
        "roll_correction_deg": 0.0,
        "camera_origin_lfu": np.array([0.0, 0.0, 0.0213]),
        "radar_origin_lfu": np.array([0.0, 0.0, 0.0]),
        "ball_position_lfu": np.array([0.0, 1.5, 0.0213]),
    }
    values.update(changes)
    return values


def test_the_calibration_wins_and_the_resting_ball_is_kept_as_a_check():
    focal, pitch, _offset, check = resting_ball_camera_model(
        setup_pitch_deg=0.5, setup_focal_px=470.0, **_reference()
    )

    assert focal == pytest.approx(470.0)
    assert math.degrees(pitch) == pytest.approx(0.5)
    assert check["status"] == "calibrated"
    assert check["inferred_focal_px"] == pytest.approx(480.0)
    assert check["inferred_pitch_deg"] == pytest.approx(0.0, abs=1e-9)
    assert check["pitch_disagreement_deg"] == pytest.approx(0.5)
    assert check["disagrees"] is False


def test_a_disagreement_beyond_the_tolerance_is_flagged():
    _focal, _pitch, _offset, check = resting_ball_camera_model(
        setup_pitch_deg=SETUP_PITCH_CHECK_TOLERANCE_DEG + 0.5,
        setup_focal_px=480.0 * (1.0 + SETUP_FOCAL_CHECK_TOLERANCE_REL / 2.0),
        **_reference(),
    )

    assert check["disagrees"] is True
    assert "pitch" in check["warning"]


def test_without_calibration_the_resting_ball_still_sets_the_model_labelled():
    focal, pitch, _offset, check = resting_ball_camera_model(**_reference())

    assert focal == pytest.approx(480.0)
    assert pitch == pytest.approx(0.0, abs=1e-9)
    assert check["status"] == "uncalibrated"


def test_the_ball_estimator_uses_the_calibrated_model():
    geometry = ball_flight.CameraBallGeometry(
        camera_height_m=0.0213,
        radar_height_m=0.0213,
        tee_range_m=1.5,
        ball_height_m=0.0213,
        image_width_px=640,
        image_height_px=400,
        setup_pitch_deg=0.5,
        setup_focal_px=470.0,
    )
    anchor = ReferenceBall(x=320.0, y=200.0, diameter_px=480.0 * 0.0427 / 1.5, area_px=100)

    (focal, pitch, _offset), check = ball_flight._checked_camera_model(  # pylint: disable=protected-access
        anchor, geometry
    )

    assert focal == pytest.approx(470.0)
    assert math.degrees(pitch) == pytest.approx(0.5)
    assert check["inferred_focal_px"] == pytest.approx(480.0, rel=1e-3)
    assert ball_flight._camera_model(anchor, dataclasses.replace(geometry, setup_pitch_deg=None))[
        0
    ] == pytest.approx(480.0, rel=1e-3)


def test_the_frozen_context_carries_the_swing_pose_to_both_estimators(monkeypatch):
    from tests.test_camera_fusion_processing import _archive, _context_kwargs  # noqa: PLC0415

    pose = {"status": "calibrated", "pitch_deg": 0.5, "focal_px": 470.0}
    context = fp.build_context(**_context_kwargs(), camera_pose=pose)
    seen = {}

    def ball(*_args, geometry, **_kwargs):
        seen["ball"] = geometry
        return ball_flight.CameraBallEstimate(status="rejected_no_iwr_range")

    def club(*_args, geometry, **_kwargs):
        seen["club"] = geometry
        from openflight.camera.club_delivery import ChainedDelivery  # noqa: PLC0415

        return ChainedDelivery(status="rejected_no_impact")

    monkeypatch.setattr(fp, "estimate_camera_ball_flight", ball)
    monkeypatch.setattr(fp, "estimate_chained_delivery", club)

    fp.process_camera_fusion(context, _archive())

    assert context["camera_pose"] == pose
    for geometry in seen.values():
        assert geometry.setup_pitch_deg == 0.5
        assert geometry.setup_focal_px == 470.0
    # a context without a pose is the one recorded before, byte for byte
    assert "camera_pose" not in fp.build_context(**_context_kwargs())


def test_the_kiosk_composes_each_swing_from_its_own_reading(monkeypatch):
    monkeypatch.setattr(server_module, "rig_geometry", RigGeometry.from_json(RIG_PATH))
    monkeypatch.setattr(server_module, "camera_capture_config", {"width": 1280, "height": 800})
    monkeypatch.setattr(
        server_module,
        "setup_handoff_config",
        {"camera_tilt": {"vertical_offset_deg": 2.0, "source": "unit_calibration"}},
    )
    shot = type("ShotStub", (), {"inclinometer": _inclinometer(1.0)})()

    pose = server_module._swing_camera_pose(shot)  # pylint: disable=protected-access

    assert pose["status"] == "calibrated"
    assert pose["offset_deg"] == 2.0
    monkeypatch.setattr(server_module, "setup_handoff_config", {"camera_tilt": {}})
    assert server_module._swing_camera_pose(shot)["status"] == "uncalibrated"  # pylint: disable=protected-access


# The LIS3DH roll goes through the same composition on swings as at setup; its gate is off.


def _rolled_reading(roll_deg=-2.9):
    roll = math.radians(roll_deg)
    return {**_inclinometer(1.0), "x_g": math.sin(roll), "y_g": 0.0, "z_g": math.cos(roll)}


def test_by_default_the_roll_is_recorded_not_applied_on_swings():
    from openflight.camera import camera_roll  # noqa: PLC0415

    assert camera_roll.LIS3DH_ROLL_APPLIED is False
    pose = camera_tilt.swing_camera_pose(
        rig=RigGeometry.from_json(RIG_PATH),
        image_width_px=1280,
        inclinometer=_rolled_reading(),
        calibration=None,
    )
    geometry = ball_flight.CameraBallGeometry(
        camera_height_m=0.095,
        radar_height_m=0.05,
        tee_range_m=1.5,
        ball_height_m=0.0213,
        roll_correction_deg=0.4,
    )

    assert pose["roll"]["applied"] is False
    assert pose["roll"]["status"] == "not_applied_pending_c8"
    assert pose["roll"]["measured_deg"] == pytest.approx(-2.9)
    assert pose["roll"]["roll_correction_deg"] == 0.0
    assert camera_tilt.with_swing_pose(geometry, pose).roll_correction_deg == 0.4

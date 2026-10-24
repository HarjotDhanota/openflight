"""The tester's setup and the kiosk agree: the same camera pose, roll, tilt hand-off and gates.

Each check builds the tester's setup camera (``tester_server``) and holds it to
the kiosk's own product code.
"""

from __future__ import annotations

import json
import math

import pytest

from openflight.camera import auto_exposure, ball_flight, camera_tilt, tester_server as ts
from openflight.rig_geometry import RigGeometry
from tests.test_camera_tilt_swings import CALIBRATION, RIG_PATH, _inclinometer, _rolled_reading


def test_the_setup_camera_does_not_apply_the_roll_yet():
    """The tester's setup camera goes through camera_roll too (B2 pending)."""
    camera = ts._reference_ball_camera(  # pylint: disable=protected-access
        ts.ARMS["arm6"], RIG_PATH, {"camera_pitch_deg": 0.0, "roll_deg": -2.9}, None, None
    )
    assert camera.ray_model.roll_correction_deg == 0.0


def test_the_tester_hands_the_kiosk_the_lens_s_tilt_file(tmp_path):
    from openflight.camera import tester_server as ts  # noqa: PLC0415

    path = tmp_path / "camera-vertical-offset-6mm.json"

    args, _record = ts.tee_range_handoff(None, camera_tilt_path=path)

    assert args[args.index("--camera-tilt-calibration-path") + 1] == str(path)


def test_the_gate_matches_the_gain_screen():
    from openflight.camera import tester_server

    results = [{"gain": 4.0, "mean": 120.0, "clipped_pct": auto_exposure.BALL_CLIP_PCT + 0.05}]
    # in band but clipped past the gate: too bright, never "needs light"
    choice = tester_server.choose_gain(results)
    assert choice["too_bright"] is True
    assert choice["lighting_required"] is False


def test_the_tester_refuses_320x200_for_measurement():
    from openflight.camera import tester_server as ts  # noqa: PLC0415

    with pytest.raises(ValueError, match="320x200"):
        ts._reference_ball_camera(  # pylint: disable=protected-access
            ts.ARMS["arm1"], RIG_PATH, {"camera_pitch_deg": 0.0}, None, None
        )


def test_the_swing_pose_is_the_setup_pose_for_the_same_reading():
    """Setup (tester) and swings (kiosk) compose the same pitch and focal length."""
    rig = RigGeometry.from_json(RIG_PATH)
    expected = rig.expected_inclinometer_orientation().pitch_deg or 0.0
    reading = _inclinometer(0.75)
    tilt = {"camera_pitch_deg": rig.boresight_pitch_deg + (0.75 - expected), "roll_deg": 0.0}

    setup = ts._reference_ball_camera(  # pylint: disable=protected-access
        ts.ARMS["arm5"], RIG_PATH, tilt, None, None, CALIBRATION
    )
    swing = camera_tilt.swing_camera_pose(
        rig=rig,
        image_width_px=ts.ARMS["arm5"].width,
        inclinometer=reading,
        calibration=CALIBRATION,
    )

    assert math.degrees(setup.ray_model.pitch_rad) == pytest.approx(swing["pitch_deg"])
    assert setup.ray_model.focal_px == pytest.approx(swing["focal_px"])


def test_on_a_verified_unit_setup_and_swings_apply_the_same_roll():
    from openflight.camera import camera_roll  # noqa: PLC0415

    rig = RigGeometry.from_json(RIG_PATH)
    zeroed = {"verified": True, "zero_deg": 0.4, "residual_deg": 0.2, "applied": True}
    reading = {**_rolled_reading(-2.9), "roll_calibration": zeroed}
    expected = rig.expected_inclinometer_orientation()
    tilt = {
        "camera_pitch_deg": rig.boresight_pitch_deg + (1.0 - (expected.pitch_deg or 0.0)),
        "roll_deg": camera_roll.lis3dh_roll_deg(reading["x_g"], reading["y_g"], reading["z_g"]),
        "roll_calibration": zeroed,
    }

    setup = ts._reference_ball_camera(  # pylint: disable=protected-access
        ts.ARMS["arm5"], RIG_PATH, tilt, None, None, CALIBRATION
    )
    pose = camera_tilt.swing_camera_pose(
        rig=rig, image_width_px=1280, inclinometer=reading, calibration=CALIBRATION
    )
    geometry = camera_tilt.with_swing_pose(
        ball_flight.CameraBallGeometry(
            camera_height_m=0.095, radar_height_m=0.05, tee_range_m=1.5, ball_height_m=0.0213
        ),
        pose,
    )

    assert pose["roll"]["applied"] is True
    assert setup.roll["applied"] is True
    assert setup.ray_model.roll_correction_deg != 0.0
    assert geometry.roll_correction_deg == pytest.approx(setup.ray_model.roll_correction_deg)
    assert pose["roll"]["roll_correction_deg"] == pytest.approx(setup.ray_model.roll_correction_deg)


def _mode_artifact(width, height):
    """A checkerboard-style optical candidate for one saved-image mode (no distortion)."""
    from openflight.camera.optical_calibration import validate_mode_profile  # noqa: PLC0415

    profile = validate_mode_profile(
        {
            "version": 1,
            "camera_id": "camera-1",
            "lens_id": "6mm",
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
                "rotate_180": False,
                "mirror": False,
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
    )
    focal = 2000.0 * width / 1280.0
    return {
        "version": 1,
        "status": "candidate",
        "mode_profile": profile,
        "mode_profile_sha256": profile["sha256"],
        "camera_matrix": [
            [focal, 0.0, width / 2.0 + 3.0],
            [0.0, focal, height / 2.0 - 5.0],
            [0.0, 0.0, 1.0],
        ],
        "distortion_model": "opencv_brown_5",
        "distortion_convention": {
            "coefficient_order": ["k1", "k2", "p1", "p2", "k3"],
            "coordinates": "OpenCV normalized camera coordinates",
        },
        "distortion_coefficients": dict.fromkeys(("k1", "k2", "p1", "p2", "k3"), 0.0),
    }


def _rig_placement(rig):
    """A placement for this rig: its fingerprint, and origins that agree with its IWR offset."""
    import numpy as np  # noqa: PLC0415

    mount = [[1, 0, 0], [0, 0, 1], [0, -1, 0]]
    camera = np.asarray([0.0, 0.0, rig.lens_height_above_floor_mm / 1000.0])
    radar = camera + np.asarray(mount, dtype=float) @ (np.asarray(rig.iwr_offset_mm) / 1000.0)
    return {
        "schema": "openflight.camera.placement",
        "version": 1,
        "world_frame": "target_lfu",
        "rig_geometry_sha256": rig.snapshot()["sha256"],
        "optical_to_enclosure_lfu": mount,
        "enclosure_to_target_lfu": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        "camera_origin_lfu": camera.tolist(),
        "radar_origin_lfu": radar.tolist(),
        "enclosure_pivot_lfu": camera.tolist(),
        "reference_pose_deg": {"pitch": 0.5, "roll": 0.4},
        "origin_provenance": dict.fromkeys(
            ("camera_origin_lfu", "radar_origin_lfu", "enclosure_pivot_lfu"), "test"
        ),
        "rig_offset_consistency_tolerance_m": 0.002,
    }


@pytest.fixture(name="kiosk_camera")
def _kiosk_camera(monkeypatch, tmp_path):
    """The kiosk at one capture mode and LIS3DH reading, with the unit's calibrated tilt."""
    import time  # noqa: PLC0415
    from types import SimpleNamespace  # noqa: PLC0415

    from openflight import server  # noqa: PLC0415
    from openflight.camera import lens_profiles  # noqa: PLC0415
    from openflight.camera.capture_runtime import CameraCaptureSettings  # noqa: PLC0415
    from openflight.inclinometer.models import (  # noqa: PLC0415
        OrientationSnapshot,
        SnapshotSelection,
    )

    for name in ("rig_geometry", "rig_geometry_config", "camera_lens"):
        monkeypatch.setattr(server, name, getattr(server, name))
    monkeypatch.setattr(server, "camera_optical_calibration", None)
    monkeypatch.setattr(server, "camera_placement", None)
    # the unit"s camera-tilt file, as the tester hands it with the offset
    tilt_file = tmp_path / "camera-vertical-offset.json"
    tilt_file.write_text(
        json.dumps({"schema": camera_tilt.SCHEMA, "status": "calibrated", **CALIBRATION}),
        encoding="utf-8",
    )
    monkeypatch.setattr(server, "camera_tilt_calibration_path", tilt_file)
    # no checkerboard lens artifacts but the test's own
    monkeypatch.setattr(lens_profiles, "default_calibration_dir", lambda: tmp_path / "none")
    monkeypatch.setattr(
        server,
        "setup_handoff_config",
        {
            "camera_tilt": {
                "vertical_offset_deg": CALIBRATION["offset_deg"],
                "source": "unit_calibration",
            }
        },
    )
    reading = OrientationSnapshot(
        timestamp=time.time(),
        x_g=-0.03,
        y_g=0.0,
        z_g=1.0,
        gravity_g=1.0,
        raw_pitch_deg=0.75,
        calibrated_pitch_deg=0.75,
        pitch_std_deg=0.05,
        sample_count=20,
    )
    monkeypatch.setattr(
        server,
        "inclinometer_service",
        SimpleNamespace(snapshot_for_impact=lambda _t: SnapshotSelection(reading, "stable")),
    )
    rig = RigGeometry.from_json(RIG_PATH)
    expected = rig.expected_inclinometer_orientation()
    tilt = {
        "pitch_deg": reading.calibrated_pitch_deg,
        "camera_pitch_deg": rig.boresight_pitch_deg + (0.75 - (expected.pitch_deg or 0.0)),
        "roll_deg": math.degrees(math.atan2(reading.x_g, math.hypot(reading.y_g, reading.z_g))),
    }

    def start(arm, lens_id):
        monkeypatch.setattr(
            server,
            "camera_capture_runtime",
            SimpleNamespace(settings=CameraCaptureSettings(width=arm.width, height=arm.height)),
        )
        server.init_rig_geometry(RIG_PATH)
        server.init_camera_lens(lens_id)
        lens = lens_profiles.active_lens(rig, lens_profiles.load_catalogue(), lens_id, [])
        return server, lens

    return SimpleNamespace(start=start, tilt=tilt, rig=rig)


def _same_camera(kiosk, tester):
    import numpy as np  # noqa: PLC0415

    pixels = np.asarray(
        [[10.0, 12.0], [tester.image_width_px / 2.0, tester.image_height_px / 2.0], [600.0, 380.0]]
    )
    assert (kiosk.image_width_px, kiosk.image_height_px) == (
        tester.image_width_px,
        tester.image_height_px,
    )
    assert kiosk.focal_size_px == pytest.approx(tester.focal_size_px)
    assert np.asarray(kiosk.ray_model.rays(pixels)) == pytest.approx(
        np.asarray(tester.ray_model.rays(pixels)), abs=1e-12
    )
    assert kiosk.camera_origin_lfu == pytest.approx(tester.camera_origin_lfu)
    assert kiosk.radar_origin_lfu == pytest.approx(tester.radar_origin_lfu)
    assert kiosk.angular_uncertainty_deg == tester.angular_uncertainty_deg
    assert kiosk.source == tester.source
    assert kiosk.vertical_offset == tester.vertical_offset


@pytest.mark.parametrize("lens_id", [None, "6mm"])
@pytest.mark.parametrize("arm_id", ["arm5", "arm6"])
def test_the_kiosk_s_nominal_setup_camera_is_the_tester_s(kiosk_camera, arm_id, lens_id):
    """The kiosk's setup screen measures through the camera the tester's setup builds."""
    arm = ts.ARMS[arm_id]
    server, lens = kiosk_camera.start(arm, lens_id)

    tester = ts._reference_ball_camera(  # pylint: disable=protected-access
        arm, RIG_PATH, kiosk_camera.tilt, None, None, CALIBRATION, lens
    )
    kiosk = server.setup_camera_view()

    _same_camera(kiosk.camera, tester)
    assert kiosk.camera.ray_model.roll_correction_deg == tester.ray_model.roll_correction_deg
    assert kiosk.roll_deg == pytest.approx(
        ts._placement_roll_deg(RIG_PATH, kiosk_camera.tilt)  # pylint: disable=protected-access
    )
    if lens_id == "6mm":
        assert tester.focal_size_px == pytest.approx(lens.focal_px(arm.width))
        assert tester.focal_size_px > 2.0 * 466.6667 * arm.width / 1280.0


@pytest.mark.parametrize("lens_id", [None, "6mm"])
@pytest.mark.parametrize("arm_id", ["arm5", "arm6"])
def test_the_kiosk_s_calibrated_setup_camera_is_the_tester_s(
    kiosk_camera, tmp_path, arm_id, lens_id
):
    """With calibrated fusion loaded, both build the calibrated model at the LIS3DH's pose."""
    arm = ts.ARMS[arm_id]
    server, lens = kiosk_camera.start(arm, lens_id)
    artifact, placement = tmp_path / "optical.json", tmp_path / "placement.json"
    artifact.write_text(json.dumps(_mode_artifact(arm.width, arm.height)), encoding="utf-8")
    placement.write_text(json.dumps(_rig_placement(kiosk_camera.rig)), encoding="utf-8")
    server.init_camera_calibrated_fusion(str(artifact), str(placement))

    tester = ts._reference_ball_camera(  # pylint: disable=protected-access
        arm, RIG_PATH, kiosk_camera.tilt, artifact, placement, CALIBRATION, lens
    )
    kiosk = server.setup_camera_view()

    assert tester.source.startswith("calibrated")
    assert kiosk.camera.vertical_offset["status"] == "not_applied"
    _same_camera(kiosk.camera, tester)
    assert kiosk.roll_deg == pytest.approx(
        ts._placement_roll_deg(RIG_PATH, kiosk_camera.tilt)  # pylint: disable=protected-access
    )


def test_a_calibration_for_another_mode_is_refused_by_both(kiosk_camera, tmp_path):
    arm = ts.ARMS["arm5"]
    server, lens = kiosk_camera.start(arm, "6mm")
    artifact, placement = tmp_path / "optical.json", tmp_path / "placement.json"
    artifact.write_text(json.dumps(_mode_artifact(640, 400)), encoding="utf-8")
    placement.write_text(json.dumps(_rig_placement(kiosk_camera.rig)), encoding="utf-8")
    server.init_camera_calibrated_fusion(str(artifact), str(placement))

    with pytest.raises(ValueError, match="does not match the active capture mode"):
        ts._reference_ball_camera(  # pylint: disable=protected-access
            arm, RIG_PATH, kiosk_camera.tilt, artifact, placement, CALIBRATION, lens
        )
    with pytest.raises(ValueError, match="does not match the active capture mode"):
        server.setup_camera_view()


@pytest.mark.parametrize("arm_id", ["arm5", "arm6"])
def test_a_checkerboard_lens_takes_its_own_focal_for_the_mode_in_both(kiosk_camera, arm_id):
    """A lens measured per mode is not 2x between the modes; neither side rescales it."""
    from dataclasses import replace  # noqa: PLC0415

    arm = ts.ARMS[arm_id]
    server, lens = kiosk_camera.start(arm, "6mm")
    measured = replace(
        lens, focal_px_by_arm={"arm5": 2010.0, "arm6": 1000.0}, intrinsics="calibrated"
    )
    server.camera_lens = measured

    tester = ts._reference_ball_camera(  # pylint: disable=protected-access
        arm, RIG_PATH, kiosk_camera.tilt, None, None, CALIBRATION, measured
    )
    kiosk = server.setup_camera_view()

    assert tester.focal_size_px == {"arm5": 2010.0, "arm6": 1000.0}[arm_id]
    _same_camera(kiosk.camera, tester)

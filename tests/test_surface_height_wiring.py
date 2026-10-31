"""The solved hitting-surface height reaches every consumer of a height (owner, 30 Oct).

Heights are above the hitting surface: the lens and the radar move down by the solved
surface height (the radar may go below it) and the ball's centre stays one radius up.
The IWR's floor bounce is modelled off the lower of the base and the surface.
"""

from __future__ import annotations

import math
from datetime import datetime
from types import SimpleNamespace

import numpy as np
import pytest

from openflight import server, surface_height as sh
from openflight.camera.capture_runtime import CameraCaptureSettings
from openflight.camera.geometry_contract import EffectiveCameraGeometryInputs
from openflight.iwr6843.calibration import Calibration
from openflight.iwr6843.runtime import IWR6843Runtime
from openflight.launch_monitor import Shot
from openflight.rig_geometry import RigGeometry, camera_rdf_offset_to_target_lfu

RIG = RigGeometry.from_json("config/enclosure_v3_rig_geometry.json")
ENCLOSURE = RIG.enclosure_setup()
LENS = ENCLOSURE.camera_mount_height_m
RADAR = ENCLOSURE.radar_height_m
RADIUS = server.BALL_RADIUS_M
SIZE = (1280, 800)
TILT = 10.0


def _calibration(radar_m=RADAR, ball_m=RADIUS, depth_m=0.0):
    calibration = Calibration.identity()
    calibration.meta["radar_height_m"] = radar_m
    calibration.tee_ball_height_m = ball_m
    calibration.tilt_rad = math.radians(TILT)
    calibration.tee_range_m = 1.6
    calibration.antennas = server._antenna_layout(radar_m, ENCLOSURE.iwr_board_rotation_deg, TILT)
    calibration.reflecting_plane_depth_m = depth_m
    return calibration


# --- the camera geometry contract: a radar below the surface is a valid geometry ----


def test_the_camera_geometry_accepts_a_radar_below_the_hitting_surface():
    camera = {
        "mount_height_m": LENS - 0.068,
        "lateral_offset_m": 0.0,
        "forward_offset_m": 0.031,
        "width": 1280,
        "height": 800,
    }
    geometry = EffectiveCameraGeometryInputs.from_live(
        camera, _calibration(RADAR - 0.068, RADIUS, 0.068)
    )

    assert geometry.radar_height_m == pytest.approx(RADAR - 0.068)
    assert geometry.camera_height_m == pytest.approx(LENS - 0.068)


# --- the kiosk: per-shot solve, session aggregate, every consumer ----------------------


@pytest.fixture(name="kiosk")
def _kiosk(monkeypatch):
    calibration = _calibration()
    iwr = IWR6843Runtime(capture_monitor=None, calibration=calibration, net_range_m=4.6)
    monkeypatch.setattr(server, "iwr6843_runtime", iwr)
    monkeypatch.setattr(server, "iwr6843_runtime_config", {"enabled": True})
    monkeypatch.setattr(
        server,
        "camera_capture_runtime",
        SimpleNamespace(settings=CameraCaptureSettings(width=SIZE[0], height=SIZE[1])),
    )
    monkeypatch.setattr(
        server,
        "camera_capture_config",
        {
            "enabled": True,
            "mount_height_m": LENS,
            "width": SIZE[0],
            "height": SIZE[1],
            "lateral_offset_m": 0.0,
            "forward_offset_m": 0.031,
        },
    )
    monkeypatch.setattr(server, "rig_geometry", RIG)
    monkeypatch.setattr(server, "rig_geometry_config", {"enabled": True})
    monkeypatch.setattr(server, "camera_ball_box_px", (300, 300, 980, 680))
    monkeypatch.setattr(server, "surface_height_contributions", [])
    monkeypatch.setattr(server, "shot_processing_pending", lambda: False)
    monkeypatch.setattr(server, "get_session_logger", lambda: None)
    monkeypatch.setattr(server, "setup_handoff_config", {"tee_range": None, "scene": None})
    server.init_setup_handoff(
        SimpleNamespace(
            iwr6843_ball_height_m=RADIUS,
            camera_vertical_offset_deg=None,
            camera_vertical_offset_source=None,
        ),
        ENCLOSURE,
    )
    # the camera's pitch from its LIS3DH and the rig: level; no roll
    monkeypatch.setattr(server, "_lis3dh_camera_pitch", lambda _inclinometer: 0.0)
    monkeypatch.setattr(server, "_swing_roll_correction_deg", lambda _shot: 0.0)
    return SimpleNamespace(iwr=iwr)


def _focal():
    return server._mode_focal(SIZE[0])[0]


def _shot_seen(surface_m, number=1, *, forward_m=1.7, label="measured", diameter_scale=1.0):
    """A shot whose resting ball and IWR launch range put the surface this high."""
    focal = _focal()
    lens = np.array([0.0, 0.0, LENS])
    radar = lens + np.asarray(camera_rdf_offset_to_target_lfu(RIG.iwr_origin_mm))
    ball = np.array([0.0, forward_m, surface_m + RADIUS])
    rel = ball - lens
    distance = float(np.linalg.norm(rel))
    shot = Shot(ball_speed_mph=100.0, timestamp=datetime.now(), mode="kiosk")
    shot.shot_number = number
    shot.ball_range_m = float(np.linalg.norm(ball - radar))
    shot.ball_range_uncertainty_m = 0.002
    shot.ball_range_source = "iwr_post_impact"
    shot.ball_range_label = label
    archive = {
        "frames": np.zeros((1, SIZE[1], SIZE[0]), dtype=np.uint8),
        server.SURFACE_RESTING_BALL_KEY: {
            "x_px": SIZE[0] / 2.0,
            "y_px": SIZE[1] / 2.0 - focal * rel[2] / rel[1],
            "diameter_px": diameter_scale * focal * 2 * RADIUS / distance,
        },
    }
    return shot, archive


def _precise(monkeypatch):
    """A calibrated camera pointing: the solve's sigma is inside the bound."""
    monkeypatch.setattr(server, "_surface_angular_sigma_deg", lambda: 0.2)


def test_a_precise_shot_solve_moves_every_height(kiosk, monkeypatch):
    _precise(monkeypatch)
    shot, archive = _shot_seen(0.068)

    server._solve_surface_height(shot, archive)

    lens, radar = LENS - 0.068, RADAR - 0.068
    record = shot.scene_heights
    assert record["shot_solve"]["status"] == "solved"
    assert record["shot_solve"]["surface_height_m"] == pytest.approx(0.068, abs=0.001)
    assert record["in_force"]["source"] == sh.SOLVED_PER_SHOT
    # the camera's geometry (fusion, the range chain, the camera fallback)
    assert server.camera_capture_config["mount_height_m"] == pytest.approx(lens, abs=0.001)
    # the IWR calibration: radar below the hitting surface, ball one radius up
    calibration = kiosk.iwr.calibration
    assert calibration.radar_height_m == pytest.approx(radar, abs=0.001)
    assert calibration.radar_height_m < 0.0
    assert calibration.tee_ball_height_m == pytest.approx(RADIUS)
    assert calibration.reflecting_plane_depth_m == pytest.approx(0.068, abs=0.001)
    assert calibration.antennas.phase_centre_height_m(calibration.tilt_rad) == pytest.approx(
        radar, abs=0.001
    )
    assert server.iwr6843_runtime_config["radar_height_m"] == pytest.approx(radar, abs=0.001)
    # the scene record every later consumer reads
    scene = server.setup_handoff_config["scene"]
    assert scene["lens_height_used_m"] == pytest.approx(lens, abs=0.001)
    assert scene["lens_height_used_source"] == sh.SOLVED_PER_SHOT
    assert scene["radar_height_m"] == pytest.approx(radar, abs=0.001)
    assert scene["ball_height_m"] == pytest.approx(RADIUS)
    assert scene["surface_height"]["n"] == 1
    assert scene["surface_height"]["source"] == sh.SOLVED_PER_SHOT
    assert server.rig_geometry_config["solved_camera_height"]["radar_height_m"] == pytest.approx(
        radar, abs=0.001
    )


def test_the_fusion_geometry_and_the_scene_calibration_read_the_solved_heights(kiosk, monkeypatch):
    _precise(monkeypatch)
    shot, archive = _shot_seen(0.068)
    server._solve_surface_height(shot, archive)

    geometry = EffectiveCameraGeometryInputs.from_live(
        server.camera_capture_config, server.shot_iwr_calibration(shot)
    )
    assert geometry.camera_height_m == pytest.approx(LENS - 0.068, abs=0.001)
    assert geometry.radar_height_m == pytest.approx(RADAR - 0.068, abs=0.001)
    assert geometry.ball_height_m == pytest.approx(RADIUS)

    # a unit with no IWR runtime takes its heights from the scene record
    monkeypatch.setattr(server, "iwr6843_runtime", None)
    scene = server._scene_calibration(shot)
    assert scene.radar_height_m == pytest.approx(RADAR - 0.068, abs=0.001)
    assert scene.tee_ball_height_m == pytest.approx(RADIUS)


def test_the_shot_record_carries_the_heights_impact_location_uses(kiosk, monkeypatch):
    _precise(monkeypatch)
    shot, archive = _shot_seen(0.068)
    server._solve_surface_height(shot, archive)

    recorded = shot.to_dict()["scene_heights"]
    assert recorded["in_force"]["lens_height_m"] == pytest.approx(LENS - 0.068, abs=0.001)


def test_later_shots_use_the_session_aggregate_and_record_what_the_iwr_used(kiosk, monkeypatch):
    _precise(monkeypatch)
    first, archive = _shot_seen(0.066, 1)
    server._solve_surface_height(first, archive)
    second, archive = _shot_seen(0.070, 2, forward_m=1.9)

    server._solve_surface_height(second, archive)

    record = second.scene_heights
    assert record["in_force"]["source"] == sh.SOLVED_SESSION
    assert record["session"]["n"] == 2
    assert record["in_force"]["surface_height_above_base_m"] == pytest.approx(0.068, abs=0.002)
    # this shot's LCMF ran on the heights in force before it: shot 1's
    assert record["iwr_lcmf_used"]["source"] == sh.SOLVED_PER_SHOT
    assert record["iwr_lcmf_used"]["radar_height_m"] == pytest.approx(RADAR - 0.066, abs=0.001)


def test_an_ambiguous_range_never_solves_and_the_rig_stands_labelled(kiosk):
    shot, archive = _shot_seen(0.068, label="estimated")

    server._solve_surface_height(shot, archive)

    record = shot.scene_heights
    assert record["shot_solve"]["reason_code"] == "range_label_not_measured"
    assert record["in_force"]["source"] == sh.RIG_NOMINAL
    assert "range_label_not_measured" in record["in_force"]["reason"]
    assert server.camera_capture_config["mount_height_m"] == pytest.approx(LENS)
    assert kiosk.iwr.calibration.radar_height_m == pytest.approx(RADAR)
    assert kiosk.iwr.calibration.reflecting_plane_depth_m == 0.0


def test_a_wrong_object_never_moves_the_heights(kiosk, monkeypatch):
    _precise(monkeypatch)
    shot, archive = _shot_seen(0.068, diameter_scale=0.5)

    server._solve_surface_height(shot, archive)

    assert shot.scene_heights["shot_solve"]["reason_code"] == "ball_size_disagrees_with_range"
    assert server.camera_capture_config["mount_height_m"] == pytest.approx(LENS)


def test_an_uncalibrated_camera_s_loose_solve_near_the_base_keeps_the_rig(kiosk):
    # 1 deg of pointing at 1.7 m is ~30 mm: a 20 mm surface is not told from the base
    shot, archive = _shot_seen(0.020)

    server._solve_surface_height(shot, archive)

    record = shot.scene_heights
    assert record["shot_solve"]["status"] == "solved"
    assert record["shot_solve"]["sigma_m"] > sh.SURFACE_HEIGHT_MAX_SIGMA_M
    assert record["in_force"]["source"] == sh.RIG_NOMINAL
    assert server.camera_capture_config["mount_height_m"] == pytest.approx(LENS)


def test_a_mock_shot_is_never_solved(kiosk):
    shot, archive = _shot_seen(0.068)
    shot.mode = "mock"

    server._solve_surface_height(shot, archive)

    assert shot.scene_heights is None
    assert server.surface_height_contributions == []


def test_the_setup_s_solve_joins_the_session_and_a_new_setup_starts_it_again(kiosk, monkeypatch):
    _precise(monkeypatch)
    server.apply_setup_scene(
        {
            "state": "set",
            "lens_height_m": LENS - 0.060,
            "uncertainty_m": 0.01,
            "source": "radar_solved",
        }
    )
    assert server.setup_handoff_config["scene"]["lens_height_used_source"] == "radar_solved"
    shot, archive = _shot_seen(0.068, 1)

    server._solve_surface_height(shot, archive)

    record = shot.scene_heights
    assert record["session"]["n"] == 2
    assert record["in_force"]["source"] == sh.SOLVED_SESSION
    assert 0.060 < record["in_force"]["surface_height_above_base_m"] < 0.069

    server.apply_setup_scene(None)

    assert server.surface_height_contributions == []
    assert server.setup_handoff_config["scene"]["lens_height_used_source"] == sh.RIG_NOMINAL
    assert kiosk.iwr.calibration.radar_height_m == pytest.approx(RADAR)


def test_the_surface_is_solved_after_the_iwr_and_before_the_camera_stages(monkeypatch):
    order = []
    monkeypatch.setattr(server, "camera_capture_runtime", None)
    monkeypatch.setattr(server, "kld7_vertical", None)
    monkeypatch.setattr(server, "kld7_horizontal", None)
    monkeypatch.setattr(server, "get_session_logger", lambda: None)
    for name in (
        "_shot_ball_context",
        "_snapshot_inclinometer_for_shot",
        "_record_ball_reference_check",
        "_attach_camera_replay",
    ):
        monkeypatch.setattr(server, name, lambda *_args: None)
    monkeypatch.setattr(server, "_process_iwr6843_angle", lambda *_args: order.append("iwr6843"))
    monkeypatch.setattr(
        server, "_solve_surface_height", lambda *_args: order.append("surface_height")
    )
    monkeypatch.setattr(
        server, "_fuse_camera_measurements", lambda *_args: order.append("camera_fusion")
    )
    shot = Shot(ball_speed_mph=100.0, timestamp=datetime.now(), mode="kiosk")

    server._enrich_shot_from_optional_hardware(shot)

    assert order == ["iwr6843", "surface_height", "camera_fusion"]

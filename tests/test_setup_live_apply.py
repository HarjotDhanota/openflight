"""The setup record's sections reach the running kiosk between shots, without a restart."""

from __future__ import annotations

import inspect
import re
import threading
from types import SimpleNamespace

import pytest

from openflight import server
from openflight.camera.capture_runtime import CameraCaptureSettings
from openflight.iwr6843.calibration import Calibration
from openflight.iwr6843.runtime import IWR6843Runtime

OLD_BOX = (400, 300, 700, 480)
BOX = {
    "state": "confirmed",
    "id": "fixed@640,450#3",
    "box_px": [480.0, 380.0, 800.0, 520.0],
    "ball_px": [640.0, 450.0],
    "diameter_px": 20.0,
    "image_size_px": [1280, 800],
    "source": "placement_box",
    "uncertainty": {"tilt_pad_deg": 1.0, "roll_pad_deg": 2.0},
}
RANGE = {
    "state": "set",
    "range_m": 2.31,
    "uncertainty_m": 0.12,
    "side_offset_m": -0.05,
    "source": "camera_prior",
}
ENCLOSURE = SimpleNamespace(
    camera_mount_height_m=0.095,
    radar_height_m=0.051,
    tee_lateral_offset_m=None,
    iwr_board_rotation_deg=None,
)


@pytest.fixture(name="kiosk")
def _kiosk(monkeypatch):
    """A running kiosk with a camera and an IWR6843, started with no setup."""
    calibration = Calibration.identity()
    calibration.meta["radar_height_m"] = 0.051
    iwr = IWR6843Runtime(capture_monitor=None, calibration=calibration, net_range_m=4.6)
    camera = SimpleNamespace(settings=CameraCaptureSettings(width=1280, height=800))
    monkeypatch.setattr(server, "iwr6843_runtime", iwr)
    monkeypatch.setattr(server, "iwr6843_runtime_config", {"enabled": True})
    monkeypatch.setattr(server, "camera_capture_runtime", camera)
    monkeypatch.setattr(
        server,
        "camera_capture_config",
        {"enabled": True, "setup_ball": None, "hitting_zone": None, "mount_height_m": 0.095},
    )
    monkeypatch.setattr(server, "camera_ball_box_px", None)
    monkeypatch.setattr(server, "ball_at_address_monitor", None)
    monkeypatch.setattr(server, "_ball_at_address_range", (None, None))
    monkeypatch.setattr(server, "_ball_at_address_loop", lambda: None)
    monkeypatch.setattr(server, "rig_geometry", SimpleNamespace(enclosure_setup=lambda: ENCLOSURE))
    monkeypatch.setattr(server, "rig_geometry_config", {"enabled": True})
    monkeypatch.setattr(
        server,
        "setup_handoff_config",
        {"tee_range": None, "scene": None, "net_range": None, "target_line": None},
    )
    monkeypatch.setattr(server, "shot_processing_pending", lambda: False)
    monkeypatch.setattr(server, "get_session_logger", lambda: None)
    return SimpleNamespace(iwr=iwr, camera=camera)


def test_box_change_waits_for_shot_in_progress(kiosk, monkeypatch):
    server.camera_ball_box_px = OLD_BOX
    shot = threading.Event()
    shot.set()
    monkeypatch.setattr(server, "shot_processing_pending", shot.is_set)

    change = threading.Thread(target=server.apply_setup_box, args=(BOX,))
    change.start()
    change.join(0.3)

    assert change.is_alive()
    assert server.camera_ball_box_px == OLD_BOX
    shot.clear()
    change.join(5.0)
    assert not change.is_alive()
    assert server.camera_ball_box_px == (480, 380, 800, 520)


def test_a_box_reaches_the_monitor_and_the_capture_s_setup_ball_and_zone(kiosk):
    server.apply_setup_box(BOX)

    ball = {"x": 640.0, "y": 450.0, "diameter_px": 20.0}
    assert server.ball_at_address_monitor.box == (480, 380, 800, 520)
    assert server.ball_at_address_monitor.setup_ball == ball
    assert kiosk.camera.settings.setup_ball == ball
    assert kiosk.camera.settings.hitting_zone == (480, 380, 800, 520)
    assert server.camera_capture_config["setup_ball"] == ball
    assert server.camera_capture_config["hitting_zone"] == [480, 380, 800, 520]


def test_a_new_box_reconfigures_the_running_monitor_and_keeps_the_range(kiosk):
    server.apply_setup_ball_range(RANGE)
    server.apply_setup_box(BOX)
    monitor = server.ball_at_address_monitor

    server.apply_setup_box({**BOX, "box_px": [500, 390, 820, 530], "ball_px": [660.0, 460.0]})

    assert server.ball_at_address_monitor is monitor
    assert monitor.box == (500, 390, 820, 530)
    assert monitor.setup_range_m == pytest.approx(2.31)
    assert monitor.setup_range_kind == "camera_prior"


def test_clearing_box_stops_ball_at_address(kiosk):
    server.apply_setup_box(BOX)

    server.apply_setup_box(None)

    look = server.app.test_client().get("/api/ball-at-address").get_json()
    assert look["state"] == "unknown"
    assert look["reason"] == "no ball box"
    assert server.camera_ball_box_px is None
    assert kiosk.camera.settings.setup_ball is None
    assert kiosk.camera.settings.hitting_zone is None
    assert server.camera_capture_config["setup_ball"] is None
    assert server.camera_capture_config["hitting_zone"] is None


def test_a_box_confirmed_at_1280x800_is_halved_at_640x400(kiosk):
    kiosk.camera.settings = CameraCaptureSettings(width=640, height=400)

    server.apply_setup_box(BOX)

    assert server.camera_ball_box_px == (240, 190, 400, 260)
    assert kiosk.camera.settings.hitting_zone == (240, 190, 400, 260)
    assert kiosk.camera.settings.setup_ball == {"x": 320.0, "y": 225.0, "diameter_px": 10.0}
    assert server.ball_at_address_monitor.box == (240, 190, 400, 260)


@pytest.mark.parametrize(
    "size, reason",
    [([1280, 720], "1280x720"), (None, "no picture size")],
)
def test_a_box_from_another_picture_shape_is_cleared_with_a_reason(kiosk, size, reason):
    server.apply_setup_box(BOX)
    stale = {key: value for key, value in BOX.items() if key != "image_size_px"}
    if size is not None:
        stale["image_size_px"] = size

    with pytest.raises(ValueError, match=reason):
        server.apply_setup_box(stale)

    assert server.camera_ball_box_px is None
    assert server.ball_at_address_monitor is None
    assert kiosk.camera.settings.hitting_zone is None


def test_a_box_outside_the_picture_is_refused(kiosk):
    server.apply_setup_box(BOX)

    with pytest.raises(ValueError, match="outside the 1280x800 picture"):
        server.apply_setup_box({**BOX, "box_px": [480, 380, 1300, 520]})

    assert server.camera_ball_box_px is None
    assert server.ball_at_address_monitor is None


def test_a_box_without_a_camera_starts_no_monitor(kiosk, monkeypatch):
    monkeypatch.setattr(server, "camera_capture_runtime", None)

    server.apply_setup_box(BOX)

    assert server.ball_at_address_monitor is None


def test_ball_range_change_updates_iwr_prior(kiosk):
    server.apply_setup_ball_range(RANGE)

    assert server.iwr6843_runtime is kiosk.iwr
    prior = kiosk.iwr.range_prior
    assert prior.range_m == pytest.approx(2.31)
    assert prior.uncertainty_m == pytest.approx(0.12)
    assert prior.source == "camera_prior"
    assert kiosk.iwr.calibration.tee_range_m is None
    assert kiosk.iwr.calibration.lateral_tee_offset_m == pytest.approx(-0.05)
    assert server.iwr6843_runtime_config["range_prior"]["range_m"] == pytest.approx(2.31)
    assert server.iwr6843_runtime_config["tee_range_status"] == "per_shot_with_camera_prior"
    assert server.camera_capture_config["tee_side_offset_m"] == pytest.approx(-0.05)
    handoff = server.setup_handoff_config["tee_range"]
    assert handoff["status"] == "prior"
    assert handoff["prior"]["uncertainty_m"] == pytest.approx(0.12)
    assert handoff["prior"]["source"] == "camera_prior"


def test_a_range_set_before_the_box_starts_the_monitor_with_it(kiosk):
    server.apply_setup_ball_range(RANGE)
    server.apply_setup_box(BOX)

    monitor = server.ball_at_address_monitor
    assert monitor.setup_range_m == pytest.approx(2.31)
    assert monitor.setup_range_uncertainty_m == pytest.approx(0.12)


def test_clearing_the_ball_range_returns_to_per_shot_ranging(kiosk):
    server.apply_setup_box(BOX)
    server.apply_setup_ball_range(RANGE)

    server.apply_setup_ball_range(None)

    assert kiosk.iwr.range_prior is None
    assert kiosk.iwr.calibration.lateral_tee_offset_m == 0.0
    assert server.iwr6843_runtime_config["tee_range_status"] == "per_shot_without_prior"
    assert server.ball_at_address_monitor.setup_range_m is None
    assert server.setup_handoff_config["tee_range"]["status"] == "pending"
    assert server.setup_handoff_config["scene"]["tee_side_offset_source"] == (
        "assumed_directly_downrange"
    )


def test_a_measured_net_range_reaches_the_radar_gate_and_clearing_restores_the_default(kiosk):
    server.apply_setup_net_range(
        {"state": "set", "value_m": 3.9, "uncertainty_m": 0.2, "source": "camera_floor_line"}
    )

    assert kiosk.iwr.net_range_m == pytest.approx(3.9)
    assert server.iwr6843_runtime_config["net_range_m"] == pytest.approx(3.9)
    net = server.setup_handoff_config["net_range"]
    assert net["source"] == "camera_floor_line"
    assert net["uncertainty_m"] == pytest.approx(0.2)
    assert net["assumed"] is False

    server.apply_setup_net_range(None)

    assert kiosk.iwr.net_range_m == pytest.approx(server.DEFAULT_NET_RANGE_M)
    net = server.setup_handoff_config["net_range"]
    assert net["source"] == "default_not_measured"
    assert net["assumed"] is True


def test_a_solved_scene_moves_camera_and_radar_and_clearing_returns_to_the_rig(kiosk):
    server.apply_setup_scene(
        {"state": "set", "lens_height_m": 0.081, "uncertainty_m": 0.01, "source": "radar_solved"}
    )

    assert server.camera_capture_config["mount_height_m"] == pytest.approx(0.081)
    assert kiosk.iwr.calibration.radar_height_m == pytest.approx(0.037)
    assert server.iwr6843_runtime_config["radar_height_m"] == pytest.approx(0.037)
    assert server.rig_geometry_config["solved_camera_height"]["camera_mount_height_m"] == (
        pytest.approx(0.081)
    )
    scene = server.setup_handoff_config["scene"]
    assert scene["lens_height_used_source"] == "radar_solved"
    assert scene["lens_height_solved_uncertainty_m"] == pytest.approx(0.01)

    server.apply_setup_scene(None)

    assert server.camera_capture_config["mount_height_m"] == pytest.approx(0.095)
    assert kiosk.iwr.calibration.radar_height_m == pytest.approx(0.051)
    assert "solved_camera_height" not in server.rig_geometry_config
    assert server.setup_handoff_config["scene"]["lens_height_used_source"] == "rig_nominal"


def test_a_scene_that_would_bury_the_radar_changes_nothing(kiosk):
    with pytest.raises(ValueError, match="below the hitting surface"):
        server.apply_setup_scene({"state": "set", "lens_height_m": 0.03, "source": "radar_solved"})

    assert server.camera_capture_config["mount_height_m"] == pytest.approx(0.095)
    assert kiosk.iwr.calibration.radar_height_m == pytest.approx(0.051)


def test_the_aim_section_moves_the_target_line(kiosk, monkeypatch):
    monkeypatch.setattr(server, "horizontal_offset_deg", None)
    monkeypatch.setattr(server, "_target_line_revision", 0)
    aim = {
        "state": "set",
        "yaw_deg": 3.0,
        "uncertainty_deg": 0.4,
        "method": "aim_point_direction",
        "source": "aim_point",
    }

    server.apply_setup_aim(aim)

    assert server.horizontal_offset_deg == pytest.approx(-3.0)
    assert kiosk.iwr.azimuth_offset_deg == pytest.approx(-3.0)
    line = server.setup_handoff_config["target_line"]
    assert line["total_uncertainty_deg"] == pytest.approx(0.4)
    assert line["method"] == "aim_point_direction"

    server.apply_setup_aim({"state": "skipped", "source": "skipped"})

    assert server.horizontal_offset_deg is None
    assert server.target_line_in_force()["status"] == "skipped"

    server.apply_setup_aim(None)

    assert server.horizontal_offset_deg is None
    assert server.target_line_in_force()["status"] == "not_set"
    assert server._target_line_revision == 3


OUTLINE = [[520.0, 500.0], [760.0, 500.0], [760.0, 400.0], [520.0, 400.0]]


def test_the_confirmed_outline_reaches_the_monitor_scaled_with_the_mode(kiosk):
    kiosk.camera.settings = CameraCaptureSettings(width=640, height=400)

    server.apply_setup_box({**BOX, "outline_px": OUTLINE})

    monitor = server.ball_at_address_monitor
    assert monitor.outline_px == ((260.0, 250.0), (380.0, 250.0), (380.0, 200.0), (260.0, 200.0))
    # the detector's window stays the bounding rectangle
    assert monitor.box == (240, 190, 400, 260)


def test_an_older_box_without_an_outline_is_tested_as_its_rectangle(kiosk):
    server.apply_setup_box(BOX)

    monitor = server.ball_at_address_monitor
    assert monitor.outline_px is None
    assert monitor._in_box(482.0, 382.0)  # pylint: disable=protected-access
    assert not monitor._in_box(470.0, 382.0)  # pylint: disable=protected-access


def test_a_range_change_keeps_the_outline(kiosk):
    server.apply_setup_box({**BOX, "outline_px": OUTLINE})

    server.apply_setup_ball_range(RANGE)

    assert server.ball_at_address_monitor.outline_px[0] == (520.0, 500.0)


def test_the_preview_the_overlays_draw_on_is_not_rolled():
    """The Camera and Live views draw setup pixels on /api/camera/preview.jpg, which the
    capture rotates by its roll correction: the kiosk must start the camera unrolled."""
    assert inspect.signature(server.init_camera_capture).parameters[
        "roll_correction_deg"
    ].default == pytest.approx(0.0)
    calls = re.findall(r"init_camera_capture\((.*?)\):", inspect.getsource(server.main), re.S)
    assert calls, "the kiosk no longer starts the camera through init_camera_capture"
    assert all("roll_correction_deg" not in call for call in calls)

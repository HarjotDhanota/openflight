"""The session's solved lens height replaces the rig file's nominal one."""

from types import SimpleNamespace

import pytest

from openflight import server


def test_the_solved_height_moves_camera_and_radar_together():
    args = SimpleNamespace(
        solved_camera_height_m=0.081,
        camera_capture_mount_height_m=0.095,
        iwr6843_radar_height_m=0.051,
    )
    enclosure = SimpleNamespace(camera_mount_height_m=0.095, radar_height_m=0.051)

    server._apply_solved_camera_height(args, enclosure)

    assert args.camera_capture_mount_height_m == pytest.approx(0.081)
    assert args.iwr6843_radar_height_m == pytest.approx(0.037)


def test_an_impossible_solved_height_is_refused():
    args = SimpleNamespace(
        solved_camera_height_m=1.5, camera_capture_mount_height_m=0.095, iwr6843_radar_height_m=None
    )

    with pytest.raises(ValueError, match="between 0 and 1 m"):
        server._apply_solved_camera_height(args, None)


def test_a_ball_on_the_ground_is_the_default_ball_height():
    assert server.BALL_RADIUS_M == pytest.approx(0.021335)


def test_a_ball_teed_above_the_radar_lifts_every_height_and_keeps_the_differences():
    # lens 30 mm above the tee top: the radar, 44 mm below the lens, is under the tee top
    args = SimpleNamespace(
        solved_camera_height_m=0.030,
        camera_capture_mount_height_m=0.095,
        iwr6843_radar_height_m=0.051,
        iwr6843_ball_height_m=0.021335,
    )
    enclosure = SimpleNamespace(camera_mount_height_m=0.095, radar_height_m=0.051)

    server._apply_solved_camera_height(args, enclosure)

    assert args.iwr6843_radar_height_m == pytest.approx(server.MIN_DATUM_CLEARANCE_M)
    assert args.camera_capture_mount_height_m - args.iwr6843_radar_height_m == pytest.approx(0.044)
    assert args.iwr6843_ball_height_m - args.camera_capture_mount_height_m == pytest.approx(
        0.021335 - 0.030
    )


def test_the_solved_height_is_recorded_in_the_session_geometry(monkeypatch):
    monkeypatch.setattr(
        server,
        "rig_geometry_config",
        {"enabled": True, "derived": {"camera_mount_height_m": 0.095, "radar_height_m": 0.051}},
    )
    args = SimpleNamespace(
        solved_camera_height_m=0.081,
        camera_capture_mount_height_m=0.095,
        iwr6843_radar_height_m=0.051,
        iwr6843_ball_height_m=0.021335,
    )

    server._apply_solved_camera_height(
        args, SimpleNamespace(camera_mount_height_m=0.095, radar_height_m=0.051)
    )

    recorded = server.rig_geometry_config
    assert recorded["derived"]["camera_mount_height_m"] == pytest.approx(0.081)
    assert recorded["derived"]["radar_height_m"] == pytest.approx(0.037)
    assert recorded["solved_camera_height"]["source"] == "range_setup"
    assert recorded["solved_camera_height"]["reference"] == "ball_support"

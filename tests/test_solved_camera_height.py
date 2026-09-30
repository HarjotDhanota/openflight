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


def test_a_lens_height_that_would_bury_the_radar_is_refused():
    # heights are above the hitting surface: the radar, 44 mm below the lens,
    # cannot be under it, so nothing is lifted to make it fit
    args = SimpleNamespace(
        solved_camera_height_m=0.030,
        camera_capture_mount_height_m=0.095,
        iwr6843_radar_height_m=0.051,
        iwr6843_ball_height_m=0.021335,
    )
    enclosure = SimpleNamespace(camera_mount_height_m=0.095, radar_height_m=0.051)

    with pytest.raises(ValueError, match="below the hitting surface"):
        server._apply_solved_camera_height(args, enclosure)


def test_a_teed_ball_never_moves_the_radar_height():
    # the floor-bounce model needs the radar above the surface it reflects from;
    # the ball's own height is a separate input
    args = SimpleNamespace(
        solved_camera_height_m=0.095,
        camera_capture_mount_height_m=0.095,
        iwr6843_radar_height_m=0.051,
        iwr6843_ball_height_m=0.060,
    )
    enclosure = SimpleNamespace(camera_mount_height_m=0.095, radar_height_m=0.051)

    server._apply_solved_camera_height(args, enclosure)

    assert args.iwr6843_radar_height_m == pytest.approx(0.051)
    assert args.iwr6843_ball_height_m == pytest.approx(0.060)


def test_the_solved_height_is_recorded_apart_from_the_rig_file(monkeypatch):
    # wiring audit C4/C13: "derived" stays what the rig file says
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
    assert recorded["derived"]["camera_mount_height_m"] == pytest.approx(0.095)
    assert recorded["derived"]["radar_height_m"] == pytest.approx(0.051)
    assert recorded["solved_camera_height"]["camera_mount_height_m"] == pytest.approx(0.081)
    assert recorded["solved_camera_height"]["radar_height_m"] == pytest.approx(0.037)
    assert recorded["solved_camera_height"]["source"] == "range_setup"
    assert recorded["solved_camera_height"]["reference"] == "hitting_surface"
    assert "datum_shift_m" not in recorded["solved_camera_height"]


def _handoff_args(**overrides):
    values = {
        "iwr6843_tee_m": None,
        "iwr6843_tee_range_source": None,
        "iwr6843_tee_range_candidate": None,
        "solved_camera_height_m": None,
        "camera_capture_mount_height_m": 0.095,
        "iwr6843_radar_height_m": 0.051,
        "iwr6843_ball_height_m": server.BALL_RADIUS_M,
        "scene_lens_height_solved_m": None,
        "scene_lens_height_solved_uncertainty_m": None,
        "iwr6843": True,
        "iwr6843_net_m": server.DEFAULT_NET_RANGE_M,
        "iwr6843_net_range_source": "default_not_measured",
    }
    return SimpleNamespace(**{**values, **overrides})


def test_the_session_records_where_its_tee_range_and_heights_came_from(monkeypatch):
    # wiring audit S3, C4, C5: recorded in session_start
    monkeypatch.setattr(server, "setup_handoff_config", {"tee_range": None, "scene": None})
    enclosure = SimpleNamespace(camera_mount_height_m=0.095, radar_height_m=0.051)

    server.init_setup_handoff(
        _handoff_args(
            iwr6843_tee_m=1.2,
            iwr6843_tee_range_source="unqualified_static_iwr",
            iwr6843_tee_range_candidate="iwr-static-setup-1",
            scene_lens_height_solved_m=0.11,
            scene_lens_height_solved_uncertainty_m=0.03,
        ),
        enclosure,
    )

    recorded = server._session_start_config()
    assert recorded["tee_range_handoff"] == {
        "tee_slant_range_m": 1.2,
        "status": "configured",
        "source": "unqualified_static_iwr",
        "candidate_id": "iwr-static-setup-1",
    }
    scene = recorded["scene"]
    assert scene["lens_height_used_m"] == pytest.approx(0.095)
    assert scene["lens_height_used_source"] == "rig_nominal"
    assert scene["lens_height_solved_m"] == pytest.approx(0.11)
    assert scene["lens_height_solved_uncertainty_m"] == pytest.approx(0.03)
    assert scene["ball_height_basis"] == "assumed_on_surface"
    # C7: without a measured net the 4.6 m default is flagged
    assert recorded["net_range"] == {
        "net_range_m": pytest.approx(4.6),
        "source": "default_not_measured",
        "range_space": "apparent",
        "assumed": True,
    }


def test_a_pending_session_without_a_source_says_pending(monkeypatch):
    monkeypatch.setattr(server, "setup_handoff_config", {"tee_range": None, "scene": None})

    server.init_setup_handoff(_handoff_args(solved_camera_height_m=0.4), None)

    handoff = server.setup_handoff_config
    assert handoff["tee_range"]["status"] == handoff["tee_range"]["source"] == "pending"
    assert handoff["scene"]["lens_height_used_source"] == "range_setup"


def test_a_typed_ball_height_is_not_labelled_as_assumed(monkeypatch):
    monkeypatch.setattr(server, "setup_handoff_config", {"tee_range": None, "scene": None})

    server.init_setup_handoff(_handoff_args(iwr6843_ball_height_m=0.06), None)

    assert server.setup_handoff_config["scene"]["ball_height_basis"] == "command_line"


def test_the_cli_takes_the_setup_hand_off():
    import argparse  # noqa: PLC0415

    parser = argparse.ArgumentParser()
    server._add_iwr_tee_range_arguments(parser)

    args = parser.parse_args(
        [
            "--iwr6843-tee-range-source",
            "unqualified_static_iwr_camera_steered",
            "--iwr6843-tee-range-candidate",
            "iwr-static-setup-1",
        ]
    )

    assert args.iwr6843_tee_range_source == "unqualified_static_iwr_camera_steered"
    assert args.iwr6843_tee_range_candidate == "iwr-static-setup-1"

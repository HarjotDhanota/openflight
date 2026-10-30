"""The setup record's solved lens height replaces the rig file's nominal one."""

from types import SimpleNamespace

import pytest

from openflight import server

ENCLOSURE = SimpleNamespace(
    camera_mount_height_m=0.095,
    radar_height_m=0.051,
    tee_lateral_offset_m=None,
    iwr_board_rotation_deg=None,
)


@pytest.mark.parametrize(("lens_m", "radar_m"), [(0.081, 0.037), (0.095, 0.051)])
def test_the_solved_height_moves_the_radar_with_the_camera(lens_m, radar_m):
    assert server._solved_radar_height(lens_m, ENCLOSURE, name="h") == pytest.approx(radar_m)


@pytest.mark.parametrize(
    ("lens_m", "enclosure", "match"),
    [
        (1.5, None, "between 0 and 1 m"),
        # heights are above the hitting surface: the radar 44 mm below the lens cannot be under it
        (0.030, ENCLOSURE, "below the hitting surface"),
    ],
)
def test_an_impossible_solved_height_is_refused(lens_m, enclosure, match):
    with pytest.raises(ValueError, match=match):
        server._solved_radar_height(lens_m, enclosure, name="the solved lens height")


def test_the_solved_height_is_recorded_apart_from_the_rig_file(monkeypatch):
    monkeypatch.setattr(
        server,
        "rig_geometry_config",
        {"enabled": True, "derived": {"camera_mount_height_m": 0.095, "radar_height_m": 0.051}},
    )
    monkeypatch.setattr(server, "rig_geometry", SimpleNamespace(enclosure_setup=lambda: ENCLOSURE))
    monkeypatch.setattr(server, "iwr6843_runtime", None)
    monkeypatch.setattr(server, "camera_capture_config", {"enabled": False})
    monkeypatch.setattr(server, "setup_handoff_config", {})
    monkeypatch.setattr(server, "shot_processing_pending", lambda: False)

    server.apply_setup_scene(
        {
            "state": "set",
            "lens_height_m": 0.081,
            "uncertainty_m": 0.01,
            "source": "radar_solved_height",
        }
    )

    recorded = server.rig_geometry_config
    assert recorded["derived"]["camera_mount_height_m"] == pytest.approx(0.095)
    assert recorded["derived"]["radar_height_m"] == pytest.approx(0.051)
    assert recorded["solved_camera_height"]["camera_mount_height_m"] == pytest.approx(0.081)
    assert recorded["solved_camera_height"]["radar_height_m"] == pytest.approx(0.037)
    assert recorded["solved_camera_height"]["source"] == "radar_solved_height"
    assert recorded["solved_camera_height"]["reference"] == "hitting_surface"
    assert "datum_shift_m" not in recorded["solved_camera_height"]


def _handoff_args(**overrides):
    values = {
        "iwr6843_ball_height_m": server.BALL_RADIUS_M,
        "camera_vertical_offset_deg": None,
        "camera_vertical_offset_source": None,
    }
    return SimpleNamespace(**{**values, **overrides})


def test_the_session_starts_with_the_rig_s_heights_and_nothing_else_filled_in(monkeypatch):
    monkeypatch.setattr(server, "setup_handoff_config", {"tee_range": None, "scene": None})

    server.init_setup_handoff(_handoff_args(), ENCLOSURE)

    recorded = server._session_start_config()
    assert recorded["tee_range_handoff"] == {
        "tee_slant_range_m": None,
        "status": "pending",
        "source": "pending",
        "candidate_id": None,
    }
    scene = recorded["scene"]
    assert scene["lens_height_used_m"] == pytest.approx(0.095)
    assert scene["lens_height_used_source"] == "rig_nominal"
    assert scene["lens_height_solved_m"] is None
    assert scene["radar_height_m"] == pytest.approx(0.051)
    assert scene["ball_height_basis"] == "assumed_on_surface"
    assert scene["tee_side_offset_source"] == "assumed_directly_downrange"
    assert recorded["net_range"] == {
        "net_range_m": pytest.approx(5.3528),
        "source": "default_not_measured",
        "range_space": "apparent",
        "assumed": True,
    }


def test_without_a_rig_file_no_lens_height_is_claimed(monkeypatch):
    monkeypatch.setattr(server, "setup_handoff_config", {"tee_range": None, "scene": None})

    server.init_setup_handoff(_handoff_args(), None)

    scene = server.setup_handoff_config["scene"]
    assert scene["lens_height_used_m"] is None
    assert scene["lens_height_used_source"] is None


def test_a_typed_ball_height_is_not_labelled_as_assumed(monkeypatch):
    monkeypatch.setattr(server, "setup_handoff_config", {"tee_range": None, "scene": None})

    server.init_setup_handoff(_handoff_args(iwr6843_ball_height_m=0.06), None)

    assert server.setup_handoff_config["scene"]["ball_height_basis"] == "command_line"

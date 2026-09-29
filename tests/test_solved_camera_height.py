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

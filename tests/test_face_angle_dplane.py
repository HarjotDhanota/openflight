"""Experimental D-plane face angle from start direction and club path."""

from datetime import datetime

import pytest

from openflight import server
from openflight.launch_monitor import ClubType, Shot


def _shot(**fields):
    return Shot(ball_speed_mph=120.0, timestamp=datetime.now(), club=ClubType.IRON_7, **fields)


def test_face_angle_is_mostly_start_direction_and_partly_path():
    shot = _shot(launch_angle_horizontal=2.0, launch_angle_horizontal_source="camera")
    shot.experimental_fused_club_path_deg = -4.0

    server._attach_experimental_face_angle(shot)

    # start = 0.8 face + 0.2 path  ->  face = (2 + 0.8) / 0.8
    assert shot.experimental_face_angle_deg == pytest.approx(3.5)
    assert shot.experimental_face_angle_status == "d_plane_estimate"
    assert shot.to_dict()["experimental_face_angle_deg"] == pytest.approx(3.5)


@pytest.mark.parametrize(
    ("fields", "path", "status"),
    [
        ({}, -4.0, "missing_measured_start_direction"),
        (
            {"launch_angle_horizontal": 0.0, "launch_angle_horizontal_source": "estimated"},
            -4.0,
            "missing_measured_start_direction",
        ),
        (
            {"launch_angle_horizontal": 1.0, "launch_angle_horizontal_source": "radar"},
            None,
            "missing_club_path",
        ),
    ],
)
def test_no_face_angle_without_a_measured_direction_and_path(fields, path, status):
    shot = _shot(**fields)
    shot.experimental_fused_club_path_deg = path

    server._attach_experimental_face_angle(shot)

    assert shot.experimental_face_angle_deg is None
    assert shot.experimental_face_angle_status == status

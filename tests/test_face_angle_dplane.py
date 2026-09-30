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
            "missing_accepted_club_path",
        ),
    ],
)
def test_no_face_angle_without_a_measured_direction_and_path(fields, path, status):
    shot = _shot(**fields)
    shot.experimental_fused_club_path_deg = path

    server._attach_experimental_face_angle(shot)

    assert shot.experimental_face_angle_deg is None
    assert shot.experimental_face_angle_status == status


def _measured_start(**fields):
    return _shot(
        launch_angle_horizontal=2.0,
        launch_angle_horizontal_source="camera_assisted_experimental",
        **fields,
    )


@pytest.mark.parametrize(
    "iwr_status", ["candidate_out_of_bounds", "candidate_noisy_fit", "rejected_phase_span"]
)
def test_iwr_path_that_is_not_accepted_gives_no_face_angle(iwr_status):
    # F1: the kiosk shows an IWR candidate with its status, but face angle is
    # built only on an accepted path.
    shot = _measured_start(
        experimental_club_path_deg=35.2, experimental_club_path_status=iwr_status
    )

    server._attach_experimental_face_angle(shot)

    assert shot.experimental_face_angle_deg is None
    assert shot.experimental_face_angle_status == "missing_accepted_club_path"
    assert shot.experimental_face_angle_path_source is None


@pytest.mark.parametrize(
    ("fused_status", "source"),
    [
        ("chained_high", "camera_fused_chained"),
        ("approach_mixed", "camera_fused_chained"),
        ("camera_ops_fallback", "camera_fused_ops"),
    ],
)
def test_accepted_camera_path_gives_face_angle_with_its_source(fused_status, source):
    shot = _measured_start(
        experimental_fused_club_path_deg=-4.0,
        experimental_fused_status=fused_status,
        experimental_fused_club_path_confidence="low",
    )

    server._attach_experimental_face_angle(shot)

    assert shot.experimental_face_angle_deg == pytest.approx(3.5)
    assert shot.experimental_face_angle_path_source == source
    assert shot.experimental_face_angle_launch_source == "camera_assisted_experimental"
    record = shot.to_dict()
    assert record["experimental_face_angle_path_source"] == source
    assert record["experimental_face_angle_launch_source"] == "camera_assisted_experimental"


def test_face_angle_uses_the_camera_path_the_kiosk_shows_over_an_iwr_path():
    shot = _measured_start(
        experimental_fused_club_path_deg=-4.0,
        experimental_fused_status="chained_high",
        experimental_club_path_deg=6.0,
        experimental_club_path_status="accepted",
    )

    server._attach_experimental_face_angle(shot)

    assert shot.experimental_face_angle_deg == pytest.approx(3.5)
    assert shot.experimental_face_angle_path_source == "camera_fused_chained"


def test_rejected_camera_fusion_hides_the_iwr_path_from_face_angle():
    # The kiosk hides the IWR path once camera fusion ran, so face angle must too.
    shot = _measured_start(
        experimental_fused_status="rejected_no_impact",
        experimental_club_path_deg=6.0,
        experimental_club_path_status="accepted",
    )

    server._attach_experimental_face_angle(shot)

    assert shot.experimental_face_angle_deg is None
    assert shot.experimental_face_angle_status == "missing_accepted_club_path"


def test_accepted_iwr_path_without_camera_fusion_is_used_and_named():
    shot = _measured_start(
        experimental_club_path_deg=-4.0, experimental_club_path_status="accepted"
    )

    server._attach_experimental_face_angle(shot)

    assert shot.experimental_face_angle_deg == pytest.approx(3.5)
    assert shot.experimental_face_angle_path_source == "iwr"


def test_displayed_club_path_withholds_a_withheld_camera_path():
    shot = _measured_start(
        experimental_fused_club_path_deg=-4.0,
        experimental_fused_status="approach_path_only",
        experimental_fused_club_path_confidence="withheld",
    )

    assert server.displayed_club_path(shot) == (None, None)

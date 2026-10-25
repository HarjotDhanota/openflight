"""D-plane face angle from start direction and club path (E2 collision model).

The face angle comes from inverting the published PING launch ratio (Wood,
Henrikson & Broadie 2018): a 7-iron ball starts 69 % of the way from the club path
to the face, so face = path + (launch - path) / 0.69. It replaced the fixed 0.8
weight.
"""

from datetime import datetime

import pytest

from openflight import shot_models
from openflight.launch_monitor import ClubType, Shot


def _shot(**fields):
    return Shot(ball_speed_mph=120.0, timestamp=datetime.now(), club=ClubType.IRON_7, **fields)


def test_face_angle_is_mostly_start_direction_and_partly_path():
    shot = _shot(
        launch_angle_horizontal=2.0,
        launch_angle_horizontal_source="camera",
        launch_angle_horizontal_confidence=0.9,
    )
    shot.experimental_fused_club_path_deg = -4.0

    shot_models._attach_collision_model(shot)

    # 7-iron: start = path + 0.69 (face - path)  ->  face = -4 + 6 / 0.69
    assert shot.experimental_face_angle_deg == pytest.approx(4.7)
    assert shot.face_angle_deg == pytest.approx(4.7)
    assert shot.experimental_face_angle_status == "d_plane_estimate"
    assert shot.to_dict()["experimental_face_angle_deg"] == pytest.approx(4.7)


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

    shot_models._attach_collision_model(shot)

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

    shot_models._attach_collision_model(shot)

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

    shot_models._attach_collision_model(shot)

    assert shot.experimental_face_angle_deg == pytest.approx(4.7)
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

    shot_models._attach_collision_model(shot)

    assert shot.experimental_face_angle_deg == pytest.approx(4.7)
    assert shot.experimental_face_angle_path_source == "camera_fused_chained"


@pytest.mark.parametrize("fused_status", ["rejected_no_impact", "rejected_no_camera_capture"])
def test_a_rejected_camera_fusion_leaves_the_accepted_iwr_path(fused_status):
    # Order: camera + IWR fused, accepted IWR, camera head track, the arc.
    shot = _measured_start(
        experimental_fused_status=fused_status,
        experimental_club_path_deg=6.0,
        experimental_club_path_status="accepted",
    )

    shot_models._attach_collision_model(shot)

    assert shot_models.displayed_club_path(shot) == (6.0, "iwr")
    assert shot.experimental_face_angle_path_source == "iwr"


def _head_track(**fields):
    return _measured_start(
        experimental_fused_status="camera_head_track",
        experimental_fused_club_path_deg=-4.0,
        experimental_fused_attack_angle_deg=-3.0,
        experimental_fused_club_path_confidence="experimental",
        experimental_fused_attack_angle_confidence="experimental",
        **fields,
    )


def test_the_head_track_feeds_the_face_angle_estimated_with_its_reason():
    """Owner decision 5: fed on, its outputs labelled experimental in shot_metrics."""
    shot = _head_track()

    shot_models._attach_collision_model(shot)

    assert shot_models.displayed_club_path(shot) == (-4.0, "camera_head_track")
    assert shot_models.displayed_attack_angle(shot) == (-3.0, "camera_head_track")
    assert shot.experimental_face_angle_deg is not None
    assert shot.experimental_face_angle_path_source == "camera_head_track"
    inputs = shot.collision_model["inputs"]
    assert inputs["club_path"]["value"] == -4.0
    assert inputs["club_path"]["label"] == "estimated"
    assert inputs["club_path"]["reason"] == "camera head track (experimental)"


def test_an_accepted_iwr_path_outranks_the_head_track():
    shot = _head_track(experimental_club_path_deg=6.0, experimental_club_path_status="accepted")

    assert shot_models.displayed_club_path(shot) == (6.0, "iwr")


def test_a_flagged_head_track_angle_is_not_shown():
    shot = _head_track()
    shot.experimental_fused_attack_angle_deg = None
    shot.experimental_fused_attack_angle_confidence = "withheld"

    assert shot_models.displayed_attack_angle(shot) == (None, None)


def test_accepted_iwr_path_without_camera_fusion_is_used_and_named():
    shot = _measured_start(
        experimental_club_path_deg=-4.0, experimental_club_path_status="accepted"
    )

    shot_models._attach_collision_model(shot)

    assert shot.experimental_face_angle_deg == pytest.approx(4.7)
    assert shot.experimental_face_angle_path_source == "iwr"


def test_displayed_club_path_withholds_a_withheld_camera_path():
    shot = _measured_start(
        experimental_fused_club_path_deg=-4.0,
        experimental_fused_status="approach_path_only",
        experimental_fused_club_path_confidence="withheld",
    )

    assert shot_models.displayed_club_path(shot) == (None, None)


def test_a_frame_rotation_moves_face_with_launch_and_path():
    """F9: turning launch and path by the same offset turns face angle with them.

    Face-to-path, the quantity the offset must not change, stays the same.
    """
    faces = []
    for offset in (0.0, 2.0):
        shot = _measured_start(
            experimental_fused_club_path_deg=-4.0 + offset,
            experimental_fused_status="chained_high",
        )
        shot.launch_angle_horizontal = 2.0 + offset
        shot_models._attach_collision_model(shot)
        faces.append((shot.experimental_face_angle_deg, shot.experimental_fused_club_path_deg))

    (face_0, path_0), (face_2, path_2) = faces
    assert face_2 == pytest.approx(face_0 + 2.0)
    assert face_2 - path_2 == pytest.approx(face_0 - path_0)

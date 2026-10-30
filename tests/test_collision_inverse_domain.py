"""The inverse must return a delivery in the forward collision model's domain."""

import pytest

from openflight import collision_forward as cf
from openflight.clubs import ClubType


@pytest.mark.parametrize("club", [ClubType.DRIVER, ClubType.IRON_7, ClubType.SW])
@pytest.mark.parametrize("elevation,heading", [(0.0, 0.0), (10.0, -5.0), (-5.0, 8.0)])
def test_parallel_launch_recovers_zero_spin_loft(club, elevation, heading):
    recovered = cf.face_and_loft_from_launch(
        club=club,
        launch_vertical_deg=elevation,
        launch_horizontal_deg=heading,
        club_path_deg=heading,
        attack_angle_deg=elevation,
    )

    assert recovered is not None
    assert recovered == pytest.approx((heading, elevation), abs=1e-9)


@pytest.mark.parametrize("club", [ClubType.DRIVER, ClubType.IRON_7, ClubType.SW])
@pytest.mark.parametrize("launch,attack", [(80.0, -20.0), (45.0, -45.0), (90.0, -90.0)])
def test_inverse_refuses_launch_beyond_forward_model_domain(club, launch, attack):
    assert (
        cf.face_and_loft_from_launch(
            club=club,
            launch_vertical_deg=launch,
            launch_horizontal_deg=0.0,
            club_path_deg=0.0,
            attack_angle_deg=attack,
        )
        is None
    )


@pytest.mark.parametrize("club", [ClubType.DRIVER, ClubType.IRON_7, ClubType.SW])
def test_unconverged_fixed_point_is_not_reported_as_a_delivery(club):
    assert (
        cf.face_and_loft_from_launch(
            club=club,
            launch_vertical_deg=75.0,
            launch_horizontal_deg=0.0,
            club_path_deg=0.0,
            attack_angle_deg=0.0,
        )
        is None
    )

"""The forward collision model (club delivery to ball launch) and its inverse.

Properties come from the published physics the model is built on: the launch lies
in the D plane between the face normal and the club's direction (Jorgensen 1999;
Penner 2001), the normal-impulse ball speed (Penner 2001, Eq. (7)), rolling and
sliding (Penner 2001, Sec. II), and the gear effect's signs (Cross & Nathan 2007).
The known answers at the end are the fitted model's own outputs, to catch an
unintended change to a constant.
"""

import math

import pytest

from openflight import collision_forward as cf, metric_definitions as md
from openflight.clubs import ClubType


def _forward(club=ClubType.IRON_7, speed=85.0, attack=-4.0, path=0.0, face=0.0, loft=22.0, **kw):
    return cf.forward(
        club=club,
        club_speed_mph=speed,
        attack_angle_deg=attack,
        club_path_deg=path,
        face_angle_deg=face,
        dynamic_loft_deg=loft,
        **kw,
    )


def _angle(a, b):
    return math.degrees(math.acos(max(-1.0, min(1.0, sum(p * q for p, q in zip(a, b))))))


# ---- categories --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("club", "category"),
    [
        (ClubType.DRIVER, cf.DRIVER_WOOD),
        (ClubType.WOOD_5, cf.DRIVER_WOOD),
        (ClubType.HYBRID_3, cf.IRON_HYBRID),
        (ClubType.IRON_4, cf.IRON_HYBRID),
        (ClubType.IRON_9, cf.IRON_HYBRID),
        (ClubType.PW, cf.WEDGE),
        (ClubType.SW, cf.WEDGE),
        (ClubType.UNKNOWN, None),
        (None, None),
    ],
)
def test_every_known_club_has_a_category(club, category):
    assert cf.club_category(club) == category


def test_a_hybrid_takes_the_loft_of_the_iron_it_launches_like():
    assert cf.model_loft_deg(ClubType.HYBRID_3) == pytest.approx(19.0 + cf.HYBRID_LOFT_SHIFT_DEG)
    assert cf.model_loft_deg(ClubType.IRON_7) == pytest.approx(34.0)


def test_an_unknown_club_has_no_model():
    assert _forward(club=ClubType.UNKNOWN) is None


# ---- launch geometry ---------------------------------------------------------------


def test_a_square_delivery_launches_on_the_target_line_with_no_axis_tilt():
    impact = _forward(path=0.0, face=0.0)

    assert impact.launch_horizontal_deg == pytest.approx(0.0, abs=1e-9)
    assert impact.spin_axis_deg == pytest.approx(0.0, abs=1e-9)
    assert -4.0 < impact.launch_vertical_deg < 22.0


@pytest.mark.parametrize("club", [ClubType.DRIVER, ClubType.IRON_7, ClubType.SW])
@pytest.mark.parametrize(("path", "face"), [(-4.0, 3.0), (5.0, -2.0), (0.0, 6.0)])
def test_the_launch_lies_in_the_d_plane_between_face_and_club(club, path, face):
    impact = _forward(club=club, path=path, face=face, attack=-3.0, loft=30.0)
    club_dir = md.club_direction(-3.0, path)
    normal = md.face_normal(30.0, face)
    ball = md.ball_direction(impact.launch_vertical_deg, impact.launch_horizontal_deg)

    assert _angle(normal, ball) + _angle(ball, club_dir) == pytest.approx(impact.spin_loft_deg)
    assert 0.0 < _angle(normal, ball) < impact.spin_loft_deg


def test_the_launch_falls_back_from_the_face_by_more_as_spin_loft_rises():
    low = _forward(loft=15.0)
    high = _forward(loft=40.0)

    assert (40.0 - high.launch_vertical_deg) > (15.0 - low.launch_vertical_deg)


# ---- ball speed --------------------------------------------------------------------


def test_centre_ball_speed_scales_with_club_speed():
    slow, fast = _forward(speed=60.0), _forward(speed=120.0)

    assert fast.ball_speed_mph / slow.ball_speed_mph == pytest.approx(2.0)


def test_a_driver_square_and_low_lofted_has_a_driver_smash():
    impact = _forward(club=ClubType.DRIVER, speed=100.0, attack=3.0, loft=12.0)

    assert 1.45 < impact.ball_speed_mph / 100.0 < 1.52


def test_more_spin_loft_means_less_ball_speed():
    assert _forward(loft=40.0).ball_speed_mph < _forward(loft=20.0).ball_speed_mph


def test_an_off_centre_strike_loses_ball_speed_and_the_heel_loses_more():
    centre = _forward(club=ClubType.DRIVER, loft=12.0, attack=2.0)
    toe = _forward(club=ClubType.DRIVER, loft=12.0, attack=2.0, impact_offset_mm=15.0)
    heel = _forward(club=ClubType.DRIVER, loft=12.0, attack=2.0, impact_offset_mm=-15.0)
    high = _forward(club=ClubType.DRIVER, loft=12.0, attack=2.0, impact_height_mm=10.0)

    assert heel.ball_speed_mph < toe.ball_speed_mph < centre.ball_speed_mph
    assert high.ball_speed_mph < centre.ball_speed_mph


# ---- spin --------------------------------------------------------------------------


def test_spin_rises_with_spin_loft_and_club_speed_in_the_normal_range():
    assert _forward(loft=30.0).spin_rpm > _forward(loft=20.0).spin_rpm
    assert _forward(speed=95.0).spin_rpm > _forward(speed=75.0).spin_rpm


def test_a_seven_iron_and_a_driver_spin_like_their_clubs():
    iron = _forward(speed=85.0, attack=-4.0, loft=21.0)
    driver = _forward(club=ClubType.DRIVER, speed=105.0, attack=2.0, loft=13.0)

    assert 5000.0 < iron.spin_rpm < 7500.0
    assert 1800.0 < driver.spin_rpm < 3200.0


def test_spin_rolls_off_at_very_high_spin_loft():
    assert (
        _forward(club=ClubType.SW, loft=70.0, attack=-6.0).spin_rpm
        < _forward(club=ClubType.SW, loft=45.0, attack=-6.0).spin_rpm
    )


# ---- spin axis and gear effect -----------------------------------------------------


def test_an_open_face_to_path_tilts_the_axis_right_and_a_closed_one_left():
    assert _forward(path=-3.0, face=2.0).spin_axis_deg > 0.0
    assert _forward(path=3.0, face=-2.0).spin_axis_deg < 0.0


def test_the_centre_axis_is_smaller_than_the_d_plane_tilt():
    impact = _forward(club=ClubType.DRIVER, path=-4.0, face=2.0, loft=12.0, attack=2.0)

    assert 0.0 < impact.spin_axis_deg < impact.d_plane_tilt_deg


def test_a_toe_strike_draws_a_right_hander_and_fades_a_left_hander():
    kw = dict(club=ClubType.DRIVER, loft=12.0, attack=2.0)
    centre = _forward(**kw)
    toe_rh = _forward(**kw, impact_offset_mm=12.0)
    toe_lh = _forward(**kw, impact_offset_mm=12.0, handedness="LH")

    assert toe_rh.spin_axis_deg < centre.spin_axis_deg < toe_lh.spin_axis_deg
    assert toe_rh.spin_axis_deg == pytest.approx(-toe_lh.spin_axis_deg)


def test_a_strike_high_on_the_face_spins_less():
    kw = dict(club=ClubType.DRIVER, loft=12.0, attack=2.0)

    assert _forward(**kw, impact_height_mm=8.0).spin_rpm < _forward(**kw).spin_rpm
    assert _forward(**kw, impact_height_mm=-8.0).spin_rpm > _forward(**kw).spin_rpm


def test_the_gear_effect_is_far_smaller_for_irons_than_drivers():
    def axis_change(club, loft):
        return abs(
            _forward(club=club, loft=loft, impact_offset_mm=12.0).spin_axis_deg
            - _forward(club=club, loft=loft).spin_axis_deg
        )

    assert axis_change(ClubType.IRON_7, 22.0) < 0.2 * axis_change(ClubType.DRIVER, 12.0)


def test_gear_components_are_reported_and_zero_at_the_centre():
    centre = _forward(club=ClubType.DRIVER, loft=12.0)
    toe = _forward(club=ClubType.DRIVER, loft=12.0, impact_offset_mm=10.0, impact_height_mm=5.0)

    assert centre.gear_back_rpm == pytest.approx(0.0)
    assert centre.gear_side_rpm == pytest.approx(0.0)
    assert toe.gear_side_rpm < 0.0 and toe.gear_back_rpm < 0.0


# ---- inverse: face and loft from the launch -----------------------------------------


@pytest.mark.parametrize("club", [ClubType.DRIVER, ClubType.WOOD_3, ClubType.IRON_7, ClubType.PW])
@pytest.mark.parametrize(
    ("path", "face", "attack", "loft"),
    [(0.0, 0.0, -4.0, 22.0), (-3.0, 2.5, 2.0, 12.0), (4.0, -1.0, -6.0, 45.0)],
)
def test_the_inverse_recovers_face_and_loft_from_the_models_launch(club, path, face, attack, loft):
    impact = _forward(club=club, path=path, face=face, attack=attack, loft=loft)

    recovered = cf.face_and_loft_from_launch(
        club=club,
        launch_vertical_deg=impact.launch_vertical_deg,
        launch_horizontal_deg=impact.launch_horizontal_deg,
        club_path_deg=path,
        attack_angle_deg=attack,
    )

    assert recovered == pytest.approx((face, loft), abs=1e-6)


def test_the_inverse_refuses_what_it_cannot_solve():
    common = dict(launch_vertical_deg=10.0, launch_horizontal_deg=0.0, club_path_deg=0.0)
    assert (
        cf.face_and_loft_from_launch(club=ClubType.UNKNOWN, attack_angle_deg=0.0, **common) is None
    )
    assert (
        cf.face_and_loft_from_launch(club=ClubType.IRON_7, attack_angle_deg=95.0, **common) is None
    )


# ---- expected spin and ball speed (the indices) ------------------------------------


def test_expected_spin_is_club_speed_times_sine_spin_loft():
    one = cf.expected_spin_rpm(club=ClubType.IRON_7, club_speed_mph=80.0, spin_loft_deg=20.0)
    two = cf.expected_spin_rpm(club=ClubType.IRON_7, club_speed_mph=40.0, spin_loft_deg=20.0)

    assert one == pytest.approx(2.0 * two)
    ratio = (
        cf.expected_spin_rpm(club=ClubType.IRON_7, club_speed_mph=80.0, spin_loft_deg=30.0) / one
    )
    assert ratio == pytest.approx(math.sin(math.radians(30.0)) / math.sin(math.radians(20.0)))


def test_expected_ball_speed_follows_the_normal_impulse():
    square = cf.expected_ball_speed_mph(
        club=ClubType.DRIVER,
        club_speed_mph=100.0,
        spin_loft_deg=12.0,
        face_angle_deg=0.0,
        dynamic_loft_deg=12.0,
        launch_vertical_deg=12.0,
        launch_horizontal_deg=0.0,
    )
    tilted = cf.expected_ball_speed_mph(
        club=ClubType.DRIVER,
        club_speed_mph=100.0,
        spin_loft_deg=12.0,
        face_angle_deg=0.0,
        dynamic_loft_deg=12.0,
        launch_vertical_deg=9.0,
        launch_horizontal_deg=0.0,
    )

    assert tilted / square == pytest.approx(1.0 / math.cos(math.radians(3.0)))


def test_the_indices_are_undefined_for_an_unknown_club_or_no_speed():
    assert (
        cf.expected_spin_rpm(club=ClubType.UNKNOWN, club_speed_mph=80.0, spin_loft_deg=20.0) is None
    )
    assert (
        cf.expected_spin_rpm(club=ClubType.IRON_7, club_speed_mph=0.0, spin_loft_deg=20.0) is None
    )


# ---- known answers (the fitted model's own outputs) --------------------------------

KNOWN = [
    # club, (speed, attack, path, face, loft, offset, height), (ball, vla, hla, spin, axis)
    (
        ClubType.DRIVER,
        (105.0, 2.0, -2.0, 1.0, 13.0, 0.0, 0.0),
        (154.8791, 11.5777, 0.6015, 2606.1222, 9.9348),
    ),
    (
        ClubType.DRIVER,
        (95.0, -1.0, 3.0, -1.5, 11.0, 12.0, 4.0),
        (136.8379, 9.4437, -0.9033, 2606.284, -22.9922),
    ),
    (
        ClubType.WOOD_3,
        (98.0, -2.0, 1.0, 2.0, 15.0, -8.0, -3.0),
        (139.0239, 12.1391, 1.8255, 4139.4037, 4.0888),
    ),
    (
        ClubType.HYBRID_3,
        (90.0, -3.0, -1.0, 0.5, 18.0, 0.0, 0.0),
        (127.8258, 14.251, 0.218, 5183.3339, 3.1725),
    ),
    (
        ClubType.IRON_7,
        (85.0, -4.0, -2.0, 1.5, 21.0, 0.0, 0.0),
        (117.5499, 16.3491, 0.8014, 5810.6524, 6.2734),
    ),
    (
        ClubType.IRON_7,
        (80.0, -5.0, 4.0, 1.0, 24.0, -10.0, 2.0),
        (106.204, 18.4771, 1.6258, 6280.6923, -4.6641),
    ),
    (
        ClubType.PW,
        (75.0, -5.0, 0.0, -2.0, 33.0, 0.0, 0.0),
        (91.9325, 24.6069, -1.4803, 8281.4286, -2.4098),
    ),
    (
        ClubType.SW,
        (65.0, -6.0, 2.0, 4.0, 52.0, 0.0, 0.0),
        (54.5934, 38.4446, 3.298, 6927.2661, 1.4543),
    ),
]


@pytest.mark.parametrize(("club", "delivery", "expected"), KNOWN)
def test_the_fitted_model_reproduces_its_known_answers(club, delivery, expected):
    speed, attack, path, face, loft, offset, height = delivery
    impact = _forward(
        club=club,
        speed=speed,
        attack=attack,
        path=path,
        face=face,
        loft=loft,
        impact_offset_mm=offset,
        impact_height_mm=height,
    )
    got = (
        impact.ball_speed_mph,
        impact.launch_vertical_deg,
        impact.launch_horizontal_deg,
        impact.spin_rpm,
        impact.spin_axis_deg,
    )
    assert got == pytest.approx(expected, abs=2e-4)

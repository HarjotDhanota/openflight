"""The forward collision model (club delivery to ball launch) and its inverse.

Checked against published physics: D-plane launch (Jorgensen 1999; Penner 2001), ball speed and
spin (Penner 2001 Eq. (7), Sec. II; Cross 2002), off-centre mass and gear (Cross & Nathan 2007).
"""

import math

import pytest

from openflight import collision_forward as cf, metric_definitions as md
from openflight.clubs import ClubType
from openflight.clubs.physics import get_club_physics


def _forward(club=ClubType.IRON_7, speed=85.0, attack=-4.0, path=0.0, face=0.0, loft=22.0, **kw):
    kw.setdefault("assume_centred", True)
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


def test_centre_ball_speed_scales_with_club_speed():
    slow, fast = _forward(speed=60.0), _forward(speed=120.0)

    assert fast.ball_speed_mph / slow.ball_speed_mph == pytest.approx(2.0)


def test_a_driver_square_and_low_lofted_has_a_driver_smash():
    impact = _forward(club=ClubType.DRIVER, speed=100.0, attack=3.0, loft=12.0)

    assert 1.45 < impact.ball_speed_mph / 100.0 < 1.52


def test_more_spin_loft_means_less_ball_speed():
    assert _forward(loft=40.0).ball_speed_mph < _forward(loft=20.0).ball_speed_mph


def test_an_off_centre_strike_loses_ball_speed_with_the_square_of_the_offset():
    kw = dict(club=ClubType.DRIVER, loft=12.0, attack=2.0)
    centre = _forward(**kw).ball_speed_mph

    def loss(**at):
        return centre / _forward(**kw, **at).ball_speed_mph - 1.0

    assert loss(impact_offset_mm=15.0) == pytest.approx(loss(impact_offset_mm=-15.0))
    assert loss(impact_offset_mm=20.0) == pytest.approx(4.0 * loss(impact_offset_mm=10.0))
    assert loss(impact_height_mm=10.0) == pytest.approx(loss(impact_height_mm=-10.0))
    assert loss(impact_height_mm=10.0) > 0.0 and loss(impact_offset_mm=10.0) > 0.0


def test_spin_rises_with_spin_loft_and_club_speed_in_the_normal_range():
    assert _forward(loft=30.0).spin_rpm > _forward(loft=20.0).spin_rpm
    assert _forward(speed=95.0).spin_rpm > _forward(speed=75.0).spin_rpm


def test_a_seven_iron_and_a_driver_spin_like_their_clubs():
    iron = _forward(speed=85.0, attack=-4.0, loft=21.0)
    driver = _forward(club=ClubType.DRIVER, speed=105.0, attack=2.0, loft=13.0)

    assert 5000.0 < iron.spin_rpm < 7500.0
    assert 1800.0 < driver.spin_rpm < 3200.0


def _centre_spin(club, sl, speed=None):
    if speed is None:
        physics = get_club_physics(club)
        speed = physics.average_ball_speed_mph / physics.optimal_smash
    return cf.centre_spin_rpm(club=club, club_speed_mph=speed, spin_loft_deg=sl)


def test_the_centre_spin_is_the_forward_models_spin_at_the_face_centre():
    impact = _forward(path=0.0, face=0.0)

    assert impact.spin_rpm == pytest.approx(
        cf.centre_spin_rpm(
            club=ClubType.IRON_7, club_speed_mph=85.0, spin_loft_deg=impact.spin_loft_deg
        )
    )


@pytest.mark.parametrize("club", [ClubType.DRIVER, ClubType.IRON_7, ClubType.PW, ClubType.SW])
def test_spin_rises_monotonically_over_spin_loft_with_no_floor(club):
    spins = [_centre_spin(club, sl / 2.0) for sl in range(0, 91)]

    assert spins[0] == pytest.approx(0.0, abs=1e-9)
    assert all(b > a for a, b in zip(spins, spins[1:]))
    # rolling: proportional to sin(spin loft) at small spin loft, no floor
    assert _centre_spin(club, 2.0) / _centre_spin(club, 8.0) == pytest.approx(
        math.sin(math.radians(2.0)) / math.sin(math.radians(8.0))
    )


@pytest.mark.parametrize("club", [club for club in ClubType if cf.club_category(club)])
@pytest.mark.parametrize("scale", [0.8, 1.0, 1.25])
def test_the_grip_to_slip_corner_is_above_55_deg_spin_loft(club, scale):
    speed = (
        scale * get_club_physics(club).average_ball_speed_mph / get_club_physics(club).optimal_smash
    )
    spins = [_centre_spin(club, sl, speed) for sl in (50.0, 53.0, 55.0)]

    assert spins[0] < spins[1] < spins[2]
    # past the corner the ball leaves the face still sliding: the spin falls
    assert _centre_spin(club, 85.0, speed) < _centre_spin(club, 70.0, speed)


def test_the_spin_depends_on_the_club_speeds_normal_and_tangential_parts_only():
    one = cf.centre_spin_rpm(club=ClubType.PW, club_speed_mph=80.0, spin_loft_deg=30.0)
    two = cf.centre_spin_rpm(club=ClubType.PW, club_speed_mph=40.0, spin_loft_deg=30.0)

    assert one == pytest.approx(2.0 * two)
    assert (
        cf.centre_spin_rpm(club=ClubType.UNKNOWN, club_speed_mph=80.0, spin_loft_deg=30.0) is None
    )


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


def test_the_gear_effect_is_linear_in_the_offset():
    kw = dict(club=ClubType.DRIVER, loft=12.0, attack=2.0)
    side = [_forward(**kw, impact_offset_mm=x).gear_side_rpm for x in (5.0, 10.0, 20.0, -10.0)]
    back = [_forward(**kw, impact_height_mm=y).gear_back_rpm for y in (5.0, 10.0, 20.0, -10.0)]

    for gear in (side, back):
        assert gear[0] < 0.0
        assert gear[1] == pytest.approx(2.0 * gear[0])
        assert gear[2] == pytest.approx(4.0 * gear[0])
        assert gear[3] == pytest.approx(-gear[1])


def test_without_an_impact_location_there_is_no_gear_effect():
    impact = _forward(club=ClubType.PW, loft=40.0)

    assert not impact.impact_location
    assert impact.gear_back_rpm == 0.0 and impact.gear_side_rpm == 0.0


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


def test_expected_spin_is_club_speed_times_sine_spin_loft():
    one = cf.expected_spin_rpm(club=ClubType.IRON_7, club_speed_mph=80.0, spin_loft_deg=20.0)
    two = cf.expected_spin_rpm(club=ClubType.IRON_7, club_speed_mph=40.0, spin_loft_deg=20.0)

    assert one == pytest.approx(2.0 * two)
    ratio = (
        cf.expected_spin_rpm(club=ClubType.IRON_7, club_speed_mph=80.0, spin_loft_deg=30.0) / one
    )
    assert ratio == pytest.approx(math.sin(math.radians(30.0)) / math.sin(math.radians(20.0)))


@pytest.mark.parametrize(
    ("club", "a", "c"),
    [
        (ClubType.DRIVER, 1.5059, 0.0072),
        (ClubType.IRON_7, 1.4956, 0.0321),
        (ClubType.PW, 1.4682, 0.0716),
    ],
)
def test_a_data_clubs_expected_ball_speed_is_its_fitted_line_in_cos_spin_loft(club, a, c):
    for speed, spin_loft in ((90.0, 12.0), (70.0, 35.0)):
        expected = cf.expected_ball_speed_mph(
            club=club, club_speed_mph=speed, spin_loft_deg=spin_loft
        )
        assert expected == pytest.approx(speed * (a * math.cos(math.radians(spin_loft)) + c))
    assert cf.has_fitted_index(club)


@pytest.mark.parametrize(
    ("club", "k"), [(ClubType.DRIVER, 109.50), (ClubType.IRON_7, 150.46), (ClubType.PW, 165.13)]
)
def test_a_data_clubs_expected_spin_takes_its_own_constant(club, k):
    expected = cf.expected_spin_rpm(club=club, club_speed_mph=80.0, spin_loft_deg=20.0)

    assert expected == pytest.approx(80.0 * k * math.sin(math.radians(20.0)))


@pytest.mark.parametrize("club", [ClubType.WOOD_3, ClubType.HYBRID_5, ClubType.IRON_5, ClubType.SW])
def test_a_club_without_data_takes_the_published_normal_impulse_fallback(club):
    assert not cf.has_fitted_index(club)
    low = cf.expected_ball_speed_mph(club=club, club_speed_mph=80.0, spin_loft_deg=15.0)
    high = cf.expected_ball_speed_mph(club=club, club_speed_mph=80.0, spin_loft_deg=40.0)

    assert 1.1 * 80.0 < high < low < 1.6 * 80.0


def test_the_indices_are_undefined_for_an_unknown_club_or_no_speed():
    assert (
        cf.expected_spin_rpm(club=ClubType.UNKNOWN, club_speed_mph=80.0, spin_loft_deg=20.0) is None
    )
    assert (
        cf.expected_spin_rpm(club=ClubType.IRON_7, club_speed_mph=0.0, spin_loft_deg=20.0) is None
    )


# the model's own outputs, pinned against an unintended change to a constant

KNOWN = [
    # club, (speed, attack, path, face, loft, offset, height), (ball, vla, hla, spin, axis)
    (
        ClubType.DRIVER,
        (105.0, 2.0, -2.0, 1.0, 13.0, 0.0, 0.0),
        (154.8791, 11.5777, 0.6015, 2623.3318, 9.9348),
    ),
    (
        ClubType.DRIVER,
        (95.0, -1.0, 3.0, -1.5, 11.0, 12.0, 4.0),
        (136.9428, 9.4437, -0.9033, 2561.7886, -24.578),
    ),
    (
        ClubType.WOOD_3,
        (98.0, -2.0, 1.0, 2.0, 15.0, -8.0, -3.0),
        (140.6204, 12.1391, 1.8255, 3867.5483, 7.0069),
    ),
    (
        ClubType.HYBRID_3,
        (90.0, -3.0, -1.0, 0.5, 18.0, 0.0, 0.0),
        (127.8258, 14.251, 0.218, 4845.8684, 3.1725),
    ),
    (
        ClubType.IRON_7,
        (85.0, -4.0, -2.0, 1.5, 21.0, 0.0, 0.0),
        (117.5499, 16.3491, 0.8014, 5465.809, 6.2734),
    ),
    (
        ClubType.IRON_7,
        (80.0, -5.0, 4.0, 1.0, 24.0, -10.0, 2.0),
        (106.5534, 18.4771, 1.6258, 5776.0803, -3.7736),
    ),
    (
        ClubType.PW,
        (75.0, -5.0, 0.0, -2.0, 33.0, 0.0, 0.0),
        (91.9325, 24.6069, -1.4803, 7800.2028, -2.4098),
    ),
    (
        ClubType.SW,
        (65.0, -6.0, 2.0, 4.0, 52.0, 0.0, 0.0),
        (54.5935, 38.4445, 3.298, 9323.1982, 1.4543),
    ),
]


@pytest.mark.parametrize(("club", "delivery", "expected"), KNOWN)
def test_the_model_reproduces_its_known_answers(club, delivery, expected):
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


def _bare(**location):
    return cf.forward(
        club=ClubType.IRON_7,
        club_speed_mph=85.0,
        attack_angle_deg=-4.0,
        club_path_deg=0.0,
        face_angle_deg=0.0,
        dynamic_loft_deg=22.0,
        **location,
    )


def test_an_unknown_strike_location_is_not_a_centred_strike():
    unknown = _bare()

    assert unknown.centre_spin_rpm > 0.0
    assert unknown.gear_back_rpm is None and unknown.gear_side_rpm is None
    assert unknown.spin_rpm is None and unknown.spin_axis_deg is None
    assert unknown.ball_speed_mph is None
    assert not unknown.impact_location


def test_one_known_coordinate_gives_only_its_own_gear_term():
    partial = _bare(impact_offset_mm=8.0)

    assert partial.gear_side_rpm is not None
    assert partial.gear_back_rpm is None
    assert partial.spin_rpm is None and partial.ball_speed_mph is None


def test_a_centred_strike_is_assumed_only_when_asked_and_matches_a_measured_centre():
    assumed = _bare(assume_centred=True)
    measured = _bare(impact_offset_mm=0.0, impact_height_mm=0.0)

    for field in ("ball_speed_mph", "spin_rpm", "spin_axis_deg", "gear_back_rpm", "gear_side_rpm"):
        assert getattr(assumed, field) == pytest.approx(getattr(measured, field))
    assert not assumed.impact_location
    assert measured.impact_location

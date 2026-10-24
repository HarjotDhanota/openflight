"""D37: TrackMan's public metric definitions, checked from first principles.

Every expected value below is worked by hand from the public definition (see
docs/reference/metric-definitions.md), not taken from any device output.
Frame: x downrange, y target-right, z up; horizontal angles are positive right
of the target line, vertical angles positive up.
"""

import math
import random

import pytest

from openflight import metric_definitions as md
from tests.tm_owner_export import number, owner_shots

NON_FINITE = [math.nan, math.inf, -math.inf]


# --- face to path -----------------------------------------------------------


class TestFaceToPath:
    def test_is_face_angle_minus_club_path(self):
        assert md.face_to_path_deg(2.0, -3.0) == pytest.approx(5.0)
        assert md.face_to_path_deg(-1.0, 4.0) == pytest.approx(-5.0)
        assert md.face_to_path_deg(3.0, 3.0) == 0.0

    def test_positive_means_face_right_of_path(self):
        # an open face to an in-to-out path still reads right of the path
        assert md.face_to_path_deg(1.0, 0.5) > 0

    @pytest.mark.parametrize("args", [(None, 1.0), (1.0, None)])
    def test_none_in_none_out(self, args):
        assert md.face_to_path_deg(*args) is None

    @pytest.mark.parametrize("bad", NON_FINITE)
    def test_non_finite_in_non_finite_out(self, bad):
        assert not math.isfinite(md.face_to_path_deg(bad, 1.0))
        assert not math.isfinite(md.face_to_path_deg(1.0, bad))


# --- the two unit vectors ---------------------------------------------------


class TestDirectionVectors:
    def test_square_level_club_moves_straight_downrange(self):
        assert md.club_direction(0.0, 0.0) == pytest.approx((1.0, 0.0, 0.0))

    def test_attack_angle_tilts_the_club_direction_up(self):
        x, y, z = md.club_direction(30.0, 0.0)
        assert (x, y, z) == pytest.approx((math.sqrt(3) / 2, 0.0, 0.5))

    def test_club_path_turns_the_club_direction_right(self):
        x, y, z = md.club_direction(0.0, 30.0)
        assert (x, y, z) == pytest.approx((math.sqrt(3) / 2, 0.5, 0.0))

    def test_face_normal_elevation_is_loft_and_azimuth_is_face_angle(self):
        assert md.face_normal(90.0, 0.0) == pytest.approx((0.0, 0.0, 1.0), abs=1e-12)
        x, y, z = md.face_normal(60.0, 90.0)
        assert (x, y, z) == pytest.approx((0.0, 0.5, math.sqrt(3) / 2), abs=1e-12)

    def test_vectors_are_unit(self):
        rng = random.Random(37)
        for _ in range(200):
            a, b = rng.uniform(-90, 90), rng.uniform(-180, 180)
            for vec in (md.club_direction(a, b), md.face_normal(a, b)):
                assert math.fsum(c * c for c in vec) == pytest.approx(1.0)

    @pytest.mark.parametrize("elevation", [90.5, -91.0, 180.0])
    def test_an_elevation_beyond_vertical_is_rejected(self, elevation):
        assert md.club_direction(elevation, 0.0) is None
        assert md.face_normal(elevation, 0.0) is None

    def test_none_in_none_out(self):
        assert md.club_direction(None, 0.0) is None
        assert md.face_normal(10.0, None) is None


# --- spin loft --------------------------------------------------------------


class TestSpinLoft:
    def test_club_view_examples_and_their_mirrors_match_vector_separation(self):
        for loft in (0.0, 10.4, 14.9, 16.5, 18.8, 31.4, 56.3):
            for attack in (-6.25, -4.2, 0.0, 6.25):
                for face in (-3.17, 0.0, 3.17):
                    for path in (-4.23, 0.0, 4.23):
                        normal = md.face_normal(loft, face)
                        velocity = md.club_direction(attack, path)
                        cross = (
                            normal[1] * velocity[2] - normal[2] * velocity[1],
                            normal[2] * velocity[0] - normal[0] * velocity[2],
                            normal[0] * velocity[1] - normal[1] * velocity[0],
                        )
                        dot = math.fsum(a * b for a, b in zip(normal, velocity))
                        expected = math.degrees(math.atan2(math.hypot(*cross), dot))
                        actual = md.spin_loft_deg(loft, face, attack, path)
                        assert actual == pytest.approx(expected, abs=1e-10)
                        assert md.spin_loft_deg(loft, -face, attack, -path) == pytest.approx(actual)
                        assert md.face_to_path_deg(-face, -path) == pytest.approx(
                            -md.face_to_path_deg(face, path)
                        )

    def test_pure_loft_is_loft_minus_attack(self):
        # square face on a square path: the 2D picture is exact
        assert md.spin_loft_deg(12.0, 0.0, -3.0, 0.0) == pytest.approx(15.0)
        assert md.spin_loft_deg(10.5, 0.0, 4.0, 0.0) == pytest.approx(6.5)

    def test_pure_face_is_the_horizontal_face_to_path_angle(self):
        # zero loft, level club: both vectors horizontal, 5 degrees apart
        assert md.spin_loft_deg(0.0, 5.0, 0.0, 0.0) == pytest.approx(5.0)
        assert md.spin_loft_deg(0.0, -2.0, 0.0, 3.0) == pytest.approx(5.0)

    def test_combined_loft_and_face_by_the_spherical_law_of_cosines(self):
        # level square-path club, so cos(spin loft) = cos(loft) * cos(face):
        # 45 deg loft and 45 deg face give cos = 1/2, a 60 deg spin loft
        assert md.spin_loft_deg(45.0, 45.0, 0.0, 0.0) == pytest.approx(60.0)
        # 10 deg loft and 10 deg face: arccos(cos^2 10 deg)
        expected = math.degrees(math.acos(math.cos(math.radians(10.0)) ** 2))
        assert md.spin_loft_deg(10.0, 10.0, 0.0, 0.0) == pytest.approx(expected)
        assert expected == pytest.approx(14.1060, abs=1e-4)

    def test_is_not_loft_minus_attack_once_face_to_path_is_not_zero(self):
        spin_loft = md.spin_loft_deg(15.0, 4.0, -4.0, -4.0)
        assert spin_loft > 15.0 - (-4.0) + 0.1

    def test_two_dimensional_limit_matches_loft_minus_attack(self):
        rng = random.Random(371)
        for _ in range(500):
            loft = rng.uniform(-10, 60)
            attack = rng.uniform(-10, 10)
            path = rng.uniform(-20, 20)
            result = md.spin_loft_deg(loft, path, attack, path)
            assert result == pytest.approx(abs(loft - attack), abs=1e-6)

    def test_never_less_than_the_two_dimensional_difference(self):
        rng = random.Random(372)
        for _ in range(500):
            loft, face = rng.uniform(-10, 60), rng.uniform(-20, 20)
            attack, path = rng.uniform(-10, 10), rng.uniform(-20, 20)
            assert md.spin_loft_deg(loft, face, attack, path) >= abs(loft - attack) - 1e-9

    def test_depends_on_face_and_path_only_through_their_difference(self):
        rng = random.Random(373)
        for _ in range(200):
            loft, face = rng.uniform(0, 50), rng.uniform(-10, 10)
            attack, path = rng.uniform(-8, 8), rng.uniform(-10, 10)
            turn = rng.uniform(-30, 30)
            assert md.spin_loft_deg(loft, face + turn, attack, path + turn) == pytest.approx(
                md.spin_loft_deg(loft, face, attack, path), abs=1e-9
            )

    def test_is_symmetric_left_and_right(self):
        assert md.spin_loft_deg(20.0, 3.0, -2.0, -1.0) == pytest.approx(
            md.spin_loft_deg(20.0, -3.0, -2.0, 1.0)
        )

    def test_lies_between_zero_and_180(self):
        rng = random.Random(374)
        for _ in range(200):
            args = [rng.uniform(-90, 90), rng.uniform(-180, 180)]
            args += [rng.uniform(-90, 90), rng.uniform(-180, 180)]
            assert 0.0 <= md.spin_loft_deg(*args) <= 180.0

    @pytest.mark.parametrize("position", range(4))
    def test_none_in_none_out(self, position):
        args = [12.0, 1.0, -2.0, 0.5]
        args[position] = None
        assert md.spin_loft_deg(*args) is None

    @pytest.mark.parametrize("bad", NON_FINITE)
    @pytest.mark.parametrize("position", range(4))
    def test_non_finite_in_non_finite_out(self, position, bad):
        args = [12.0, 1.0, -2.0, 0.5]
        args[position] = bad
        result = md.spin_loft_deg(*args)
        assert result is not None and math.isnan(result)

    def test_a_loft_beyond_vertical_is_rejected(self):
        assert md.spin_loft_deg(95.0, 0.0, 0.0, 0.0) is None


# --- D-plane tilt -----------------------------------------------------------


class TestDPlaneTilt:
    def test_zero_when_face_to_path_is_zero(self):
        # TrackMan: "if face-to-path is zero, D Plane Tilt will be zero"
        rng = random.Random(375)
        for _ in range(200):
            loft, attack = rng.uniform(0, 50), rng.uniform(-8, 8)
            if abs(loft - attack) < 0.5:
                continue
            path = rng.uniform(-15, 15)
            assert md.d_plane_tilt_deg(loft, path, attack, path) == pytest.approx(0.0, abs=1e-9)

    def test_hand_worked_level_square_path(self):
        # level club on the target line: the plane holds (1,0,0) and the face
        # normal, so its normal is (0, -sin L, cos L sin F) and the tilt is
        # atan(sin F / tan L); 45 deg loft, 45 deg face -> atan(1/sqrt 2)
        expected = math.degrees(math.atan(math.sin(math.radians(45.0))))
        assert md.d_plane_tilt_deg(45.0, 45.0, 0.0, 0.0) == pytest.approx(expected)
        assert expected == pytest.approx(35.2644, abs=1e-4)

    def test_zero_loft_pure_face_plane_is_horizontal(self):
        # both vectors level: the D plane is the ground, 90 deg from plumb
        assert md.d_plane_tilt_deg(0.0, 5.0, 0.0, 0.0) == pytest.approx(90.0)
        assert md.d_plane_tilt_deg(0.0, -5.0, 0.0, 0.0) == pytest.approx(-90.0)

    def test_sign_follows_face_to_path_on_a_backspin_strike(self):
        # loft above attack (backspin): face right of path tilts the plane right
        rng = random.Random(376)
        for _ in range(500):
            loft, attack = rng.uniform(10, 50), rng.uniform(-6, 6)
            path = rng.uniform(-10, 10)
            face_to_path = rng.choice([-1, 1]) * rng.uniform(0.2, 10)
            tilt = md.d_plane_tilt_deg(loft, path + face_to_path, attack, path)
            assert math.copysign(1.0, tilt) == math.copysign(1.0, face_to_path)

    def test_is_antisymmetric_left_and_right(self):
        assert md.d_plane_tilt_deg(15.0, 3.0, -2.0, -1.0) == pytest.approx(
            -md.d_plane_tilt_deg(15.0, -3.0, -2.0, 1.0)
        )

    def test_undefined_when_the_vectors_are_parallel(self):
        assert md.d_plane_tilt_deg(5.0, 2.0, 5.0, 2.0) is None

    @pytest.mark.parametrize("position", range(4))
    def test_none_in_none_out(self, position):
        args = [12.0, 1.0, -2.0, 0.5]
        args[position] = None
        assert md.d_plane_tilt_deg(*args) is None

    @pytest.mark.parametrize("bad", NON_FINITE)
    def test_non_finite_in_non_finite_out(self, bad):
        result = md.d_plane_tilt_deg(12.0, bad, -2.0, 0.5)
        assert result is not None and math.isnan(result)


# --- curve ------------------------------------------------------------------


class TestBallDirection:
    def test_it_is_the_spherical_unit_vector_of_the_launch(self):
        x, y, z = md.ball_direction(30.0, 90.0)
        assert (x, y, z) == pytest.approx((0.0, math.cos(math.radians(30.0)), 0.5))

    def test_none_and_beyond_vertical_give_none(self):
        assert md.ball_direction(None, 0.0) is None
        assert md.ball_direction(91.0, 0.0) is None


class TestCurve:
    def test_the_owner_export_curve_is_reproduced(self):
        """TM4's Curve (ft) from its flat carry and carry side (yd) and launch direction."""
        shots = owner_shots()
        for shot in shots:
            carry, side = number(shot, "Carry Flat - Length"), number(shot, "Carry Flat - Side")
            got = md.curve(carry, side, number(shot, "Launch Direction")) * 3.0
            assert got == pytest.approx(number(shot, "Curve"), abs=1e-9)
        assert len(shots) == 24

    def test_straight_at_the_target_is_zero(self):
        assert md.curve(200.0, 0.0, 0.0) == pytest.approx(0.0)

    def test_landing_on_the_launch_line_is_zero(self):
        for launch in (-6.0, -1.0, 3.0, 8.0):
            side = 200.0 * math.sin(math.radians(launch))
            assert md.curve(200.0, side, launch) == pytest.approx(0.0, abs=1e-9)

    def test_on_the_target_line_launch_curve_equals_carry_side(self):
        assert md.curve(200.0, 10.0, 0.0) == pytest.approx(10.0)
        assert md.curve(200.0, -7.5, 0.0) == pytest.approx(-7.5)

    def test_started_right_and_finished_on_the_target_line_curved_left(self):
        # landing (200, 0); launch line 5 deg right; offset = -200 sin 5 deg
        assert md.curve(200.0, 0.0, 5.0) == pytest.approx(-200.0 * math.sin(math.radians(5.0)))
        assert md.curve(200.0, 0.0, 5.0) == pytest.approx(-17.4311, abs=1e-4)

    def test_hand_worked_general_case(self):
        # carry 150, side 20: landing (sqrt(150^2 - 20^2), 20) = (148.661, 20);
        # launch 2 deg right: curve = 20 cos 2 - 148.661 sin 2 = 19.988 - 5.188
        x = math.sqrt(150.0**2 - 20.0**2)
        expected = 20.0 * math.cos(math.radians(2.0)) - x * math.sin(math.radians(2.0))
        assert md.curve(150.0, 20.0, 2.0) == pytest.approx(expected)
        assert expected == pytest.approx(14.7996, abs=1e-4)

    def test_mirror_image_flips_the_sign(self):
        assert md.curve(180.0, 12.0, 3.0) == pytest.approx(-md.curve(180.0, -12.0, -3.0))

    def test_is_in_the_unit_of_carry(self):
        yards_to_m = 0.9144
        assert md.curve(200.0 * yards_to_m, 10.0 * yards_to_m, 2.0) == pytest.approx(
            md.curve(200.0, 10.0, 2.0) * yards_to_m
        )

    def test_never_more_than_carry(self):
        rng = random.Random(377)
        for _ in range(300):
            carry = rng.uniform(1, 300)
            side = rng.uniform(-carry, carry)
            assert abs(md.curve(carry, side, rng.uniform(-20, 20))) <= carry + 1e-9

    @pytest.mark.parametrize("carry", [0.0, -10.0])
    def test_non_positive_carry_is_rejected(self, carry):
        assert md.curve(carry, 0.0, 0.0) is None

    def test_a_side_longer_than_carry_is_rejected(self):
        assert md.curve(100.0, 100.5, 0.0) is None

    @pytest.mark.parametrize("launch", [90.0, -95.0])
    def test_a_launch_that_is_not_downrange_is_rejected(self, launch):
        assert md.curve(100.0, 0.0, launch) is None

    @pytest.mark.parametrize("position", range(3))
    def test_none_in_none_out(self, position):
        args = [200.0, 5.0, 1.0]
        args[position] = None
        assert md.curve(*args) is None

    @pytest.mark.parametrize("bad", NON_FINITE)
    @pytest.mark.parametrize("position", range(3))
    def test_non_finite_in_non_finite_out(self, position, bad):
        args = [200.0, 5.0, 1.0]
        args[position] = bad
        result = md.curve(*args)
        assert result is not None and math.isnan(result)


def test_every_definition_cites_its_public_source():
    for fn in (
        md.smash_factor,
        md.face_to_path_deg,
        md.spin_loft_deg,
        md.d_plane_tilt_deg,
        md.curve,
    ):
        assert "https://" in (fn.__doc__ or ""), fn.__name__


def test_the_smash_factor_is_the_product_definition():
    from openflight.smash import smash_factor

    assert md.smash_factor is smash_factor

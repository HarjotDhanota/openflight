"""The 3-D impact projection: exact on constructed geometry, honest about signs.

The truth is built in 3-D, projected through the same pinhole the module
assumes, and the module must hand back the constructed numbers to machine
precision -- there is no tolerance to hide arithmetic in. The correction
tests then pin the SIZE of what the 2-D channel was missing: the contact-
point drop, the foreshortening, and the face-angle shift.
"""

from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np
import pytest

from openflight.camera.clubpose import impact_projection as proj, impact_zone as zone

FX = 466.7
PP = (160.0, 100.0)


def _pixel(point) -> tuple[float, float]:
    return (
        FX * point[0] / point[2] + PP[0],
        FX * point[1] / point[2] + PP[1],
    )


def _truth(loft_deg: float = 30.0, face_deg: float = 0.0):
    """A face touching the ball, with landmarks ON the plane, all in 3-D."""
    normal = proj.face_normal_camera(loft_deg, face_deg)
    ball = 1581.0 * proj._ray((176.5, 146.0), FX, PP)  # pylint: disable=protected-access
    contact = ball - proj.BALL_RADIUS_MM * normal
    toe_ward = np.array([1.0, 0.0, 0.0])
    toe_ward = toe_ward - (toe_ward @ normal) * normal
    toe_ward /= np.linalg.norm(toe_ward)
    up_face = np.cross(toe_ward, normal)
    up_face /= np.linalg.norm(up_face)
    if up_face[1] > 0.0:
        up_face = -up_face
    toe = contact + 45.0 * toe_ward + 10.0 * up_face
    heel = contact - 50.0 * toe_ward + 8.0 * up_face
    topline = contact + 5.0 * toe_ward + 14.0 * up_face
    return normal, ball, contact, heel, toe, topline


def _run(ball, heel, toe, topline, loft_deg=30.0, face_deg=0.0):
    return proj.project_impact(
        _pixel(ball),
        _pixel(heel),
        _pixel(toe),
        _pixel(topline),
        range_to_ball_mm=float(np.linalg.norm(ball)),
        focal_px=FX,
        principal_point=PP,
        loft_deg=loft_deg,
        face_angle_deg=face_deg,
    )


class TestExactRecovery:
    def test_the_constructed_contact_is_recovered_to_machine_precision(self):
        normal, ball, contact, heel, toe, topline = _truth()
        result = _run(ball, heel, toe, topline)

        span = toe - heel
        toe_ward = span / np.linalg.norm(span)
        up_face = np.cross(toe_ward, normal)
        up_face /= np.linalg.norm(up_face)
        if up_face[1] > 0.0:
            up_face = -up_face

        assert result.ok, result.reason
        assert result.ball_from_toe_mm == pytest.approx(float((contact - toe) @ toe_ward), abs=1e-6)
        assert result.heel_toe_span_mm == pytest.approx(float(np.linalg.norm(span)), abs=1e-6)
        assert result.high_low_mm == pytest.approx(float((topline - contact) @ up_face), abs=1e-6)
        assert np.allclose(result.contact_camera_mm, contact, atol=1e-6)

    def test_the_signs_read_like_the_two_d_channel(self):
        """Heel-ward of the toe is negative; below the topline is positive."""
        _, ball, _, heel, toe, topline = _truth()
        result = _run(ball, heel, toe, topline)

        assert result.ball_from_toe_mm < 0.0, "the contact sits heel-ward of the toe"
        assert result.high_low_mm == pytest.approx(14.0, abs=0.5), (
            "the truth put the contact 14 mm below the topline"
        )

    def test_the_face_normal_sign_conventions_are_pinned(self):
        square = proj.face_normal_camera(0.0, 0.0)
        lofted = proj.face_normal_camera(30.0, 0.0)
        open_face = proj.face_normal_camera(0.0, 10.0)

        assert np.allclose(square, [0.0, 0.0, 1.0]), "zero loft points straight downrange"
        assert lofted[1] < 0.0, "loft pitches the normal UP, which is image -y"
        assert open_face[0] > 0.0, "an open face points right of the target for a RH golfer"


class TestTheCorrections:
    def test_the_reading_is_the_contact_point_not_the_ball_centre(self):
        """The naive image reading of ball-below-topline understates the
        contact depth by the r*sin(loft) drop plus the foreshortening."""
        _, ball, _, heel, toe, topline = _truth()
        naive_mm = (_pixel(ball)[1] - _pixel(topline)[1]) * (float(np.linalg.norm(ball)) / FX)
        result = _run(ball, heel, toe, topline)

        drop_mm = proj.BALL_RADIUS_MM * math.sin(math.radians(30.0))
        assert result.high_low_mm > naive_mm + 0.8 * drop_mm

    def test_face_angle_shifts_the_touch_point_by_about_r_sin_f_cos_loft(self):
        _, ball, _, heel, toe, topline = _truth()
        square = _run(ball, heel, toe, topline, face_deg=0.0)
        open_face = _run(ball, heel, toe, topline, face_deg=6.0)

        expected = proj.BALL_RADIUS_MM * math.sin(math.radians(6.0)) * math.cos(math.radians(30.0))
        shift = square.ball_from_toe_mm - open_face.ball_from_toe_mm
        assert shift == pytest.approx(expected, rel=0.35)
        assert open_face.ball_from_toe_mm < square.ball_from_toe_mm, (
            "an open face moves the touch point heel-ward"
        )

    def test_loft_uncertainty_moves_the_vertical_reading_boundedly(self):
        """The +-4 deg loft bracket moves the vertical reading by more than
        the pure contact-point drop (the plane's changed tilt also re-reads
        the topline intersection) but stays within twice it -- measured at
        4.4 mm over the full 8 deg on this geometry, ~0.55 mm/deg."""
        _, ball, _, heel, toe, topline = _truth()
        low = _run(ball, heel, toe, topline, loft_deg=26.0)
        high = _run(ball, heel, toe, topline, loft_deg=34.0)

        drop_only = proj.BALL_RADIUS_MM * (
            math.sin(math.radians(34.0)) - math.sin(math.radians(26.0))
        )
        delta = abs(high.high_low_mm - low.high_low_mm)
        assert drop_only < delta < 2.0 * drop_only


class TestFailModes:
    def test_coincident_landmarks_span_no_face(self):
        _, ball, _, heel, toe, topline = _truth()
        result = proj.project_impact(
            _pixel(ball),
            _pixel(toe),
            _pixel(toe),
            _pixel(topline),
            range_to_ball_mm=float(np.linalg.norm(ball)),
            focal_px=FX,
            principal_point=PP,
            loft_deg=30.0,
        )

        assert not result.ok
        assert result.reason == "landmarks_span_no_face"
        assert result.ball_from_toe_mm is None

    def test_a_degenerate_range_is_refused_by_name(self):
        _, ball, _, heel, toe, topline = _truth()
        for bad in (0.0, -10.0, math.nan):
            result = proj.project_impact(
                _pixel(ball),
                _pixel(heel),
                _pixel(toe),
                _pixel(topline),
                range_to_ball_mm=bad,
                focal_px=FX,
                principal_point=PP,
                loft_deg=30.0,
            )
            assert not result.ok
            assert "not_a_distance" in result.reason

    def test_every_assumption_travels_with_the_reading(self):
        _, ball, _, heel, toe, topline = _truth()
        result = _run(ball, heel, toe, topline)

        joined = " ".join(result.assumptions)
        assert "assumed_not_measured" in joined
        assert "assumed_square" in joined
        assert "roll_zero" in joined
        assert "face_plane" in joined


class TestTheImpactZoneChannel:
    """`impact_zone.projection_fields` -- the additive 3-D channel."""

    @staticmethod
    def _carry_and_swing():
        _, ball, _, heel, toe, topline = _truth()
        carry = zone.Carry(
            values={
                "heel_x": _pixel(heel)[0],
                "heel_y": _pixel(heel)[1],
                "toe_x": _pixel(toe)[0],
                "toe_y": _pixel(toe)[1],
                "topline_at_ball_y": _pixel(topline)[1],
            },
            model="quadratic",
            disagreement_px=None,
            frames=(68, 69, 70, 71),
        )
        ball_px = _pixel(ball)
        swing = SimpleNamespace(ball=SimpleNamespace(x=ball_px[0], y=ball_px[1]))
        setup = SimpleNamespace(range_to_ball_mm=float(np.linalg.norm(ball)))
        rig = SimpleNamespace(focal_px=FX, principal_point=PP)
        return swing, carry, setup, rig

    def test_without_setup_geometry_the_channel_says_it_did_not_run(self):
        swing, carry, _, rig = self._carry_and_swing()

        fields = zone.projection_fields(swing, carry, None, rig, None, 0.0, "7-iron")
        assert fields == {"projection_status": "not_run_no_setup_geometry"}

    def test_an_unknown_club_with_no_loft_is_named_not_guessed(self):
        swing, carry, setup, rig = self._carry_and_swing()

        fields = zone.projection_fields(swing, carry, setup, rig, None, 0.0, "putter")
        assert fields["projection_status"] == "not_run_no_loft_for_putter"

    def test_a_known_club_uses_the_named_loft_convention(self):
        swing, carry, setup, rig = self._carry_and_swing()

        fields = zone.projection_fields(swing, carry, setup, rig, None, 0.0, "7-iron")
        assert fields["projection_status"] == "ok"
        assert fields["ball_from_toe_3d_mm"] is not None
        assert any("30.0_deg" in item for item in fields["projection_assumptions"])

    def test_the_result_dict_carries_the_channel(self):
        result = zone.withheld("because")

        as_dict = result.as_dict()
        assert as_dict["projection_status"] == "not_run"
        assert as_dict["ball_from_toe_3d_mm"] is None
        assert as_dict["projection_assumptions"] == []

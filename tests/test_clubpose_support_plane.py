"""The support-plane datum and the mat-geometry gate over it.

The vertical datum is the BALL'S OWN SUPPORT PLANE -- its centre lowered by
one ball radius, whatever it is resting on. No mat thickness is known, asked
for, or assumed anywhere in here.

Every number below is built in 3-D first, projected through the same pinhole
`impact_projection` assumes, and handed back: the scenes are constructed with
the topline at a CHOSEN height above the support plane, so the recovery tests
have a truth to machine precision rather than a tolerance to hide in.
"""

from __future__ import annotations

import json
import math

import numpy as np
import pytest

from openflight.camera.clubpose import (
    head_outline as outline,
    impact_projection as proj,
    impact_zone as zone,
)

FX = 466.7
PP = (160.0, 100.0)
RANGE_MM = 1581.0
BALL_PX = (176.5, 146.0)
R = proj.BALL_RADIUS_MM

# A soled 7-iron: 45 mm of face leaning back 30 deg puts the topline here.
SOLED_TOPLINE_MM = 45.0 * math.cos(math.radians(30.0))


def _pixel(point) -> tuple[float, float]:
    return (FX * point[0] / point[2] + PP[0], FX * point[1] / point[2] + PP[1])


def _scene(topline_above_support_mm: float, loft_deg: float = 30.0, face_deg: float = 0.0):
    """A face touching the ball with its topline at a CHOSEN support height.

    Returns the projection plus the truths the tests check against.
    """
    normal = proj.face_normal_camera(loft_deg, face_deg)
    ball = RANGE_MM * proj._ray(BALL_PX, FX, PP)  # pylint: disable=protected-access
    contact = ball - R * normal
    toe_ward = np.array([1.0, 0.0, 0.0])
    toe_ward = toe_ward - (toe_ward @ normal) * normal
    toe_ward /= np.linalg.norm(toe_ward)
    up_face = np.cross(toe_ward, normal)
    up_face /= np.linalg.norm(up_face)
    if up_face[1] > 0.0:
        up_face = -up_face

    plane_y = float(ball[1]) + R
    wanted_y = plane_y - float(topline_above_support_mm)
    # T = contact + a*toe_ward + b*up_face, solved for the row we want.
    rise = (float(contact[1]) - wanted_y) / float(up_face[1]) * -1.0
    topline = contact + 5.0 * toe_ward + rise * up_face
    heel = contact - 50.0 * toe_ward + 8.0 * up_face
    toe = contact + 45.0 * toe_ward + 10.0 * up_face

    projection = proj.project_impact(
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
    return projection, {"ball": ball, "contact": contact, "topline": topline, "plane_y": plane_y}


def _reading(topline_above_support_mm: float, loft_deg: float = 30.0, face_height_mm=45.0):
    projection, truth = _scene(topline_above_support_mm, loft_deg=loft_deg)
    assert projection.ok, projection.reason
    return (
        proj.read_support_plane(
            projection, face_height_mm=face_height_mm, face_height_source="the test's own scene"
        ),
        projection,
        truth,
    )


class TestTheSupportPlaneIsTheBallsBottom:
    def test_the_plane_sits_one_ball_radius_below_the_ball_centre(self):
        ball = (10.0, 20.0, 1500.0)

        assert proj.support_plane_camera_y(ball) == pytest.approx(20.0 + R, abs=1e-12)

    def test_no_surface_thickness_enters_the_datum(self):
        """The only inputs are the ball centre and the ball radius."""
        assert proj.support_plane_camera_y((0.0, 0.0, 1.0), ball_radius_mm=30.0) == 30.0

    def test_the_datum_string_says_the_surface_is_never_asked_for(self):
        reading, _, _ = _reading(SOLED_TOPLINE_MM)

        assert "mat, grass or tee alike" in reading.contact_above_support_source
        assert "no surface thickness is known or assumed" in reading.contact_above_support_source


class TestExactRecoveryOnConstructedGeometry:
    def test_the_contact_height_is_r_times_one_minus_sine_loft(self):
        reading, _, _ = _reading(SOLED_TOPLINE_MM, loft_deg=30.0)

        assert reading.contact_above_support_mm == pytest.approx(
            R * (1.0 - math.sin(math.radians(30.0))), abs=1e-6
        )

    def test_the_constructed_topline_height_comes_back_exactly(self):
        for wanted in (25.0, SOLED_TOPLINE_MM, 55.0):
            reading, _, _ = _reading(wanted)

            assert reading.topline_above_support_mm == pytest.approx(wanted, abs=1e-6)

    def test_the_topline_matches_the_scenes_own_three_d_point(self):
        reading, projection, truth = _reading(SOLED_TOPLINE_MM)

        assert projection.topline_camera_mm[1] == pytest.approx(
            float(truth["topline"][1]), abs=1e-6
        )
        assert reading.topline_above_support_mm == pytest.approx(
            truth["plane_y"] - float(truth["topline"][1]), abs=1e-6
        )

    def test_the_sole_is_the_topline_less_the_face_leaned_by_the_loft(self):
        reading, _, _ = _reading(SOLED_TOPLINE_MM, face_height_mm=45.0)

        assert reading.sole_above_support_mm == pytest.approx(
            SOLED_TOPLINE_MM - 45.0 * math.cos(math.radians(30.0)), abs=1e-6
        )

    def test_a_soled_seven_iron_puts_its_topline_thirty_nine_mm_up_and_its_sole_on_the_plane(self):
        reading, _, _ = _reading(SOLED_TOPLINE_MM)

        assert reading.topline_above_support_mm == pytest.approx(39.0, abs=0.1)
        assert reading.sole_above_support_mm == pytest.approx(0.0, abs=1e-6)
        assert reading.contact_above_support_mm == pytest.approx(10.7, abs=0.1)

    def test_the_face_height_is_measured_along_the_lean_not_vertically(self):
        """cos(loft), not 1: a 45 mm face at 30 deg spans 39 mm of height."""
        flat, _, _ = _reading(50.0, loft_deg=0.0)
        leaning, _, _ = _reading(50.0, loft_deg=30.0)

        assert flat.sole_above_support_mm == pytest.approx(5.0, abs=1e-6)
        assert leaning.sole_above_support_mm == pytest.approx(50.0 - SOLED_TOPLINE_MM, abs=1e-6)


class TestTheReadingFailsClosed:
    def test_a_failed_projection_gives_a_named_reason_not_numbers(self):
        broken = proj.ImpactProjection(False, "landmarks_span_no_face")
        reading = proj.read_support_plane(broken)

        assert not reading.ok
        assert reading.reason == "projection_landmarks_span_no_face"
        assert reading.contact_above_support_mm is None

    def test_a_projection_without_camera_points_is_named(self):
        bare = proj.ImpactProjection(True, "ok")
        reading = proj.read_support_plane(bare)

        assert reading.reason == "projection_carries_no_camera_frame_points"

    def test_a_projection_without_a_loft_is_named(self):
        bare = proj.ImpactProjection(
            True, "ok", contact_camera_mm=(0.0, 0.0, 1.0), ball_camera_mm=(0.0, 0.0, 1.0)
        )
        reading = proj.read_support_plane(bare)

        assert reading.reason == "projection_carries_no_loft"

    def test_no_face_height_leaves_the_sole_unreported_by_name(self):
        reading, _, _ = _reading(SOLED_TOPLINE_MM, face_height_mm=None)

        assert reading.ok
        assert reading.sole_above_support_mm is None
        assert "no face height" in reading.sole_above_support_source
        assert reading.topline_above_support_mm is not None

    def test_a_non_finite_face_height_is_refused_like_a_missing_one(self):
        reading, _, _ = _reading(SOLED_TOPLINE_MM, face_height_mm=float("nan"))

        assert reading.sole_above_support_mm is None
        assert reading.face_height_mm is None


class TestEveryNewFieldCarriesItsProvenance:
    def test_each_measurement_names_where_it_came_from(self):
        reading, _, _ = _reading(SOLED_TOPLINE_MM)

        assert "dynamic loft 30.0 deg" in reading.contact_above_support_source
        assert "topline landmark on the posed face plane" in reading.topline_above_support_source
        assert "45.0 mm face height leaned" in reading.sole_above_support_source
        assert reading.face_height_source == "the test's own scene"

    def test_the_contact_source_admits_it_moves_only_with_the_loft(self):
        reading, _, _ = _reading(SOLED_TOPLINE_MM)

        assert "moves ONLY with the loft" in reading.contact_above_support_source


class TestTheFaceHeightSource:
    def test_the_templates_own_extent_is_preferred(self):
        template = outline.bootstrap_outline_from_category("7-iron")
        height, source = zone.template_face_height_mm(template, "7-iron")

        assert height == pytest.approx(template.extent_mm()[1], abs=1e-9)
        assert "template outline's own topline-to-sole extent" in source

    def test_without_a_template_the_named_per_club_assumption_stands_in(self):
        height, source = zone.template_face_height_mm(None, "7-iron")

        assert height == proj.ASSUMED_FACE_HEIGHT_MM["7-iron"]
        assert source.startswith("ASSUMED")
        assert "unpublished by every official source" in source

    def test_an_outline_with_no_extent_falls_back_to_the_assumption(self):
        template = outline.bootstrap_outline_from_category("7-iron")
        blank = outline.ClubOutlineTemplate(
            club="7-iron",
            outline=np.zeros_like(template.outline),
            occupancy=np.zeros_like(template.occupancy),
            landmarks=template.landmarks,
            mm_per_px=template.mm_per_px,
            n_samples=0,
            source="self-built",
        )
        height, source = zone.template_face_height_mm(blank, "7-iron")

        assert height == proj.ASSUMED_FACE_HEIGHT_MM["7-iron"]
        assert source.startswith("ASSUMED")

    def test_an_unknown_club_with_no_template_gets_no_number_and_says_so(self):
        height, source = zone.template_face_height_mm(None, "putter")

        assert height is None
        assert source == "no template extent and no assumed face height for putter"


class TestTheLieTolerances:
    @pytest.mark.parametrize(
        ("lie", "expected_mm"),
        [
            ("mat", zone.SOLE_NOISE_MARGIN_MM),
            ("grass", zone.GRASS_DIVOT_MM + zone.SOLE_NOISE_MARGIN_MM),
            ("tee", zone.UNKNOWN_TEE_HEIGHT_MM + zone.SOLE_NOISE_MARGIN_MM),
            ("unknown", None),
        ],
    )
    def test_each_lie_has_its_own_named_tolerance(self, lie, expected_mm):
        tolerance, reason = zone.sole_tolerance_for(lie)

        assert tolerance == expected_mm
        assert reason == zone.LIE_TOLERANCE_REASON[lie]

    def test_the_default_lie_is_unknown_and_is_the_widest(self):
        assert zone.DEFAULT_LIE == "unknown"
        assert zone.sole_tolerance_for("unknown")[0] is None
        assert "WIDEST" in zone.sole_tolerance_for("unknown")[1]

    def test_a_tee_names_that_its_height_is_not_sensed(self):
        assert "unknown_tee_height" in zone.sole_tolerance_for("tee")[1]

    def test_a_mat_has_no_give_beyond_the_noise_margin(self):
        assert zone.LIE_SOLE_ALLOWANCE_MM["mat"] == 0.0
        assert "no give" in zone.sole_tolerance_for("mat")[1]

    def test_an_unrecognised_lie_is_refused_rather_than_defaulted(self):
        with pytest.raises(ValueError, match="unknown lie 'sand'"):
            zone.sole_tolerance_for("sand")


class TestTheMatGeometryGate:
    def test_a_topline_below_ball_centre_is_impossible_by_name(self):
        reading, _, _ = _reading(0.9 * R)
        status, detail, _, _ = zone.mat_geometry_gate(reading)

        assert status == "impossible_topline_below_ball_centre"
        assert "topline_19.2_mm_above_the_support_plane" in detail

    def test_a_soled_club_passes_the_topline_rule(self):
        reading, _, _ = _reading(SOLED_TOPLINE_MM)

        assert zone.mat_geometry_gate(reading)[0] == "ok"

    def test_the_topline_rule_is_lie_independent(self):
        reading, _, _ = _reading(0.5 * R)

        for lie in sorted(zone.LIE_SOLE_ALLOWANCE_MM):
            assert zone.mat_geometry_gate(reading, lie=lie)[0] == (
                "impossible_topline_below_ball_centre"
            ), lie

    def test_the_boundary_is_exactly_one_ball_radius(self):
        just_below, _, _ = _reading(R - 0.01)
        just_above, _, _ = _reading(R + 0.01)

        assert zone.mat_geometry_gate(just_below)[0] == "impossible_topline_below_ball_centre"
        assert zone.mat_geometry_gate(just_above)[0] == "ok"

    @pytest.mark.parametrize(
        ("lie", "expected"),
        [
            ("mat", "impossible_sole_below_support_plane"),
            ("grass", "ok"),
            ("tee", "ok"),
            ("unknown", "ok"),
        ],
    )
    def test_a_sole_ten_mm_below_the_plane_is_judged_by_the_lie(self, lie, expected):
        reading, _, _ = _reading(SOLED_TOPLINE_MM - 10.0)

        assert reading.sole_above_support_mm == pytest.approx(-10.0, abs=1e-6)
        assert zone.mat_geometry_gate(reading, lie=lie)[0] == expected

    def test_a_divot_deeper_than_turf_allows_is_impossible_on_grass(self):
        # A 60 mm face keeps the topline legally high while the sole goes deep;
        # with a 45 mm face the topline rule would fire first and mask this one.
        reading, _, _ = _reading(30.0, face_height_mm=60.0)
        status, detail, tolerance, _ = zone.mat_geometry_gate(reading, lie="grass")

        assert reading.sole_above_support_mm == pytest.approx(-21.96, abs=0.01)
        assert status == "impossible_sole_below_support_plane"
        assert tolerance == zone.GRASS_DIVOT_MM + zone.SOLE_NOISE_MARGIN_MM
        assert "sole_22.0_mm_below_the_support_plane_on_a_grass_lie" in detail

    def test_the_unknown_lie_never_enforces_the_sole_rule(self):
        reading, _, _ = _reading(30.0, face_height_mm=200.0)

        assert reading.sole_above_support_mm < -140.0
        assert zone.mat_geometry_gate(reading, lie="unknown")[0] == "ok"
        assert zone.mat_geometry_gate(reading, lie="tee")[0] == (
            "impossible_sole_below_support_plane"
        )

    def test_the_noise_margin_lets_a_sole_sit_marginally_low_on_a_mat(self):
        inside, _, _ = _reading(SOLED_TOPLINE_MM - 0.9 * zone.SOLE_NOISE_MARGIN_MM)
        outside, _, _ = _reading(SOLED_TOPLINE_MM - 1.1 * zone.SOLE_NOISE_MARGIN_MM)

        assert zone.mat_geometry_gate(inside, lie="mat")[0] == "ok"
        assert zone.mat_geometry_gate(outside, lie="mat")[0] == (
            "impossible_sole_below_support_plane"
        )

    def test_the_gate_is_always_a_string_never_a_silent_none(self):
        cases = [None, proj.SupportPlaneReading(False, "nope")]
        cases.append(_reading(SOLED_TOPLINE_MM, face_height_mm=None)[0])
        for case in cases:
            status, _, _, reason = zone.mat_geometry_gate(case)

            assert isinstance(status, str) and status
            assert isinstance(reason, str) and reason

    def test_a_missing_reading_is_named_not_passed(self):
        assert zone.mat_geometry_gate(None)[0] == "not_run_no_support_plane_reading"
        assert zone.mat_geometry_gate(proj.SupportPlaneReading(False, "why"))[0] == (
            "not_run_no_support_plane_reading"
        )

    def test_a_reading_with_no_topline_cannot_be_gated_and_says_so(self):
        bare = proj.SupportPlaneReading(True, "ok", contact_above_support_mm=10.0)

        assert zone.mat_geometry_gate(bare)[0] == "not_run_no_topline_height"


class TestTheFatAndThinSignaturesAreNumbers:
    """No labels. The owner wants measured millimetres, not verdicts."""

    def test_a_normal_strike_puts_the_contact_about_ten_mm_above_the_support(self):
        reading, _, _ = _reading(SOLED_TOPLINE_MM, loft_deg=30.0)

        assert reading.contact_above_support_mm == pytest.approx(10.7, abs=0.2)

    def test_a_bladed_delofted_strike_reads_about_twice_that(self):
        """A thin strike is a delofted one, and the contact climbs towards r."""
        normal, _, _ = _reading(SOLED_TOPLINE_MM, loft_deg=30.0)
        thin, _, _ = _reading(SOLED_TOPLINE_MM, loft_deg=5.0)

        assert thin.contact_above_support_mm == pytest.approx(19.5, abs=0.2)
        assert thin.contact_above_support_mm / normal.contact_above_support_mm > 1.8

    def test_twice_the_normal_reading_is_the_ceiling_at_zero_dynamic_loft(self):
        """r*(1 - sin L) tops out at r, which is exactly 2x the 30 deg value."""
        normal, _, _ = _reading(SOLED_TOPLINE_MM, loft_deg=30.0)
        square, _, _ = _reading(SOLED_TOPLINE_MM, loft_deg=0.0)

        assert square.contact_above_support_mm == pytest.approx(R, abs=1e-6)
        assert square.contact_above_support_mm / normal.contact_above_support_mm == pytest.approx(
            2.0, abs=1e-9
        )

    def test_the_contact_height_moves_only_with_the_loft_under_the_tangent_model(self):
        """Documented limitation: the ball is always tangent to the face.

        Until the D-plane dynamic loft ships, `contact_above_support_mm` is a
        per-club constant, and the strike signature that actually moves is
        `sole_above_support_mm`.
        """
        low, _, _ = _reading(SOLED_TOPLINE_MM - 12.0)
        high, _, _ = _reading(SOLED_TOPLINE_MM + 12.0)

        assert low.contact_above_support_mm == pytest.approx(
            high.contact_above_support_mm, abs=1e-9
        )
        assert high.sole_above_support_mm - low.sole_above_support_mm == pytest.approx(
            24.0, abs=1e-6
        )

    def test_a_thin_strike_lifts_the_sole_a_ball_radius_off_the_plane(self):
        reading, _, _ = _reading(SOLED_TOPLINE_MM + R)

        assert reading.sole_above_support_mm >= R
        assert zone.mat_geometry_gate(reading, lie="mat")[0] == "ok", (
            "a club held high is unusual, not impossible"
        )

    def test_a_fat_strike_on_grass_puts_the_sole_below_the_plane(self):
        reading, _, _ = _reading(SOLED_TOPLINE_MM - 12.0)

        assert reading.sole_above_support_mm == pytest.approx(-12.0, abs=1e-6)
        assert zone.mat_geometry_gate(reading, lie="grass")[0] == "ok"

    def test_no_verdict_word_is_ever_produced(self):
        reading, _, _ = _reading(SOLED_TOPLINE_MM - 12.0)
        status, detail, _, reason = zone.mat_geometry_gate(reading, lie="grass")
        text = " ".join([status, detail, reason, reading.sole_above_support_source]).lower()

        for verdict in ("fat", "thin", "chunk", "blade"):
            assert verdict not in text.split(), verdict


class TestTheSupportPlaneFields:
    """The dict `extract_impact_zone` splices in."""

    TWO_D_FIELDS = (
        "heel_toe_mm",
        "zone",
        "face_width_mm",
        "ball_from_toe_mm",
        "high_low_mm",
        "high_low_status",
    )

    def _fields(self, topline_mm, *, lie=zone.DEFAULT_LIE, loft_deg=30.0):
        projection, _ = _scene(topline_mm, loft_deg=loft_deg)
        return zone.support_plane_fields(projection, "ok", None, "7-iron", lie=lie)

    def test_the_channel_never_writes_a_two_d_field(self):
        fields = self._fields(SOLED_TOPLINE_MM)

        assert not set(fields) & set(self.TWO_D_FIELDS)

    def test_a_good_placement_carries_every_number(self):
        fields = self._fields(SOLED_TOPLINE_MM)

        assert fields["support_plane_status"] == "ok"
        assert fields["mat_gate_status"] == "ok"
        assert fields["vertical_channel_status"] == "ok"
        assert fields["contact_above_support_mm"] == pytest.approx(10.7, abs=0.2)
        assert fields["topline_above_support_mm"] == pytest.approx(SOLED_TOPLINE_MM, abs=1e-6)
        assert fields["sole_above_support_mm"] == pytest.approx(0.0, abs=1e-6)
        assert fields["face_height_mm"] == proj.ASSUMED_FACE_HEIGHT_MM["7-iron"]

    def test_the_gate_withholds_the_template_derived_heights_by_name(self):
        fields = self._fields(0.5 * R)

        assert fields["mat_gate_status"] == "impossible_topline_below_ball_centre"
        assert fields["vertical_channel_status"] == (
            "withheld_impossible_topline_below_ball_centre"
        )
        assert fields["topline_above_support_mm"] is None
        assert fields["sole_above_support_mm"] is None

    def test_the_contact_height_survives_the_gate_because_it_owes_the_template_nothing(self):
        fields = self._fields(0.5 * R)

        assert fields["contact_above_support_mm"] == pytest.approx(10.7, abs=0.2)

    def test_the_lie_and_its_tolerance_travel_on_every_result(self):
        for lie in sorted(zone.LIE_SOLE_ALLOWANCE_MM):
            fields = self._fields(SOLED_TOPLINE_MM, lie=lie)

            assert fields["lie"] == lie
            assert fields["sole_tolerance_reason"] == zone.LIE_TOLERANCE_REASON[lie]
            assert fields["sole_tolerance_mm"] == zone.sole_tolerance_for(lie)[0]

    def test_without_a_projection_the_channel_names_why_it_did_not_run(self):
        fields = zone.support_plane_fields(None, "not_run_no_setup_geometry", None, "7-iron")

        assert fields["support_plane_status"] == "not_run_projection_not_run_no_setup_geometry"
        assert fields["vertical_channel_status"] == "not_run_projection_not_run_no_setup_geometry"
        assert "contact_above_support_mm" not in fields

    def test_the_template_supplies_the_face_height_when_it_has_one(self):
        projection, _ = _scene(SOLED_TOPLINE_MM)
        template = outline.bootstrap_outline_from_category("7-iron")
        fields = zone.support_plane_fields(projection, "ok", template, "7-iron")

        assert fields["face_height_mm"] == pytest.approx(template.extent_mm()[1], abs=1e-9)
        assert "template outline's own" in fields["face_height_source"]


class TestTheResultCarriesTheChannel:
    def test_a_withheld_result_carries_no_support_plane_numbers(self):
        result = zone.withheld("because")

        assert result.contact_above_support_mm is None
        assert result.topline_above_support_mm is None
        assert result.sole_above_support_mm is None
        assert result.support_plane_status == "not_run"
        assert result.mat_gate_status == "not_run"
        assert result.vertical_channel_status == "not_run"
        assert result.lie == zone.DEFAULT_LIE

    def test_the_dict_carries_every_new_field(self):
        as_dict = zone.withheld("because").as_dict()

        for name in (
            "contact_above_support_mm",
            "contact_above_support_source",
            "topline_above_support_mm",
            "topline_above_support_source",
            "sole_above_support_mm",
            "sole_above_support_source",
            "face_height_mm",
            "face_height_source",
            "support_plane_status",
            "lie",
            "sole_tolerance_mm",
            "sole_tolerance_reason",
            "mat_gate_status",
            "mat_gate_detail",
            "vertical_channel_status",
        ):
            assert name in as_dict, name

    def test_the_channel_round_trips_through_json(self):
        projection, _ = _scene(SOLED_TOPLINE_MM)
        fields = zone.support_plane_fields(projection, "ok", None, "7-iron", lie="mat")
        result = zone.ImpactZoneResult(status="ok", reason="ok", heel_toe_mm=3.0, **fields)

        restored = json.loads(json.dumps(result.as_dict()))

        assert restored["contact_above_support_mm"] == pytest.approx(
            fields["contact_above_support_mm"], abs=1e-9
        )
        assert restored["sole_tolerance_mm"] == zone.SOLE_NOISE_MARGIN_MM
        assert restored["mat_gate_status"] == "ok"
        assert restored["lie"] == "mat"

    def test_an_unknown_lie_tolerance_serialises_as_null_not_infinity(self):
        projection, _ = _scene(SOLED_TOPLINE_MM)
        fields = zone.support_plane_fields(projection, "ok", None, "7-iron")
        result = zone.ImpactZoneResult(status="ok", reason="ok", **fields)

        assert json.dumps(result.as_dict()).count("Infinity") == 0
        assert json.loads(json.dumps(result.as_dict()))["sole_tolerance_mm"] is None

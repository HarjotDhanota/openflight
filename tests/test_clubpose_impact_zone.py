"""Every gate in the impact-zone extractor, on data whose answer we chose.

NO GROUND TRUTH EXISTS on real frames, so the only place this pipeline's
arithmetic is knowable is synthetic data: put a rectangle where we want it,
move it the way we want, and ask each stage to recover what we wrote down.

Passing here does NOT mean the extractor works on real pixels. It means it is
not broken. `test_clubpose_impact_zone_regression` is the other half, and even
that is a consistency figure against one annotator.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from openflight.camera.club_motion import ReferenceBall
from openflight.camera.clubpose import head_outline as outline, impact_zone as zone

FPS = 467.6
CONTACT = 71.853
RANGE_RATE_MS = 32.5
BALL = ReferenceBall(x=170.0, y=146.0, diameter_px=11.9, area_px=111)
HEIGHT, WIDTH = 200, 320


def _frames(
    head_at, *, head_size=(16, 30), background=90, head_value=25, ball_value=235
) -> np.ndarray:
    """99 frames: a still bright ball, and a dark head at ``head_at(frame)``.

    Returns None from ``head_at`` for a frame with no head in it. The head is
    DARKER than the mat and the ball is brighter, which is what the real
    capture looks like from behind.
    """
    frames = np.full((99, HEIGHT, WIDTH), background, dtype=np.uint8)
    radius = int(round(BALL.diameter_px / 2.0))
    grid_y, grid_x = np.mgrid[0:HEIGHT, 0:WIDTH]
    ball_disc = (grid_x - BALL.x) ** 2 + (grid_y - BALL.y) ** 2 <= radius**2
    frames[:, ball_disc] = ball_value
    tall, wide = head_size
    for index in range(frames.shape[0]):
        where = head_at(index)
        if where is None:
            continue
        x, y = where
        top, left = int(round(y - tall / 2)), int(round(x - wide / 2))
        frames[index, top : top + tall, left : left + wide] = head_value
        frames[index, ball_disc] = ball_value
    return frames


def _swing(frames: np.ndarray, **kwargs) -> outline.SwingFrames:
    return outline.SwingFrames(
        frames=frames,
        ball=BALL,
        fps=FPS,
        contact_frame=CONTACT,
        range_rate_ms=RANGE_RATE_MS,
        club="7-iron",
        name="synthetic",
        **kwargs,
    )


def _approaching(index: int):
    """A head sliding in from the left and arriving at the ball at contact."""
    if index < 64:
        return None
    return (BALL.x - 9.0 * (CONTACT - index), BALL.y + 4.0)


APPROACH = _swing(_frames(_approaching))


class TestSwingFrames:
    def test_the_align_window_is_six_frames_before_contact_and_one_after(self):
        assert APPROACH.align_frames == (66, 67, 68, 69, 70, 71, 72)

    def test_the_background_is_taken_before_the_club_enters(self):
        window = APPROACH.background_slice

        assert window.start == outline.BACKGROUND_START_FRAME
        assert window.stop == 56
        assert window.stop < 64, "the background must not contain the club"

    def test_the_range_is_the_radar_rate_anchored_at_the_ball(self):
        from openflight.camera.clubpose.projection import CAMERA_BALL_RANGE_MM

        assert APPROACH.range_mm(CONTACT) == pytest.approx(CAMERA_BALL_RANGE_MM)
        assert APPROACH.range_mm(66) < APPROACH.range_mm(71) < APPROACH.range_mm(72)

    def test_the_head_images_larger_when_it_is_nearer(self):
        """36 % wider six frames out, which is what pins the outline's scale."""
        ratio = APPROACH.mm_per_px(71) / APPROACH.mm_per_px(66)

        assert ratio == pytest.approx(1.29, abs=0.05)


class TestHeadMasks:
    def test_a_head_is_found_on_every_frame_of_the_window(self):
        report = outline.head_masks(APPROACH)

        assert set(report.masks) == set(APPROACH.align_frames)
        assert not report.reasons

    def test_a_frame_with_no_moving_head_fails_closed_with_a_reason(self):
        swing = _swing(_frames(lambda index: None if index < 70 else (BALL.x, BALL.y + 4.0)))
        report = outline.head_masks(swing)

        assert 66 not in report.masks
        assert report.reasons[66] == "no_moving_component_near_the_ball"

    def test_a_mask_too_large_to_be_a_clubhead_is_refused_in_millimetres(self):
        """The gate is physical: a pixel gate is 85 % wrong across the window."""
        swing = _swing(_frames(_approaching, head_size=(90, 130)))
        report = outline.head_masks(swing)

        assert not report.masks
        assert all("merged_with_turf" in reason for reason in report.reasons.values())

    def test_the_same_pixel_area_passes_when_the_head_is_far_and_fails_when_near(self):
        """A range-blind gate would get this backwards on one end or the other."""
        near = APPROACH.mm_per_px(66)
        far = APPROACH.mm_per_px(72)
        area_px = outline.MAX_HEAD_AREA_MM2 / (0.5 * (near + far)) ** 2

        assert area_px * near**2 < outline.MAX_HEAD_AREA_MM2
        assert area_px * far**2 > outline.MAX_HEAD_AREA_MM2


class TestTheBallVeto:
    def test_the_ball_is_never_vetoed_before_contact(self):
        """It is stationary there, so it is not in the moving mask anyway, and
        vetoing punches a hole through the head that is sitting on it."""
        report = outline.head_masks(APPROACH)

        assert all(frame > CONTACT for frame in report.ball_vetoes)

    def test_the_departing_ball_is_vetoed_after_contact(self):
        moved = _frames(_approaching)
        radius = int(round(BALL.diameter_px / 2.0))
        grid_y, grid_x = np.mgrid[0:HEIGHT, 0:WIDTH]
        for index in (72,):
            moved[index][(grid_x - BALL.x) ** 2 + (grid_y - BALL.y) ** 2 <= radius**2] = 90
            moved[index][
                (grid_x - (BALL.x + 4)) ** 2 + (grid_y - (BALL.y - 3)) ** 2 <= radius**2
            ] = 250
        report = outline.head_masks(_swing(moved))

        assert 72 in report.ball_vetoes
        x, y, _ = report.ball_vetoes[72]
        assert math.hypot(x - (BALL.x + 4), y - (BALL.y - 3)) < 3.0

    def test_the_veto_fails_closed_when_no_ball_is_bright_enough(self):
        dark = _frames(_approaching)
        radius = int(round(BALL.diameter_px / 2.0))
        grid_y, grid_x = np.mgrid[0:HEIGHT, 0:WIDTH]
        dark[72][(grid_x - BALL.x) ** 2 + (grid_y - BALL.y) ** 2 <= radius**2] = 90
        report = outline.head_masks(_swing(dark))

        assert 72 not in report.ball_vetoes, "no ball found must mean no veto, not a guess"


class TestTheTemplate:
    def test_the_sole_edge_comes_from_the_occupancy_falling_off(self):
        occupancy = np.zeros(outline.TEMPLATE_SHAPE)
        origin_x, origin_y = outline.TEMPLATE_ORIGIN
        occupancy[origin_y : origin_y + 40, origin_x - 30 : origin_x + 30] = 1.0
        # A shadow below, present on only a third of the samples.
        occupancy[origin_y + 40 : origin_y + 80, origin_x - 30 : origin_x + 30] = 0.33

        built = outline.outline_from_occupancy(occupancy, heel_from_data=False)
        rows = np.flatnonzero(built.any(axis=1))

        assert int(rows.max()) < origin_y + 41, "the shadow must not join the outline"

    def test_the_heel_edge_comes_from_the_occupancy_falling_off_in_x(self):
        occupancy = np.zeros(outline.TEMPLATE_SHAPE)
        origin_x, origin_y = outline.TEMPLATE_ORIGIN
        occupancy[origin_y : origin_y + 40, origin_x - 30 : origin_x + 30] = 1.0
        occupancy[origin_y : origin_y + 40, origin_x - 70 : origin_x - 30] = 0.4

        built = outline.outline_from_occupancy(occupancy, heel_from_data=True)
        cols = np.flatnonzero(built.any(axis=0))

        assert int(cols.min()) >= origin_x - 31

    def test_a_template_round_trips_through_a_file(self, tmp_path):
        template = outline.bootstrap_outline_from_category("7-iron")
        path = tmp_path / "template.npz"
        template.save(path)
        loaded = outline.ClubOutlineTemplate.load(path)

        np.testing.assert_array_equal(loaded.outline, template.outline)
        assert loaded.club == template.club
        assert loaded.source == template.source
        assert loaded.mm_per_px == pytest.approx(template.mm_per_px)
        for name, point in template.landmarks.items():
            np.testing.assert_allclose(loaded.landmarks[name], point)


class TestTheCategoryPrior:
    def test_the_blade_lengths_are_the_official_bands(self):
        assert outline.CATEGORY_BLADE_LENGTH_MM["players"] == (74.0, 77.0)
        assert outline.CATEGORY_BLADE_LENGTH_MM["players_cavity"] == (77.0, 81.0)
        assert outline.CATEGORY_BLADE_LENGTH_MM["game_improvement"] == (85.0, 87.0)

    @pytest.mark.parametrize("category", sorted(outline.CATEGORY_BLADE_LENGTH_MM))
    def test_the_bootstrap_outline_is_the_size_the_category_says(self, category):
        low, high = outline.CATEGORY_BLADE_LENGTH_MM[category]
        width_mm, _ = outline.bootstrap_outline_from_category("7-iron", category).extent_mm()

        assert low - 2.0 <= width_mm <= high + 2.0

    def test_a_bootstrap_outline_is_never_labelled_as_a_measurement(self):
        assert outline.bootstrap_outline_from_category("7-iron").source == "self-built"
        assert outline.bootstrap_outline_from_category("7-iron").n_samples == 0

    def test_an_unknown_category_is_refused(self):
        with pytest.raises(ValueError):
            outline.bootstrap_outline_from_category("7-iron", "hollow_body")


class TestBuildingWithoutMarks:
    def test_landmarks_bootstrap_from_a_masks_own_extremes(self):
        mask = np.zeros((HEIGHT, WIDTH), dtype=bool)
        mask[100:130, 150:200] = True
        marks = outline.bootstrap_landmarks(mask)

        assert marks is not None
        assert marks.heel[0] == pytest.approx(150.0)
        assert marks.toe[0] == pytest.approx(199.0)
        assert marks.topline[1] == pytest.approx(100.0)

    def test_a_mask_too_small_to_bootstrap_returns_nothing(self):
        mask = np.zeros((HEIGHT, WIDTH), dtype=bool)
        mask[100:103, 150:155] = True

        assert outline.bootstrap_landmarks(mask) is None

    def test_a_self_built_template_reports_its_convergence(self):
        report = outline.build_template_self_built([APPROACH], "7-iron", 3.5732, passes=2)

        assert report.template is not None
        assert report.template.source == "self-built"
        assert report.template.n_samples > 0
        # Convergence is REPORTED, never assumed: the movement per pass is
        # returned whether or not it settled.
        assert isinstance(report.converged, bool)
        assert len(report.movement_px) <= 1

    def test_a_session_with_no_masks_says_so_rather_than_returning_a_shape(self):
        empty = _swing(np.full((99, HEIGHT, WIDTH), 90, dtype=np.uint8))
        report = outline.build_template_self_built([empty], "7-iron", 3.5732)

        assert report.template is None
        assert report.reason == "no_head_masks_in_the_session"

    def test_the_address_photo_route_is_reserved_and_says_why(self):
        with pytest.raises(NotImplementedError, match="address"):
            outline.build_template_from_address_photo(
                np.zeros((HEIGHT, WIDTH), np.uint8), "7-iron", 3.5732
            )


class TestAlignment:
    def test_a_template_is_recovered_on_the_frame_it_was_built_from(self):
        report = outline.head_masks(APPROACH)
        samples = [
            (APPROACH, frame, mask, outline.bootstrap_landmarks(mask))
            for frame, mask in report.masks.items()
        ]
        template = outline.build_template(samples, "7-iron", 3.5732, source="self-built")
        # f69, where the head is clear of the ball. On the last two frames the
        # ball's own disc is cut out of the head mask and the IoU falls to
        # 0.65-0.75, which is the real capture's problem too.
        placed = outline.align_frame(APPROACH, template, 69, report.masks[69])

        assert placed is not None
        assert placed.iou > 0.85
        assert abs(placed.roll_deg) < 3.0

    def test_the_iou_gate_and_the_track_gate_both_fail_closed(self):
        report = outline.head_masks(APPROACH)
        samples = [
            (APPROACH, frame, mask, outline.bootstrap_landmarks(mask))
            for frame, mask in report.masks.items()
        ]
        template = outline.build_template(samples, "7-iron", 3.5732, source="self-built")
        accepted, rejected = outline.align_swing(APPROACH, template, report)

        assert accepted
        assert all(placement.iou >= outline.IOU_GATE for placement in accepted.values())
        assert all(isinstance(reason, str) and reason for reason in rejected.values())


class TestCarryToContact:
    """The single largest error in the pipeline was a straight line."""

    @staticmethod
    def _parabola(frames, quadratic=0.6):
        per_frame = {}
        for frame in frames:
            offset = frame - CONTACT
            per_frame[frame] = {
                "heel_x": 100.0 + 9.0 * offset + quadratic * offset**2,
                "heel_y": 150.0,
                "toe_x": 200.0 + 9.0 * offset + quadratic * offset**2,
                "toe_y": 150.0,
                "topline_at_ball_y": 140.0 + 2.0 * offset,
            }
        return per_frame

    def test_the_quadratic_lands_on_the_truth_exactly(self):
        carried = zone.carry_to_contact(self._parabola(range(66, 73)), CONTACT)

        assert carried is not None
        assert carried.model == "quadratic"
        assert carried.values["heel_x"] == pytest.approx(100.0, abs=1e-6)
        assert carried.values["toe_x"] == pytest.approx(200.0, abs=1e-6)

    def test_a_straight_line_over_the_same_frames_would_miss_by_pixels(self):
        """Why the model is a parabola and not a line, measured."""
        frames = list(range(66, 72))
        per_frame = self._parabola(frames)
        times = np.asarray(frames, float)
        values = np.asarray([per_frame[f]["heel_x"] for f in frames])
        linear = float(np.polyval(np.polyfit(times, values, 1), CONTACT))

        assert abs(linear - 100.0) > 4.0
        assert zone.carry_to_contact(per_frame, CONTACT).values["heel_x"] == pytest.approx(
            100.0, abs=1e-6
        )

    def test_the_pair_interpolation_is_reported_as_a_cross_check(self):
        carried = zone.carry_to_contact(self._parabola(range(66, 73)), CONTACT)

        assert carried.disagreement_px is not None
        assert carried.disagreement_px < 1.0

    def test_too_few_frames_returns_nothing_rather_than_a_guess(self):
        assert zone.carry_to_contact(self._parabola([70, 71]), CONTACT) is None


class TestTheUsgaGates:
    def _alignment(self, heel, toe, midpoint, topline=None):
        return outline.Alignment(
            frame=71,
            iou=0.8,
            roll_deg=0.0,
            step=1.0,
            anchor=np.zeros(2),
            landmarks={
                "heel": np.asarray(heel, float),
                "toe": np.asarray(toe, float),
                "midpoint": np.asarray(midpoint, float),
                "topline": np.asarray(topline if topline is not None else midpoint, float),
            },
        )

    def test_a_heel_far_from_the_shaft_plane_is_refused(self, monkeypatch):
        monkeypatch.setattr(outline, "shaft_line", lambda *_: (0.0, 100.0))
        monkeypatch.setattr(zone, "shaft_line", lambda *_: (0.0, 100.0))
        far = self._alignment(heel=(160.0, 140.0), toe=(200.0, 140.0), midpoint=(180.0, 140.0))

        ok, reason = zone.heel_within_shaft_plane(APPROACH, far, 71)

        assert not ok
        assert "shaft_plane" in reason

    def test_a_heel_on_the_shaft_plane_passes(self, monkeypatch):
        monkeypatch.setattr(zone, "shaft_line", lambda *_: (0.0, 160.0))
        near = self._alignment(heel=(161.0, 140.0), toe=(200.0, 140.0), midpoint=(180.0, 140.0))

        ok, _ = zone.heel_within_shaft_plane(APPROACH, near, 71)

        assert ok

    def test_a_frame_with_no_visible_shaft_is_not_failed_for_it(self, monkeypatch):
        """The rule is untestable there. Inventing a failure is not fail-closed."""
        monkeypatch.setattr(zone, "shaft_line", lambda *_: None)
        anything = self._alignment(heel=(10.0, 140.0), toe=(200.0, 140.0), midpoint=(105.0, 140.0))

        ok, reason = zone.heel_within_shaft_plane(APPROACH, anything, 71)

        assert ok
        assert "not_testable" in reason

    def test_the_topline_gate_is_looser_than_the_rule_and_says_so(self):
        """2.54 mm is 0.7 px here; the extractor's own topline p90 is 1.78 px."""
        assert zone.USGA_ABOVE_TOPLINE_MM == pytest.approx(2.54)
        assert zone.USGA_ABOVE_TOPLINE_MM / APPROACH.mm_per_px(71) < 1.0
        assert zone.TOPLINE_PLACEMENT_TOLERANCE_PX >= 2.0

    def test_a_mask_rising_well_above_the_topline_is_refused(self):
        report = outline.head_masks(APPROACH)
        samples = [
            (APPROACH, frame, mask, outline.bootstrap_landmarks(mask))
            for frame, mask in report.masks.items()
        ]
        template = outline.build_template(samples, "7-iron", 3.5732, source="self-built")
        frame = 69
        placed = outline.align_frame(APPROACH, template, frame, report.masks[frame])
        clean = report.masks[frame]

        ok, _ = zone.nothing_rises_above_the_topline(APPROACH, template, placed, clean, frame)
        assert ok, "the mask the outline was fitted to must not violate its own topline"

        # A block of mask 10 px above the crown, on the toe side, clear of the ball.
        raised = clean.copy()
        rows, cols = np.nonzero(clean)
        toe_x = int(round(float(placed.landmarks["toe"][0])))
        top = int(rows.min())
        raised[top - 12 : top - 8, toe_x - 4 : toe_x] = True

        ok, reason = zone.nothing_rises_above_the_topline(APPROACH, template, placed, raised, frame)

        assert not ok
        assert "above_the_topline" in reason


class TestTheReading:
    @pytest.mark.parametrize(
        ("offset_mm", "expected"),
        [
            (-30.0, "heel"),
            (-10.0, "heel-centre"),
            (0.0, "centre"),
            (4.9, "centre"),
            (10.0, "centre-toe"),
            (30.0, "toe"),
        ],
    )
    def test_the_zone_bands_are_plus_or_minus_five_and_fifteen(self, offset_mm, expected):
        assert zone.zone_for(offset_mm) == expected

    def test_a_withheld_result_carries_no_numbers_at_all(self):
        result = zone.withheld("because")

        assert result.status == "withheld"
        assert result.heel_toe_mm is None
        assert result.zone is None
        assert result.high_low_mm is None

    def test_the_vertical_channel_is_never_a_zone_and_is_always_flagged(self):
        result = zone.extract_impact_zone(
            APPROACH, outline.bootstrap_outline_from_category("7-iron")
        )

        assert result.high_low_status == "experimental_unvalidated"
        assert "high_low" not in (result.zone or "")

    def test_the_centre_convention_travels_with_every_result(self):
        result = zone.withheld("because")

        assert "CONVENTION" in result.centre_convention
        assert "foot-spray" in result.as_dict()["centre_convention"]

    def test_the_result_is_json_safe(self):
        import json

        result = zone.withheld("because", rejected_frames={66: "reason"})

        assert json.loads(json.dumps(result.as_dict()))["rejected_frames"] == {"66": "reason"}

    def test_an_empty_template_withholds_rather_than_reading_zero(self):
        empty = outline.bootstrap_outline_from_category("7-iron")
        blank = outline.ClubOutlineTemplate(
            club="7-iron",
            outline=np.zeros_like(empty.outline),
            occupancy=np.zeros_like(empty.occupancy),
            landmarks=empty.landmarks,
            mm_per_px=empty.mm_per_px,
            n_samples=0,
            source="self-built",
        )
        result = zone.extract_impact_zone(APPROACH, blank)

        assert result.status == "withheld"
        assert result.reason == "template_outline_is_empty"

    def test_a_swing_with_no_masks_withholds_with_the_masks_own_reasons(self):
        still = _swing(np.full((99, HEIGHT, WIDTH), 90, dtype=np.uint8))
        result = zone.extract_impact_zone(still, outline.bootstrap_outline_from_category("7-iron"))

        assert result.status == "withheld"
        assert result.reason == "no_head_mask_on_any_frame"

    def test_the_template_source_is_recorded_on_the_result(self):
        result = zone.extract_impact_zone(
            APPROACH, outline.bootstrap_outline_from_category("7-iron")
        )

        assert result.template_source == "self-built"


class TestZoneCorrelation:
    def test_two_identical_readings_correlate_perfectly(self):
        values = [-30.0, -10.0, 0.0, 12.0, 25.0]

        assert zone.zone_correlation(values, values) == pytest.approx(1.0)

    def test_a_constant_offset_does_not_hurt_the_correlation(self):
        values = [-30.0, -10.0, 0.0, 12.0, 25.0]
        shifted = [value + 8.0 for value in values]

        assert zone.zone_correlation(values, shifted) == pytest.approx(1.0)

    def test_too_few_pairs_is_refused(self):
        with pytest.raises(ValueError):
            zone.zone_correlation([1.0, 2.0], [1.0, 2.0])


class TestMaskSpeeds:
    """The blur proxy: transverse centroid speed, per frame, in millimetres."""

    def test_the_synthetic_approach_measures_its_own_nine_px_per_frame(self):
        report = outline.head_masks(APPROACH)
        speeds = outline.mask_speeds(APPROACH, report)

        assert set(speeds) <= set(APPROACH.align_frames)
        assert len(speeds) >= 4
        for frame, speed in speeds.items():
            expected = 9.0 * APPROACH.mm_per_px(frame)
            assert 0.5 * expected <= speed <= 1.5 * expected, (frame, speed, expected)

    def test_a_lone_mask_has_no_estimate_rather_than_a_guess(self):
        report = outline.head_masks(APPROACH)
        only = outline.MaskReport(masks={69: report.masks[69]}, reasons={}, ball_vetoes={})

        assert outline.mask_speeds(APPROACH, only) == {}


class TestTheToeCalibration:
    """The fast frames' smear must not end up in the toe landmark.

    The 2026-09-01 audit: the head moves 14-29 mm/frame at f_c-5..f_c-3 and
    ~0 at contact, the fast masks are smeared along the motion, and a template
    averaged over them carried a toe 3-10 mm beyond even the sharpest mask's,
    which shifted every reading 10-25 mm heel-ward. The shape is still built
    from every frame (a slow-frames-only shape failed the topline and heel
    gates); only the toe LANDMARK is calibrated against the slow masks.
    """

    @staticmethod
    def _two_phase_frames() -> np.ndarray:
        """Fast-and-smeared before f68, slow-and-true from f68 on."""

        def head_at(index):
            if index >= 68:
                return BALL.x - 1.5 * (CONTACT - index), BALL.y + 4.0
            x_at_68 = BALL.x - 1.5 * (CONTACT - 68)
            return x_at_68 - 9.0 * (68 - index), BALL.y + 4.0

        frames = np.full((99, HEIGHT, WIDTH), 90, dtype=np.uint8)
        radius = int(round(BALL.diameter_px / 2.0))
        grid_y, grid_x = np.mgrid[0:HEIGHT, 0:WIDTH]
        disc = (grid_x - BALL.x) ** 2 + (grid_y - BALL.y) ** 2 <= radius**2
        frames[:, disc] = 235
        for index in range(64, 99):
            x, y = head_at(index)
            # The fast frames are SMEARED. 30 px of extra width is the field
            # magnitude: 14-29 mm/frame of motion at ~3 mm/px. The width
            # returns to true on the first slow frame, so the toe edge's own
            # motion -- the proxy -- is fast exactly where the mask is wide.
            wide = 30 if index >= 68 else 60
            top, left = int(round(y - 8)), int(round(x - wide / 2))
            frames[index, top : top + 16, left : left + wide] = 25
            frames[index, disc] = 235
        return frames

    @staticmethod
    def _toe_offset_mm(template) -> float:
        span = template.landmarks["toe"][0] - template.landmarks["midpoint"][0]
        return float(span) * template.mm_per_template_px

    def test_the_overhang_is_measured_on_the_slow_frames_and_reported(self):
        swing = _swing(self._two_phase_frames())
        report = outline.build_template_self_built([swing], "7-iron", 3.5732, passes=1)

        assert report.template is not None
        assert report.toe_calibrated
        assert report.toe_calibration_frames >= outline.MIN_TOE_CALIBRATION_FRAMES
        assert report.toe_overhang_mm is not None
        assert report.toe_overhang_mm > 3.0

    def test_the_calibrated_toe_sits_short_of_the_raw_average_by_the_overhang(self):
        """The toe LANDMARK is the mean of the samples' toe extremes, so it --
        not the outline shape, which the 0.5 occupancy threshold protects from
        a minority of wide masks -- is where the smear lands, and it is the
        anchor every impact reading is measured from."""
        swing = _swing(self._two_phase_frames())
        calibrated = outline.build_template_self_built([swing], "7-iron", 3.5732, passes=1)
        raw = outline.build_template_self_built(
            [swing], "7-iron", 3.5732, passes=1, toe_calibration=False
        )

        assert calibrated.template is not None and raw.template is not None
        assert not raw.toe_calibrated
        pulled_in = self._toe_offset_mm(raw.template) - self._toe_offset_mm(calibrated.template)
        assert pulled_in > 3.0
        assert pulled_in == pytest.approx(calibrated.toe_overhang_mm, abs=1.0)

    def test_the_shape_is_untouched_by_the_calibration(self):
        """Only the landmark moves; every alignment gate sees the validated shape."""
        swing = _swing(self._two_phase_frames())
        calibrated = outline.build_template_self_built([swing], "7-iron", 3.5732, passes=1)
        raw = outline.build_template_self_built(
            [swing], "7-iron", 3.5732, passes=1, toe_calibration=False
        )

        np.testing.assert_array_equal(calibrated.template.outline, raw.template.outline)
        np.testing.assert_allclose(
            calibrated.template.landmarks["heel"], raw.template.landmarks["heel"]
        )

    def test_a_session_with_no_slow_frames_leaves_the_landmark_alone_and_says_so(self):
        """APPROACH moves 9 px/frame everywhere -- no frame is slow, so the
        calibration stands down rather than refusing a template."""
        report = outline.build_template_self_built([APPROACH], "7-iron", 3.5732, passes=1)

        assert report.template is not None
        assert not report.toe_calibrated
        assert report.toe_overhang_mm is None
        assert report.toe_calibration_frames == 0


class TestThePhysicsGate:
    """A reading beyond any conforming face is a failed anchor, not a strike."""

    def test_a_mid_face_contact_passes(self):
        ok, reason = zone.reading_is_physical(-45.0)

        assert ok
        assert reason == "ok"

    def test_contact_beyond_any_iron_face_is_withheld_by_name(self):
        deep = zone.MAX_IRON_BLADE_MM + zone.BLADE_GATE_MARGIN_MM + 1.0
        ok, reason = zone.reading_is_physical(-deep)

        assert not ok
        assert "beyond_any_iron_face" in reason

    def test_contact_beyond_the_toe_is_withheld_by_name(self):
        ok, reason = zone.reading_is_physical(zone.TOE_SIDE_MARGIN_MM + 1.0)

        assert not ok
        assert "beyond_the_toe" in reason

    def test_the_boundary_is_the_blade_plus_the_margin(self):
        edge = zone.MAX_IRON_BLADE_MM + zone.BLADE_GATE_MARGIN_MM

        assert zone.reading_is_physical(-edge + 0.1)[0]
        assert not zone.reading_is_physical(-edge - 0.1)[0]

    def test_read_impact_reports_the_toe_anchored_offset(self):
        carry = zone.Carry(
            values={
                "heel_x": 100.0,
                "heel_y": 150.0,
                "toe_x": 200.0,
                "toe_y": 150.0,
                "topline_at_ball_y": 140.0,
            },
            model="quadratic",
            disagreement_px=None,
            frames=(68, 69, 70, 71),
        )
        reading = zone.read_impact(carry, np.array([150.0, 146.0]), 1.0)

        assert reading is not None
        heel_toe, width, high_low, from_toe = reading
        assert from_toe == pytest.approx(-50.0)
        assert width == pytest.approx(100.0)
        assert heel_toe == pytest.approx(-8.0)
        assert high_low == pytest.approx(6.0)

    def test_the_result_dict_carries_the_toe_anchored_channel(self):
        assert "ball_from_toe_mm" in zone.withheld("because").as_dict()

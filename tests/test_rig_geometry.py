"""The two-layer setup geometry: exact synthetic solves, and the taped rig pinned.

`RigGeometry` is enclosure truth, `SetupSolution` is per-setup truth, and the
tests hold them to the split: every solved number is exact given its stated
assumption, every assumed number names itself, and the test rig's constants
agree with the tape chain in `clubpose.projection` that they duplicate.
"""

from __future__ import annotations

import json
import math

import pytest

from openflight.camera.club_motion import ReferenceBall
from openflight.rig_geometry import (
    BALL_DIAMETER_MM,
    MIN_BALL_DIAMETER_PX,
    RigGeometry,
    solve_setup,
)

RIG = RigGeometry.test_rig()


def _ball(x: float = 160.0, y: float = 100.0, diameter_px: float = 12.6) -> ReferenceBall:
    return ReferenceBall(x=x, y=y, diameter_px=diameter_px, area_px=120)


class TestRigGeometry:
    def test_the_test_rig_focal_matches_the_fitters(self):
        """One optics derivation, pinned in two modules so neither drifts."""
        from openflight.camera.clubpose.fit import FOCAL_PX

        assert RIG.focal_px == pytest.approx(FOCAL_PX)

    def test_the_test_rig_matches_the_tape_chain_in_projection(self):
        from openflight.camera.clubpose import projection

        # Image axes: the ball line sits at MINUS the camera's lateral offset,
        # and the OPS is below the lens by the difference of the taped heights.
        assert RIG.ops_offset_mm[0] == pytest.approx(-projection.CAMERA_LATERAL_OFFSET_MM)
        assert RIG.ops_offset_mm[1] == pytest.approx(
            projection.CAMERA_LENS_HEIGHT_MM - projection.RADAR_HEIGHT_MM
        )

    def test_the_test_rig_names_its_provenance_and_its_gaps(self):
        # 2026-09-09: these numbers were never measured, and the provenance
        # must say so rather than call them a tape chain.
        assert "ASSUMED" in RIG.provenance
        assert "never measured" in RIG.provenance
        assert "IWR" in RIG.provenance
        assert RIG.iwr_offset_mm is None, "the IWR position was never known; None must say so"
        assert RIG.mic_offset_mm == RIG.ops_offset_mm, "the OPS stands in for the mic, by name"

    def test_the_test_rig_carries_no_enclosure_facts(self):
        setup = RIG.enclosure_setup()

        assert setup.camera_mount_height_m is None
        assert setup.radar_height_m is None
        assert setup.iwr_tilt_deg is None
        assert set(setup.missing) == {
            "lens_height_above_floor_mm",
            "iwr_offset_mm",
            "iwr_boresight_pitch_deg",
        }

    def test_json_round_trips_tuples_and_nones(self, tmp_path):
        path = tmp_path / "rig.json"
        RIG.to_json(path)
        loaded = RigGeometry.from_json(path)

        assert loaded == RIG
        assert isinstance(loaded.ops_offset_mm, tuple)
        assert loaded.iwr_offset_mm is None
        assert json.loads(path.read_text(encoding="utf-8"))["focal_px"] == RIG.focal_px


class TestTheEnclosureSetup:
    """The v42 enclosure's file drives the live pipeline's geometry inputs."""

    V42 = RigGeometry(
        focal_px=466.6667,
        boresight_pitch_deg=-1.0,
        iwr_offset_mm=(75.05, 165.06, 16.72),
        ops_offset_mm=(-27.0, 170.0, 16.0),
        mic_offset_mm=(-30.0, 3.0, -9.0),
        lens_height_above_floor_mm=220.65,
        iwr_boresight_pitch_deg=10.0,
        ops_boresight_pitch_deg=10.0,
        housing_tilt_deg=10.0,
        provenance="Fusion v44 + TI PROC116A, hole-registered",
    )

    def test_the_live_inputs_are_derived_not_typed(self):
        setup = self.V42.enclosure_setup()

        assert setup.camera_mount_height_m == pytest.approx(0.22065)
        # The IWR sits 75.05 mm to the camera's image-right, so the CAMERA is
        # 75.05 mm target-LEFT of the radar: the server's convention is
        # camera-relative-to-radar, positive target-right.
        assert setup.camera_lateral_offset_m == pytest.approx(-0.07505)
        # Antenna height = lens height minus the antenna's drop below the lens.
        assert setup.radar_height_m == pytest.approx(0.05559, abs=1e-5)
        assert setup.iwr_tilt_deg == pytest.approx(10.0)
        assert setup.missing == ()
        assert setup.provenance == self.V42.provenance

    def test_the_shipped_v42_file_derives_the_same_numbers(self):
        rig = RigGeometry.from_json("config/enclosure_v42_rig_geometry.json")
        setup = rig.enclosure_setup()

        assert setup.missing == ()
        assert setup.camera_mount_height_m == pytest.approx(0.2207, abs=5e-4)
        assert setup.radar_height_m == pytest.approx(0.0556, abs=5e-4)
        assert setup.camera_lateral_offset_m == pytest.approx(-0.0750, abs=5e-4)
        assert setup.iwr_tilt_deg == pytest.approx(10.0)
        assert "20260825" in rig.provenance, "the file must say it is not the August rig"

    def test_missing_pieces_are_named_not_defaulted(self):
        import dataclasses

        no_height = dataclasses.replace(self.V42, lens_height_above_floor_mm=None)
        setup = no_height.enclosure_setup()
        assert setup.camera_mount_height_m is None
        assert setup.radar_height_m is None, "radar height needs the lens height"
        assert setup.camera_lateral_offset_m == pytest.approx(-0.07505), "lateral does not"
        assert setup.missing == ("lens_height_above_floor_mm",)

        no_tilt = dataclasses.replace(self.V42, iwr_boresight_pitch_deg=None)
        assert no_tilt.enclosure_setup().missing == ("iwr_boresight_pitch_deg",)

    def test_the_new_fields_round_trip_and_old_files_still_load(self, tmp_path):
        path = tmp_path / "v42.json"
        self.V42.to_json(path)
        assert RigGeometry.from_json(path) == self.V42

        # A file written before the enclosure fields existed loads with them None.
        old = {
            k: v
            for k, v in json.loads(path.read_text(encoding="utf-8")).items()
            if k
            not in (
                "lens_height_above_floor_mm",
                "iwr_boresight_pitch_deg",
                "ops_boresight_pitch_deg",
                "housing_tilt_deg",
            )
        }
        path.write_text(json.dumps(old), encoding="utf-8")
        loaded = RigGeometry.from_json(path)
        assert loaded.lens_height_above_floor_mm is None
        assert loaded.iwr_offset_mm == self.V42.iwr_offset_mm
        assert "lens_height_above_floor_mm" in loaded.enclosure_setup().missing

    def test_as_dict_is_json_safe(self):
        payload = self.V42.enclosure_setup().as_dict()
        json.dumps(payload)
        assert payload["missing"] == []


class TestSolveSetup:
    def test_range_from_angular_size_is_exact(self):
        """A ball whose pixel diameter equals its millimetre diameter sits at
        exactly one focal length of range."""
        rig = RigGeometry(focal_px=1000.0)
        solution = solve_setup(_ball(diameter_px=BALL_DIAMETER_MM), rig)

        assert solution.range_to_ball_mm == pytest.approx(1000.0)
        assert solution.mm_per_px_at_ball == pytest.approx(1.0)

    def test_lateral_sign_matches_the_taped_convention(self):
        """Ball right of centre = camera LEFT of the ball line = negative,
        the same sign as the taped -60.325 mm."""
        right = solve_setup(_ball(x=176.0), RIG)
        left = solve_setup(_ball(x=144.0), RIG)

        assert right.lateral_offset_mm < 0.0 < left.lateral_offset_mm
        assert right.lateral_offset_mm == pytest.approx(-left.lateral_offset_mm)

    def test_the_session_ball_reproduces_the_taped_rig(self):
        """At the tape-consistent diameter the solves land on the taped chain:
        range, lateral, height, and the 1.575 m mic distance the acoustic
        walk-back hardcodes today. 12.665 px is the diameter whose pinhole
        DEPTH puts the SLANT at the taped 1581 mm at this ball's off-axis
        position (the 2026-09-01 audit's depth-vs-slant fix)."""
        solution = solve_setup(_ball(x=176.5, y=146.0, diameter_px=12.665), RIG)

        assert solution.range_to_ball_mm == pytest.approx(1581.0, abs=1.5)
        assert solution.lateral_offset_mm == pytest.approx(-55.9, abs=1.0)
        # 163.2 mm taped; the ball-row solve assumes a level boresight and the
        # solved -0.22 deg mount accounts for the ~6 mm gap.
        assert solution.height_above_ball_mm == pytest.approx(163.2, abs=10.0)
        assert solution.mic_to_ball_m == pytest.approx(1.575, abs=0.015)

    def test_the_measured_ball_diameter_shows_the_cross_check_working(self):
        """The session's measured 12.77 px ball solves ~13 mm nearer than the
        tape (post-slant-fix; it was 21 before the audit's correction) -- the
        range_disagreement_mm cross-check is exactly for this."""
        solution = solve_setup(_ball(x=176.5, y=146.0, diameter_px=12.77), RIG)

        disagreement = solution.range_disagreement_mm(1581.0)
        assert disagreement < 0.0
        assert abs(disagreement) == pytest.approx(13.0, abs=5.0)

    def test_the_height_assumption_is_named(self):
        solution = solve_setup(_ball(), RIG)

        assert "ASSUMING" in solution.solved_from["height_above_ball_mm"]
        assert "ASSUMED" in solution.solved_from["camera_pitch_deg"]

    def test_no_mic_offset_means_none_by_name_not_a_guess(self):
        rig = RigGeometry(focal_px=466.7, mic_offset_mm=None)
        solution = solve_setup(_ball(), rig)

        assert solution.mic_to_ball_m is None
        assert "unavailable" in solution.solved_from["mic_to_ball_m"]
        assert any("mic" in warning for warning in solution.warnings)

    def test_a_tiny_ball_warns_about_the_soft_range_solve(self):
        solution = solve_setup(_ball(diameter_px=MIN_BALL_DIAMETER_PX - 1.0), RIG)

        assert any("range_solve_is_soft" in warning for warning in solution.warnings)

    def test_a_ball_against_the_frame_edge_warns(self):
        solution = solve_setup(_ball(x=6.0), RIG)

        assert any("frame_edge" in warning for warning in solution.warnings)

    def test_a_clean_solve_carries_no_warnings(self):
        assert solve_setup(_ball(x=160.0, y=100.0, diameter_px=12.6), RIG).warnings == ()

    def test_a_degenerate_ball_is_refused(self):
        with pytest.raises(ValueError):
            solve_setup(_ball(diameter_px=0.0), RIG)
        with pytest.raises(ValueError):
            solve_setup(_ball(diameter_px=math.nan), RIG)

    def test_the_solution_is_json_safe(self):
        solution = solve_setup(_ball(), RIG)

        round_tripped = json.loads(json.dumps(solution.as_dict()))
        assert round_tripped["range_to_ball_mm"] == solution.range_to_ball_mm
        assert round_tripped["warnings"] == list(solution.warnings)


class TestTheAcousticSeam:
    def test_the_solved_mic_distance_feeds_the_contact_walk_back(self):
        """The walk-back moves with the setup: a unit twice as far from the
        ball hears the impact later, and the contact frame walks back more."""
        from openflight.camera.clubpose.impact_zone import contact_frame_from_trigger

        near = solve_setup(_ball(diameter_px=25.2), RIG)  # ~half range
        far = solve_setup(_ball(diameter_px=12.6), RIG)

        assert near.mic_to_ball_m < far.mic_to_ball_m
        assert contact_frame_from_trigger(
            71, 467.6, ball_to_unit_m=near.mic_to_ball_m
        ) > contact_frame_from_trigger(71, 467.6, ball_to_unit_m=far.mic_to_ball_m)


class TestDepthVersusSlant:
    """The 2026-09-01 audit's fix: f*D/d is DEPTH; the reported range is SLANT."""

    def test_an_off_axis_ball_reports_a_longer_range_by_exactly_the_ray_length(self):
        centred = solve_setup(_ball(x=160.0, y=100.0, diameter_px=12.6), RIG)
        off_axis = solve_setup(_ball(x=176.5, y=146.0, diameter_px=12.6), RIG)

        ray_length = math.hypot(16.5 / RIG.focal_px, 46.0 / RIG.focal_px, 1.0)
        assert centred.range_to_ball_mm == pytest.approx(RIG.focal_px * BALL_DIAMETER_MM / 12.6), (
            "on the axis, slant IS depth"
        )
        assert off_axis.range_to_ball_mm / centred.range_to_ball_mm == pytest.approx(
            ray_length, abs=1e-9
        )
        assert off_axis.range_to_ball_mm - centred.range_to_ball_mm == pytest.approx(8.6, abs=0.3)

    def test_the_transverse_scale_uses_the_depth_not_the_slant(self):
        off_axis = solve_setup(_ball(x=176.5, y=146.0, diameter_px=12.6), RIG)

        depth = RIG.focal_px * BALL_DIAMETER_MM / 12.6
        assert off_axis.mm_per_px_at_ball == pytest.approx(depth / RIG.focal_px)
        assert off_axis.mm_per_px_at_ball < off_axis.range_to_ball_mm / RIG.focal_px

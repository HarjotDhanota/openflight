"""Enclosure geometry: the static file and the resting-ball solve."""

from __future__ import annotations

import dataclasses
import json
import math
from types import SimpleNamespace

import pytest

from openflight.rig_geometry import (
    BALL_DIAMETER_MM,
    EnclosureSetup,
    RigGeometry,
    SetupSolution,
    solve_setup,
)

V3 = "config/enclosure_v3_rig_geometry.json"


def rig(**overrides) -> RigGeometry:
    base = dict(
        focal_px=466.6667,
        image_width=320,
        image_height=200,
        iwr_offset_mm=(0.0, 44.0, -30.0),
        mic_offset_mm=(-80.0, 0.0, 0.0),
        lens_height_above_floor_mm=95.0,
        iwr_boresight_pitch_deg=10.0,
        housing_tilt_deg=0.0,
        provenance="test",
    )
    base.update(overrides)
    return RigGeometry(**base)


def ball(x=160.0, y=100.0, diameter_px=12.0):
    return SimpleNamespace(x=x, y=y, diameter_px=diameter_px)


class TestTheFile:
    def test_json_round_trips_tuples_and_nones(self, tmp_path):
        path = tmp_path / "rig.json"
        original = rig(ops_offset_mm=None)
        original.to_json(path)
        loaded = RigGeometry.from_json(path)
        assert loaded == original
        assert isinstance(loaded.iwr_offset_mm, tuple)
        assert loaded.ops_offset_mm is None

    def test_older_files_without_the_mount_fields_still_load(self, tmp_path):
        data = dataclasses.asdict(rig())
        for key in ("lis3dh_mount_pitch_deg", "lis3dh_mount_roll_deg", "housing_tilt_deg"):
            data.pop(key)
        path = tmp_path / "old.json"
        path.write_text(json.dumps(data))
        loaded = RigGeometry.from_json(path)
        assert loaded.housing_tilt_deg is None

    def test_the_shipped_v3_file_derives_the_measured_numbers(self):
        setup = RigGeometry.from_json(V3).enclosure_setup()
        assert setup.missing == ()
        assert setup.camera_mount_height_m == pytest.approx(0.095, abs=5e-4)
        assert setup.radar_height_m == pytest.approx(0.051, abs=5e-4)  # lens 95, RX 44 below
        assert setup.camera_lateral_offset_m == pytest.approx(0.0, abs=5e-4)
        assert setup.iwr_tilt_deg == pytest.approx(10.0)

    def test_the_v3_provenance_says_what_it_is_not(self):
        text = RigGeometry.from_json(V3).provenance
        assert "20260825" in text, "must say it does not describe the August session"
        assert "PENDING checkerboard" in text


class TestTheEnclosureSetup:
    def test_the_live_inputs_are_derived_not_typed(self):
        setup = rig().enclosure_setup()
        assert isinstance(setup, EnclosureSetup)
        assert setup.camera_mount_height_m == pytest.approx(0.095)
        assert setup.radar_height_m == pytest.approx(0.051)
        assert setup.camera_lateral_offset_m == pytest.approx(-0.0)
        assert setup.iwr_tilt_deg == 10.0
        assert setup.missing == ()

    def test_lateral_sign_is_camera_relative_to_radar(self):
        # radar 75 mm to the image right of the lens -> camera is 75 mm left of it
        setup = rig(iwr_offset_mm=(75.0, 44.0, 0.0)).enclosure_setup()
        assert setup.camera_lateral_offset_m == pytest.approx(-0.075)

    def test_missing_pieces_are_named_not_defaulted(self):
        setup = rig(lens_height_above_floor_mm=None, iwr_boresight_pitch_deg=None).enclosure_setup()
        assert setup.camera_mount_height_m is None
        assert setup.radar_height_m is None
        assert setup.iwr_tilt_deg is None
        assert set(setup.missing) == {"lens_height_above_floor_mm", "iwr_boresight_pitch_deg"}

    def test_no_iwr_offset_means_no_lateral_and_no_radar_height(self):
        setup = rig(iwr_offset_mm=None).enclosure_setup()
        assert setup.camera_lateral_offset_m is None
        assert setup.radar_height_m is None
        assert "iwr_offset_mm" in setup.missing

    def test_as_dict_is_json_safe(self):
        json.dumps(rig().enclosure_setup().as_dict())


class TestSolveSetup:
    def test_range_from_angular_size_is_exact_on_axis(self):
        r = rig()
        solution = solve_setup(ball(x=160.0, y=100.0, diameter_px=12.0), r)
        assert solution.range_to_ball_mm == pytest.approx(r.focal_px * BALL_DIAMETER_MM / 12.0)
        assert solution.mm_per_px_at_ball == pytest.approx(BALL_DIAMETER_MM / 12.0)
        assert solution.lateral_offset_mm == pytest.approx(0.0)
        assert solution.height_above_ball_mm == pytest.approx(0.0)

    def test_an_off_axis_ball_reports_the_slant_not_the_depth(self):
        r = rig()
        depth = r.focal_px * BALL_DIAMETER_MM / 12.0
        solution = solve_setup(ball(x=200.0, y=140.0, diameter_px=12.0), r)
        ray = math.hypot((200.0 - 160.0) / r.focal_px, (140.0 - 100.0) / r.focal_px, 1.0)
        assert solution.range_to_ball_mm == pytest.approx(depth * ray)
        assert solution.mm_per_px_at_ball == pytest.approx(depth / r.focal_px)

    def test_lateral_sign_a_ball_imaging_right_means_the_camera_sits_left(self):
        solution = solve_setup(ball(x=200.0), rig())
        assert solution.lateral_offset_mm < 0

    def test_the_height_assumption_is_named(self):
        solution = solve_setup(ball(), rig(boresight_pitch_deg=-1.0))
        assert "ASSUMING" in solution.solved_from["height_above_ball_mm"]
        assert solution.camera_pitch_deg == -1.0

    def test_mic_distance_is_solved_when_the_rig_has_a_mic(self):
        solution = solve_setup(ball(), rig())
        assert solution.mic_to_ball_m is not None
        assert 1.5 < solution.mic_to_ball_m < 1.8

    def test_no_mic_offset_means_none_by_name_not_a_guess(self):
        solution = solve_setup(ball(), rig(mic_offset_mm=None))
        assert solution.mic_to_ball_m is None
        assert "rig_has_no_mic_offset_acoustic_walkback_uses_its_default" in solution.warnings

    def test_a_tiny_ball_warns_about_the_soft_range_solve(self):
        solution = solve_setup(ball(diameter_px=6.0), rig())
        assert any(w.startswith("ball_only_6.0_px") for w in solution.warnings)

    def test_a_ball_against_the_frame_edge_warns(self):
        solution = solve_setup(ball(x=8.0), rig())
        assert "ball_near_frame_edge_diameter_at_risk" in solution.warnings

    def test_a_clean_solve_carries_no_warnings(self):
        assert solve_setup(ball(), rig()).warnings == ()

    def test_a_degenerate_ball_is_refused(self):
        with pytest.raises(ValueError):
            solve_setup(ball(diameter_px=0.0), rig())

    def test_range_disagreement_is_camera_minus_reference(self):
        solution = solve_setup(ball(), rig())
        assert solution.range_disagreement_mm(solution.range_to_ball_mm - 50.0) == pytest.approx(
            50.0
        )

    def test_the_solution_is_json_safe(self):
        assert isinstance(solve_setup(ball(), rig()), SetupSolution)
        json.dumps(solve_setup(ball(), rig()).as_dict())


class TestTheExpectedInclinometerOrientation:
    def test_a_file_without_mount_angles_assumes_the_board_is_parallel(self):
        expected = rig(housing_tilt_deg=10.0).expected_inclinometer_orientation()
        assert expected.pitch_deg == pytest.approx(10.0)
        assert expected.roll_deg == pytest.approx(0.0)
        assert "ASSUMED" in expected.provenance["pitch_deg"]
        assert set(expected.missing) == {"lis3dh_mount_pitch_deg", "lis3dh_mount_roll_deg"}

    def test_measured_mount_angles_are_added_and_credited(self):
        expected = rig(
            housing_tilt_deg=10.0, lis3dh_mount_pitch_deg=0.75, lis3dh_mount_roll_deg=0.2
        ).expected_inclinometer_orientation()
        assert expected.pitch_deg == pytest.approx(10.75)
        assert expected.roll_deg == pytest.approx(0.2)
        assert "from the rig file" in expected.provenance["pitch_deg"]
        assert expected.missing == ()

    def test_without_a_housing_tilt_there_is_no_expected_pitch(self):
        expected = rig(housing_tilt_deg=None).expected_inclinometer_orientation()
        assert expected.pitch_deg is None
        assert "housing_tilt_deg" in expected.missing
        assert expected.roll_deg == pytest.approx(0.0)

    def test_the_shipped_v3_file_expects_a_level_enclosure(self):
        expected = RigGeometry.from_json(V3).expected_inclinometer_orientation()
        assert expected.pitch_deg == pytest.approx(0.0)
        assert expected.roll_deg == pytest.approx(0.0)
        assert expected.missing == ()

    def test_as_dict_is_json_safe(self):
        json.dumps(rig().expected_inclinometer_orientation().as_dict())

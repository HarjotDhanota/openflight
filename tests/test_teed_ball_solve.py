"""The tester's solve from the teed ball's pixel size."""

from __future__ import annotations

import json
import math
from types import SimpleNamespace

import pytest

from openflight.camera.teed_ball_solve import SetupSolution, solve_setup
from openflight.constants import BALL_DIAMETER_MM
from tests.test_rig_geometry import rig


def ball(x=160.0, y=100.0, diameter_px=12.0):
    return SimpleNamespace(x=x, y=y, diameter_px=diameter_px)


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

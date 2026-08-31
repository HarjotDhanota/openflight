"""Does the two-sensor fusion recover a motion we chose ourselves?

On real captures the fused velocity is checked against the OPS243's independent
club speed, which is a cross-sensor agreement rather than a truth comparison.
Here the truth IS known: a point is put on a straight 3-D line, the measured
camera is asked for the rays it would see, the radar's contribution is the
analytic range rate at the middle sample, and the recovered velocity must be
the one we wrote down.

Passing here does not mean the fusion works on real pixels. It means the
arithmetic is right, so a disagreement on real pixels is about the pixels.
"""

from __future__ import annotations

import math
import types

import numpy as np
import pytest

from openflight.camera.clubpose.fit import measured_camera
from openflight.camera.clubpose.fusion import (
    axis_basis,
    clubhead_velocity_world,
    omega_from_phase,
    ranges_from_radar,
    rotation_from_omega_deg_s,
)
from openflight.camera.clubpose.projection import CAMERA_BALL_RANGE_MM

CAPTURE_FPS = 467.6
BALL_RANGE_MM = 1581.0


def _straight_line_observation(velocity_mm_s, start_mm, frames: int = 7):
    """What the measured camera sees of a point on a known straight line.

    Returns the rays, the true ranges, the frame times, and the radar range
    rate a perfect sensor would report at the middle sample.
    """
    camera = measured_camera()
    velocity = np.asarray(velocity_mm_s, dtype=float)
    times = np.arange(frames, dtype=float) / CAPTURE_FPS
    points = (
        np.asarray(start_mm, dtype=float)[None, :]
        + velocity[None, :] * (times - times[frames // 2])[:, None]
    )
    offsets = points - camera.center_world
    ranges_mm = np.linalg.norm(offsets, axis=1)
    rays = offsets / ranges_mm[:, None]
    range_rate_ms = float(velocity @ rays[frames // 2]) / 1000.0
    return rays, ranges_mm, times, range_rate_ms


class TestRangesFromRadar:
    def test_the_anchor_frame_sits_exactly_at_the_ball_range(self):
        times = np.arange(8, dtype=float) / CAPTURE_FPS
        impact = float(times[5])
        ranges = ranges_from_radar(times, impact, 33.0, BALL_RANGE_MM)

        assert ranges.ranges_mm[5] == pytest.approx(BALL_RANGE_MM, abs=1e-9)

    def test_the_default_anchor_is_the_measured_tape_range(self):
        ranges = ranges_from_radar([0.0], 0.0, 33.0)

        assert ranges.ranges_mm[0] == pytest.approx(CAMERA_BALL_RANGE_MM, abs=1e-9)
        assert CAMERA_BALL_RANGE_MM == pytest.approx(BALL_RANGE_MM)

    def test_range_walks_back_along_the_radar_range_rate(self):
        """The camera is BEHIND the ball, so the club recedes into impact.

        Before contact the clubhead is nearer the camera than the ball is, and
        each earlier frame is one ``v * dt`` nearer still.
        """
        dt = 1.0 / CAPTURE_FPS
        times = np.asarray([0.0, dt, 2.0 * dt])
        walked = ranges_from_radar(times, 2.0 * dt, 33.0, BALL_RANGE_MM).ranges_mm

        assert walked[0] < walked[1] < walked[2] == pytest.approx(BALL_RANGE_MM)
        np.testing.assert_allclose(np.diff(walked), 33.0 * 1000.0 * dt, atol=1e-9)

    def test_the_span_is_the_distance_the_club_covers(self):
        times = np.arange(16, dtype=float) / CAPTURE_FPS
        ranges = ranges_from_radar(times, float(times[-1]), 33.0)

        assert float(np.ptp(ranges.ranges_mm)) == pytest.approx(
            33.0 * 1000.0 * float(np.ptp(times))
        )

    @pytest.mark.parametrize(
        ("times", "impact", "rate", "anchor"),
        (
            ([0.0, float("nan")], 0.0, 33.0, BALL_RANGE_MM),
            ([0.0, 1.0], float("inf"), 33.0, BALL_RANGE_MM),
            ([0.0, 1.0], 0.0, float("nan"), BALL_RANGE_MM),
            ([0.0, 1.0], 0.0, 33.0, 0.0),
        ),
    )
    def test_rejects_inputs_that_cannot_anchor_a_range(self, times, impact, rate, anchor):
        with pytest.raises(ValueError):
            ranges_from_radar(times, impact, rate, anchor)


class TestClubheadVelocity:
    @pytest.mark.parametrize(
        "velocity",
        (
            (30_000.0, 2_000.0, 1_000.0),
            (33_000.0, -1_500.0, 4_000.0),
            (25_000.0, 0.0, 0.0),
        ),
    )
    def test_recovers_a_known_straight_line_velocity_within_one_percent(self, velocity):
        rays, ranges_mm, times, range_rate_ms = _straight_line_observation(
            velocity, (-150.0, -30.0, 60.0)
        )

        recovered = clubhead_velocity_world(rays, ranges_mm, times, range_rate_ms)

        truth = np.asarray(velocity, dtype=float)
        error = float(np.linalg.norm(recovered - truth)) / float(np.linalg.norm(truth))
        assert error < 0.01, f"recovered {recovered} for truth {truth}"

    def test_neither_sensor_alone_would_have_got_there(self):
        """The radar term and the camera term must BOTH carry real magnitude."""
        velocity = (30_000.0, 2_000.0, 6_000.0)
        rays, ranges_mm, times, range_rate_ms = _straight_line_observation(
            velocity, (-150.0, -30.0, 60.0)
        )
        recovered = clubhead_velocity_world(rays, ranges_mm, times, range_rate_ms)

        radar_only = range_rate_ms * 1000.0 * rays[len(rays) // 2]
        transverse = recovered - radar_only
        assert float(np.linalg.norm(radar_only)) > 0.5 * float(np.linalg.norm(recovered))
        assert float(np.linalg.norm(transverse)) > 0.05 * float(np.linalg.norm(recovered))

    def test_rejects_a_single_frame(self):
        with pytest.raises(ValueError):
            clubhead_velocity_world([[1.0, 0.0, 0.0]], [1581.0], [0.0], 33.0)

    def test_rejects_mismatched_frames(self):
        with pytest.raises(ValueError):
            clubhead_velocity_world(
                [[1.0, 0.0, 0.0], [1.0, 0.0, 0.0]], [1581.0], [0.0, 0.002], 33.0
            )

    def test_rejects_frames_that_span_no_time(self):
        with pytest.raises(ValueError):
            clubhead_velocity_world(
                [[1.0, 0.0, 0.0], [1.0, 0.0, 0.0]], [1581.0, 1581.0], [0.0, 0.0], 33.0
            )


class TestAxisBasis:
    @pytest.mark.parametrize(
        "velocity",
        (
            (30_000.0, 2_000.0, 1_000.0),
            (0.0, 0.0, 12_000.0),  # exercises the polar seed switch
            (-4.0, 9.0, -2.0),
        ),
    )
    def test_the_basis_is_orthonormal_and_perpendicular_to_the_velocity(self, velocity):
        first, second = axis_basis(velocity)
        unit = np.asarray(velocity, dtype=float) / np.linalg.norm(velocity)

        assert float(np.linalg.norm(first)) == pytest.approx(1.0, abs=1e-12)
        assert float(np.linalg.norm(second)) == pytest.approx(1.0, abs=1e-12)
        assert float(first @ second) == pytest.approx(0.0, abs=1e-12)
        assert float(first @ unit) == pytest.approx(0.0, abs=1e-12)
        assert float(second @ unit) == pytest.approx(0.0, abs=1e-12)

    def test_rejects_a_degenerate_velocity(self):
        with pytest.raises(ValueError):
            axis_basis((0.0, 0.0, 0.0))

    def test_rejects_a_non_vector(self):
        with pytest.raises(ValueError):
            axis_basis((1.0, 0.0))


class TestOmegaFromPhase:
    @pytest.mark.parametrize("phase_deg", (0.0, 45.0, 90.0, 180.0, 271.0))
    def test_omega_is_perpendicular_to_v_at_every_phase(self, phase_deg):
        velocity = np.asarray([30_000.0, 2_000.0, 1_000.0])
        magnitude = math.degrees(33.0 / 1.6)

        omega = omega_from_phase(axis_basis(velocity), phase_deg, magnitude)

        assert float(np.linalg.norm(omega)) == pytest.approx(magnitude, rel=1e-12)
        cosine = float(omega @ velocity) / (np.linalg.norm(omega) * np.linalg.norm(velocity))
        assert cosine == pytest.approx(0.0, abs=1e-12)

    def test_the_phase_is_the_only_thing_that_moves(self):
        basis = axis_basis((30_000.0, 2_000.0, 1_000.0))
        magnitude = math.degrees(33.0 / 1.6)

        np.testing.assert_allclose(
            omega_from_phase(basis, 180.0, magnitude),
            -omega_from_phase(basis, 0.0, magnitude),
            atol=1e-9,
        )

    def test_rejects_a_non_finite_magnitude(self):
        with pytest.raises(ValueError):
            omega_from_phase(axis_basis((1.0, 0.0, 0.0)), 0.0, float("nan"))


class TestRotationFromOmega:
    def test_a_constant_omega_turns_by_its_own_magnitude(self):
        omega = np.asarray([0.0, 0.0, 900.0])
        elapsed = 0.1

        rotation = rotation_from_omega_deg_s(omega, elapsed)

        np.testing.assert_allclose(rotation.T @ rotation, np.eye(3), atol=1e-12)
        assert float(np.linalg.det(rotation)) == pytest.approx(1.0, abs=1e-12)
        turned = math.degrees(math.acos((float(np.trace(rotation)) - 1.0) / 2.0))
        assert turned == pytest.approx(90.0, abs=1e-9)
        np.testing.assert_allclose(rotation @ np.asarray([1.0, 0.0, 0.0]), [0, 1, 0], atol=1e-12)

    def test_a_negligible_rotation_is_the_identity(self):
        np.testing.assert_allclose(rotation_from_omega_deg_s([1.0, 0.0, 0.0], 0.0), np.eye(3))

    def test_the_axis_is_untouched_by_its_own_rotation(self):
        omega = omega_from_phase(axis_basis((30_000.0, 2_000.0, 1_000.0)), 37.0, 1_180.0)
        axis = omega / np.linalg.norm(omega)

        rotation = rotation_from_omega_deg_s(omega, 0.0021)

        np.testing.assert_allclose(rotation @ axis, axis, atol=1e-12)

    def test_rejects_a_non_finite_omega(self):
        with pytest.raises(ValueError):
            rotation_from_omega_deg_s([0.0, float("nan"), 0.0], 0.002)


class TestTheRangeModelIsStated:
    """A straight-line range is a MODEL, and a caller has to be able to see it.

    `fit_sequence` takes `range_mm_by_frame` and calls it a measurement. It is
    a measurement of the range RATE walked out under an assumption about the
    rate's own constancy, and on a clubhead that assumption is wrong by tens of
    millimetres over a capture (`test_iwr6843_club_quadratic_range`). Nothing
    downstream could previously tell which model it had been handed.
    """

    def test_a_rate_alone_reports_a_linear_walk(self):
        ranges = ranges_from_radar([0.0, 0.002, 0.004], 0.004, 33.0)

        assert ranges.model == "linear"
        assert ranges.range_accel_ms2 == pytest.approx(0.0)

    def test_an_acceleration_reports_a_quadratic_walk_and_curves(self):
        times = np.asarray([0.0, 0.002, 0.004])
        straight = ranges_from_radar(times, 0.004, 33.0).ranges_mm
        curved = ranges_from_radar(times, 0.004, 33.0, range_accel_ms2=-1500.0)

        assert curved.model == "quadratic"
        assert curved.ranges_mm[-1] == pytest.approx(CAMERA_BALL_RANGE_MM, abs=1e-9)
        assert float(np.abs(curved.ranges_mm - straight).max()) > 2.0

    def test_a_zero_acceleration_reproduces_the_linear_walk_exactly(self):
        times = np.asarray([0.0, 0.002, 0.004])

        np.testing.assert_allclose(
            ranges_from_radar(times, 0.004, 33.0, range_accel_ms2=0.0).ranges_mm,
            ranges_from_radar(times, 0.004, 33.0).ranges_mm,
            atol=1e-12,
        )

    def test_a_track_without_a_quadratic_refit_says_linear(self):
        from openflight.camera.clubpose.fusion import ranges_from_track

        track = types.SimpleNamespace(speed_ms=33.0, quad_bins=None)
        ranges = ranges_from_track(track, [0.0, 0.002, 0.004], 0.004, 0.0469)

        assert ranges.model == "linear"
        np.testing.assert_allclose(
            ranges.ranges_mm,
            ranges_from_radar([0.0, 0.002, 0.004], 0.004, 33.0).ranges_mm,
            atol=1e-9,
        )

    def test_a_track_with_a_quadratic_refit_says_quadratic_and_uses_it(self):
        from openflight.camera.clubpose.fusion import ranges_from_track

        res = 0.0469
        # bins(t) = q2 t^2 + q1 t + q0, chosen so the rate at impact is 33 m/s
        # and the radial acceleration is -1500 m/s^2.
        q2 = -1500.0 / (2.0 * res)
        q1 = (33.0 - (-1500.0) * 0.004) / res
        track = types.SimpleNamespace(speed_ms=33.0, quad_bins=(q2, q1, 100.0))
        times = np.asarray([0.0, 0.002, 0.004])
        ranges = ranges_from_track(track, times, 0.004, res)

        assert ranges.model == "quadratic"
        assert ranges.ranges_mm[-1] == pytest.approx(CAMERA_BALL_RANGE_MM, abs=1e-9)
        assert ranges.range_accel_ms2 == pytest.approx(-1500.0)
        assert ranges.range_rate_ms == pytest.approx(33.0)

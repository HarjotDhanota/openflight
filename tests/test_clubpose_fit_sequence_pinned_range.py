"""Depth is a measurement, not a fitted parameter.

`fit_sequence` searched a static three-point range grid and then hill-climbed
off it, which is the right thing to do when nothing else knows the depth. The
radar does know it: `fusion.ranges_from_radar` gives a range per frame, anchored
at the taped ball range at impact. When that is supplied the fit must USE it --
pin it, ignore the grid, refine nothing in depth -- and it must not charge the
smoothness penalty for range motion the radar measured.
"""

from __future__ import annotations

import math
import types

import numpy as np
import pytest

from openflight.camera.clubpose import fit

# This file is about DEPTH. Orientation is pinned to the single pose the fitter
# is allowed to reach -- the grounded one -- because `pose_in_bounds` scores
# anything outside the box -1.0 and a singleton grid at the backwards triad
# origin would leave every frame unfitted for a reason that is not depth.
_YAW, _PITCH, _ROLL = fit.GROUNDED_POSE_DEG


def _stub_camera() -> types.SimpleNamespace:
    """A camera that carries only what `fit_sequence` asks a camera for."""
    return types.SimpleNamespace(center_world=np.zeros(3))


def _stub_renderer(monkeypatch) -> None:
    """Make the rendered 'mask' carry the range, and IoU read it back."""
    monkeypatch.setattr(fit, "_ray_world", lambda _uv, _camera: np.asarray([1.0, 0.0, 0.0]))
    monkeypatch.setattr(
        fit,
        "render_mask_6dof",
        lambda _mesh, centre, _yaw, _pitch, _roll, _camera: np.asarray([[centre[0]]]),
    )
    monkeypatch.setattr(
        fit,
        "iou",
        lambda rendered, _observed: 1.0 - abs(float(rendered[0, 0]) - 1531.0) / 1000.0,
    )


def _masks(*frames: int) -> dict[int, np.ndarray]:
    return {frame: np.ones((10, 10), dtype=np.uint8) for frame in frames}


def test_fit_sequence_can_keep_a_singleton_range_hard_pinned(monkeypatch):
    _stub_renderer(monkeypatch)

    result = fit.fit_sequence(
        object(),
        _masks(0),
        _stub_camera(),
        range_grid_mm=(1581.0,),
        yaw_grid=(_YAW,),
        pitch_grid=(_PITCH,),
        roll_grid=(_ROLL,),
        refine_range=False,
    )

    assert result[0]["range_mm"] == 1581.0


class TestPerFrameRadarRange:
    RANGES = {0: 1500.0, 1: 1550.5, 2: 1600.25}

    def _fit(self, monkeypatch, **kwargs):
        _stub_renderer(monkeypatch)
        return fit.fit_sequence(
            object(),
            _masks(*self.RANGES),
            _stub_camera(),
            yaw_grid=(_YAW,),
            pitch_grid=(_PITCH,),
            roll_grid=(_ROLL,),
            **kwargs,
        )

    def test_each_frame_lands_exactly_on_its_measured_range(self, monkeypatch):
        """Exactly: a refined range would land near these, not on them."""
        result = self._fit(monkeypatch, range_mm_by_frame=self.RANGES)

        for frame, range_mm in self.RANGES.items():
            assert result[frame]["range_mm"] == range_mm

    def test_the_static_grid_is_ignored_when_a_measurement_is_supplied(self, monkeypatch):
        result = self._fit(
            monkeypatch, range_mm_by_frame=self.RANGES, range_grid_mm=(999.0, 1001.0)
        )

        for frame, range_mm in self.RANGES.items():
            assert result[frame]["range_mm"] == range_mm

    def test_local_refinement_cannot_climb_off_a_measured_range(self, monkeypatch):
        """`refine_range=True` is the default and must not override the radar."""
        result = self._fit(monkeypatch, range_mm_by_frame=self.RANGES, refine_range=True)

        for frame, range_mm in self.RANGES.items():
            assert result[frame]["range_mm"] == range_mm

    def test_without_a_measurement_the_old_behaviour_stands(self, monkeypatch):
        result = self._fit(monkeypatch, range_grid_mm=(1481.0, 1581.0, 1681.0))

        assert set(result) == set(self.RANGES)
        assert any(record["range_mm"] not in self.RANGES.values() for record in result.values())

    def test_rejects_a_frame_with_no_measured_range(self, monkeypatch):
        _stub_renderer(monkeypatch)
        with pytest.raises(ValueError):
            fit.fit_sequence(
                object(),
                _masks(0, 1, 2),
                _stub_camera(),
                range_mm_by_frame={0: 1500.0, 1: 1550.0},
                yaw_grid=(_YAW,),
                pitch_grid=(_PITCH,),
                roll_grid=(_ROLL,),
            )

    @pytest.mark.parametrize("bad", (float("nan"), 0.0, -1.0))
    def test_rejects_a_range_that_is_not_a_distance(self, monkeypatch, bad):
        _stub_renderer(monkeypatch)
        with pytest.raises(ValueError):
            fit.fit_sequence(
                object(),
                _masks(0),
                _stub_camera(),
                range_mm_by_frame={0: bad},
                yaw_grid=(_YAW,),
                pitch_grid=(_PITCH,),
                roll_grid=(_ROLL,),
            )


class TestSmoothnessPenalty:
    """The penalty exists to stop the fit inventing motion between frames.

    Radar-measured range motion is not invented, so charging for it would push
    every frame back toward its neighbour's depth and undo the measurement.
    """

    PREV = {"range_mm": 1500.0, "yaw_deg": 3.0, "pitch_deg": -4.0, "roll_deg": 10.0}

    def test_a_pinned_range_costs_nothing(self):
        penalty = fit._smoothness_penalty(
            self.PREV, 1600.0, 3.0, -4.0, 10.0, 70.0, 300.0, penalise_range=False
        )

        assert penalty == pytest.approx(0.0)

    def test_a_searched_range_still_costs_what_it_did(self):
        penalty = fit._smoothness_penalty(
            self.PREV, 1600.0, 3.0, -4.0, 10.0, 70.0, 300.0, penalise_range=True
        )

        assert penalty == pytest.approx(100.0 / 300.0)

    def test_orientation_is_charged_either_way(self):
        for penalise_range in (True, False):
            penalty = fit._smoothness_penalty(
                self.PREV, 1500.0, 33.0, -4.0, 10.0, 70.0, 300.0, penalise_range=penalise_range
            )
            assert penalty == pytest.approx(30.0 / (3.0 * 70.0))

    def test_the_first_frame_has_nothing_to_be_smooth_against(self):
        assert fit._smoothness_penalty(
            None, 1600.0, 3.0, -4.0, 10.0, 70.0, 300.0, penalise_range=True
        ) == pytest.approx(0.0)

    def test_the_penalty_is_never_negative(self):
        assert (
            fit._smoothness_penalty(
                self.PREV, 1400.0, -20.0, 40.0, -30.0, 70.0, 300.0, penalise_range=True
            )
            > 0.0
        )
        assert math.isfinite(
            fit._smoothness_penalty(
                self.PREV, 1400.0, -20.0, 40.0, -30.0, 70.0, 300.0, penalise_range=True
            )
        )

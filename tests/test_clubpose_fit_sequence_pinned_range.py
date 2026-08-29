from __future__ import annotations

import types

import numpy as np

from openflight.camera.clubpose import fit


def _stub_camera() -> types.SimpleNamespace:
    """A camera that carries only what `fit_sequence` asks a camera for."""
    return types.SimpleNamespace(center_world=np.zeros(3))


def test_fit_sequence_can_keep_a_singleton_range_hard_pinned(monkeypatch):
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

    result = fit.fit_sequence(
        object(),
        {0: np.ones((10, 10), dtype=np.uint8)},
        _stub_camera(),
        range_grid_mm=(1581.0,),
        yaw_grid=(0.0,),
        pitch_grid=(0.0,),
        roll_grid=(0.0,),
        refine_range=False,
    )

    assert result[0]["range_mm"] == 1581.0

"""The ball search gives the same answer in a worker process as in the tester process."""

import numpy as np
import pytest

from openflight.camera import tester_server as ts
from openflight.camera.reference_ball_range import BallPlaneCamera


def _scene():
    camera = BallPlaneCamera.nominal(
        focal_px=466.6667,
        image_width_px=640,
        image_height_px=400,
        pitch_deg=0.0,
        roll_correction_deg=0.0,
        mirror_horizontal=False,
        camera_origin_lfu=(0.0, 0.0, 0.095),
        radar_origin_lfu=(0.0, -0.03, 0.051),
        angular_uncertainty_deg=1.0,
        focal_relative_uncertainty=0.08,
    )
    rng = np.random.default_rng(1)
    yy, xx = np.indices((400, 640), dtype=float)
    image = 55.0 + rng.normal(0.0, 1.0, (400, 640))
    image[np.hypot(xx - 322.0, yy - 229.0) <= 8.0] = 150.0
    frames = np.repeat(np.clip(image, 0, 255).astype(np.uint8)[None], 5, axis=0)
    return frames, camera


def test_the_worker_process_finds_the_same_ball():
    frames, camera = _scene()
    kwargs = {"ball_center_height_m": 0.021335, "plausible_radar_range_m": (0.6, 3.5)}
    local = ts._run_ball_search(frames, camera, kwargs)
    ts.configure_ball_search_workers(1)
    try:
        remote = ts._run_ball_search(frames, camera, kwargs)
    finally:
        ts.configure_ball_search_workers(0)

    assert remote.status == local.status
    assert remote.selected is not None
    assert remote.selected.x_px == pytest.approx(local.selected.x_px)
    assert remote.selected.y_px == pytest.approx(local.selected.y_px)

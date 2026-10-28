"""The batched finite-difference Jacobian gives the same lit-ball fits as scipy's."""

import numpy as np
import pytest

from openflight.camera import ball_model


def _lit_ball_image(cx, cy, radius, *, seed=3):
    rng = np.random.default_rng(seed)
    yy, xx = np.indices((96, 128), dtype=float)
    params = np.array(
        [cx, cy, radius, np.radians(250.0), np.radians(55.0), 60.0, 120.0, 70.0, 0.0, 0.0]
    )
    image = ball_model._render(params, yy, xx) + rng.normal(0.0, 1.0, yy.shape)
    return np.clip(image, 0, 255).astype(np.float32)


@pytest.mark.parametrize(
    ("cx", "cy", "radius", "held"),
    [(64.3, 47.8, 9.0, None), (60.0, 50.0, 14.0, 14.0), (70.5, 44.2, 6.5, None)],
)
def test_batched_and_scipy_jacobians_find_the_same_ball(monkeypatch, cx, cy, radius, held):
    image = _lit_ball_image(cx, cy, radius)
    fits = {}
    for batched in (False, True):
        monkeypatch.setattr(ball_model, "BATCHED_JACOBIAN", batched)
        fits[batched] = ball_model.fit_lit_ball(
            image, cx + 1.0, cy - 1.0, radius, noise_dn=1.0, expected_radius=held
        )

    assert fits[True] is not None and fits[False] is not None
    assert fits[True].x == pytest.approx(fits[False].x, abs=0.02)
    assert fits[True].y == pytest.approx(fits[False].y, abs=0.02)
    assert fits[True].radius_px == pytest.approx(fits[False].radius_px, abs=0.02)
    assert fits[True].x == pytest.approx(cx, abs=0.5)


def test_the_batched_render_matches_the_single_render():
    yy, xx = np.indices((40, 50), dtype=float)
    params = np.array(
        [
            [25.0, 20.0, 8.0, 4.4, 1.0, 50.0, 100.0, 70.0, 0.2, -0.1],
            [24.0, 19.0, 7.0, 4.0, 0.9, 40.0, 90.0, 60.0, 0.0, 0.0],
        ]
    )

    batch = ball_model._render_batch(params, yy, xx)

    for index in range(2):
        np.testing.assert_allclose(
            batch[index], ball_model._render(params[index], yy, xx), atol=1e-9
        )


def test_batched_derivatives_preserve_scipys_column_layout():
    yy, xx = np.indices((40, 50), dtype=float)
    params = np.array([25.1, 20.2, 8.3, 4.4, 1.0, 50.0, 100.0, 70.0, 0.2, -0.1])
    use = np.ones_like(xx, dtype=bool)
    lower, upper = np.full(10, -1000.0), np.full(10, 1000.0)
    noise = 2.3
    actual = ball_model._batched_jacobian(yy, xx, xx, use, noise, lower, upper)(params)
    assert actual.flags.f_contiguous
    expected = np.empty_like(actual)
    base = (ball_model._render(params, yy, xx) - xx) / noise
    for index in range(len(params)):
        shifted = params.copy()
        shifted[index] += (
            np.sqrt(np.finfo(float).eps)
            * max(1.0, abs(params[index]))
            * (1.0 if params[index] >= 0.0 else -1.0)
        )
        expected[:, index] = (
            ((ball_model._render(shifted, yy, xx) - xx) / noise - base)
            / (shifted[index] - params[index])
        ).ravel()
    np.testing.assert_array_equal(actual, expected)

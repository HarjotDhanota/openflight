"""Direct image derivatives preserve the sphere model and nonsmooth fallback."""

import numpy as np
import pytest

from openflight.camera import ball_model as model


@pytest.mark.parametrize("diffuse", [90.0, 260.0])
def test_direct_derivative_matches_numerical_image_changes(diffuse):
    params = np.array([21.37, 19.23, 8.71, 4.1, 1.0, 45, diffuse, 65, 0.13, -0.07])
    yy, xx = np.indices((45, 49), dtype=float)
    picture = model._render(params, yy, xx)
    use = (xx + yy) % 3 != 0
    lower, upper = np.full(10, -np.inf), np.full(10, np.inf)

    direct = model._analytic_jacobian(yy, xx, picture, use, 1.7, lower, upper)(params)
    numerical = model._batched_jacobian(yy, xx, picture, use, 1.7, lower, upper)(params)

    np.testing.assert_allclose(direct, numerical, rtol=2e-4, atol=2e-5)
    assert direct.flags.f_contiguous


def test_square_root_boundary_keeps_the_existing_one_sided_difference():
    params = np.array([21, 19, 8, 4.1, 1, 45, 90, 65, 0.13, -0.07])
    yy, xx = np.indices((45, 49), dtype=float)
    picture = model._render(params, yy, xx)
    use = np.ones_like(picture, dtype=bool)
    lower, upper = np.full(10, -np.inf), np.full(10, np.inf)

    direct = model._analytic_jacobian(yy, xx, picture, use, 1, lower, upper)(params)
    numerical = model._batched_jacobian(yy, xx, picture, use, 1, lower, upper)(params)

    np.testing.assert_array_equal(direct, numerical)


def test_fast_solver_cannot_supply_the_published_geometry(monkeypatch):
    fast = model.LitBall(40, 40, 8, 270, 60, 80, 40, 40, 2)
    reference = model.LitBall(41, 42, 9, 270, 60, 80, 40, 40, 2)
    calls = []

    def fitted(*_args, **options):
        calls.append(options)
        return reference if options.get("reference_jacobian") else fast

    monkeypatch.setattr(model, "_fit_sphere", fitted)
    monkeypatch.setattr(model, "measure_rim", lambda _image, fit, **_kwargs: fit)
    assert model.fit_lit_ball(np.ones((80, 80)), 40, 40, 8) == reference
    assert len(calls) == 2


def test_fast_false_positive_stays_withheld_when_reference_solver_refuses(monkeypatch):
    fast = model.LitBall(40, 40, 8, 270, 60, 80, 40, 40, 2)

    def fitted(*_args, **options):
        return None if options.get("reference_jacobian") else fast

    monkeypatch.setattr(model, "_fit_sphere", fitted)
    assert model.fit_lit_ball(np.ones((80, 80)), 40, 40, 8) is None

"""Direct image derivatives preserve the sphere model and nonsmooth fallback."""

from types import SimpleNamespace

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


@pytest.mark.parametrize(
    "options,size_count",
    [
        ({}, 1),
        ({"reference_jacobian": True}, 3),
        ({"check_other_starts": True}, 3),
        ({"size_starts": (0.85, 1.0, 1.2)}, 3),
        ({"expected_radius": 8.0}, 1),
        ({"batched": False}, 3),
    ],
)
def test_screen_avoids_duplicate_radius_starts_but_keeps_lights_and_reference_starts(
    monkeypatch, options, size_count
):
    yy, xx = np.indices((80, 80), dtype=float)
    params = np.array([40, 40, 8, np.radians(270), np.radians(60), 60, 90, 60, 0, 0])
    image = model._render(params, yy, xx)
    starts = []

    def solve(_residuals, initial, **_kwargs):
        starts.append(initial.copy())
        return SimpleNamespace(x=initial, cost=float(len(starts)))

    monkeypatch.setattr(model.optimize, "least_squares", solve)
    options = dict(options)
    monkeypatch.setattr(model, "BATCHED_JACOBIAN", options.pop("batched", True))
    defaults = dict(
        noise_dn=1,
        expected_radius=None,
        size_tolerance=0.01,
        size_starts=None,
        extra_light_starts=True,
    )
    model._fit_sphere(image, 40, 40, 8, **{**defaults, **options})
    assert len({round(float(initial[2]), 5) for initial in starts}) == size_count
    assert len({round(float(initial[3]), 5) for initial in starts}) == 3
    assert len(starts) == 3 * size_count

"""The address preview's compiled residual against its double-precision reference."""

from __future__ import annotations

import os
import shutil

import numpy as np
import pytest

from openflight.camera.address_preview import native

NATIVE = native.availability()
needs_native = pytest.mark.skipif(
    not NATIVE["available"],
    reason=f"no compiled residual on this machine: {NATIVE['reason']}",
)


def _case(case):
    rng = np.random.default_rng(case + 23029)
    parameters = rng.uniform(
        [32, 32, 8, np.radians(170), 0.05], [88, 88, 45, np.radians(370), 1.52]
    )
    back = rng.uniform(0, 255, (60, 60))
    yy, xx = np.mgrid[:60, :60] * 2 + 0.5
    use = np.hypot(xx - 60, yy - 60) < 51
    observed = rng.uniform(0, 255, int(use.sum()))
    scale = rng.uniform(4, 40)
    return parameters, back, use, observed, scale


@needs_native
@pytest.mark.parametrize("case", range(20))
def test_native_geometry_and_robust_residual_match_double_precision_reference(case):
    parameters, back, use, observed, scale = _case(case)
    expected_geometry = native.sphere_geometry(parameters, back)
    actual = [np.empty((60, 60)) for _ in range(3)]
    native.library().sphere_geometry(parameters, back, *actual)
    for expected, computed in zip(expected_geometry, actual):
        np.testing.assert_allclose(computed, expected, rtol=0, atol=1e-10)
    expected = native.conditional_residual(expected_geometry, observed, use, scale)
    result = native.Residual(back, observed, use, scale)(parameters, True)
    np.testing.assert_allclose(result[0], expected[0], rtol=0, atol=1e-7)
    np.testing.assert_allclose(result[1:], expected[1:], rtol=0, atol=1e-7)


def test_native_wrapper_refuses_incorrect_buffers_before_call():
    with pytest.raises(ValueError):
        native.Residual(np.zeros((30, 30)), np.zeros(1), np.ones((30, 30), bool), 4)
    with pytest.raises(ValueError):
        native.Residual(np.zeros((60, 60)), np.zeros(1), np.ones((60, 60), bool), 4)
    residual = native.Residual(np.zeros((60, 60)), np.zeros(3600), np.ones((60, 60), bool), 4)
    with pytest.raises(ValueError):
        residual(np.ones(1))


def test_the_reference_residual_is_the_numpy_formula():
    parameters, back, use, observed, scale = _case(3)
    residual, ambient, diffuse = native.ReferenceResidual(back, observed, use, scale)(
        parameters, True
    )
    expected = native.conditional_residual(
        native.sphere_geometry(parameters, back), observed, use, scale
    )
    np.testing.assert_array_equal(residual, expected[0])
    assert (ambient, diffuse) == (float(expected[1]), float(expected[2]))


def test_a_missing_compiler_is_reported_not_raised_as_something_else(tmp_path):
    with pytest.raises(native.NativeUnavailable, match="no C compiler"):
        native.build(cache_dir=tmp_path, command=["openflight-no-such-compiler"])
    assert not list(tmp_path.iterdir())


def test_a_failed_compile_leaves_no_partial_library(tmp_path):
    broken = tmp_path / "broken.c"
    broken.write_text("this is not C\n", encoding="utf-8")
    command = native.compiler()
    if shutil.which(command[0]) is None:
        pytest.skip(f"no C compiler on this machine: {command[0]} was not found")
    with pytest.raises(native.NativeUnavailable, match="compiling broken.c failed"):
        native.build(broken, cache_dir=tmp_path / "cache", command=command)
    assert not list((tmp_path / "cache").iterdir())


def test_the_library_is_cached_by_the_source_hash(tmp_path):
    source = tmp_path / "residual.c"
    source.write_bytes(native.SOURCE.read_bytes())
    path = native.library_path(source, tmp_path)
    assert native.library_path(native.SOURCE, tmp_path) == path
    source.write_bytes(native.SOURCE.read_bytes() + b"\n")
    assert native.library_path(source, tmp_path) != path
    assert path.name.endswith(".dll" if os.name == "nt" else ".so")


def test_an_existing_cached_library_is_used_without_compiling(tmp_path):
    target = native.library_path(native.SOURCE, tmp_path)
    target.write_bytes(b"cached")
    assert native.build(cache_dir=tmp_path, command=["openflight-no-such-compiler"]) == target


def test_a_build_failure_is_remembered_and_reported(monkeypatch):
    monkeypatch.setattr(native, "_LOADED", {"library": None, "error": None})
    calls = []

    def failing_build():
        calls.append(True)
        raise native.NativeUnavailable("no C compiler: cc was not found")

    monkeypatch.setattr(native, "build", failing_build)
    assert native.availability() == {
        "available": False,
        "reason": "no C compiler: cc was not found",
    }
    assert native.availability()["available"] is False
    assert len(calls) == 1

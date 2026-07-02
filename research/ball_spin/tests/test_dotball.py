import numpy as np


def test_dot_pattern_returns_unit_vectors_and_radius_constant():
    from ball_spin.dotball import BALL_RADIUS_MM, dot_pattern

    dots = dot_pattern(27, seed=7)

    assert BALL_RADIUS_MM == 21.35
    assert dots.shape == (27, 3)
    assert np.allclose(np.linalg.norm(dots, axis=1), 1.0)


def test_dot_pattern_is_reproducible_and_asymmetric():
    from ball_spin.dotball import dot_pattern

    a = dot_pattern(40, seed=11)
    b = dot_pattern(40, seed=11)
    c = dot_pattern(40, seed=12)

    assert np.allclose(a, b)
    assert not np.allclose(a, c)
    assert np.unique(np.round(a, decimals=5), axis=0).shape[0] == 40

    abs_dots = np.round(np.abs(a), decimals=4)
    assert np.unique(abs_dots, axis=0).shape[0] == 40


def test_dot_pattern_rejects_too_few_dots():
    from ball_spin.dotball import dot_pattern

    try:
        dot_pattern(4, seed=1)
    except ValueError as exc:
        assert "at least 5" in str(exc)
    else:
        raise AssertionError("dot_pattern should reject fewer than 5 dots")

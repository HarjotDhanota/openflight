import numpy as np
import pytest

from club_pose.sensitivity import (
    depth_error_to_impact_mm,
    error_budget,
    loft_error_to_loft_deg,
    rotation_error_to_face_deg,
)
from club_pose.template import default_template


def test_zero_perturbation_zero_error():
    t = default_template("driver")
    assert loft_error_to_loft_deg(t, [0.0])[0][1] == pytest.approx(0.0, abs=1e-9)
    assert rotation_error_to_face_deg(t, [0.0])[0][1] == pytest.approx(0.0, abs=1e-9)


def test_template_loft_error_is_one_to_one():
    t = default_template("driver")
    out = dict(loft_error_to_loft_deg(t, [1.0, 2.0, 3.0]))
    assert out[2.0] == pytest.approx(2.0, abs=1e-6)


def test_rotation_error_monotonic():
    t = default_template("driver")
    out = rotation_error_to_face_deg(t, [0.0, 1.0, 2.0])
    errs = [e for _, e in out]
    assert errs[0] < errs[1] < errs[2]


def test_error_budget_has_two_tiers():
    b = error_budget(default_template("driver"))
    assert "single_camera" in b and "stereo" in b

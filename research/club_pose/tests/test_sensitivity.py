import numpy as np
import pytest

from club_pose.sensitivity import (
    DEPTH_AXIS,
    LATERAL_AXIS,
    error_budget,
    loft_error_to_loft_deg,
    rotation_error_to_face_deg,
    rotation_error_to_impact_mm,
    translation_error_to_impact_mm,
)
from club_pose.template import default_template


def test_zero_perturbation_zero_error():
    t = default_template("driver")
    assert loft_error_to_loft_deg(t, [0.0])[0][1] == pytest.approx(0.0, abs=1e-9)
    assert rotation_error_to_face_deg(t, [0.0])[0][1] == pytest.approx(0.0, abs=1e-9)
    _, du, dv = translation_error_to_impact_mm(t, 10.0, -5.0, LATERAL_AXIS, [0.0])[0]
    assert du == pytest.approx(0.0, abs=1e-9) and dv == pytest.approx(0.0, abs=1e-9)


def test_template_loft_error_is_one_to_one():
    t = default_template("driver")
    out = {e: x for e, x in loft_error_to_loft_deg(t, [1.0, 2.0, 3.0])}
    assert out[2.0] == pytest.approx(2.0, abs=1e-6)


def test_rotation_error_face_monotonic():
    t = default_template("driver")
    errs = [x for _, x in rotation_error_to_face_deg(t, [0.0, 1.0, 2.0])]
    assert errs[0] < errs[1] < errs[2]


def test_inplane_translation_is_one_to_one_offset():
    # flat iron -> exact: lateral head move maps 1:1 to offset, leaves height unchanged
    t = default_template("iron")
    _, du, dv = translation_error_to_impact_mm(t, 10.0, -5.0, LATERAL_AXIS, [5.0])[0]
    assert du == pytest.approx(5.0, abs=1e-6)
    assert dv == pytest.approx(0.0, abs=1e-6)


def test_depth_translation_is_finite_and_sub_unity():
    # The bug this guards: depth error used to return nan via the contact gate.
    t = default_template("iron")
    for e, du, dv in translation_error_to_impact_mm(t, 10.0, -5.0, DEPTH_AXIS, [5.0, 10.0, 20.0]):
        mag = float(np.hypot(du, dv))
        assert np.isfinite(mag)  # no nan (the regression this guards)
        # pure-3D depth couples to impact HEIGHT via sin(2*loft) (~0.93 for a 34 deg iron);
        # still <= e here. The perspective/scale amplification is a Stage 0B effect.
        assert mag <= e


def test_rotation_impact_monotonic_and_bounded():
    t = default_template("iron")
    rows = rotation_error_to_impact_mm(t, 20.0, 0.0, [0.0, 1.0, 2.0])
    offs = [du for _, du, _ in rows]
    assert offs[0] == pytest.approx(0.0, abs=1e-9)
    assert offs[0] < offs[1] < offs[2]
    assert offs[1] < 3.0  # ~lever-arm scale (order ~1 mm/deg)


def test_error_budget_reports_coefficients():
    b = error_budget(default_template("driver"))
    assert b["deg_face_per_deg_yaw"] == pytest.approx(1.0, abs=1e-3)
    assert b["deg_loft_per_deg_template_loft"] == pytest.approx(1.0, abs=1e-3)
    # curvature foreshortens the curved driver's in-plane coefficient (~0.92); a flat face = 1.0
    assert 0.8 <= b["mm_offset_per_mm_inplane_translation"] <= 1.0
    assert "Stage-0B" in b["note"]

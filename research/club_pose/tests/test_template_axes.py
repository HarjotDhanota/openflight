import numpy as np
import pytest

from club_pose.template import ClubTemplate


def _flat_template(loft):
    return ClubTemplate(
        category="iron",
        static_loft_deg=loft,
        face_width_mm=80.0,
        face_height_mm=55.0,
        bulge_radius_mm=None,
        roll_radius_mm=None,
        face_center_offset=np.array([20.0, 0.0, 0.0]),
    )


def test_zero_loft_normal_is_downrange():
    _, _, w = _flat_template(0.0).face_axes()
    np.testing.assert_allclose(w, [1, 0, 0], atol=1e-9)


def test_positive_loft_tilts_normal_up():
    _, _, w = _flat_template(10.0).face_axes()
    # +X rotated up by +10 deg -> (cos10, 0, sin10)
    np.testing.assert_allclose(w, [np.cos(np.radians(10)), 0, np.sin(np.radians(10))], atol=1e-9)


def test_face_axes_orthonormal_right_handed():
    u, v, w = _flat_template(15.0).face_axes()
    np.testing.assert_allclose(np.cross(u, v), w, atol=1e-9)
    for a in (u, v, w):
        assert np.linalg.norm(a) == pytest.approx(1.0)


def test_loft_override_changes_loft():
    t = _flat_template(10.0).with_loft_override(20.0)
    _, _, w = t.face_axes()
    np.testing.assert_allclose(w, [np.cos(np.radians(20)), 0, np.sin(np.radians(20))], atol=1e-9)


def test_invalid_dims_raise():
    with pytest.raises(ValueError):
        ClubTemplate("iron", 30.0, -1.0, 55.0, None, None, np.zeros(3))

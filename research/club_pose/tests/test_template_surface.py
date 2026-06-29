import numpy as np
import pytest

from club_pose.template import ClubTemplate


def _driver():
    return ClubTemplate("driver", 10.0, 117.0, 57.0, 254.0, 254.0, np.array([20.0, 0.0, 0.0]))


def test_flat_face_height_is_zero():
    t = ClubTemplate("iron", 0.0, 80.0, 55.0, None, None, np.zeros(3))
    assert t.surface_height_face(30.0, 20.0) == pytest.approx(0.0)


def test_convex_edges_recede_inward():
    t = _driver()
    assert t.surface_height_face(40.0, 0.0) < 0  # toe edge behind center (h<0)
    assert t.surface_height_face(0.0, 0.0) == pytest.approx(0.0)


def test_normal_tilts_toward_toe_edge():
    t = _driver()
    n = t.surface_normal_face(40.0, 0.0)
    assert n[0] > 0  # +u component (toward toe)
    assert n[2] > 0  # still mostly outward (+w)


def test_face_to_body_roundtrip_at_zero_loft():
    t = ClubTemplate("iron", 0.0, 80.0, 55.0, None, None, np.array([20.0, 0.0, 0.0]))
    # at zero loft, face axes = body axes; (u,v,w)=(2,3,5) -> body offset (5,2,3)
    np.testing.assert_allclose(t.face_to_body_vec([2, 3, 5]), [5, 2, 3], atol=1e-9)
    p_body = t.face_center_offset + np.array([5, 2, 3])
    np.testing.assert_allclose(t.to_face_coords(p_body), [2, 3, 5], atol=1e-9)

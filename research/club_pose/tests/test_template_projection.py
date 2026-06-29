import numpy as np
import pytest

from club_pose.template import ClubTemplate, default_template
from club_pose.types import BALL_RADIUS_MM


def _driver():
    return default_template("driver")


def test_flat_projection_is_orthogonal():
    t = ClubTemplate("iron", 0.0, 80.0, 55.0, None, None, np.array([20.0, 0.0, 0.0]))
    # point 10mm out along +X (=+w at zero loft) above face point (u=12, v=-7)
    p = t.face_center_offset + np.array([10.0, 12.0, -7.0])  # body (w,u,v)->(x,y,z) at zero loft
    proj = t.point_to_face_uv(p)
    assert proj.u == pytest.approx(12.0, abs=1e-6)
    assert proj.v == pytest.approx(-7.0, abs=1e-6)
    assert proj.signed_distance_mm == pytest.approx(10.0, abs=1e-6)
    assert proj.in_patch


def test_curved_projection_recovers_known_impact():
    t = _driver()
    u0, v0 = 15.0, -8.0
    surf_body = t.face_center_offset + t.face_to_body_vec([u0, v0, t.surface_height_face(u0, v0)])
    n_body = t.face_to_body_vec(t.surface_normal_face(u0, v0))
    ball = surf_body + BALL_RADIUS_MM * n_body
    proj = t.point_to_face_uv(ball)
    assert proj.u == pytest.approx(u0, abs=1e-4)
    assert proj.v == pytest.approx(v0, abs=1e-4)
    assert proj.signed_distance_mm == pytest.approx(BALL_RADIUS_MM, abs=1e-4)
    assert proj.in_patch


def test_off_face_point_flagged():
    t = _driver()
    far = t.face_center_offset + t.face_to_body_vec([200.0, 0.0, BALL_RADIUS_MM])  # way past toe
    proj = t.point_to_face_uv(far)
    assert not proj.in_patch


def test_default_templates_exist():
    assert default_template("driver").bulge_radius_mm is not None
    assert default_template("iron").bulge_radius_mm is None

"""Synthetic tests for translation-only contact-outline alignment."""

from __future__ import annotations

import math

import cv2
import numpy as np
import pytest

from openflight.camera.club_motion import ReferenceBall
from openflight.camera.clubpose.angles import angles_from_pose, in_envelope
from openflight.camera.clubpose.fit import measured_camera
from openflight.camera.clubpose.mesh import TriangleMesh
from openflight.camera.clubpose.outline_align import (
    ShaftLine,
    align_outline,
    alignment_gate_reason,
    build_nominal_outline,
    linear_motion_carry,
    nonsole_boundary_mask,
    polarity_consistent_edges,
)

BALL = ReferenceBall(x=160.0, y=120.0, diameter_px=12.0, area_px=113)
RANGE_MM = 1_581.0
MAT_LEVEL = 150


def _box_mesh(scale: float = 1.0) -> TriangleMesh:
    depth, width, height = 16.0 * scale, 80.0 * scale, 42.0 * scale
    vertices = np.asarray(
        [
            [x, y, z]
            for x in (-depth / 2, depth / 2)
            for y in (-width / 2, width / 2)
            for z in (-height / 2, height / 2)
        ],
        dtype=float,
    )
    faces = np.asarray(
        [
            [0, 1, 3],
            [0, 3, 2],
            [4, 6, 7],
            [4, 7, 5],
            [0, 4, 5],
            [0, 5, 1],
            [2, 3, 7],
            [2, 7, 6],
            [0, 2, 6],
            [0, 6, 4],
            [1, 5, 7],
            [1, 7, 3],
        ],
        dtype=np.int32,
    )
    return TriangleMesh(vertices, faces, "synthetic-box", "synthetic")


def _shifted_frame(dx: float, dy: float):
    camera = measured_camera()
    mesh = _box_mesh()
    template, reason = build_nominal_outline(
        BALL,
        mesh,
        camera,
        RANGE_MM,
        shaft_angle_deg=None,
        loft_deg=33.1,
    )
    assert reason == "ok"
    assert template is not None
    alpha = cv2.warpAffine(
        template.mask.astype(np.float32),
        np.asarray([[1.0, 0.0, dx], [0.0, 1.0, dy]], dtype=np.float32),
        (camera.width, camera.height),
        flags=cv2.INTER_LINEAR,
    )
    frame = np.rint(MAT_LEVEL + (70 - MAT_LEVEL) * alpha).astype(np.uint8)

    shifted = alpha >= 0.5
    ys, xs = np.nonzero(shifted)
    shadow_top = int(ys.max()) + 2
    frame[shadow_top : shadow_top + 3, int(xs.min()) : int(xs.max()) + 1] = 80

    angle = math.radians(template.shaft_angle_deg)
    direction = np.asarray([math.cos(angle), math.sin(angle)])
    junction = np.asarray([template.heel_x + dx, template.topline_row_at_ball + dy], dtype=float)
    upper = junction - direction * 52.0
    cv2.line(
        frame,
        tuple(np.rint(upper).astype(int)),
        tuple(np.rint(junction).astype(int)),
        240,
        3,
    )
    shaft = ShaftLine(
        center_xy=(float(junction[0]), float(junction[1])),
        direction_xy=(float(direction[0]), float(direction[1])),
        angle_deg=template.shaft_angle_deg,
        status="ok",
        reason="ok",
    )
    yy, xx = np.indices(frame.shape)
    ball_cap = (xx - BALL.x) ** 2 + (yy - BALL.y) ** 2 <= (BALL.diameter_px / 2) ** 2
    frame[ball_cap] = 250
    return frame, mesh, camera, shaft, template


def test_recovers_known_translation_with_shadow_shaft_and_ball_cap():
    frame, mesh, camera, shaft, _template = _shifted_frame(4.25, -3.5)

    result = align_outline(
        frame,
        BALL,
        mesh,
        camera,
        RANGE_MM,
        shaft.angle_deg,
        33.1,
        shaft_line=shaft,
    )

    assert result.status == "ok"
    assert result.reason == "ok"
    assert result.dx == pytest.approx(4.25, abs=0.25)
    # With the occluded sole removed, the sampled topline/side edges constrain
    # vertical translation to one sensor pixel in this synthetic 320x200 frame.
    assert result.dy == pytest.approx(-3.5, abs=1.0)
    assert result.support >= 0.5
    assert result.residual_px <= 1.5


def test_polarity_filter_rejects_near_shadow_edge():
    frame = np.full((40, 50), MAT_LEVEL, dtype=np.uint8)
    frame[10:20, 10:40] = 70
    frame[22:25, 10:40] = 80

    downward = polarity_consistent_edges(frame, (0.0, 1.0), edge_threshold=80.0)

    assert downward[19:21, 15:35].any()
    assert not downward[21:23, 15:35].any()
    assert downward[24:26, 15:35].any()


def test_nonsole_boundary_drops_only_downward_dominant_normals():
    normals = np.asarray(
        [
            [0.0, -1.0],
            [-1.0, 0.0],
            [1.0, 0.0],
            [0.0, 1.0],
            [0.4, 0.9],
            [0.9, 0.4],
            [-0.9, 0.4],
        ]
    )

    assert nonsole_boundary_mask(normals).tolist() == [True, True, True, False, False, True, True]


def test_nominal_template_reports_fraction_after_dropping_sole():
    template, reason = build_nominal_outline(
        BALL,
        _box_mesh(),
        measured_camera(),
        RANGE_MM,
        shaft_angle_deg=None,
        loft_deg=33.1,
    )

    assert reason == "ok"
    assert template is not None
    assert 0.0 < template.boundary_fraction_kept < 1.0
    assert template.boundary_kept_count < template.boundary_candidate_count
    assert nonsole_boundary_mask(template.normals_xy).all()


def test_linear_motion_carries_each_observation_to_target_frame():
    frames = np.asarray([69.0, 70.0, 71.0])
    centres = np.asarray([[10.0, 20.0], [12.0, 19.0], [14.0, 18.0]])

    velocity, carries = linear_motion_carry(frames, centres, target_frame=71.85)

    assert velocity == pytest.approx([2.0, -1.0])
    assert centres + carries == pytest.approx(np.tile([15.7, 17.15], (3, 1)))


def test_nominal_pose_is_physical_and_flags_static_lie_fallback():
    template, reason = build_nominal_outline(
        BALL,
        _box_mesh(),
        measured_camera(),
        RANGE_MM,
        shaft_angle_deg=None,
        loft_deg=33.1,
    )

    assert reason == "ok"
    assert template is not None
    assert in_envelope(angles_from_pose(*template.pose_yaw_pitch_roll_deg))
    assert template.lie_source == "static_fallback_no_shaft"


def test_out_of_envelope_loft_prior_fails_closed():
    template, reason = build_nominal_outline(
        BALL,
        _box_mesh(),
        measured_camera(),
        RANGE_MM,
        shaft_angle_deg=60.0,
        loft_deg=2.12,
    )

    assert template is None
    assert reason == "nominal_pose_failed"


def test_render_fails_closed_for_empty_mesh():
    mesh = TriangleMesh(
        np.zeros((0, 3), dtype=float),
        np.zeros((0, 3), dtype=np.int32),
        "empty",
        "empty",
    )

    template, reason = build_nominal_outline(
        BALL,
        mesh,
        measured_camera(),
        RANGE_MM,
        shaft_angle_deg=None,
        loft_deg=33.1,
    )

    assert template is None
    assert reason == "render_failed"


def test_tiny_render_fails_closed_without_boundary_support():
    template, reason = build_nominal_outline(
        BALL,
        _box_mesh(scale=0.01),
        measured_camera(),
        RANGE_MM,
        shaft_angle_deg=None,
        loft_deg=33.1,
    )

    assert template is None
    assert reason == "template_boundary_too_small"


def test_alignment_fails_closed_without_image_edges():
    result = align_outline(
        np.full((200, 320), MAT_LEVEL, dtype=np.uint8),
        BALL,
        _box_mesh(),
        measured_camera(),
        RANGE_MM,
        None,
        33.1,
    )

    assert result.status == "failed"
    assert result.reason == "no_polarity_edges"


@pytest.mark.parametrize(
    ("residual", "support", "dx", "dy", "reason"),
    [
        (0.8, 0.8, 12.0, 0.0, "search_boundary"),
        (0.8, 0.49, 0.0, 0.0, "support_below_half"),
        (1.51, 0.8, 0.0, 0.0, "residual_above_1_5"),
        (1.0, 0.5, 0.0, 0.0, "ok"),
    ],
)
def test_every_alignment_gate_has_a_named_reason(residual, support, dx, dy, reason):
    assert alignment_gate_reason(residual, support, dx, dy, search_limit=12.0) == reason

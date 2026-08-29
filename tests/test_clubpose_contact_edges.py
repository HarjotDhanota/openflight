"""Synthetic tests for the contact-frame clubhead edge finders."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from openflight.camera.club_motion import ReferenceBall
from openflight.camera.clubpose.contact_edges import (
    ToplineEdge,
    detect_heel,
    detect_toe,
    detect_topline,
    find_contact_edges,
)

MAT_LEVEL = 150.0
BALL = ReferenceBall(x=70.0, y=68.0, diameter_px=12.0, area_px=113)
TOPLINE_ROW = 62.5
HEEL_X = 48.25
TOE_X = 95.0 + (115.0 - 75.0) / (150.0 - 75.0)


def _synthetic_frame() -> np.ndarray:
    frame = np.full((110, 150), int(MAT_LEVEL), dtype=np.uint8)
    frame[64:79, 48:96] = 75
    frame[62:64, 48:96] = 235
    cv2.line(frame, (24, 14), (48, 62), 240, 3)

    yy, xx = np.indices(frame.shape)
    ball_cap = (xx - BALL.x) ** 2 + (yy - BALL.y) ** 2 <= (BALL.diameter_px / 2) ** 2
    frame[ball_cap] = 250
    return frame


def test_synthetic_frame_recovers_all_three_edges():
    frame = _synthetic_frame()

    result = find_contact_edges(frame, BALL, MAT_LEVEL)

    assert result.status == "ok"
    assert result.reason == "ok"
    assert result.topline_slope == pytest.approx(0.0, abs=0.02)
    assert result.topline_row_at_ball == pytest.approx(TOPLINE_ROW, abs=0.5)
    assert result.heel_x == pytest.approx(HEEL_X, abs=0.5)
    assert result.toe_x == pytest.approx(TOE_X, abs=0.5)
    assert result.width_px == pytest.approx(TOE_X - HEEL_X, abs=0.7)
    assert result.toe_row_spread_px == pytest.approx(0.0, abs=0.1)
    assert result.topline_reason == "ok"
    assert result.heel_reason == "ok"
    assert result.toe_reason == "ok"


def test_topline_excludes_columns_covered_by_ball():
    frame = _synthetic_frame()
    ball_radius = BALL.diameter_px / 2
    covered = np.abs(np.arange(frame.shape[1]) - BALL.x) <= ball_radius + 1.5
    frame[57:59, covered] = 255
    frame[59, covered] = 50

    topline, reason = detect_topline(frame, BALL, MAT_LEVEL)

    assert reason == "ok"
    assert topline is not None
    assert topline.row_at_ball == pytest.approx(TOPLINE_ROW, abs=0.5)
    assert topline.slope == pytest.approx(0.0, abs=0.02)


def test_topline_uses_unoccluded_toe_side_when_shaft_is_brighter():
    frame = _synthetic_frame()
    frame[62:64, 48:79] = int(MAT_LEVEL)
    for x in range(38, 61):
        y = 49 + (x - 38) // 2
        frame[y, x] = 245
        frame[y + 2, x] = 70

    topline, reason = detect_topline(frame, BALL, MAT_LEVEL)

    assert reason == "ok"
    assert topline is not None
    assert topline.row_at_ball == pytest.approx(TOPLINE_ROW, abs=0.5)
    assert topline.slope == pytest.approx(0.0, abs=0.02)


def test_toe_tolerates_one_pixel_speck():
    frame = _synthetic_frame()
    frame[66, 88] = int(MAT_LEVEL)
    topline, reason = detect_topline(frame, BALL, MAT_LEVEL)
    assert reason == "ok"
    assert topline is not None

    toe, reason = detect_toe(frame, BALL, MAT_LEVEL, topline)

    assert reason == "ok"
    assert toe is not None
    assert toe.x == pytest.approx(TOE_X, abs=0.5)


def test_topline_fails_closed_without_a_ridge():
    frame = np.full((110, 150), int(MAT_LEVEL), dtype=np.uint8)

    topline, reason = detect_topline(frame, BALL, MAT_LEVEL)

    assert topline is None
    assert reason == "topline_insufficient_points"


def test_topline_fails_closed_for_invalid_window():
    topline, reason = detect_topline(np.zeros((0, 0), np.uint8), BALL, MAT_LEVEL)

    assert topline is None
    assert reason == "topline_invalid_window"


def test_topline_fails_closed_without_enough_inlier_span():
    frame = _synthetic_frame()
    frame[62:64, 85:96] = int(MAT_LEVEL)

    topline, reason = detect_topline(frame, BALL, MAT_LEVEL)

    assert topline is None
    assert reason == "topline_insufficient_inliers"


def test_toe_fails_closed_without_a_dark_to_mat_crossing():
    frame = _synthetic_frame()
    frame[64:79, 96:103] = 75
    topline, reason = detect_topline(frame, BALL, MAT_LEVEL)
    assert reason == "ok"
    assert topline is not None

    toe, reason = detect_toe(frame, BALL, MAT_LEVEL, topline)

    assert toe is None
    assert reason == "toe_insufficient_crossings"


def test_toe_fails_closed_for_invalid_window():
    toe, reason = detect_toe(
        np.zeros((0, 0), np.uint8), BALL, MAT_LEVEL, ToplineEdge(0.0, TOPLINE_ROW)
    )

    assert toe is None
    assert reason == "toe_invalid_window"


def test_toe_fails_closed_when_start_is_outside_window():
    oversized_ball = ReferenceBall(x=70.0, y=68.0, diameter_px=70.0, area_px=1000)

    toe, reason = detect_toe(
        _synthetic_frame(),
        oversized_ball,
        MAT_LEVEL,
        ToplineEdge(0.0, TOPLINE_ROW),
    )

    assert toe is None
    assert reason == "toe_start_outside_window"


def test_heel_fails_closed_without_enough_shaft_pixels():
    frame = _synthetic_frame()
    frame[8:62, :48] = int(MAT_LEVEL)
    topline, reason = detect_topline(frame, BALL, MAT_LEVEL)
    assert reason == "ok"
    assert topline is not None

    heel, reason = detect_heel(frame, BALL, MAT_LEVEL, topline)

    assert heel is None
    assert reason == "shaft_insufficient_pixels"


def test_heel_fails_closed_for_invalid_window():
    heel, reason = detect_heel(
        np.zeros((2, 2, 2), np.uint8), BALL, MAT_LEVEL, ToplineEdge(0.0, TOPLINE_ROW)
    )

    assert heel is None
    assert reason == "shaft_invalid_window"


def test_heel_fails_closed_without_enough_pca_inliers():
    frame = np.full((110, 150), int(MAT_LEVEL), dtype=np.uint8)
    for x in (20, 30, 40, 50, 60):
        for y in (20, 28, 36, 44, 52):
            frame[y, x] = 240

    heel, reason = detect_heel(frame, BALL, MAT_LEVEL, ToplineEdge(0.0, TOPLINE_ROW))

    assert heel is None
    assert reason == "shaft_insufficient_inliers"


def test_heel_fails_closed_when_shaft_is_parallel_to_topline():
    frame = np.full((110, 150), int(MAT_LEVEL), dtype=np.uint8)
    cv2.line(frame, (10, 45), (62, 45), 240, 2)

    heel, reason = detect_heel(frame, BALL, MAT_LEVEL, ToplineEdge(0.0, TOPLINE_ROW))

    assert heel is None
    assert reason == "shaft_parallel_to_topline"


def test_heel_fails_closed_when_intersection_is_outside_topline():
    frame = np.full((110, 150), int(MAT_LEVEL), dtype=np.uint8)
    cv2.line(frame, (20, 10), (60, 50), 240, 2)

    heel, reason = detect_heel(frame, BALL, MAT_LEVEL, ToplineEdge(0.0, TOPLINE_ROW))

    assert heel is None
    assert reason == "heel_outside_topline"


def test_composite_preserves_named_dependency_failures():
    frame = np.full((110, 150), int(MAT_LEVEL), dtype=np.uint8)

    result = find_contact_edges(frame, BALL, MAT_LEVEL)

    assert result.status == "failed"
    assert result.reason == "topline_insufficient_points"
    assert result.topline_reason == "topline_insufficient_points"
    assert result.heel_reason == "topline_unavailable"
    assert result.toe_reason == "topline_unavailable"

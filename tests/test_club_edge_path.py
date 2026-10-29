"""Output parity under valid, mirrored, clipped, empty and tied input evidence."""

import cv2
import numpy as np

from openflight.camera._club_edge_path import _trace_full, trace_column


def scene(origin=(0, 0), mirror=False):
    dx, dy = origin
    edges = np.zeros((400, 640), np.uint8)
    cv2.line(edges, (43 + dx, 70 + dy), (80 + dx, 40 + dy), 1, 1)
    cv2.line(edges, (43 + dx, 90 + dy), (80 + dx, 50 + dy), 1, 1)
    change = cv2.GaussianBlur(edges.astype(float) * 8, (5, 5), 1)
    neck = np.array([[43.0 + dx, float(v) + dy] for v in range(64, 95)])
    tip, anchor = np.array([80.0 + dx, 40.0 + dy]), np.array([40.0 + dx, 90.0 + dy])
    side = 1
    if mirror:
        edges, change = np.fliplr(edges).copy(), np.fliplr(change).copy()
        neck[:, 0], tip[0], anchor[0], side = 639 - neck[:, 0], 639 - tip[0], 639 - anchor[0], -1
    return [
        tip,
        neck,
        anchor,
        np.eye(2),
        side,
        6.0,
        edges.astype(bool),
        change,
        np.ones(edges.shape, bool),
    ]


def test_translated_and_mirrored_field_matches_exactly():
    for origin in ((0, 0), (215, 160), (480, 270)):
        for mirrored in (False, True):
            args = scene(origin, mirrored)
            assert trace_column(*args) == _trace_full(*args)


def test_invalid_barrier_preserves_refusal():
    args = scene((215, 160))
    args[-1][:, 270:275] = False
    assert trace_column(*args) == _trace_full(*args) is None


def test_empty_edge_image_preserves_reference_decision():
    args = scene((215, 160))
    args[6][:] = False
    assert trace_column(*args) == _trace_full(*args)


def test_frame_edge_crop_matches_reference():
    args = scene((-42, -39))
    assert trace_column(*args) == _trace_full(*args)


def test_random_subpixel_controls_and_edges_match_exactly():
    rng = np.random.default_rng(2826)
    for _ in range(25):
        args = scene((215, 160))
        args[0] += rng.uniform(-0.45, 0.45, 2)
        args[2] += rng.uniform(-0.45, 0.45, 2)
        angle = rng.uniform(-0.2, 0.2)
        args[3] = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
        args[5] += rng.uniform(-1, 1)
        xy = rng.integers([200, 180], [330, 260], size=(100, 2))
        args[6][xy[:, 1], xy[:, 0]] = True
        assert trace_column(*args) == _trace_full(*args)


def test_distant_unrelated_edges_cannot_change_the_optimized_path():
    args = scene((215, 160))
    before = trace_column(*args)
    args[6][350:370, 500:520] = True
    assert trace_column(*args) == before


def test_inputs_are_not_mutated():
    args = scene((215, 160))
    copies = [a.copy() if isinstance(a, np.ndarray) else a for a in args]
    trace_column(*args)
    for a, b in zip(args, copies, strict=True):
        assert np.array_equal(a, b)
